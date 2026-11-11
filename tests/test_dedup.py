"""标题相似度与选稿解析的回归测试：不联网、不花钱。

背景：早期去重用「字符 2-gram 余弦 >= 0.39」判断重复，这个阈值对中文没问题，
但对英文会稳定误判——任意两段英文标题都共享大量字母组合（th / he / in / er…），
实测两篇毫不相关的长标题余弦也能到 0.41，于是被合并成一条，读者会看到
「删除 LLM 权重」的论文下面挂着「可穿戴设备测心血管风险」的链接。

现在英文改用词集合 Jaccard，中文仍用字符 2-gram。
用法：python tests/test_dedup.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import curate  # noqa: E402
import main as app  # noqa: E402

RESULTS = []


def check(name, passed, detail=""):
    RESULTS.append((name, passed))
    print(("  PASS  " if passed else "  FAIL  ") + name + (("  <- " + detail) if detail and not passed else ""))


def test_similarity():
    print("[1] 标题相似度判断")
    cases = [
        ("英文：论文 vs 心血管科普（曾误判为重复）",
         "The Surprising Effectiveness of Deleting Weights in LLM Post-Training",
         "Using wearables to enable cardiovascular-kidney-metabolic (CKM) risk profiling for personalized coaching",
         False),
        ("英文：同一产品的两次报道",
         "Nvidia unveils next-gen GPU with 480GB memory for AI training",
         "Nvidia announces new GPU with 480GB memory for AI training",
         True),
        ("英文：数额缩写不同写法",
         "OpenAI raises 11B at a 1.25T valuation",
         "OpenAI raises 11 billion at a 1.25 trillion valuation",
         True),
        ("英文：同公司不同事件",
         "Meta releases new open-source model",
         "Meta lays off 3000 employees in hardware unit",
         False),
        ("英文：无关长标题",
         "Apple is racing to make Siri more agentic",
         "Chipmaker TSMC reports record quarterly revenue on AI demand",
         False),
        ("中文：同事件不同措辞",
         "华为发布昇腾新一代芯片，算力翻倍",
         "华为昇腾新芯片发布 算力提升一倍",
         True),
        ("中文：不同事件",
         "华为发布昇腾新一代芯片",
         "DeepSeek 完成新一轮融资",
         False),
    ]
    for name, a, b, want in cases:
        got = app.similar_titles(a, b)
        # 判断必须对称，否则去重结果会依赖输入顺序
        got_rev = app.similar_titles(b, a)
        check(name, got == want and got_rev == want,
              "got=%s rev=%s want=%s" % (got, got_rev, want))


def test_collapse_merges_links():
    print("[2] collapse_similar 合并行为")
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)

    def art(title, url, source):
        a = app.Article(title=title, url=url, source=source, domain="ai",
                        kind="article", published=now, summary="", weight=10)
        a.score = 10.0
        return a

    a1 = art("Nvidia unveils next-gen GPU with 480GB memory for AI training",
             "https://a.com/1", "SourceA")
    a2 = art("Nvidia announces new GPU with 480GB memory for AI training",
             "https://b.com/2", "SourceB")
    a3 = art("Using wearables to enable cardiovascular-kidney-metabolic risk profiling",
             "https://c.com/3", "SourceC")
    kept = app.collapse_similar([a1, a2, a3])
    check("同一事件被合并成一条", len(kept) == 2, "kept=%d" % len(kept))
    check("被合并条目的链接挂到保留项", any(u == "https://b.com/2" for u, _ in a1.extra_links))
    check("无关条目没有被吞掉", any(a is a3 for a in kept))


def test_curation_config():
    print("[3] 选稿配置解析")
    cfg = {"curation": {"enabled": True, "score_threshold": 7.5,
                        "pool_per_domain": 10, "budget_cny": 0.2}}
    cur = curate.curation_config(cfg)
    check("阈值读取正确", cur["threshold"] == 7.5, str(cur["threshold"]))
    check("候选池读取正确", cur["pool_per_domain"] == 10, str(cur["pool_per_domain"]))
    check("预算读取正确", abs(cur["budget_cny"] - 0.2) < 1e-9, str(cur["budget_cny"]))
    check("缺省值可用", curate.curation_config({})["enabled"] is True)


def test_json_parsing():
    print("[4] 模型返回解析")
    check("标准 JSON", (curate._load_json('{"items":[{"i":0,"score":7}]}') or {}).get("items") is not None)
    check("代码块包裹", (curate._load_json('```json\n{"duplicates":[[0,1]]}\n```') or {}).get("duplicates") == [[0, 1]])
    check("前后有废话", (curate._load_json('好的：\n{"duplicates":[]}\n以上') or {}).get("duplicates") == [])
    check("中文引号可修复", (curate._load_json('{“items”:[]}') or {}).get("items") == [])
    check("坏输入返回 None", curate._load_json("完全不是 JSON") is None)


def main_test():
    test_similarity()
    test_collapse_merges_links()
    test_curation_config()
    test_json_parsing()
    passed = sum(1 for _, ok in RESULTS if ok)
    total = len(RESULTS)
    print("\n%d/%d 通过" % (passed, total))
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main_test())
