#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tech-weekly 的中文摘要模块。

设计要点：
1. 走 OpenAI 兼容的 /chat/completions 接口，所以 DeepSeek / Kimi / 通义 / OpenAI / 智谱
   / 本地 Ollama 都只是改 config.json 里的三行配置。
2. 反复用同一封邮件里的多条内容合并成一次请求（batch_size 条），把 API 调用次数和费用压到最低。
3. 结果按正文链接落盘缓存（cache/llm-cache.json），同一条内容重复运行不会重复付费。
4. 只依赖标准库：用 urllib 发请求、json 解析，不引入任何第三方包。
5. 任何失败都退化成"保留英文"，绝不因为翻译问题让整封周报发不出去。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(HERE, "cache", "llm-cache.json")
CACHE_VERSION = 1

SYSTEM_PROMPT = (
    "你是一名中文科技媒体编辑，负责把英文科技资讯改写成简体中文。"
    "要求：\n"
    "1. 保留专有名词原样，不要翻译或音译公司名、产品名、模型名、论文名（如 OpenAI、GPT-5、"
    "arXiv、CUDA、HBM、CRISPR、Transformer）。\n"
    "2. 标题：改写成中文标题，不超过 30 个字，不要句末句号，不要加引号，不要空话。\n"
    "3. 摘要：用一两句中文说清「做了什么 / 发现了什么 / 为什么重要」，不超过 110 个字。\n"
    "4. 只依据给出的原文改写，不要补充原文没有的数字、结论或评价。\n"
    "5. 原文本身就是中文时，只做通顺化处理，不要改变事实。\n"
    "6. 如果 original_summary 为空字符串，summary 也必须返回空字符串。\n"
    "7. 只输出 JSON，不要输出 Markdown 代码块或任何解释文字。\n"
    "8. 下列术语必须按指定译法翻译（同一篇内前后必须一致）：\n"
    "   agent → 智能体；agentic → 智能体式；LLM agent → 大模型智能体；\n"
    "   inference → 推理；training → 训练；benchmark → 基准测试；\n"
    "   open-source model → 开源模型；fine-tuning → 微调；\n"
    "   advanced packaging → 先进封装；wafer → 晶圆；lithography → 光刻；\n"
    "   clinical trial → 临床试验；gene therapy → 基因治疗；\n"
    "   brain-computer interface / BCI → 脑机接口；\n"
    "   humanoid robot → 人形机器人；embodied AI → 具身智能；\n"
    "   electric vehicle → 电动汽车；battery → 电池。\n"
    "   这些词若在原文中作为产品名或型号出现（如 Agent 手机、BCI Society），可保留英文。"
)

USER_TEMPLATE = (
    "请把下面 %d 条科技资讯改写成中文，严格按这个 JSON 结构返回：\n"
    '{"items":[{"i":<编号>,"title":"<中文标题>","summary":"<中文摘要>"}]}\n'
    "items 必须包含全部编号，顺序不限。\n\n"
    "待处理内容：\n%s"
)


# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #
def llm_config(cfg: dict) -> dict:
    raw = dict(cfg.get("llm") or {})
    env = os.environ.get
    return {
        "enabled": _as_bool(env("LLM_ENABLED"), raw.get("enabled", True)),
        "provider": raw.get("provider", "deepseek"),
        "base_url": (env("LLM_BASE_URL") or raw.get("base_url") or "https://api.deepseek.com").rstrip("/"),
        "api_key": (env("LLM_API_KEY") or raw.get("api_key") or "").strip(),
        "model": env("LLM_MODEL") or raw.get("model") or "deepseek-chat",
        "batch_size": int(env("LLM_BATCH_SIZE") or raw.get("batch_size") or 10),
        "timeout_seconds": int(raw.get("timeout_seconds") or 120),
        "max_retries": int(raw.get("max_retries") or 3),
        "temperature": float(raw.get("temperature", 0.2)),
        "max_tokens": int(raw.get("max_tokens") or 2048),
        "use_cache": _as_bool(None, raw.get("use_cache", True)),
        "price_in_per_m": float(raw.get("price_in_per_m") or 0.0),
        "price_out_per_m": float(raw.get("price_out_per_m") or 0.0),
        "currency": raw.get("currency", "CNY"),
    }


def _as_bool(value, default: bool) -> bool:
    if value is None or value == "":
        return bool(default)
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def describe(llm: dict) -> str:
    return "%s / %s @ %s" % (llm.get("provider", "?"), llm.get("model", "?"), llm.get("base_url", "?"))


# --------------------------------------------------------------------------- #
# 缓存
# --------------------------------------------------------------------------- #
class Cache:
    def __init__(self, path: str = CACHE_PATH, max_entries: int = 4000):
        self.path = path
        self.max_entries = max_entries
        self.data = {}
        self.hits = 0
        self._dirty = False
        self._load()

    def _load(self):
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                blob = json.load(fh)
            if blob.get("version") == CACHE_VERSION:
                self.data = blob.get("items") or {}
        except Exception:  # noqa: BLE001 - 缓存坏了就当作空缓存，不能因此中断
            self.data = {}

    def key(self, article, model: str) -> str:
        raw = "|".join([
            CACHE_VERSION.__str__(), model,
            (getattr(article, "title", "") or "").strip(),
            (getattr(article, "summary", "") or "").strip()[:400],
        ])
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def get(self, key: str):
        item = self.data.get(key)
        if item:
            self.hits += 1
            return item
        return None

    def put(self, key: str, title: str, summary: str):
        self.data[key] = {"title": title, "summary": summary, "at": int(time.time())}
        self._dirty = True

    def save(self):
        if not self._dirty:
            return
        if len(self.data) > self.max_entries:
            ordered = sorted(self.data.items(), key=lambda kv: kv[1].get("at", 0), reverse=True)
            self.data = dict(ordered[: self.max_entries])
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as fh:
                json.dump({"version": CACHE_VERSION, "items": self.data}, fh,
                          ensure_ascii=False, separators=(",", ":"))
            self._dirty = False
        except Exception:  # noqa: BLE001 - 缓存写不进去不算错误
            pass


# --------------------------------------------------------------------------- #
# 翻译器
# --------------------------------------------------------------------------- #
def clean_zh(text: str) -> str:
    """去掉中文与英文/数字之间被模型塞进去的空格，纯文本邮件里更顺眼。"""
    if not text:
        return ""
    text = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", text)      # 中文 中文
    text = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[A-Za-z0-9(])", "", text)        # 中文 英文
    text = re.sub(r"(?<=[A-Za-z0-9)%.])\s+(?=[\u4e00-\u9fff])", "", text)      # 英文 中文
    return " ".join(text.split())


class Translator:
    """把 Articles 的标题与摘要改写成中文。失败时保留英文，不抛出。"""

    def __init__(self, llm: dict, log_fn=None, warn_fn=None):
        self.llm = llm
        self._log = log_fn or (lambda m: None)
        self._warn = warn_fn or (lambda m: None)
        self.cache = Cache() if llm.get("use_cache", True) else None
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
                      "requests": 0, "failures": 0, "cache_hits": 0}
        self.failed_batches = 0

    # ---- 对外入口 ---------------------------------------------------------- #
    def translate_articles(self, articles: list) -> dict:
        """返回统计信息；就地修改 article 的 title_zh / summary_zh 字段。"""
        pending = []
        for art in articles:
            art.title_zh, art.summary_zh = "", ""
            key = None
            if self.cache:
                key = self.cache.key(art, self.llm["model"])
                hit = self.cache.get(key)
                if hit:
                    art.title_zh, art.summary_zh = hit.get("title", ""), hit.get("summary", "")
                    self.usage["cache_hits"] += 1
                    continue
            pending.append((art, key))

        if not pending:
            self._log("  全部命中缓存（%d 条）" % self.usage["cache_hits"])
            return self.usage

        size = max(1, int(self.llm.get("batch_size", 10)))
        batches = [pending[i:i + size] for i in range(0, len(pending), size)]
        self._log("  待翻译 %d 条，分 %d 批提交（每批最多 %d 条）"
                  % (len(pending), len(batches), size))

        for idx, batch in enumerate(batches, 1):
            still_missing = batch
            # 模型偶尔会漏掉几条，只对漏掉的那些再补一次请求，避免整篇内容掉队
            for attempt in (1, 2):
                try:
                    still_missing = self._translate_batch(still_missing)
                except Exception as exc:  # noqa: BLE001
                    self.failed_batches += 1
                    self.usage["failures"] += 1
                    self._warn("  第 %d/%d 批失败，这批保留英文：%s"
                               % (idx, len(batches), str(exc)[:160]))
                    break
                if not still_missing:
                    break
                self._warn("  第 %d/%d 批有 %d 条未被模型返回，补一次请求"
                           % (idx, len(batches), len(still_missing)))
            self._log("  第 %d/%d 批完成" % (idx, len(batches)))
        if self.cache:
            self.cache.save()
        return self.usage

    # ---- 单批 -------------------------------------------------------------- #
    def _translate_batch(self, batch: list) -> list:
        """翻译一批，返回其中"模型没有返回"的 (article, key) 列表。"""
        arts = [a for a, _ in batch]
        source_items = []
        for i, art in enumerate(arts):
            source_items.append({
                "i": i,
                "title": (art.title or "")[:300],
                "original_summary": (art.summary or "")[:700],
                "source": art.source or "",
            })
        user = USER_TEMPLATE % (len(source_items),
                                json.dumps(source_items, ensure_ascii=False, indent=1))
        content = self._chat(user)
        parsed = parse_items(content)
        if not parsed:
            raise ValueError("模型返回里没有可用的 items")

        got = 0
        seen = set()
        for item in parsed:
            idx = item.get("i")
            if not isinstance(idx, int) or not (0 <= idx < len(arts)) or idx in seen:
                continue
            seen.add(idx)
            art = arts[idx]
            title_zh = clean_zh(item.get("title") or "").strip("\"'“”")
            summary_zh = clean_zh(item.get("summary") or "")
            if not title_zh:
                continue
            art.title_zh = title_zh
            art.summary_zh = "" if not art.summary else summary_zh
            if self.cache:
                key = self.cache.key(art, self.llm["model"])
                self.cache.put(key, art.title_zh, art.summary_zh)
            got += 1
        if got == 0:
            raise ValueError("模型返回的编号与请求不匹配")
        # 返回"这一批里还没翻成的"，调用方会对这些补一次请求
        return [pair for pair in batch if not pair[0].title_zh]

    # ---- HTTP -------------------------------------------------------------- #
    def _chat(self, user_prompt: str) -> str:
        llm = self.llm
        if not llm.get("api_key"):
            raise RuntimeError("未配置 LLM API Key")
        url = llm["base_url"] + "/chat/completions"
        payload = {
            "model": llm["model"],
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": llm.get("temperature", 0.2),
            "max_tokens": llm.get("max_tokens", 2048),
            # 强制 JSON 输出，解析更稳。若你的服务商不支持这个参数，
            # 把下面这行删掉即可——parse_items() 本身能容忍代码块和前后废话。
            "response_format": {"type": "json_object"},
            "stream": False,
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer %s" % llm["api_key"],
            "Accept": "application/json",
            "User-Agent": "tech-weekly/1.0",
        }
        last_err = None
        for attempt in range(max(1, int(llm.get("max_retries", 3)))):
            try:
                req = urllib.request.Request(url, data=body, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=int(llm.get("timeout_seconds", 120))) as resp:
                    raw = resp.read().decode("utf-8", "replace")
                data = json.loads(raw)
                self._account(data.get("usage") or {})
                choices = data.get("choices") or []
                if not choices:
                    raise ValueError("响应里没有 choices: %s" % raw[:200])
                return choices[0].get("message", {}).get("content") or ""
            except urllib.error.HTTPError as exc:
                detail = ""
                try:
                    detail = exc.read().decode("utf-8", "replace")[:300]
                except Exception:  # noqa: BLE001
                    pass
                last_err = RuntimeError("HTTP %s %s" % (exc.code, detail))
                # 401/403 是 Key 或权限问题，重试没有意义
                if exc.code in (400, 401, 403, 404, 422):
                    raise last_err
                time.sleep(2 * (attempt + 1))
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                time.sleep(2 * (attempt + 1))
        raise last_err  # type: ignore[misc]

    def _account(self, usage: dict):
        self.usage["requests"] += 1
        self.usage["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
        self.usage["completion_tokens"] += int(usage.get("completion_tokens") or 0)
        self.usage["total_tokens"] += int(usage.get("total_tokens") or 0)

    # ---- 费用 -------------------------------------------------------------- #
    def cost_line(self) -> str:
        u = self.usage
        money = (u["prompt_tokens"] / 1e6) * float(self.llm.get("price_in_per_m") or 0.0) \
            + (u["completion_tokens"] / 1e6) * float(self.llm.get("price_out_per_m") or 0.0)
        text = "token 输入 %d / 输出 %d，请求 %d 次，缓存命中 %d 条" % (
            u["prompt_tokens"], u["completion_tokens"], u["requests"], u["cache_hits"])
        if money > 0:
            text += "，预计花费 %.4f %s" % (money, self.llm.get("currency", "CNY"))
        return text


# --------------------------------------------------------------------------- #
# 解析模型返回
# --------------------------------------------------------------------------- #
def parse_items(content: str) -> list:
    """从模型返回里稳健地取出 items 数组：容忍代码块、前后废话、单引号等常见毛病。"""
    if not content:
        return []
    text = content.strip()

    # 去掉 ```json … ``` 包裹
    fence = re.match(r"^```[a-zA-Z]*\s*(.*?)\s*```$", text, re.S)
    if fence:
        text = fence.group(1).strip()

    candidates = [text]
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start:end + 1])
    start, end = text.find("["), text.rfind("]")
    if start != -1 and end > start:
        candidates.append(text[start:end + 1])

    for cand in candidates:
        for attempt in (cand, _repair_json(cand)):
            if not attempt:
                continue
            try:
                data = json.loads(attempt)
            except Exception:  # noqa: BLE001
                continue
            items = data.get("items") if isinstance(data, dict) else data
            if isinstance(items, list):
                out = []
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    idx = item.get("i", item.get("index", item.get("id")))
                    try:
                        idx = int(idx)
                    except Exception:  # noqa: BLE001
                        continue
                    out.append({
                        "i": idx,
                        "title": str(item.get("title") or ""),
                        "summary": str(item.get("summary") or ""),
                    })
                if out:
                    return out
    return []


def _repair_json(text: str) -> str:
    """修掉最常见的两种坏 JSON：中文引号、尾随逗号。"""
    fixed = text.replace("“", '"').replace("”", '"').replace("，", ",").replace("：", ":")
    fixed = re.sub(r",\s*([}\]])", r"\1", fixed)
    return fixed


# --------------------------------------------------------------------------- #
# 便捷函数
# --------------------------------------------------------------------------- #
def translate_report_articles(report: dict, cfg: dict, log_fn=None, warn_fn=None, enabled: bool = True):
    """把 report 里所有入选条目翻译成中文。返回 (usage, translator 或 None)。"""
    llm = llm_config(cfg)
    log = log_fn or (lambda m: None)
    warn = warn_fn or (lambda m: None)
    articles = [a for sec in report.get("sections", []) for a in sec["articles"]]
    for art in articles:
        art.title_zh = ""
        art.summary_zh = ""
    if not articles:
        return {}, None
    if not enabled:
        log("已用 --no-llm 关闭中文摘要，保留英文原文。")
        return {}, None
    if not llm.get("enabled", True):
        log("config.json 里 llm.enabled=false，中文摘要已关闭，保留英文原文。")
        return {}, None
    if not llm.get("api_key"):
        warn("未配置 LLM API Key，跳过中文摘要，邮件将保留英文原文。"
             "配置方法见 README「中文摘要」一节。")
        return {}, None

    log("开始生成中文摘要（%s）…" % describe(llm))
    translator = Translator(llm, log_fn=log_fn, warn_fn=warn_fn)
    usage = translator.translate_articles(articles)
    done = sum(1 for a in articles if a.title_zh)
    log("中文摘要完成：%d/%d 条（%s）" % (done, len(articles), translator.cost_line()))
    usage["translated"] = done
    usage["total_articles"] = len(articles)
    return usage, translator
