"""抓取层的测试：http_get 的重试策略、按源覆盖、Bing 新闻解析。

不联网：用假的 urlopen / 假的 http_get 顶替。

重点覆盖两个曾经踩过的坑：
  1. 403 不该重试（早期写成了无条件重试，白跑还拖慢整轮）
  2. fetch_bing_news 里给 http_get 传了不存在的 src 变量，NameError 被
     上层吞成「Bing 新闻没有结果」，四个 Bing 源静默失效了整整一期

用法：python tests/test_fetch.py
"""
import os
import sys
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import main as app  # noqa: E402

RESULTS = []


def check(name, passed, detail=""):
    RESULTS.append((name, passed))
    print(("  PASS  " if passed else "  FAIL  ") + name + (("  <- " + detail) if detail and not passed else ""))


class FakeResponse:
    def __init__(self, body=b"<rss><channel></channel></rss>"):
        self._body = body
        self.status = 200
        self.headers = {}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def with_urlopen(fake, fn):
    """临时顶替 urllib.request.urlopen，跑完还原。"""
    original = app.urllib.request.urlopen
    app.urllib.request.urlopen = fake
    try:
        return fn()
    finally:
        app.urllib.request.urlopen = original


def test_403_not_retried():
    print("[1] 403 不重试")
    calls = []

    def fake(req, timeout=None):
        calls.append(req.full_url)
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)

    def run():
        try:
            with_urlopen(fake, lambda: app.http_get("https://example.com/a", {"fetch": {"retries": 3}}))
            return "raised-nothing"
        except urllib.error.HTTPError:
            return "raised"

    got = run()
    check("抛出原来的 HTTPError", got == "raised", got)
    check("只请求了 1 次", len(calls) == 1, "calls=%d" % len(calls))


def test_500_retried():
    print("[2] 5xx 会重试")
    calls = []

    def fake(req, timeout=None):
        calls.append(req.full_url)
        raise urllib.error.HTTPError(req.full_url, 500, "boom", {}, None)

    def run():
        try:
            with_urlopen(fake, lambda: app.http_get("https://example.com/b",
                                                    {"fetch": {"retries": 1}}))
            return "raised-nothing"
        except urllib.error.HTTPError:
            return "raised"

    got = run()
    check("最终仍然抛出", got == "raised", got)
    check("retries=1 时请求 2 次", len(calls) == 2, "calls=%d" % len(calls))


def test_per_source_overrides():
    print("[3] 按源覆盖 UA / Referer / 超时")
    seen = {}

    def fake(req, timeout=None):
        seen["ua"] = req.get_header("User-agent")
        seen["referer"] = req.get_header("Referer")
        seen["timeout"] = timeout
        return FakeResponse()

    cfg = {"fetch": {"user_agent": "GLOBAL-UA", "timeout_seconds": 25, "retries": 0}}
    src = {"user_agent": "SOURCE-UA", "referer": "https://ref.example/", "timeout_seconds": 7}
    with_urlopen(fake, lambda: app.http_get("https://example.com/c", cfg, src=src))
    check("源的 UA 覆盖全局", seen.get("ua") == "SOURCE-UA", str(seen.get("ua")))
    check("Referer 透传", seen.get("referer") == "https://ref.example/", str(seen.get("referer")))
    check("超时按源覆盖", seen.get("timeout") == 7, str(seen.get("timeout")))

    with_urlopen(fake, lambda: app.http_get("https://example.com/d", cfg))
    check("没配就用全局 UA", seen.get("ua") == "GLOBAL-UA", str(seen.get("ua")))


BING_RSS = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:News="https://www.bing.com/news/search">
  <channel>
    <title>脑机接口 - BingNews</title>
    <item>
      <title>首家脑机接口公司完成临床试验</title>
      <link>https://www.bing.com/news/apiclick.aspx?url=https%3A%2F%2Fexample.com%2F1</link>
      <description>摘要一</description>
      <pubDate>Wed, 12 Nov 2026 08:00:00 GMT</pubDate>
      <News:Source>搜狐</News:Source>
    </item>
    <item>
      <title>脑机接口电极材料取得突破</title>
      <link>https://www.bing.com/news/apiclick.aspx?url=https%3A%2F%2Fexample.com%2F2</link>
      <description>摘要二</description>
      <pubDate>Tue, 11 Nov 2026 08:00:00 GMT</pubDate>
      <News:Source>新浪财经</News:Source>
    </item>
  </channel>
</rss>
"""


def test_bing_parsing():
    print("[4] Bing 新闻解析（回归：曾因 NameError 静默失效）")
    passed_src = {}

    def fake_get(url, cfg, accept="*/*", src=None):
        passed_src["src"] = src
        return BING_RSS

    original = app.http_get
    app.http_get = fake_get
    try:
        src = {"name": "Bing 新闻 · 脑机接口", "queries": ["脑机接口"], "weight": 11,
               "group": "脑机接口"}
        arts = app.fetch_bing_news(src, "bio", {"fetch": {}})
    finally:
        app.http_get = original

    check("解析出条目", len(arts) == 2, "got=%d" % len(arts))
    check("信源配置透传给了 http_get", passed_src.get("src") is src)
    if arts:
        check("媒体名取自 News:Source", arts[0].source == "搜狐", arts[0].source)
        check("URL 还原成真实链接", "example.com/1" in arts[0].url, arts[0].url)
        check("分组沿用信源配置", arts[0].group == "脑机接口", arts[0].group)
        check("权重沿用信源配置", arts[0].weight == 11, str(arts[0].weight))


def test_bing_empty_raises():
    print("[5] Bing 无结果时明确报错")
    original = app.http_get
    app.http_get = lambda url, cfg, accept="*/*", src=None: "<rss><channel></channel></rss>"
    try:
        try:
            app.fetch_bing_news({"name": "x", "queries": ["q"]}, "ai", {"fetch": {}})
            got = "no-raise"
        except ValueError as exc:
            got = "ValueError: %s" % exc
    finally:
        app.http_get = original
    check("抛 ValueError 而不是静默返回空", got.startswith("ValueError"), got)


def main_test():
    test_403_not_retried()
    test_500_retried()
    test_per_source_overrides()
    test_bing_parsing()
    test_bing_empty_raises()
    passed = sum(1 for _, ok in RESULTS if ok)
    total = len(RESULTS)
    print("\n抓取层测试：%d/%d 通过" % (passed, total))
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main_test())
