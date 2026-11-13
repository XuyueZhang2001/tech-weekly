#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""选稿：让大模型做「编辑判断」。

周报的问题从来不是信息不够，而是「什么值得看」缺少判断。规则打分只能回答
"这条新不新、来源靠不靠谱"，回答不了"这条到底重不重要"。这个模块补两件事：

1. 相关度打分：把编辑标准写成一段 rubric，让模型给每条 0-10 分，低于阈值丢掉。
2. 主题去重：把「同一事件、不同标题」合并，保留分数最高的那条。

三条设计原则：
- 任何失败都退回规则打分，绝不因为选稿挂了而发不出周报。
- 预算有上限（`curation.budget_cny`），超了就停止调用，剩余条目按规则分排序。
- 只对候选池打分（每领域 `pool_per_domain` 条），不是把所有抓到的都送进去。
"""

from __future__ import annotations

import json
import os
import re

import translate

# --------------------------------------------------------------------------- #
# 提示词
# --------------------------------------------------------------------------- #
SCORE_SYSTEM = (
    "你是科技周报的资深编辑，负责从候选条目里挑出本周真正值得读的内容。"
    "给每条打 0-10 分，表示它值得被收入本周周报的程度。\n"
    "\n"
    "分数档位：\n"
    "9-10 突破性：改变技术路线或行业格局，从业者必须知道\n"
    "7-8  高价值：重要的产品、研究或政策，有明显信息量\n"
    "5-6  有趣：有点意思，但影响有限\n"
    "3-4  低优先：常规更新、增量改进、例行发布\n"
    "0-2  噪音：营销稿、标题党、与所属领域关系弱\n"
    "\n"
    "判断维度：技术深度、新颖度、影响面、来源可信度、与所属领域的相关性、"
    "是否有具体证据（数字、实验、可复现结果）、是否引发实质性讨论。\n"
    "\n"
    "几条硬规则，优先级高于你的直觉：\n"
    "1. 热度不等于重要性。Hacker News 上很火的段子、榜单、吐槽，最多 4 分。\n"
    "2. 不奖励夸张标题。看到「史诗级」「颠覆」「炸裂」「彻底改变」这类词，"
    "反而要更谨慎，除非正文证据充足。\n"
    "3. 纯融资、人事变动、诉讼纠纷、股价波动，通常不超过 4 分。\n"
    "4. 综述、教程、榜单、「XX 入门指南」、招聘启事，通常不超过 4 分。\n"
    "5. 只有标题和摘要能支撑你的判断，不要凭空推测正文内容。\n"
    "6. 同一件事被多家媒体报道时，按事件本身的价值打分，不要因为重复而加分。\n"
    "\n"
    "只输出 JSON，不要输出任何解释文字：\n"
    '{"items":[{"i":0,"score":7.5,"reason":"首次把X做到Y"}]}\n'
    "reason 是不超过 20 个字的中文短句，说明这条为什么值这个分。"
)

SCORE_USER = (
    "所属领域：%s\n"
    "%s"
    "候选条目共 %d 条，请逐条打分：\n%s"
)

DEDUPE_SYSTEM = (
    "你在给一份科技周报做去重。下面这组条目已经按重要性从高到低排好序，"
    "请找出描述「同一件事」的重复条目并分组。\n"
    "\n"
    "判断规则：\n"
    "1. 只有讲同一事件才算重复：同一篇论文、同一次产品发布、同一项研究结果、"
    "同一起事故。\n"
    "2. 同一家公司的不同事件不算重复。例如「Meta 发布新模型」和「Meta 裁员」"
    "是两件事，不要合并。\n"
    "3. 同一主题下的不同进展也不算重复。例如「A 公司开源模型」和「B 公司开源模型」"
    "是两件事。\n"
    "4. 拿不准的时候保留，不要合并。漏合并的代价小于错合并。\n"
    "5. 每组第一个编号是保留项（分数最高的那条），其余编号会被丢弃。\n"
    "\n"
    "只输出 JSON，不要输出任何解释文字：\n"
    '{"duplicates":[[0,5,9],[3,7]]}\n'
    "没有重复就输出 {\"duplicates\":[]}"
)


# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #
def _as_bool(value, default: bool) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def curation_config(cfg: dict) -> dict:
    raw = dict(cfg.get("curation") or {})
    env = os.environ.get
    return {
        "enabled": _as_bool(env("CURATION_ENABLED"), raw.get("enabled", True)),
        "threshold": float(env("CURATION_THRESHOLD") or raw.get("score_threshold", 6.0)),
        "pool_per_domain": int(raw.get("pool_per_domain") or 12),
        "min_keep_per_domain": int(raw.get("min_keep_per_domain") or 4),
        "topic_dedup": _as_bool(None, raw.get("topic_dedup", True)),
        "batch_size": int(raw.get("batch_size") or 20),
        "budget_cny": float(env("CURATION_BUDGET_CNY") or raw.get("budget_cny", 0.10)),
        "max_dedup_items": int(raw.get("max_dedup_items") or 40),
    }


# --------------------------------------------------------------------------- #
# JSON 解析
# --------------------------------------------------------------------------- #
def _load_json(content: str):
    """解析模型返回的 JSON，容忍代码块包裹与常见坏格式。取不到就返回 None。"""
    if not content:
        return None
    text = content.strip()
    fence = re.match(r"^```[a-zA-Z]*\s*(.*?)\s*```$", text, re.S)
    if fence:
        text = fence.group(1).strip()
    candidates = [text]
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start:end + 1])
    for cand in candidates:
        for attempt in (cand, translate._repair_json(cand)):
            if not attempt:
                continue
            try:
                return json.loads(attempt)
            except Exception:  # noqa: BLE001
                continue
    return None


# --------------------------------------------------------------------------- #
# 选稿
# --------------------------------------------------------------------------- #
class Curator:
    def __init__(self, cfg: dict, llm: dict, log_fn=None, warn_fn=None):
        self.cfg = cfg
        self.llm = llm
        self.cur = curation_config(cfg)
        self._log = log_fn or (lambda m: None)
        self._warn = warn_fn or (lambda m: None)
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "requests": 0}
        self.budget_hit = False

    # ---- 记账 -------------------------------------------------------------- #
    def _account(self, usage: dict) -> None:
        self.usage["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
        self.usage["completion_tokens"] += int(usage.get("completion_tokens") or 0)
        self.usage["requests"] += 1

    def cost_cny(self) -> float:
        u = self.usage
        return (u["prompt_tokens"] / 1e6) * float(self.llm.get("price_in_per_m") or 0.0) \
            + (u["completion_tokens"] / 1e6) * float(self.llm.get("price_out_per_m") or 0.0)

    def budget_left(self) -> bool:
        if self.cost_cny() >= self.cur["budget_cny"]:
            self.budget_hit = True
            return False
        return True

    # ---- 打分 -------------------------------------------------------------- #
    def score(self, pool: list) -> None:
        """给候选池逐批打相关度分，写回 art.relevance / art.reason。"""
        by_domain = {}
        for art in pool:
            by_domain.setdefault(art.domain, []).append(art)

        for dom in self.cfg.get("domains", []):
            dom_id = dom.get("id")
            arts = by_domain.get(dom_id) or []
            if not arts:
                continue
            name = dom.get("name", dom_id)
            # 领域可以给一条评分侧重。默认的标准是"影响力优先"，
            # 但政策类内容（尤其是地方试点）按这个标准会被系统性压低，
            # 所以允许每个领域自己说明该看重什么。
            hint = str(dom.get("curation_hint") or "").strip()
            hint_text = ("本领域评分侧重（优先于通用标准）：%s\n" % hint) if hint else ""
            size = max(1, self.cur["batch_size"])
            for start in range(0, len(arts), size):
                batch = arts[start:start + size]
                if not self.budget_left():
                    self._warn("选稿预算已达上限（%.3f 元），剩余条目按规则分排序"
                               % self.cost_cny())
                    return
                items = [{
                    "i": i,
                    "title": (a.title or "")[:180],
                    "summary": (a.summary or "")[:200],
                    "source": a.source or "",
                } for i, a in enumerate(batch)]
                user = SCORE_USER % (name, hint_text, len(items),
                                     json.dumps(items, ensure_ascii=False, indent=1))
                try:
                    content, usage = translate.post_chat(
                        self.llm, SCORE_SYSTEM, user, temperature=0.1)
                except Exception as exc:  # noqa: BLE001
                    self._warn("相关度打分失败，本批退回规则分：%s" % str(exc)[:120])
                    continue
                self._account(usage)
                data = _load_json(content) or {}
                for item in data.get("items") or []:
                    try:
                        idx = int(item.get("i"))
                    except (TypeError, ValueError):
                        continue
                    if not (0 <= idx < len(batch)):
                        continue
                    try:
                        score = float(item.get("score"))
                    except (TypeError, ValueError):
                        continue
                    score = max(0.0, min(10.0, score))
                    batch[idx].relevance = score
                    batch[idx].reason = str(item.get("reason") or "")[:40]

    # ---- 主题去重 ---------------------------------------------------------- #
    def dedupe(self, arts: list) -> list:
        """合并「同一事件、不同标题」的条目，保留得分最高的那条。"""
        arts = [a for a in arts if getattr(a, "relevance", None) is not None]
        if len(arts) < 2:
            return arts
        ranked = sorted(arts, key=lambda a: (-a.relevance, -a.score))[: self.cur["max_dedup_items"]]
        ranked_ids = {id(a) for a in ranked}
        rest = [a for a in arts if id(a) not in ranked_ids]

        if not self.budget_left():
            self._warn("选稿预算已达上限，跳过主题去重")
            return arts

        items = [{
            "i": i,
            "title": (a.title or "")[:180],
            "summary": (a.summary or "")[:120],
            "source": a.source or "",
        } for i, a in enumerate(ranked)]
        user = json.dumps(items, ensure_ascii=False, indent=1)
        try:
            content, usage = translate.post_chat(
                self.llm, DEDUPE_SYSTEM, user, temperature=0.0)
        except Exception as exc:  # noqa: BLE001
            self._warn("主题去重失败，保留全部条目：%s" % str(exc)[:120])
            return arts
        self._account(usage)
        data = _load_json(content) or {}

        dropped = set()
        groups = data.get("duplicates") or []
        for group in groups:
            if not isinstance(group, list) or len(group) < 2:
                continue
            idxs = []
            for raw in group:
                try:
                    idx = int(raw)
                except (TypeError, ValueError):
                    continue
                if 0 <= idx < len(ranked) and idx not in idxs:
                    idxs.append(idx)
            if len(idxs) < 2:
                continue
            keeper = ranked[idxs[0]]
            for idx in idxs[1:]:
                other = ranked[idx]
                if id(other) in dropped or other is keeper:
                    continue
                # 把被合并条目的链接挂到保留项上，读者仍然能点过去
                if other.source != keeper.source and \
                        all(other.url != u for u, _ in keeper.extra_links):
                    keeper.extra_links.append((other.url, other.source))
                dropped.add(id(other))
            self._log("  合并重复：%s ← %s" % (
                (keeper.title or "")[:24],
                "、".join((ranked[i].source or "") for i in idxs[1:])))
        if dropped:
            kept = [a for a in ranked if id(a) not in dropped]
            return kept + rest
        return arts

    # ---- 主流程 ------------------------------------------------------------ #
    def run(self, articles: list) -> tuple:
        """返回 (保留的条目, 统计)。任何异常都在外层兜住。"""
        in_window = [a for a in articles if getattr(a, "in_window", True)]
        stale = [a for a in articles if not getattr(a, "in_window", True)]

        # 候选池：每个领域按规则分取前 N 条，保证领域间机会均等
        pool = []
        for dom in self.cfg.get("domains", []):
            if not dom.get("enabled", True):
                continue
            dom_id = dom.get("id")
            cand = [a for a in in_window if a.domain == dom_id]
            cand.sort(key=lambda a: (a.score, a.published), reverse=True)
            pool.extend(cand[: self.cur["pool_per_domain"]])

        self._log("选稿：%d 条候选送模型打分（阈值 %.1f，预算 %.2f 元）"
                  % (len(pool), self.cur["threshold"], self.cur["budget_cny"]))
        self.score(pool)

        # 阈值过滤 + 每领域保底，避免某个领域被清空
        graded = [a for a in pool if getattr(a, "relevance", None) is not None]
        ungraded = [a for a in pool if getattr(a, "relevance", None) is None]
        kept = []
        for dom in self.cfg.get("domains", []):
            dom_id = dom.get("id")
            arts = [a for a in graded if a.domain == dom_id]
            arts.sort(key=lambda a: (-a.relevance, -a.score))
            passed = [a for a in arts if a.relevance >= self.cur["threshold"]]
            floor = self.cur["min_keep_per_domain"]
            if len(passed) < floor:
                passed = arts[:floor]
            kept.extend(passed)

        # 打分失败的条目退回规则分，排在已打分条目之后
        if ungraded:
            ungraded.sort(key=lambda a: (a.score, a.published), reverse=True)
            kept.extend(ungraded[: max(0, self.cur["min_keep_per_domain"])])
            kept = list({id(a): a for a in kept}.values())

        before = len(kept)
        if self.cur["topic_dedup"]:
            kept = self.dedupe(kept)
        after = len(kept)

        # 未进入候选池的条目直接丢弃；窗口外的旧条目保留给"补位"逻辑
        kept_ids = {id(a) for a in kept}
        pool_ids = {id(a) for a in pool}
        leftover = [a for a in in_window
                    if id(a) not in kept_ids and id(a) not in pool_ids]

        # 融合分数：相关度为主，规则分为辅，后续配额与轮转沿用原有逻辑
        for art in kept:
            if getattr(art, "relevance", None) is not None:
                art.score = 0.35 * art.score + 1.25 * art.relevance
            elif art.score < 0:
                art.score = 0.0

        # 没送审的条目（规则分排在候选池之外）只用于名额补位，压一档避免挤掉送审过的
        for art in leftover:
            art.score *= 0.5

        stats = {
            "enabled": True,
            "pool": len(pool),
            "scored": len(graded),
            "kept": after,
            "dropped": max(0, before - after),
            "threshold": self.cur["threshold"],
            "budget_hit": self.budget_hit,
            "cost_cny": round(self.cost_cny(), 4),
            "requests": self.usage["requests"],
            "tokens_in": self.usage["prompt_tokens"],
            "tokens_out": self.usage["completion_tokens"],
        }
        self._log("选稿完成：%d 条候选 → %d 条入选（合并 %d 条重复，花费 %.4f 元，请求 %d 次）"
                  % (len(pool), after, max(0, before - after), stats["cost_cny"],
                     stats["requests"]))

        return kept + stale + leftover, stats


def curate(articles: list, cfg: dict, log_fn=None, warn_fn=None,
           enabled: bool = True) -> tuple:
    """选稿入口。未启用、无 Key 或出任何异常时，原样返回并说明原因。"""
    if not enabled:
        return articles, {"enabled": False, "reason": "no-llm"}
    llm = translate.llm_config(cfg)
    cur = curation_config(cfg)
    if not cur["enabled"]:
        return articles, {"enabled": False, "reason": "config"}
    if not llm.get("enabled", True):
        # llm.enabled=false（或 --no-llm）是用户的明确意愿：一个字都别调
        return articles, {"enabled": False, "reason": "llm-disabled"}
    if not llm.get("api_key"):
        (warn_fn or (lambda m: None))("未配置 API Key，跳过选稿，改用规则打分")
        return articles, {"enabled": False, "reason": "no-key"}
    try:
        curator = Curator(cfg, llm, log_fn, warn_fn)
        return curator.run(articles)
    except Exception as exc:  # noqa: BLE001
        (warn_fn or (lambda m: None))("选稿过程出错，回退到规则打分：%s" % str(exc)[:160])
        return articles, {"enabled": False, "reason": "error", "error": str(exc)[:200]}
