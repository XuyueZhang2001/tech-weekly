"""web_search 信源类型的端到端测试：本地假搜索服务，不联网、不花钱。

覆盖：
  1. Brave：解析、跨查询去重、来源取域名
  2. Tavily：POST 请求体（api_key/query/days）
  3. SearXNG：base_url 作为 key 的自托管用法
  4. 没配 Key / 不支持的服务商 -> 明确报错
  5. 标题关键词过滤能否套用
用法：python tests/test_web_search.py
"""
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import main as app  # noqa: E402

RESULTS = []


def check(name, passed, detail=""):
    RESULTS.append((name, bool(passed), str(detail)))


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _json(self, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/res/v1/web/search"):
            # 两条查询都返回同一条，用来验证跨查询去重
            self._json({"web": {"results": [
                {"title": "芯片进展甲", "url": "https://example.com/a",
                 "description": "这是甲的描述", "page_age": "2026-10-20T00:00:00"},
                {"title": "芯片进展乙", "url": "https://example.com/b",
                 "description": "这是乙的描述", "page_age": "2026-10-21T00:00:00"},
            ]}})
            return
        if self.path.startswith("/searxng/search"):
            self._json({"results": [
                {"title": "自托管结果", "url": "https://self.example.org/x",
                 "content": "自托管摘要", "publishedDate": "2026-10-19T00:00:00"},
            ]})
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        self.server.last_payload = payload
        self._json({"results": [
            {"title": "Tavily结果", "url": "https://tavily.example.com/t",
             "content": "Tavily 摘要", "published_date": "2026-10-21T00:00:00"},
        ]})


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    base = "http://127.0.0.1:%d" % port
    threading.Thread(target=server.serve_forever, daemon=True).start()
    time.sleep(0.2)

    try:
        cfg = {"fetch": {"timeout_seconds": 10, "user_agent": "test"}}

        # 1) Brave
        src = {"type": "web_search", "provider": "brave", "api_key": "test-key",
               "base_url": base, "queries": ["芯片 甲", "芯片 乙"], "limit": 5,
               "weight": 10, "group": "脑机接口"}
        items = app.fetch_web_search(src, "china", cfg)
        check("Brave 解析出 2 条结果", len(items) == 2, "得到 %d 条" % len(items))
        check("Brave 跨查询去重生效", len({a.url for a in items}) == len(items))
        check("来源取网页域名", items[0].source == "example.com", items[0].source)
        check("摘要非空", bool(items[0].summary), items[0].summary[:30])
        check("group 透传", items[0].group == "脑机接口", items[0].group)

        # 2) Tavily（POST）
        src_t = {"type": "web_search", "provider": "tavily", "api_key": "tavily-key",
                 "base_url": base, "queries": ["测试"], "limit": 5, "days": 7}
        items_t = app.fetch_web_search(src_t, "ai", cfg)
        check("Tavily 解析成功", len(items_t) == 1 and items_t[0].url.endswith("/t"),
              items_t[0].url if items_t else "无")
        payload = getattr(server, "last_payload", {}) or {}
        check("Tavily 请求体含 api_key", payload.get("api_key") == "tavily-key", str(payload)[:80])
        check("Tavily 请求体含 query", payload.get("query") == "测试", str(payload.get("query")))

        # 3) SearXNG（base_url 作为 key）
        src_s = {"type": "web_search", "provider": "searxng",
                 "api_key": base + "/searxng", "queries": ["测试"], "limit": 5}
        items_s = app.fetch_web_search(src_s, "ai", cfg)
        check("SearXNG 解析成功", len(items_s) == 1 and "self.example.org" in items_s[0].url,
              items_s[0].url if items_s else "无")

        # 4) 缺少 Key / 未知服务商
        try:
            app.fetch_web_search({"provider": "brave", "queries": ["x"], "api_key": ""},
                                 "ai", cfg)
            check("缺 Key 时抛错", False, "没有抛错")
        except ValueError as exc:
            check("缺 Key 时抛错", "Key" in str(exc), str(exc)[:40])
        try:
            app.fetch_web_search({"provider": "bing", "queries": ["x"], "api_key": "k"},
                                 "ai", cfg)
            check("不支持的服务商抛错", False, "没有抛错")
        except ValueError as exc:
            check("不支持的服务商抛错", "不支持" in str(exc), str(exc)[:40])

        # 5) 关键词过滤可套用
        src_f = dict(src)
        src_f["title_keywords"] = ["苯"]          # 故意不匹配
        kept = [a for a in items if app.source_relevance_ok(a, src_f)]
        check("标题过滤可拦掉不相关", len(kept) == 0, "剩 %d 条" % len(kept))

        # 6) 环境变量优先于 config
        os.environ["BRAVE_API_KEY"] = "from-env"
        check("环境变量 Key 优先", app._search_provider_key({"api_key": "from-config"}, "brave")
              == "from-env", app._search_provider_key({"api_key": "from-config"}, "brave"))
        os.environ.pop("BRAVE_API_KEY", None)
    finally:
        server.shutdown()
        server.server_close()

    print()
    ok = True
    for name, passed, detail in RESULTS:
        print("%s %-28s %s" % ("PASS" if passed else "FAIL", name, detail))
        ok = ok and passed
    print()
    print("web_search 测试：%d/%d 通过" % (sum(1 for _n, p, _d in RESULTS if p), len(RESULTS)))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
