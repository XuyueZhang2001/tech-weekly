#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""信源健康度与快照回退。

解决的问题：某个源这周挂了（403 / 429 / 改版 / DNS 抽风），现在的结果是
整栏缺一块，而且下周期照样再挂一次。这个模块做三件事：

1. 记住每个源最近一次成功抓到的条目（快照）。下次挂掉时用它补位。
2. 记录连续失败次数。连续失败到阈值就先停抓一段时间，避免每期白发请求。
3. 冷却期结束后自动恢复尝试，源修好了就能自己回来。

GitHub Actions 的 runner 每次都是全新的，所以状态必须落盘。为了不额外引入
存储，状态放在 `cache/` 下——工作流已经用 actions/cache 缓存了整个 `cache/`，
跨期自然保留；本地运行时也同样有效。
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

STATE_VERSION = 1


def key(domain_id: str, name: str) -> str:
    """状态键：领域 + 信源名。

    不能只用名字——配置里 `Hacker News` 在 5 个领域各有一个（查询词不同），
    `arXiv` 有 4 个。只按名字记状态的话，它们会共用一份快照和失败计数，
    一个挂了会把其余的也标成失败。
    """
    return "%s|%s" % (domain_id or "", name or "")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def default_path(base_dir: str) -> str:
    return os.path.join(base_dir, "cache", "source-state.json")


def empty_state() -> dict:
    return {"version": STATE_VERSION, "sources": {}}


def load(path: str) -> dict:
    """读状态文件。任何异常都返回空状态——状态丢了最多退化成"没有快照"。"""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:  # noqa: BLE001
        return empty_state()
    if not isinstance(data, dict) or data.get("version") != STATE_VERSION:
        return empty_state()
    if not isinstance(data.get("sources"), dict):
        data["sources"] = {}
    return data


def save(path: str, state: dict) -> None:
    """原子写，避免中断时留下半个 JSON。"""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False)
    os.replace(tmp, path)


def entry(state: dict, name: str) -> dict:
    rec = state["sources"].get(name)
    if not isinstance(rec, dict):
        rec = {}
        state["sources"][name] = rec
    rec.setdefault("fail_streak", 0)
    rec.setdefault("last_ok", "")
    rec.setdefault("last_error", "")
    rec.setdefault("disabled_until", "")
    rec.setdefault("items", [])
    return rec


def record_success(state: dict, name: str, items: list, now: datetime | None = None) -> None:
    """记得这次抓到的内容，并把失败计数清零。"""
    now = now or _now()
    rec = entry(state, name)
    rec["fail_streak"] = 0
    rec["last_ok"] = now.isoformat()
    rec["last_error"] = ""
    rec["disabled_until"] = ""
    rec["items"] = items


def record_failure(state: dict, name: str, error: str,
                   disable_after: int = 0, cooldown_days: int = 0,
                   now: datetime | None = None) -> None:
    """记一次失败。连续失败到阈值就停抓一段时间（可关）。"""
    now = now or _now()
    rec = entry(state, name)
    rec["fail_streak"] = int(rec.get("fail_streak") or 0) + 1
    rec["last_error"] = error[:200]
    if disable_after > 0 and rec["fail_streak"] >= disable_after and cooldown_days > 0:
        until = now + timedelta(days=cooldown_days)
        rec["disabled_until"] = until.isoformat()


def is_disabled(state: dict, name: str, now: datetime | None = None) -> bool:
    now = now or _now()
    rec = state["sources"].get(name) or {}
    raw = rec.get("disabled_until") or ""
    if not raw:
        return False
    try:
        until = datetime.fromisoformat(raw)
    except Exception:  # noqa: BLE001
        return False
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    return until > now


def disabled_until(state: dict, name: str) -> str:
    return (state["sources"].get(name) or {}).get("disabled_until") or ""


def snapshot(state: dict, name: str) -> list:
    rec = state["sources"].get(name) or {}
    items = rec.get("items")
    return items if isinstance(items, list) else []


def fail_streak(state: dict, name: str) -> int:
    return int((state["sources"].get(name) or {}).get("fail_streak") or 0)


def prune(state: dict, keep_days: int = 180, now: datetime | None = None) -> int:
    """删掉很久没成功过的源，避免状态文件越积越大。返回删掉的条数。"""
    now = now or _now()
    cutoff = now - timedelta(days=keep_days)
    dropped = 0
    for name in list(state["sources"]):
        rec = state["sources"][name] or {}
        last = rec.get("last_ok") or ""
        if not last:
            # 从来没成功过：看它第一次失败到现在多久了
            continue
        try:
            when = datetime.fromisoformat(last)
        except Exception:  # noqa: BLE001
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        if when < cutoff:
            state["sources"].pop(name, None)
            dropped += 1
    return dropped


def snapshot_of(articles: list, limit: int = 40) -> list:
    """把 Article 列表压成可 JSON 化的小快照（只要还原所需的最少字段）。"""
    out = []
    for art in articles[:limit]:
        published = getattr(art, "published", None)
        out.append({
            "title": (art.title or "")[:300],
            "url": art.url or "",
            "summary": (art.summary or "")[:400],
            "published": published.isoformat() if isinstance(published, datetime) else "",
            "points": int(getattr(art, "points", 0) or 0),
            "kind": getattr(art, "kind", "article") or "article",
            # 还原时要用到，省得再猜各类型信源的默认权重
            "weight": int(getattr(art, "weight", 10) or 10),
            "group": getattr(art, "group", "") or "",
        })
    return out
