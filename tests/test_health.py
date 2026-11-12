"""信源健康度与快照回退的测试：不联网（只连本机一个必然拒绝的端口）。

覆盖：
  1. 状态文件读写、损坏文件降级
  2. 成功清零失败计数、失败累计、达到阈值后进入冷却
  3. 冷却期内不再发起请求，直接用快照
  4. 抓取失败时用上期快照补位，并在状态里标记 fallback
  5. 快照序列化保留还原所需字段

用法：python tests/test_health.py
"""
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import health  # noqa: E402
import main as app  # noqa: E402

RESULTS = []


def check(name, passed, detail=""):
    RESULTS.append((name, passed))
    print(("  PASS  " if passed else "  FAIL  ") + name + (("  <- " + detail) if detail and not passed else ""))


def make_cfg(disable_after=0, cooldown_days=21):
    return {
        "fetch": {"workers": 2, "timeout_seconds": 2, "retries": 0,
                  "snapshot_fallback": True, "snapshot_per_source": 40,
                  "disable_after_failures": disable_after, "cooldown_days": cooldown_days},
        "domains": [{
            "id": "ai", "name": "AI", "enabled": True,
            "sources": [{"type": "rss", "name": "Dead Source",
                         # 9 号端口（discard）本机必然拒绝，失败是确定性的、毫秒级
                         "url": "http://127.0.0.1:9/feed.xml", "weight": 12}],
        }],
    }


def make_snapshot(n=3):
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    arts = []
    for i in range(n):
        arts.append(app.Article(
            title="Snapshot %d" % i, url="https://example.com/%d" % i,
            source="Dead Source", domain="ai", published=now - timedelta(days=10),
            summary="s%d" % i, weight=12, kind="article", points=i))
        arts[-1].group = "bci"
    return health.snapshot_of(arts)


def test_state_io(tmp):
    print("[1] 状态文件读写")
    path = os.path.join(tmp, "state.json")
    st = health.empty_state()
    health.record_success(st, "A", [{"title": "t"}])
    health.save(path, st)
    loaded = health.load(path)
    check("保存后能读回来", health.snapshot(loaded, "A") == [{"title": "t"}])
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("{坏掉的 json")
    check("损坏文件降级为空状态", health.load(path)["sources"] == {})
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"version": 999, "sources": {"A": {}}}, fh)
    check("版本不符时丢弃旧状态", health.load(path)["sources"] == {})


def test_failure_counting(tmp):
    print("[2] 失败计数与冷却")
    st = health.empty_state()
    health.record_failure(st, "A", "boom", disable_after=3, cooldown_days=7)
    check("一次失败后未停用", not health.is_disabled(st, "A"))
    health.record_failure(st, "A", "boom", disable_after=3, cooldown_days=7)
    check("两次失败后仍未停用", not health.is_disabled(st, "A"))
    health.record_failure(st, "A", "boom", disable_after=3, cooldown_days=7)
    check("达到阈值后进入冷却", health.is_disabled(st, "A"))
    check("冷却截止时间已记录", bool(health.disabled_until(st, "A")))
    health.record_success(st, "A", [])
    check("成功后立刻解除冷却", not health.is_disabled(st, "A") and health.fail_streak(st, "A") == 0)
    check("阈值为 0 时永不自动停用",
          (health.record_failure(st, "B", "x", disable_after=0, cooldown_days=7) or
           not health.is_disabled(st, "B")))


def test_snapshot_fields(tmp):
    print("[3] 快照字段")
    snap = make_snapshot(2)
    check("条数正确", len(snap) == 2)
    check("保留 weight", snap[0]["weight"] == 12)
    check("保留 group", snap[0]["group"] == "bci")
    check("保留发布时间", bool(snap[0]["published"]))
    check("摘要被截断到 400 字符内", len(snap[0]["summary"]) <= 400)


def test_fallback(tmp):
    print("[4] 抓取失败时用快照补位")
    path = os.path.join(tmp, "fallback.json")
    os.environ["TECH_WEEKLY_STATE"] = path
    st = health.empty_state()
    health.record_success(st, health.key("ai", "Dead Source"), make_snapshot(3))
    health.save(path, st)

    articles, status = app.collect(make_cfg())
    check("失败时仍有内容", len(articles) == 3, "got=%d" % len(articles))
    check("状态里标记了 fallback", status[0].get("fallback") is True)
    check("状态里记录失败原因", not status[0].get("ok", True))
    check("补位内容能还原出权重", articles[0].weight == 12)
    check("补位内容保留 URL", articles[0].url.startswith("https://example.com/"))


def test_auto_disable(tmp):
    print("[5] 连续失败后暂停抓取")
    path = os.path.join(tmp, "disable.json")
    os.environ["TECH_WEEKLY_STATE"] = path
    st = health.empty_state()
    health.record_success(st, health.key("ai", "Dead Source"), make_snapshot(2))
    health.save(path, st)

    cfg = make_cfg(disable_after=1)
    app.collect(cfg)                      # 第一次失败 -> 触发停用
    articles, status = app.collect(cfg)   # 第二次：冷却期内，不该再去请求
    check("冷却期内标记 disabled", status[0].get("disabled") is True)
    check("冷却期内仍用快照补位", status[0].get("fallback") is True and len(articles) == 2)
    check("状态文件里写了截止时间",
          bool(health.disabled_until(health.load(path), health.key("ai", "Dead Source"))))


def main_test():
    # 注意：不能用 tempfile.mkdtemp()。它按 0o700 建目录，在受限令牌的沙箱下
    # 写不进去（普通 makedirs 会继承父目录 ACL，就没有这个问题）。
    base = os.path.join(ROOT, "out")
    os.makedirs(base, exist_ok=True)
    tmp = os.path.join(base, "tw-health-test-%d" % os.getpid())
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    old = os.environ.get("TECH_WEEKLY_STATE")
    try:
        test_state_io(tmp)
        test_failure_counting(tmp)
        test_snapshot_fields(tmp)
        test_fallback(tmp)
        test_auto_disable(tmp)
    finally:
        if old is None:
            os.environ.pop("TECH_WEEKLY_STATE", None)
        else:
            os.environ["TECH_WEEKLY_STATE"] = old
        shutil.rmtree(tmp, ignore_errors=True)
    passed = sum(1 for _, ok in RESULTS if ok)
    total = len(RESULTS)
    print("\n信源健康度测试：%d/%d 通过" % (passed, total))
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main_test())
