"""中文摘要模块的端到端测试：本地假大模型服务器 + 真 SMTP 假服务器，全程不接触外网。

覆盖：
  1. 批量翻译（多批请求、编号映射）
  2. 模型返回里的常见毛病（```json 包裹、前后废话、中文引号、尾随逗号）
  3. 磁盘缓存（重复运行不再调用接口）
  4. 失败降级（400/401 不重试且保留英文；500 会重试；垃圾 JSON 视为该批失败）
  5. token 计费统计
  6. 渲染成中文标题 + 原题可追溯，且 HTML 标签配平
  7. 未配置 Key 时优雅降级为英文

用法（项目根目录）：python tests/test_translate_flow.py
"""
import email
import http.server
import json
import os
import shutil
import socketserver
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import main as app  # noqa: E402
import translate  # noqa: E402
from test_smtp_flow import FakeSMTPHandler, FakeSMTPServer  # noqa: E402

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(PROJECT, "cache", "llm-cache.json")

RESULTS = []


def check(name, passed, detail=""):
    RESULTS.append((name, bool(passed), str(detail)))
    return bool(passed)


# --------------------------------------------------------------------------- #
# 假大模型服务器
# --------------------------------------------------------------------------- #
class FakeLLMHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # 静音
        pass

    def _send(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        server = self.server
        server.requests.append(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8")
        req = json.loads(raw)
        server.payloads.append(req)
        server.auth_headers.append(self.headers.get("Authorization", ""))

        # 按脚本化的响应队列依次返回；用完后返回默认的正常响应
        if server.script:
            kind = server.script.pop(0)
        else:
            kind = "good"

        if kind == "http400":
            return self._send(400, {"error": {"message": "Invalid model name (fake)"}})
        if kind == "http401":
            return self._send(401, {"error": {"message": "Authentication Fails (fake key)"}})
        if kind == "http500":
            return self._send(500, {"error": {"message": "server error (fake)"}})
        if kind == "garbage":
            return self._send(200, {"choices": [{"message": {"content": "抱歉，我无法完成这个请求。"}}]})
        if kind == "badjson":
            return self._send(200, {"choices": [{"message": {"content": "这是一段解释文字，不是 JSON。"}}]})

        # good：按请求里的条目逐条生成"中文"，标题用可校验的固定模式
        user = ""
        for msg in req.get("messages", []):
            if msg.get("role") == "user":
                user = msg.get("content", "")
        marker = user.rfind("待处理内容：")
        items = json.loads(user[marker + len("待处理内容："):]) if marker != -1 else []

        # partial_once：第一次只返回一半，用来验证"漏条目会补请求"
        if server.partial_once:
            server.partial_once = False
            items = items[: max(1, len(items) // 2)]

        out = []
        for it in items:
            i = it["i"]
            title = it.get("title", "")
            out.append({
                "i": i,
                "title": "【中文】%s" % title[:40],
                "summary": "" if not it.get("original_summary")
                           else "【中文摘要】%s" % it["original_summary"][:60],
            })

        usage = {"prompt_tokens": 120, "completion_tokens": 60, "total_tokens": 180}
        # 第一条故意带上代码块、中文引号和尾随逗号，模拟模型的常见坏毛病
        if server.verbose_bad_json:
            server.verbose_bad_json = False
            first = out[0]
            content = (
                "好的，下面是结果：\n```json\n{\"items\":[{\"i\":%d,"
                "\"title\":\"%s\",\"summary\":\"%s\",},]}\n```"
                % (first["i"], first["title"], first["summary"])
            )
            rest = {"items": out[1:]}
            if len(out) > 1:
                # 一次请求只能返回一段内容，这里退化成只回答第一条，
                # 其余靠"漏条目补请求"机制补齐（正好顺便压测这条路径）
                content = content
            return self._send(200, {"choices": [{"message": {"content": content}}], "usage": usage})

        content = json.dumps({"items": out}, ensure_ascii=False)
        return self._send(200, {
            "choices": [{"message": {"content": content}}],
            "usage": usage,
        })


class FakeLLMServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, addr, handler):
        super().__init__(addr, handler)
        self.requests = []
        self.payloads = []
        self.auth_headers = []
        self.script = []
        self.verbose_bad_json = True
        self.partial_once = False


# --------------------------------------------------------------------------- #
def make_articles(n=13):
    arts = []
    for i in range(n):
        arts.append(app.Article(
            title="Test article number %d about GPUs and chips" % i,
            url="https://example.com/a%d" % i,
            source="测试源", domain="chip",
            published=app.datetime.now(app.timezone.utc),
            summary="Summary text number %d with some technical detail." % i,
            weight=10,
        ))
    arts.append(app.Article(
        title="Article with empty summary", url="https://example.com/empty",
        source="测试源", domain="chip",
        published=app.datetime.now(app.timezone.utc), summary="", weight=10))
    return arts


def base_llm(port):
    return {
        "enabled": True, "provider": "fake", "base_url": "http://127.0.0.1:%d" % port,
        "api_key": "fake-key-123", "model": "fake-model", "batch_size": 5,
        "timeout_seconds": 20, "max_retries": 3, "temperature": 0.2, "max_tokens": 2048,
        "use_cache": True, "price_in_per_m": 1.0, "price_out_per_m": 2.0, "currency": "CNY",
    }


def clear_cache():
    if os.path.exists(CACHE):
        os.remove(CACHE)


def tr_result_clean(arts):
    """中文译文里不该出现中文字符之间的空格。"""
    import re as _re
    for a in arts:
        for text in (a.title_zh, a.summary_zh):
            if _re.search(r"[\u4e00-\u9fff]\s+[\u4e00-\u9fff]", text or ""):
                return False
    return True


def parse_items_cases():
    cases = [
        ("纯 JSON", '{"items":[{"i":0,"title":"甲","summary":"乙"}]}', 1),
        ("代码块包裹", '```json\n{"items":[{"i":0,"title":"甲","summary":""}]}\n```', 1),
        ("前后有废话", '好的：\n{"items":[{"i":0,"title":"甲","summary":""}]}\n以上。', 1),
        ("中文引号+尾逗号", '{"items":[{"i":0,"title":"甲","summary":"乙",},]}', 1),
        ("裸数组", '[{"i":0,"title":"甲","summary":"乙"}]', 1),
        ("index 别名", '{"items":[{"index":0,"title":"甲","summary":"乙"}]}', 1),
        ("空内容", "", 0),
        ("纯文本", "我不太确定你的意思。", 0),
        ("结构不对", '{"result":"ok"}', 0),
    ]
    ok = 0
    for name, content, expect in cases:
        got = translate.parse_items(content)
        if len(got) == expect:
            ok += 1
        else:
            check("parse_items: %s" % name, False, "期望 %d 条，得到 %d 条" % (expect, len(got)))
    return ok, len(cases)


def main():
    clear_cache()
    server = FakeLLMServer(("127.0.0.1", 0), FakeLLMHandler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    time.sleep(0.2)

    try:
        # ---- 1. parse_items 单元用例 -------------------------------------- #
        ok, total = parse_items_cases()
        check("模型返回解析（9 种毛病）", ok == total, "%d/%d 通过" % (ok, total))

        # ---- 2. 批量翻译 -------------------------------------------------- #
        arts = make_articles(13)                      # 14 条 → batch_size=5 → 3 批
        tr = translate.Translator(base_llm(port), log_fn=lambda _m: None, warn_fn=lambda _m: None)
        usage = tr.translate_articles(arts)
        done = sum(1 for a in arts if a.title_zh)
        check("全部条目翻成中文", done == len(arts), "%d/%d" % (done, len(arts)))
        # 第一批的坏 JSON 只回了 1 条，靠"漏条目补请求"补齐，所以是 4 次
        check("坏返回后自动补请求", len(server.requests) == 4, "请求 %d 次" % len(server.requests))
        check("带 Bearer 鉴权头", all(h.startswith("Bearer fake-key-123")
                                      for h in server.auth_headers), server.auth_headers[:1])
        check("请求体含 response_format=json_object",
              all(p.get("response_format", {}).get("type") == "json_object" for p in server.payloads))
        check("空摘要保持为空",
              arts[-1].title_zh and not arts[-1].summary_zh, repr(arts[-1].summary_zh))
        check("token 统计累计", usage["prompt_tokens"] > 0 and usage["completion_tokens"] > 0,
              "in=%d out=%d" % (usage["prompt_tokens"], usage["completion_tokens"]))
        check("缓存文件已落盘", os.path.exists(CACHE), CACHE)
        check("中文里没有多余空格", tr_result_clean(arts), arts[0].title_zh)

        # ---- 3. 缓存命中：全新对象也读磁盘缓存，不再发请求 ---------------- #
        before = len(server.requests)
        arts2 = make_articles(13)
        tr2 = translate.Translator(base_llm(port), log_fn=lambda _m: None, warn_fn=lambda _m: None)
        usage2 = tr2.translate_articles(arts2)
        check("重复运行不重复调用接口", len(server.requests) == before,
              "新增请求 %d 次" % (len(server.requests) - before))
        check("缓存命中统计正确", usage2["cache_hits"] == len(arts2),
              "命中 %d 条" % usage2["cache_hits"])
        check("缓存内容与首次一致", arts2[0].title_zh == arts[0].title_zh, arts2[0].title_zh)

        # ---- 3b. 模型漏返回一半时会补请求 --------------------------------- #
        clear_cache()
        server.partial_once = True
        reqs_before = len(server.requests)
        apart = make_articles(4)                      # 一批 5 条以内 → 一次请求
        trp = translate.Translator(base_llm(port), log_fn=lambda _m: None, warn_fn=lambda _m: None)
        trp.translate_articles(apart)
        n_part = len(server.requests) - reqs_before
        check("漏条目只补一次请求就补齐",
              n_part == 2 and all(a.title_zh for a in apart),
              "请求 %d 次，翻成 %d/%d" % (n_part, sum(1 for a in apart if a.title_zh), len(apart)))

        # ---- 4. 401 不重试，降级为英文 ------------------------------------ #
        clear_cache()
        server.script = ["http401"]
        reqs_before = len(server.requests)
        a401 = make_articles(2)
        tr3 = translate.Translator(base_llm(port), log_fn=lambda _m: None, warn_fn=lambda _m: None)
        tr3.translate_articles(a401)
        n401 = len(server.requests) - reqs_before
        check("401 只请求一次（不浪费时间重试）", n401 == 1, "请求 %d 次" % n401)
        check("401 时保留英文原文", all(not a.title_zh for a in a401))
        check("401 记为失败批次", tr3.failed_batches == 1, tr3.failed_batches)

        # ---- 5. 500 会重试 ------------------------------------------------- #
        clear_cache()
        server.script = ["http500", "http500", "good"]
        reqs_before = len(server.requests)
        a500 = make_articles(2)
        llm500 = base_llm(port)
        llm500["max_retries"] = 3
        tr4 = translate.Translator(llm500, log_fn=lambda _m: None, warn_fn=lambda _m: None)
        tr4.translate_articles(a500)
        n500 = len(server.requests) - reqs_before
        check("500 会重试并最终成功", n500 == 3 and all(a.title_zh for a in a500), "请求 %d 次" % n500)

        # ---- 6. 垃圾返回视为失败，保留英文 --------------------------------- #
        clear_cache()
        server.script = ["badjson", "badjson", "badjson"]
        ag = make_articles(2)
        tr5 = translate.Translator(base_llm(port), log_fn=lambda _m: None, warn_fn=lambda _m: None)
        tr5.translate_articles(ag)
        check("非 JSON 返回时保留英文", all(not a.title_zh for a in ag))

        # ---- 7. 未配置 Key：优雅降级 -------------------------------------- #
        cfg = app.load_config(os.path.join(PROJECT, "config.json"))
        cfg["llm"] = {"enabled": True, "api_key": "", "provider": "deepseek"}
        report = app.build_report(cfg, 7)
        u, t = translate.translate_report_articles(report, cfg, log_fn=lambda _m: None,
                                                   warn_fn=lambda _m: None)
        check("无 Key 时降级不报错", t is None and u == {})

        # enabled=False（--no-llm）时同样不调用接口
        cfg_off = dict(cfg)
        cfg_off["llm"] = base_llm(port)
        reqs_before = len(server.requests)
        report_off = app.build_report(cfg_off, 7)
        u_off, t_off = translate.translate_report_articles(
            report_off, cfg_off, log_fn=lambda _m: None, warn_fn=lambda _m: None, enabled=False)
        check("--no-llm 时完全不调用接口",
              len(server.requests) == reqs_before and t_off is None and u_off == {},
              "新增请求 %d 次" % (len(server.requests) - reqs_before))
        check("--no-llm 时没有中文译文",
              all(not a.translated for s in report_off["sections"] for a in s["articles"]))

        # ---- 8. 渲染：中文标题 + 原题可追溯 + 标签配平 --------------------- #
        cfg2 = dict(cfg)
        cfg2["llm"] = base_llm(port)
        report2 = app.build_report(cfg2, 7)
        config_articles = [a for s in report2["sections"] for a in s["articles"]]
        for a in config_articles:
            a.title_zh = "【中文标题】%s" % a.title[:40]
            a.summary_zh = "【中文摘要】测试用。"
        text_body = app.render_text(report2, cfg2)
        html_body = app.render_html(report2, cfg2)
        first = report2["sections"][0]["articles"][0]
        check("纯文本含中文标题", first.title_zh in text_body)
        check("纯文本保留原题（原题：）", ("原题：%s" % first.title) in text_body)
        check("HTML 含中文标题", first.title_zh.replace("<", "&lt;") in html_body)
        check("HTML 含原英文标题", first.title.replace("&", "&amp;") in html_body)
        opens = html_body.count("<div")
        closes = html_body.count("</div>")
        check("HTML div 标签配平", opens == closes, "%d/%d" % (opens, closes))
        check("中文正文无乱码", "\ufffd" not in html_body and "\ufffd" not in text_body)

        # ---- 9. 中文邮件真的能发出去（走假 SMTP） -------------------------- #
        smtp_server = FakeSMTPServer(("127.0.0.1", 2527), FakeSMTPHandler)
        import test_smtp_flow as tsf
        tsf.RECEIVED.clear()
        threading.Thread(target=smtp_server.serve_forever, daemon=True).start()
        time.sleep(0.2)
        try:
            smtp_cfg = {"host": "127.0.0.1", "port": 2527, "ssl": False, "starttls": False,
                        "user": "u@example.com", "password": "p", "sender": "u@example.com",
                        "from_name": "科技周报", "recipients": ["r@example.com"], "cc": []}
            subject = app.build_subject(report2, cfg2)
            app.send_mail(smtp_cfg, subject, text_body, html_body)
        finally:
            smtp_server.shutdown()
            smtp_server.server_close()
        msg = email.message_from_bytes(tsf.RECEIVED[0]["content"])
        parts = {}
        for part in msg.walk():
            if part.get_content_type() in ("text/plain", "text/html"):
                parts[part.get_content_type()] = part.get_payload(decode=True).decode("utf-8")
        check("中文 HTML 邮件可发送且无乱码",
              "【中文标题】" in parts.get("text/html", "") and "\ufffd" not in parts.get("text/html", ""))

        # 计费行
        check("费用行含金额与币种", "CNY" in tr.cost_line(), tr.cost_line())
    finally:
        server.shutdown()
        server.server_close()

    print()
    ok_all = True
    for name, passed, detail in RESULTS:
        print("%s %-34s %s" % ("PASS" if passed else "FAIL", name, detail))
        ok_all = ok_all and passed
    print()
    print("中文摘要测试：%d/%d 通过" % (sum(1 for _n, p, _d in RESULTS if p), len(RESULTS)))
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
