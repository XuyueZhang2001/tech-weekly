#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tech-weekly — 每周科技周报自动生成并邮件推送（纯标准库，零依赖）。

命令：
    python main.py test                 体检所有信源，报告可用性
    python main.py preview --days 7     生成 HTML/文本预览，不发邮件
    python main.py send   --days 7      生成并发送邮件
    python main.py run    --days 7      先试发给自己，再发给全体收件人
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import gzip
import html
import json
import os
import re
import smtplib
import ssl
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, parsedate_to_datetime
from xml.etree import ElementTree as ET

import translate

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG = os.path.join(HERE, "config.json")
ATOM = "{http://www.w3.org/2005/Atom}"
PAPER_EXCLUDE = re.compile(
    r"\b(survey|review|overview|position paper|tutorial|benchmark(?:ing)? study)\b", re.I
)
# 案件 / 诉讼 / 八卦类标题：与"技术进展"关系弱，但仍可能含本领域关键词，统一降权
NOISE_RE = re.compile(
    r"\b(arrested|indicted|charged with|pleads? guilty|sentenced|jailed|police|murder|"
    r"manslaughter|sues|sued|lawsuit|litigation|court|jury|fraud|embezzl|stalk(?:ing|er)|"
    r"assault|harassment|discriminat|leak(?:ed|s)? (?:memo|email)|lawsuit)\b",
    re.I,
)
# Bing 新闻对并发很敏感，多个查询同时打过去会被限流并返回 HTML 而不是 RSS，
# 所以所有 Bing 请求串行化。
BING_LOCK = threading.Lock()
# 财经/资本类措辞：周报以"技术进展"为主。融资、股价、估值这类内容降权，
# 避免同一笔融资被多家媒体反复顶上首页。
FINANCE_RE = re.compile(
    r"融资|募资|估值|股价|涨停|跌停|市值|IPO|上市|A轮|B轮|C轮|战略投资|"
    r"亿元|亿美元|营收|净利|财报|板块",
    re.I,
)


# --------------------------------------------------------------------------- #
# 日志
# --------------------------------------------------------------------------- #
def log(msg: str) -> None:
    print("[%s] %s" % (datetime.now().strftime("%H:%M:%S"), msg), flush=True)


def warn(msg: str) -> None:
    print("[%s] ! %s" % (datetime.now().strftime("%H:%M:%S"), msg), file=sys.stderr, flush=True)


# --------------------------------------------------------------------------- #
# 数据结构
# --------------------------------------------------------------------------- #
@dataclass
class Article:
    title: str
    url: str
    source: str
    domain: str
    published: datetime
    summary: str = ""
    score: float = 0.0
    weight: int = 10
    kind: str = "article"          # article | paper | discussion
    points: int = 0                # HN 点赞
    extra_links: list = field(default_factory=list)
    in_window: bool = True
    title_zh: str = ""             # 中文摘要，由 translate.py 填充
    summary_zh: str = ""
    group: str = ""                # 子分组归属（例如"脑机接口"），用于领域内配额分配

    @property
    def display_title(self) -> str:
        return self.title_zh or self.title

    @property
    def display_summary(self) -> str:
        return self.summary_zh or self.summary

    @property
    def translated(self) -> bool:
        return bool(self.title_zh)


# --------------------------------------------------------------------------- #
# 网络
# --------------------------------------------------------------------------- #
def http_get(url: str, cfg: dict, accept: str = "*/*") -> str:
    fetch_cfg = cfg.get("fetch", {})
    headers = {
        "User-Agent": fetch_cfg.get("user_agent", "Mozilla/5.0"),
        "Accept": accept,
        "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
    }
    timeout = int(fetch_cfg.get("timeout_seconds", 25))
    last_err = None
    for attempt in range(2):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
            if raw[:2] == b"\x1f\x8b":
                raw = gzip.decompress(raw)
            charset = "utf-8"
            head = raw[:2000].decode("ascii", "ignore")
            m = re.search(r'encoding=["\']([\w-]+)["\']', head)
            if m:
                charset = m.group(1)
            return raw.decode(charset, "replace")
        except Exception as exc:  # noqa: BLE001 - 网络错误一律降级处理
            last_err = exc
            if attempt == 0:
                time.sleep(1.5)
    raise last_err  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# 解析工具
# --------------------------------------------------------------------------- #
def _strip_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _text(node) -> str:
    if node is None:
        return ""
    return " ".join("".join(node.itertext()).split())


def _child(node, *names):
    for ch in node:
        if _strip_ns(ch.tag) in names:
            return ch
    return None


def _desc(node, *names) -> str:
    return _text(_child(node, *names))


def _to_dt(value: str):
    dt = _parse_dt(value)
    if dt is None:
        return None
    return clamp_future(dt)


def clamp_future(dt: datetime) -> datetime:
    """信源时区填错或本机时钟偏差时，未来时间戳会让条目虚假置顶，这里收敛到今天。"""
    now = datetime.now(timezone.utc)
    if dt > now + timedelta(hours=6):
        return now
    return dt


def _parse_dt(value: str):
    if not value:
        return None
    value = value.strip()
    try:
        dt = parsedate_to_datetime(value)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001
        pass
    for pat in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%fZ",
                "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(value.replace("Z", "+0000") if pat.endswith("%z") else value, pat)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:  # noqa: BLE001
            continue
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001
        return None


BOILERPLATE = (
    re.compile(r"\s*(The post .{0,200}? appeared first on .{0,80}?\.?\s*)$", re.I),
    re.compile(r"\s*(Continue reading .{0,120}?\.?\s*)$", re.I),
    re.compile(r"\s*(Read more .{0,120}?\.?\s*)$", re.I),
    re.compile(r"\s*(The post .{0,200}? appeared first on .{0,80}?)$", re.I),
    re.compile(r"\s*(阅读全文.{0,40}?)$"),
    re.compile(r"\s*(点击查看原文.{0,40}?)$"),
    re.compile(r"\s*(\[…\]|…)\s*$"),
)


def clean_text(raw: str, limit: int = 320) -> str:
    """去 HTML 标签 + 反转义 + 去站点样板文字 + 截断，用于邮件正文摘要。"""
    if not raw:
        return ""
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = text.replace("\u00a0", " ")
    text = " ".join(text.split())
    for _ in range(3):
        before = text
        for pat in BOILERPLATE:
            text = pat.sub("", text).strip()
        if text == before:
            break
    if len(text) > limit:
        cut = text[:limit]
        for sep in (". ", "。", "! ", "！", "? ", "？"):
            pos = cut.rfind(sep)
            if pos > limit * 0.6:
                return cut[: pos + len(sep)].strip()
        return cut.rstrip() + "…"
    return text


def norm_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    parts = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parts.query)
    query = [(k, v) for k, v in query if not k.lower().startswith("utm_") and k.lower() not in
             ("ref", "source", "fbclid", "gclid", "mc_cid", "mc_eid", "spm", "share_token")]
    netloc = parts.netloc.lower().replace("www.", "", 1)
    path = parts.path.rstrip("/") or "/"
    return urllib.parse.urlunsplit((parts.scheme or "https", netloc, path,
                                    urllib.parse.urlencode(query), ""))


def norm_title(title: str) -> str:
    title = unicodedata.normalize("NFKC", title or "").lower()
    title = re.sub(r"[^\w\u4e00-\u9fff]+", " ", title)
    return " ".join(title.split())


def host_of(url: str) -> str:
    try:
        return urllib.parse.urlsplit(url).netloc.lower().replace("www.", "", 1)
    except Exception:  # noqa: BLE001
        return ""


def key_of(article: Article) -> str:
    """跨源去重用的稳定标识：优先用正文链接的域名+路径。"""
    target = article.url
    if article.kind == "discussion":
        for link, _ in article.extra_links:
            if link:
                target = link
                break
    nu = norm_url(target)
    if nu:
        return "u:" + nu
    return "t:" + norm_title(article.title)


# --------------------------------------------------------------------------- #
# 各类型信源抓取
# --------------------------------------------------------------------------- #
def fetch_rss(src: dict, domain: str, cfg: dict):
    xml = http_get(src["url"], cfg, "application/rss+xml, application/atom+xml, application/xml, text/xml, */*")
    root = ET.fromstring(xml)
    nodes = [el for el in root.iter() if _strip_ns(el.tag) in ("item", "entry")]

    feed_title = ""
    for el in root.iter():
        if _strip_ns(el.tag) == "title":
            feed_title = _text(el)
            break

    items = []
    for node in nodes:
        title = _desc(node, "title")
        link = _desc(node, "link")
        if not link:
            for ch in node:
                if _strip_ns(ch.tag) == "link":
                    rel = ch.attrib.get("rel", "alternate")
                    if rel == "alternate" and ch.attrib.get("href"):
                        link = ch.attrib["href"]
                        break
            if not link:
                for ch in node:
                    if _strip_ns(ch.tag) == "link" and ch.attrib.get("href"):
                        link = ch.attrib["href"]
                        break
        if not title or not link:
            continue
        if link.startswith("/"):
            base = urllib.parse.urlsplit(src["url"])
            link = "%s://%s%s" % (base.scheme, base.netloc, link)
        published = _to_dt(_desc(node, "pubDate", "published", "updated", "date", "dc:date"))
        if published is None:
            published = _to_dt(_desc(node, "lastBuildDate"))
        if published is None:
            published = datetime.now(timezone.utc)
        # 只用 description/summary 做摘要：content/encoded 常混进站点导航、配图 alt 文字，
        # 会把无关内容的关键词（例如图片 alt 里的模型名）带进来，污染相关性判断。
        summary = _desc(node, "description", "summary", "subtitle")
        if not summary:
            summary = _desc(node, "content", "encoded")
        items.append(Article(
            title=clean_text(title, 200),
            url=link.strip(),
            source=src.get("name") or feed_title or host_of(src["url"]),
            domain=domain,
            published=published,
            summary=clean_text(summary),
            weight=int(src.get("weight", 10)),
        ))
    if not items:
        raise ValueError("feed 内没有解析到条目")
    return items


def fetch_arxiv(src: dict, domain: str, cfg: dict) -> list:
    categories = src.get("categories") or []
    if not categories:
        return []
    query = " OR ".join("cat:%s" % c for c in categories)
    max_results = int(cfg.get("fetch", {}).get("arxiv_max_results", 8))
    url = ("https://export.arxiv.org/api/query?search_query=%s"
           "&sortBy=submittedDate&sortOrder=descending&max_results=%d"
           % (urllib.parse.quote(query), max_results))
    xml = http_get(url, cfg, "application/atom+xml")
    root = ET.fromstring(xml)
    keywords = [str(k).lower() for k in src.get("keywords", [])]
    items = []
    for entry in root.findall(ATOM + "entry"):
        title = " ".join((entry.findtext(ATOM + "title") or "").split())
        if not title:
            continue
        abstract = " ".join((entry.findtext(ATOM + "summary") or "").split())
        # arXiv 分类很宽（cs.AI 什么都有），摘要里一个关键词都不沾的直接丢掉
        if keywords and not matches_keywords(title + " " + abstract, keywords):
            continue
        link = ""
        for ln in entry.findall(ATOM + "link"):
            if ln.attrib.get("rel") == "alternate" or ln.attrib.get("type") == "text/html":
                link = ln.attrib.get("href", "")
                break
        if not link:
            link = entry.findtext(ATOM + "id") or ""
        published = _to_dt(entry.findtext(ATOM + "published") or entry.findtext(ATOM + "updated") or "")
        summary = clean_text(abstract, 300)
        authors = [a.findtext(ATOM + "name") or "" for a in entry.findall(ATOM + "author")]
        authors = [a for a in authors if a]
        if authors:
            who = authors[0] + (" 等 %d 人" % len(authors) if len(authors) > 1 else "")
            summary = ("%s — %s" % (who, summary)) if summary else who
        items.append(Article(
            title=title,
            url=link.strip(),
            source=src.get("name", "arXiv"),
            domain=domain,
            published=published or datetime.now(timezone.utc),
            summary=summary,
            weight=int(src.get("weight", 12)),
            kind="paper",
        ))
    if not items:
        raise ValueError("arXiv 未返回条目")
    return items


def keyword_hits(text: str, keywords: list) -> int:
    low = (text or "").lower()
    return sum(1 for kw in keywords if str(kw).lower() in low)


def matches_keywords(text: str, keywords: list, need: int = 1) -> bool:
    return keyword_hits(text, keywords) >= need


def source_relevance_ok(art: Article, src: dict) -> bool:
    """按信源自己的关键词做细粒度过滤。

    用于定位具体主题的信源（例如脑机接口），以及话题很杂的综合媒体
    （例如科技门户），避免它们把不相关内容带进对应领域。

    两级判断，因为"命中总数"单独用会误伤：
      - title_keywords：必须命中标题。真做这个主题的文章，标题一定会点明，
        这是最可靠的信号；只看摘要会因为宽泛检索词（如 [Title/Abstract] 匹配）
        把"顺带提了一句"的文章放进来。
      - filter_keywords：标题+摘要里的命中数，用于进一步收紧。
    两个都留空则全部放行。
    """
    title_kws = src.get("title_keywords") or []
    if title_kws and not matches_keywords(art.title, title_kws,
                                          int(src.get("min_title_hits", 1))):
        return False
    keywords = src.get("filter_keywords") or []
    if keywords and not matches_keywords(art.title + " " + art.summary, keywords,
                                         int(src.get("min_filter_hits", 1))):
        return False
    return True


def fetch_hackernews(src: dict, domain: str, cfg: dict) -> list:
    min_points = int(cfg.get("fetch", {}).get("hn_min_points", 10))
    # HN 是跨领域热榜，标题不沾本领域关键词的一律丢弃，否则会把无关热帖带进周报。
    # 用 hn_keywords（已剔除公司名等易误命中的词），并要求至少命中 2 个。
    keywords = [str(k).lower() for k in (src.get("hn_keywords") or src.get("keywords", []))]
    need = int(src.get("min_keyword_hits", 2))
    # 超高热度帖默认放宽关键词要求，避免漏掉真热点；但定位很窄的领域
    # （例如"中国科技动态"）必须关掉这个豁免，否则任何爆款帖都会被算进来。
    points_bypass = int(src.get("points_bypass", 150))
    since = int(time.time()) - 30 * 86400
    raw_items = []
    for query in src.get("queries", []):
        url = ("https://hn.algolia.com/api/v1/search?query=%s&tags=story"
               "&numericFilters=created_at_i>%d,points>%d&hitsPerPage=15"
               % (urllib.parse.quote(query), since, min_points))
        try:
            data = json.loads(http_get(url, cfg, "application/json"))
        except Exception as exc:  # noqa: BLE001
            warn("HN 查询失败 query=%s: %s" % (query, exc))
            continue
        for hit in data.get("hits", []):
            title = (hit.get("title") or "").strip()
            if not title:
                continue
            points = int(hit.get("points") or 0)
            if keywords and points < points_bypass and not matches_keywords(title, keywords, need):
                continue
            discussion = "https://news.ycombinator.com/item?id=%s" % hit.get("objectID")
            target = (hit.get("url") or discussion).strip()
            created = hit.get("created_at_i") or 0
            comments = int(hit.get("num_comments") or 0)
            summary = "HN 热度 %d 分 · %d 条讨论" % (points, comments)
            raw_items.append(Article(
                title=title,
                url=target,
                source=src.get("name", "Hacker News"),
                domain=domain,
                published=datetime.fromtimestamp(created, tz=timezone.utc) if created else datetime.now(timezone.utc),
                summary=summary,
                weight=int(src.get("weight", 9)),
                kind="discussion",
                points=points,
                extra_links=[(discussion, "HN 讨论")],
            ))
    if not raw_items:
        raise ValueError("HN 无命中（可能阈值过高或查询无结果）")
    return raw_items


def fetch_huggingface_papers(src: dict, domain: str, cfg: dict) -> list:
    limit = int(src.get("limit", 12))
    url = "https://huggingface.co/api/daily_papers?limit=%d" % limit
    data = json.loads(http_get(url, cfg, "application/json"))
    items = []
    for entry in data:
        paper = entry.get("paper") or {}
        title = " ".join((paper.get("title") or "").split())
        if not title:
            continue
        pid = paper.get("id") or ""
        link = "https://huggingface.co/papers/%s" % pid if pid else (entry.get("url") or "")
        published = _to_dt(paper.get("publishedAt") or entry.get("publishedAt") or "")
        upvotes = int(paper.get("upvotes") or 0)
        summary = clean_text(paper.get("summary") or entry.get("title") or "", 300)
        if upvotes:
            summary = "👍 %d · %s" % (upvotes, summary)
        items.append(Article(
            title=title,
            url=link,
            source=src.get("name", "Hugging Face Daily Papers"),
            domain=domain,
            published=published or datetime.now(timezone.utc),
            summary=summary,
            weight=int(src.get("weight", 16)),
            kind="paper",
            points=upvotes,
        ))
    if not items:
        raise ValueError("HF daily_papers 未返回条目")
    return items


def fetch_pubmed(src: dict, domain: str, cfg: dict) -> list:
    """PubMed 官方 E-utilities：先 esearch 找最近的文章，再 efetch 取含摘要的 XML。

    这是目前唯一稳定的"脑机接口"专门通道：RSS 里几乎没人做这个主题，
    而 PubMed 每隔几天就有新论文，且完全免费、无需 Key。
    """
    queries = src.get("queries") or []
    if not queries:
        return []
    fetch_cfg = cfg.get("fetch", {})
    days = int(src.get("days") or fetch_cfg.get("pubmed_days") or 30)
    retmax = int(src.get("retmax") or fetch_cfg.get("pubmed_max_results") or 12)
    now = datetime.now(timezone.utc)
    mindate = (now - timedelta(days=days)).strftime("%Y/%m/%d")
    maxdate = now.strftime("%Y/%m/%d")

    term = " OR ".join("(%s)" % q for q in queries)
    esearch = ("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
               "?db=pubmed&retmode=json&sort=date&retmax=%d&datetype=pdat&mindate=%s&maxdate=%s"
               "&term=%s" % (retmax, mindate, maxdate, urllib.parse.quote(term)))
    data = json.loads(http_get(esearch, cfg, "application/json"))
    ids = (data.get("esearchresult") or {}).get("idlist") or []
    if not ids:
        raise ValueError("PubMed 最近 %d 天没有命中" % days)

    efetch = ("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
              "?db=pubmed&retmode=xml&id=%s" % ",".join(ids))
    root = ET.fromstring(http_get(efetch, cfg, "application/xml"))

    items = []
    for art in root.findall(".//PubmedArticle"):
        # 标题里常混着 <i>/<sup> 等内联标签，itertext 会丢空格，必须补回来
        title = _joined_text(art.find(".//ArticleTitle"))
        if not title:
            continue
        abstract = " ".join(
            (_joined_text(node) if not node.attrib.get("Label")
             else "%s: %s" % (node.attrib["Label"], _joined_text(node)))
            for node in art.findall(".//Abstract/AbstractText"))
        journal = _joined_text(art.find(".//Journal/Title"))
        published = _pubmed_date(art)
        pmid = (art.findtext(".//PMID") or "").strip()
        doi = ""
        for aid in art.findall(".//ArticleId"):
            if aid.attrib.get("IdType") == "doi":
                doi = (aid.text or "").strip()
        link = ("https://doi.org/%s" % doi) if doi else (
            "https://pubmed.ncbi.nlm.nih.gov/%s/" % pmid if pmid else "")
        if not link:
            continue
        authors = [_joined_text(a.find(".//LastName"))
                   for a in art.findall(".//AuthorList/Author")]
        authors = [a for a in authors if a]
        prefix = ""
        if journal:
            prefix = "%s · " % journal[:60]
        if authors:
            prefix += authors[0] + (" 等 %d 人" % len(authors) if len(authors) > 1 else "") + " — "
        summary = clean_text(prefix + abstract, 320) if abstract else clean_text(prefix.rstrip(" —"), 200)
        items.append(Article(
            title=title,
            url=link,
            source=src.get("name", "PubMed"),
            domain=domain,
            published=published or now,
            summary=summary,
            weight=int(src.get("weight", 12)),
            kind="paper",
        ))
    if not items:
        raise ValueError("PubMed 返回了 ID 但没有解析出条目")
    return items


def _joined_text(node) -> str:
    """把节点内所有文本片段用空格连起来再归一化，修掉 itertext 丢空格的问题。"""
    if node is None:
        return ""
    return " ".join(" ".join(t for t in node.itertext()).split())


MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
          "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}


def _pubmed_date(art):
    """优先 ArticleDate（电子版日期），退回 JournalIssue/PubDate。"""
    for node in (art.find(".//ArticleDate"), art.find(".//JournalIssue/PubDate")):
        if node is None:
            continue
        year = (node.findtext("Year") or "").strip()
        if not year.isdigit():
            continue
        month_raw = (node.findtext("Month") or "").strip()
        month = MONTHS.get(month_raw[:3].lower(), None)
        if month is None and month_raw.isdigit():
            month = int(month_raw)
        day_raw = (node.findtext("Day") or "").strip()
        day = int(day_raw) if day_raw.isdigit() else 1
        try:
            return datetime(int(year), month or 1, day, tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _bing_real_url(link: str) -> str:
    """Bing 新闻的 link 是 apiclick.aspx 跳转，把真实地址从 url= 参数里取出来。"""
    try:
        parts = urllib.parse.urlsplit(link)
        if "bing.com" in parts.netloc and parts.path.endswith("apiclick.aspx"):
            query = urllib.parse.parse_qs(parts.query)
            real = (query.get("url") or [""])[0]
            if real.startswith("http"):
                return real
    except Exception:  # noqa: BLE001
        pass
    return link


def _bing_fetch_root(query: str, cfg: dict):
    """取一个查询的 RSS；串行 + 退避重试，避开 Bing 的限流（返回 HTML 的情况）。"""
    url = "https://www.bing.com/news/search?q=%s&format=RSS" % urllib.parse.quote(query)
    last_err = None
    for attempt in range(3):
        try:
            with BING_LOCK:                      # 串行化，避免并发触发限流
                xml = http_get(url, cfg,
                               "application/rss+xml, application/atom+xml, text/xml, */*")
                time.sleep(0.35)
            root = ET.fromstring(xml)            # 返回 HTML 时会在这里抛解析错误
            if _strip_ns(root.tag) != "rss":
                raise ValueError("返回的不是 RSS（可能是限流页）")
            return root
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            time.sleep(1.2 * (attempt + 1))
    raise last_err  # type: ignore[misc]


def _clean_outlet(name: str) -> str:
    """清洗媒体名：去掉 "on MSN" 之类的转载后缀。"""
    name = re.sub(r"\s+on\s+MSN\s*$", "", name, flags=re.I)
    name = re.sub(r"\s*-\s*MSN\s*$", "", name, flags=re.I)
    return name.strip()


def fetch_bing_news(src: dict, domain: str, cfg: dict) -> list:
    """Bing 新闻搜索（国内可直连，无需 Key），按 queries 逐个搜索后合并去重。

    每条结果自带真实媒体名（<News:Source>，例如 搜狐 / 新浪财经）和中文摘要，
    比固定"某站某栏目"覆盖更广。

    注意：URL 里只能有 q 和 format=RSS。加上 setmkt/setlang/mkt 等参数会返回 HTML
    而不是 RSS（实测），所以刻意保持最简。
    """
    queries = src.get("queries") or []
    if not queries:
        return []
    items, seen = [], set()
    for query in queries:
        try:
            root = _bing_fetch_root(query, cfg)
        except Exception as exc:  # noqa: BLE001
            warn("Bing 新闻查询失败 query=%s: %s" % (query, str(exc)[:120]))
            continue
        for node in (el for el in root.iter() if _strip_ns(el.tag) == "item"):
            title = _desc(node, "title")
            link = _desc(node, "link")
            if not title or not link:
                continue
            real = _bing_real_url(link)
            key = norm_url(real) or norm_title(title)
            if key in seen:
                continue
            seen.add(key)
            outlet = _clean_outlet(_desc(node, "Source") or "") or src.get("name", "Bing 新闻")
            items.append(Article(
                title=clean_text(title, 200),
                url=real,
                source=outlet,
                domain=domain,
                published=_to_dt(_desc(node, "pubDate")) or datetime.now(timezone.utc),
                summary=clean_text(_desc(node, "description")),
                weight=int(src.get("weight", 10)),
                group=src.get("group", ""),
            ))
    if not items:
        raise ValueError("Bing 新闻没有结果")
    return items


def _http_post_json(url: str, payload: dict, headers: dict, cfg: dict) -> dict:
    """POST JSON 并把响应解析成 dict（供需要 POST 的搜索 API 使用）。"""
    fetch_cfg = cfg.get("fetch", {})
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request_headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": fetch_cfg.get("user_agent", "Mozilla/5.0"),
    }
    request_headers.update(headers or {})
    req = urllib.request.Request(url, data=body, headers=request_headers, method="POST")
    with urllib.request.urlopen(req, timeout=int(fetch_cfg.get("timeout_seconds", 25))) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def _search_provider_key(src: dict, provider: str) -> str:
    """按服务商取 Key，优先环境变量，方便在 GitHub Secrets 里配置。"""
    env_names = {
        "brave": ("BRAVE_API_KEY", "SEARCH_API_KEY"),
        "tavily": ("TAVILY_API_KEY", "SEARCH_API_KEY"),
        "searxng": ("SEARXNG_URL",),
        "google": ("GOOGLE_API_KEY", "SEARCH_API_KEY"),
        "google_cse": ("GOOGLE_API_KEY", "SEARCH_API_KEY"),
    }.get(provider, ("SEARCH_API_KEY",))
    for name in env_names:
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return (src.get("api_key") or "").strip()


def _search_one(provider: str, query: str, src: dict, cfg: dict, key: str, limit: int):
    """调用一个搜索服务商，返回 [{title,url,description,published}]。"""
    timeout = int(cfg.get("fetch", {}).get("timeout_seconds", 25))
    ua = cfg.get("fetch", {}).get("user_agent", "Mozilla/5.0")

    if provider == "brave":
        base = (src.get("base_url") or "https://api.search.brave.com").rstrip("/")
        url = ("%s/res/v1/web/search?q=%s&count=%d"
               % (base, urllib.parse.quote(query), limit))
        req = urllib.request.Request(url, headers={
            "Accept": "application/json",
            "X-Subscription-Token": key,
            "User-Agent": ua,
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        return [{"title": r.get("title", ""), "url": r.get("url", ""),
                 "description": r.get("description", ""),
                 "published": r.get("page_age") or r.get("age") or ""}
                for r in (data.get("web", {}) or {}).get("results", [])]

    if provider == "tavily":
        base = (src.get("base_url") or "https://api.tavily.com").rstrip("/")
        data = _http_post_json(base + "/search",
                               {"api_key": key, "query": query, "max_results": limit,
                                "search_depth": src.get("search_depth", "basic"),
                                "include_answer": False, "days": int(src.get("days", 7))},
                               {}, cfg)
        return [{"title": r.get("title", ""), "url": r.get("url", ""),
                 "description": r.get("content", ""),
                 "published": r.get("published_date", "")}
                for r in data.get("results", [])]

    if provider == "searxng":
        base = key.rstrip("/")          # searxng 用 base_url 当"key"
        url = "%s/search?q=%s&format=json&language=zh-CN" % (
            base, urllib.parse.quote(query))
        req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": ua})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        return [{"title": r.get("title", ""), "url": r.get("url", ""),
                 "description": r.get("content", ""),
                 "published": r.get("publishedDate", "")}
                for r in data.get("results", [])]

    if provider in ("google", "google_cse"):
        cx = (os.environ.get("GOOGLE_CSE_ID") or src.get("cse_id") or "").strip()
        if not cx:
            raise ValueError("google_cse 需要配置 cse_id（或环境变量 GOOGLE_CSE_ID）")
        base = (src.get("base_url") or "https://www.googleapis.com").rstrip("/")
        url = ("%s/customsearch/v1?key=%s&cx=%s&q=%s&num=%d&dateRestrict=d%d"
               % (base, key, cx, urllib.parse.quote(query), min(limit, 10),
                  int(src.get("days", 7))))
        req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": ua})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        return [{"title": r.get("title", ""), "url": r.get("link", ""),
                 "description": r.get("snippet", ""), "published": ""}
                for r in data.get("items", [])]

    raise ValueError("不支持的搜索服务商: %s" % provider)


def fetch_web_search(src: dict, domain: str, cfg: dict) -> list:
    """通用网页搜索信源：按 queries 调用搜索 API，把网页结果并入周报。

    注意：Bing Web Search API 已于 2025-08-11 退役，所以不再支持 bing 这一项。
    可用 provider：brave / tavily / searxng / google_cse。
    Key 优先读环境变量（BRAVE_API_KEY / TAVILY_API_KEY / GOOGLE_API_KEY /
    SEARXNG_URL），没配就退回到 config 里的 api_key。
    """
    queries = src.get("queries") or []
    if not queries:
        return []
    provider = (src.get("provider") or "brave").lower()
    key = _search_provider_key(src, provider)
    if not key:
        raise ValueError("未配置 %s 的 Key（环境变量或 config 的 api_key）" % provider)

    supported = ("brave", "tavily", "searxng", "google", "google_cse")
    if provider not in supported:
        raise ValueError("不支持的搜索服务商: %s（支持 %s）"
                         % (provider, " / ".join(supported)))

    limit = int(src.get("limit", 10))
    items, seen = [], set()
    for query in queries:
        try:
            results = _search_one(provider, query, src, cfg, key, limit)
        except Exception as exc:  # noqa: BLE001
            warn("网页搜索失败 provider=%s query=%s: %s" % (provider, query, str(exc)[:120]))
            continue
        for result in results:
            title = (result.get("title") or "").strip()
            link = (result.get("url") or "").strip()
            if not title or not link:
                continue
            normalized = norm_url(link)
            if normalized in seen:
                continue
            seen.add(normalized)
            items.append(Article(
                title=clean_text(title, 200),
                url=link,
                source=host_of(link) or provider,
                domain=domain,
                published=_to_dt(result.get("published") or "") or datetime.now(timezone.utc),
                summary=clean_text(result.get("description") or ""),
                weight=int(src.get("weight", 10)),
                group=src.get("group", ""),
            ))
    if not items:
        raise ValueError("网页搜索没有结果")
    return items


FETCHERS = {
    "rss": fetch_rss,
    "arxiv": fetch_arxiv,
    "hackernews": fetch_hackernews,
    "huggingface_papers": fetch_huggingface_papers,
    "pubmed": fetch_pubmed,
    "bing_news": fetch_bing_news,
    "web_search": fetch_web_search,
}


def collect(cfg: dict):
    """并发抓取全部信源。返回 (articles, source_status)。"""
    jobs = []
    for dom in cfg.get("domains", []):
        if not dom.get("enabled", True):
            continue
        for src in dom.get("sources", []):
            if src.get("enabled", True) is False:
                continue          # 单个信源可临时停用，不必删除配置
            jobs.append((dom["id"], src))

    articles, status = [], []
    workers = max(1, int(cfg.get("fetch", {}).get("workers", 10)))
    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {}
        for domain_id, src in jobs:
            fn = FETCHERS.get(src.get("type", "rss"))
            if fn is None:
                status.append({"domain": domain_id, "source": src.get("name", "?"), "ok": False,
                               "error": "未知信源类型 %s" % src.get("type")})
                continue
            futures[pool.submit(fn, src, domain_id, cfg)] = (domain_id, src)
        for fut in cf.as_completed(futures):
            domain_id, src = futures[fut]
            name = src.get("name", src.get("url", "?"))
            try:
                got = fut.result()
                before = len(got)
                if src.get("filter_keywords") or src.get("title_keywords"):
                    got = [a for a in got if source_relevance_ok(a, src)]
                articles.extend(got)
                status.append({"domain": domain_id, "source": name, "ok": True, "count": len(got),
                               "filtered": before - len(got)})
                if before != len(got):
                    log("  ✓ %-38s %3d 条（按主题过滤掉 %d 条）"
                        % (name[:38], len(got), before - len(got)))
                else:
                    log("  ✓ %-38s %3d 条" % (name[:38], len(got)))
            except Exception as exc:  # noqa: BLE001
                status.append({"domain": domain_id, "source": name, "ok": False,
                               "error": "%s: %s" % (type(exc).__name__, str(exc)[:160])})
                warn("  ✗ %-38s %s" % (name[:38], str(exc)[:120]))
    return articles, status


# --------------------------------------------------------------------------- #
# 筛选 / 排序
# --------------------------------------------------------------------------- #
def score_article(art: Article, dom_cfg: dict, now: datetime) -> float:
    age_hours = max(0.0, (now - art.published).total_seconds() / 3600.0)
    recency = max(0.0, 1.0 - age_hours / (24.0 * 10))          # 10 天线性衰减
    score = float(art.weight) * (0.45 + 0.55 * recency)
    haystack = (art.title + " " + art.summary).lower()
    hits = 0
    for kw in dom_cfg.get("keywords", []):
        if kw.lower() in haystack:
            hits += 1
    score += min(hits, 4) * 1.1
    if art.kind == "paper":
        score += 1.2
        if PAPER_EXCLUDE.search(art.title):
            score -= 2.5                                       # 综述/教程类，信息密度低，降权
    if art.points:
        score += min(art.points / 25.0, 9.0)
    if NOISE_RE.search(art.title):
        score -= 14.0                                          # 案件/诉讼八卦类，压到候选池末尾
    if FINANCE_RE.search(art.title) and art.domain != "bio":
        score -= 4.5                                           # 财经资本类降权（生物医药的融资可保留）
    return score


def build_report(cfg: dict, window_days: int, now: datetime | None = None):
    now = now or datetime.now(timezone.utc)
    articles, status = collect(cfg)
    cutoff = now - timedelta(days=window_days)
    dom_map = {d["id"]: d for d in cfg.get("domains", []) if d.get("enabled", True)}

    for art in articles:
        art.score = score_article(art, dom_map.get(art.domain, {}), now)
        art.in_window = art.published >= cutoff

    # ---- 去重：同一篇内容在多源出现时保留最佳来源，其余挂到 extra_links ----
    # 两级判断：先按正文链接归一化；再按标题归一化。
    # 后者专门处理"同一篇稿子被腾讯/搜狐/新浪分别转载"——URL 各不相同，
    # 但标题一字不差，只用 URL 去重会把同一件事重复列两三次。
    best: dict = {}
    best_by_title: dict = {}
    for art in sorted(articles, key=lambda a: (a.score, a.published), reverse=True):
        k = key_of(art)
        t = norm_title(art.title)
        keep = best.get(k) or (best_by_title.get(t) if t else None)
        if keep is not None:
            if art.source != keep.source and all(art.url != u for u, _ in keep.extra_links):
                keep.extra_links.append((art.url, art.source))
            keep.score = max(keep.score, art.score - 0.5)
            best.setdefault(k, keep)
            if t:
                best_by_title.setdefault(t, keep)
        else:
            best[k] = art
            if t:
                best_by_title[t] = art
    deduped = list({id(a): a for a in best.values()}.values())

    all_dates = [a.published for a in deduped]
    stats = {
        "fetched": len(articles),
        "unique": len(deduped),
        "in_window": sum(1 for a in deduped if a.in_window),
        "oldest": min(all_dates).isoformat() if all_dates else "",
        "newest": max(all_dates).isoformat() if all_dates else "",
        "sources_ok": sum(1 for s in status if s["ok"]),
        "sources_total": len(status),
        "sources_degraded": [s for s in status if not s["ok"]],
    }

    # ---- 每领域配额 + 同源打散 ----
    # 领域里的信源可以带 group 字段，声明自己属于哪个子分组（例如生物医药里的"脑机接口"）。
    # 有子分组时，把领域名额按各分组信源数量比例分下去，保证小众主题不会被大主题挤掉。
    article_groups = {}
    for dom in cfg.get("domains", []):
        defaults = dom.get("source_group_defaults") or []
        for i, s in enumerate(dom.get("sources", [])):
            group = s.get("group") or (defaults[i] if i < len(defaults) else "")
            if group:
                article_groups[s.get("name")] = group
    for art in deduped:
        # 抓取阶段已带 group 的（例如 bing_news 的脑机接口）优先保留
        art.group = art.group or article_groups.get(art.source, "")

    sections = []
    for dom in cfg.get("domains", []):
        if not dom.get("enabled", True):
            continue
        quota = int(dom.get("max_items", cfg.get("report", {}).get("max_per_domain", 6)))
        pool = [a for a in deduped if a.domain == dom["id"] and a.in_window]
        stale_pool = [a for a in deduped if a.domain == dom["id"] and not a.in_window]
        pool.sort(key=lambda a: (a.score, a.published), reverse=True)
        stale_pool.sort(key=lambda a: (a.score, a.published), reverse=True)

        # 收集本领域出现的子分组（按信源声明顺序），并统计各组的信源数量
        order, weights = [], {}
        for s in dom.get("sources", []):
            g = article_groups.get(s.get("name"), "")
            if not g:
                continue
            if g not in order:
                order.append(g)
                weights[g] = 0
            weights[g] += 1

        if order:
            general_key = "__general__"
            general_sources = [s.get("name") for s in dom.get("sources", [])
                               if not article_groups.get(s.get("name"), "")]
            general_arts = [a for a in pool if a.source in general_sources]
            keys = [general_key] + order if general_sources else list(order)
            weights_map = dict(weights)
            weights_map[general_key] = len(general_sources)
            quota_map = allocate_quota(keys, weights_map, quota)
            # 分组保底名额：按比例分配后，某些分组可能只拿到 1 个甚至 0 个，
            # 主题重要但内容多时明显不够用。group_min 可以给它们设下限，
            # 差额从其他分组（配额多的优先）里借，总数不变。
            for g, minimum in (dom.get("group_min") or {}).items():
                if g not in quota_map:
                    continue
                deficit = int(minimum) - quota_map[g]
                if deficit <= 0:
                    continue
                for other in sorted((k for k in quota_map if k != g),
                                    key=lambda k: quota_map[k], reverse=True):
                    if deficit <= 0:
                        break
                    take = min(deficit, quota_map[other])
                    quota_map[other] -= take
                    quota_map[g] += take
                    deficit -= take

            chosen, overflow = [], []
            for key in keys:
                if key == general_key:
                    sub = general_arts
                else:
                    sub = [a for a in pool if a.group == key]
                picked, over = pick_with_source_spread(sub, quota_map.get(key, 0))
                chosen.extend(picked)
                overflow.extend(over)
            # 名额回收：某组没用满而另一组还有余量时，用剩余内容补满本节
            if len(chosen) < quota and overflow:
                used = {id(a) for a in chosen}
                spare = [a for a in overflow if id(a) not in used]
                spare.sort(key=lambda a: (a.score, a.published), reverse=True)
                chosen.extend(spare[: quota - len(chosen)])
            if len(chosen) < quota:                  # 本周条目不齐，用稍旧条目补
                extra, _ = pick_with_source_spread(stale_pool, quota - len(chosen))
                for art in extra:
                    art.in_window = False
                    chosen.append(art)
            chosen.sort(key=lambda a: (a.score, a.published), reverse=True)

            groups = []
            if general_arts:
                groups.append({"name": "", "articles": [a for a in chosen
                                                        if a.source in general_sources]})
            for key in order:
                groups.append({"name": key,
                               "articles": [a for a in chosen if a.group == key]})
        else:
            chosen, overflow = pick_with_source_spread(pool, quota)
            if len(chosen) < quota:                  # 本周条目太少，用稍旧的补足
                extra, _ = pick_with_source_spread(stale_pool, quota - len(chosen))
                for art in extra:
                    art.in_window = False
                    chosen.append(art)
            groups = []

        # 近似去重：同一事件被多家媒体用不同标题转载时只留一条，空出的名额补回来
        chosen = collapse_similar(chosen)
        if len(chosen) < quota:
            used = {id(a) for a in chosen}
            spare = [a for a in overflow if id(a) not in used]
            spare.sort(key=lambda a: (a.score, a.published), reverse=True)
            chosen.extend(spare[: quota - len(chosen)])
            if len(chosen) < quota:
                extra, _ = pick_with_source_spread(stale_pool, quota - len(chosen))
                for art in extra:
                    art.in_window = False
                    chosen.append(art)
            chosen.sort(key=lambda a: (a.score, a.published), reverse=True)
        if order:                                    # 重新按分组归类
            groups = []
            if general_arts:
                groups.append({"name": "",
                               "articles": [a for a in chosen if a.source in general_sources]})
            for key in order:
                groups.append({"name": key,
                               "articles": [a for a in chosen if a.group == key]})
        else:
            groups = []

        sections.append({"domain": dom, "articles": chosen, "overflow": overflow,
                         "available": len(pool), "groups": groups})

    report = {
        "generated_at": now,
        "window_days": window_days,
        "period_label": period_label(now, window_days),
        "sections": sections,
        "stats": stats,
        "source_status": status,
    }
    return report


def allocate_quota(order: list, weights: dict, total: int) -> dict:
    """按各分组的信源数量比例分配名额，用最大余数法保证总和不超也不浪费。

    举例：生物医药 5 个名额，常规源 9 个、"脑机接口"2 个 ->
    脑机接口拿到 1 个保底名额，不会被 9 个常规源全部挤掉。
    """
    weight_sum = sum(max(0, weights.get(g, 0)) for g in order) or 1
    raw = {g: total * max(0, weights.get(g, 0)) / float(weight_sum) for g in order}
    quota = {g: int(raw[g]) for g in order}
    left = total - sum(quota.values())
    ranked = sorted(order, key=lambda g: (raw[g] - int(raw[g]), weights.get(g, 0)), reverse=True)
    for g in ranked:
        if left <= 0:
            break
        quota[g] += 1
        left -= 1
    return quota


def pick_with_source_spread(pool: list, quota: int):
    """每个来源先取一条轮转，保证同一邮件里来源多样，返回 (选中, 溢出备选)。"""
    chosen, used_sources = [], {}
    remaining = list(pool)
    while len(chosen) < quota and remaining:
        pick = None
        for art in remaining:
            if used_sources.get(art.source, 0) == 0:
                pick = art
                break
        if pick is None:
            pick = remaining[0]
        remaining.remove(pick)
        used_sources[pick.source] = used_sources.get(pick.source, 0) + 1
        chosen.append(pick)
    chosen.sort(key=lambda a: (a.score, a.published), reverse=True)
    return chosen, remaining


def _title_grams(title: str) -> Counter:
    """标题的字符 2-gram 计数，用于近似重复判断（对中文无需分词）。"""
    text = re.sub(r"[^\w\u4e00-\u9fff]+", "", (title or "").lower())
    return Counter(text[i:i + 2] for i in range(len(text) - 1))


def _gram_cosine(a: Counter, b: Counter) -> float:
    if not a or not b:
        return 0.0
    common = set(a) & set(b)
    numerator = sum(a[k] * b[k] for k in common)
    na = sum(v * v for v in a.values()) ** 0.5
    nb = sum(v * v for v in b.values()) ** 0.5
    return numerator / (na * nb) if na and nb else 0.0


def collapse_similar(articles: list, threshold: float = 0.39) -> list:
    """把"同一件事、不同标题"的多条合并成一条。

    典型场景：腾讯、搜狐、新浪分别转载同一篇稿子，标题措辞略有不同，
    按 URL 或标题完全相等的去重都抓不到这类重复。用字符 2-gram 余弦相似度判断，
    阈值 0.39 是实测选出来的：同一事件的不同标题通常 >=0.39，
    而不同事件（例如"华为芯片"与"DeepSeek 融资"）只有 0.35 左右。
    """
    kept, grams = [], []
    for art in articles:
        g = _title_grams(art.title)
        hit = None
        for index, keep in enumerate(kept):
            if _gram_cosine(g, grams[index]) >= threshold:
                hit = keep
                break
        if hit is None:
            kept.append(art)
            grams.append(g)
            continue
        if art.source != hit.source and all(art.url != u for u, _ in hit.extra_links):
            hit.extra_links.append((art.url, art.source))
        hit.score = max(hit.score, art.score - 0.5)
    return kept


def period_label(now: datetime, window_days: int) -> str:
    tz = timezone(timedelta(hours=8))
    end = now.astimezone(tz)
    start = (now - timedelta(days=window_days)).astimezone(tz)
    return "%s ~ %s" % (start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))


# --------------------------------------------------------------------------- #
# 渲染
# --------------------------------------------------------------------------- #
KIND_LABEL = {"paper": "论文", "discussion": "讨论", "article": "报道"}
TYPE_TAG = {"paper": "📄 论文", "discussion": "💬 讨论", "article": "📰 报道"}


def render_text(report: dict, cfg: dict) -> str:
    rep = cfg.get("report", {})
    tz = timezone(timedelta(hours=8))
    lines = []
    lines.append("%s  %s" % (rep.get("title", "科技周报"), report["period_label"]))
    lines.append("=" * 46)
    if rep.get("intro"):
        lines.append(rep["intro"])
    lines.append("")
    total = 0
    for sec in report["sections"]:
        dom = sec["domain"]
        arts = sec["articles"]
        if not arts:
            continue
        lines.append("【%s %s】共 %d 条" % (dom.get("icon", ""), dom.get("name", dom["id"]), len(arts)))
        lines.append("-" * 46)
        # groups 里含"其他"（name 为空）与各子分组；无子分组时就是单独一组空名字
        groups = sec.get("groups") or [{"name": "", "articles": arts}]
        shown = set()
        idx = 0                       # 每个领域内重新从 1 开始编号
        for group in groups:
            gname = group.get("name") or ""
            garts = [a for a in group.get("articles", []) if id(a) not in shown]
            if not garts:
                continue
            if gname:
                lines.append("  ◆ %s" % gname)
            for art in garts:
                shown.add(id(art))
                total += 1
                idx += 1
                flag = "" if art.in_window else "（稍早）"
                lines.append("%d. %s %s" % (idx, art.display_title, flag))
                if art.translated:
                    lines.append("   原题：%s" % art.title)
                lines.append("   %s · %s · %s" % (art.source, TYPE_TAG.get(art.kind, ""),
                                                  art.published.astimezone(tz).strftime("%m-%d %H:%M")))
                if art.display_summary:
                    lines.append("   %s" % art.display_summary)
                lines.append("   %s" % art.url)
                for link, src in art.extra_links:
                    lines.append("   相关：%s（%s）" % (link, src))
                lines.append("")
        lines.append("")
    if total == 0:
        lines.append("本期窗口内没有抓取到符合条件的内容。可能是信源临时故障，见下方信源状态。")
        lines.append("")
    st = report["stats"]
    lines.append("统计：抓取 %d 条 → 去重后 %d 条 → 窗口内 %d 条 → 入选 %d 条；信源 %d/%d 正常。"
                 % (st["fetched"], st["unique"], st["in_window"], total, st["sources_ok"], st["sources_total"]))
    if st["sources_degraded"]:
        lines.append("异常信源：")
        for s in st["sources_degraded"]:
            lines.append("  - %s: %s" % (s["source"], s.get("error", "")))
    lines.append("")
    lines.append(rep.get("footer", ""))
    lines.append("生成时间：%s" % report["generated_at"].astimezone(tz).strftime("%Y-%m-%d %H:%M:%S %z"))
    return "\n".join(lines)


def _esc(text: str) -> str:
    return html.escape(text or "", quote=True)


def render_html(report: dict, cfg: dict) -> str:
    rep = cfg.get("report", {})
    tz = timezone(timedelta(hours=8))
    total = sum(len(s["articles"]) for s in report["sections"])
    st = report["stats"]

    parts = []
    parts.append("""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>%s %s</title>
</head><body style="margin:0;padding:0;background:#eef1f6;">
<div style="max-width:720px;margin:0 auto;padding:20px 12px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Hiragino Sans GB','Microsoft YaHei',sans-serif;color:#1b1f27;font-size:15px;line-height:1.65;">

  <div style="background:linear-gradient(135deg,#1e3a8a 0%%,#2563eb 55%%,#0ea5e9 100%%);border-radius:14px;padding:26px 24px;color:#fff;">
    <div style="font-size:13px;letter-spacing:2px;opacity:.85;">TECH WEEKLY · %s</div>
    <div style="font-size:27px;font-weight:700;margin:8px 0 6px;">%s %s</div>
    <div style="font-size:14px;opacity:.92;">%s</div>
    <div style="margin-top:14px;font-size:13px;opacity:.92;">本期入选 <b>%d</b> 条 · 信源正常 <b>%d/%d</b> · 去重后候选 <b>%d</b> 条</div>
  </div>
""" % (_esc(rep.get("title", "科技周报")), _esc(report["period_label"]),
       _esc(report["period_label"]), _esc(rep.get("title", "科技周报")), _esc(report["period_label"]),
       _esc(rep.get("intro", "")), total, st["sources_ok"], st["sources_total"], st["unique"]))

    for sec in report["sections"]:
        dom, arts = sec["domain"], sec["articles"]
        parts.append("""
  <div style="margin-top:26px;">
    <div style="border-left:4px solid #2563eb;padding-left:10px;font-size:19px;font-weight:700;">
      %s %s <span style="font-size:13px;font-weight:400;color:#6b7280;">%d 条 / 窗口内候选 %d</span>
    </div>
""" % (_esc(dom.get("icon", "")), _esc(dom.get("name", dom["id"])), len(arts), sec["available"]))
        if not arts:
            parts.append("""    <div style="margin:10px 0;padding:12px 14px;background:#fff;border-radius:10px;color:#6b7280;font-size:14px;">
      本期窗口内没有符合条件的内容（信源可能临时故障）。
    </div>
""")
        groups = sec.get("groups") or [{"name": "", "articles": arts}]
        shown = set()
        for group in groups:
            gname = group.get("name") or ""
            garts = [a for a in group.get("articles", []) if id(a) not in shown]
            if not garts:
                continue
            if gname:
                parts.append("""
    <div style="margin:16px 0 4px;padding:6px 10px;background:#eef4ff;border-radius:8px;
                font-size:14px;font-weight:600;color:#1d4ed8;">◆ %s
      <span style="font-weight:400;color:#6b7280;font-size:12.5px;">%d 条</span>
    </div>
""" % (_esc(gname), len(garts)))
            for art in garts:
                shown.add(id(art))
                url = _esc(art.url)
                meta = "%s · %s · %s" % (_esc(art.source), TYPE_TAG.get(art.kind, ""),
                                         art.published.astimezone(tz).strftime("%m-%d %H:%M"))
                if not art.in_window:
                    meta += " · <span style=\"color:#b45309;\">稍早</span>"
                extra = ""
                for link, src in art.extra_links[:3]:
                    extra += ' · <a href="%s" style="color:#2563eb;text-decoration:none;">%s</a>' % (_esc(link), _esc(src))
                summary = ""
                if art.display_summary:
                    summary = ('<div style="margin-top:6px;color:#4b5563;font-size:14px;">%s</div>'
                               % _esc(art.display_summary))
                original = ""
                if art.translated:
                    # 保留英文原题，便于回原文检索；纯文本形式，避免和小标题抢点击
                    original = ('<div style="margin-top:4px;color:#9aa3b0;font-size:12px;">%s</div>'
                                % _esc(art.title))
                parts.append("""
    <div style="background:#fff;border-radius:10px;padding:14px 16px;margin:10px 0;box-shadow:0 1px 3px rgba(16,24,40,.06);">
      <a href="%s" style="color:#111827;text-decoration:none;font-size:16px;font-weight:600;">%s</a>
      %s
      %s
      <div style="margin-top:7px;color:#6b7280;font-size:12.5px;">%s%s</div>
    </div>
""" % (url, _esc(art.display_title), original, summary, meta, extra))
        parts.append("  </div>\n")

    if st["sources_degraded"]:
        rows = "".join('<li style="margin:2px 0;">%s — %s</li>' % (_esc(s["source"]), _esc(s.get("error", "")))
                       for s in st["sources_degraded"])
        parts.append("""
  <div style="margin-top:26px;background:#fff7ed;border:1px solid #fed7aa;border-radius:10px;padding:12px 16px;">
    <div style="font-weight:600;color:#9a3412;">本期有 %d 个信源异常</div>
    <ul style="margin:6px 0 0;padding-left:20px;color:#7c2d12;font-size:13px;">%s</ul>
  </div>
""" % (len(st["sources_degraded"]), rows))

    parts.append("""
  <div style="margin-top:24px;padding-top:14px;border-top:1px solid #dfe3ea;color:#8b93a1;font-size:12px;text-align:center;">
    %s<br>生成时间：%s · 抓取 %d 条 → 去重 %d 条 → 窗口内 %d 条
  </div>
</div></body></html>
""" % (_esc(rep.get("footer", "")),
       report["generated_at"].astimezone(tz).strftime("%Y-%m-%d %H:%M:%S"),
       st["fetched"], st["unique"], st["in_window"]))
    return "".join(parts)


# --------------------------------------------------------------------------- #
# 邮件发送
# --------------------------------------------------------------------------- #
def env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def resolve_mail(cfg: dict, override_recipients=None) -> dict:
    smtp = dict(cfg.get("smtp", {}))
    smtp["host"] = env("SMTP_HOST", smtp.get("host", ""))
    smtp["port"] = int(env("SMTP_PORT", str(smtp.get("port", 465))))
    smtp["user"] = env("SMTP_USER", smtp.get("user", ""))
    smtp["password"] = env("SMTP_PASSWORD") or env("SMTP_PASS") or smtp.get("password", "")
    smtp["sender"] = env("MAIL_FROM", smtp.get("sender", "")) or smtp["user"]
    smtp["from_name"] = env("MAIL_FROM_NAME", smtp.get("from_name", ""))

    recipients = override_recipients
    if not recipients:
        raw = env("MAIL_TO")
        if raw:
            recipients = [x.strip() for x in re.split(r"[,;\s]+", raw) if x.strip()]
        else:
            recipients = smtp.get("recipients") or []
    smtp["recipients"] = [r for r in recipients if r]

    raw_cc = env("MAIL_CC")
    if raw_cc:
        smtp["cc"] = [x.strip() for x in re.split(r"[,;\s]+", raw_cc) if x.strip()]
    else:
        smtp["cc"] = [c for c in (smtp.get("cc") or []) if c]

    ssl_flag = smtp.get("ssl", True)
    if env("SMTP_SSL"):
        ssl_flag = env("SMTP_SSL").lower() in ("1", "true", "yes", "on")
    smtp["ssl"] = bool(ssl_flag)
    starttls = smtp.get("starttls", False)
    if env("SMTP_STARTTLS"):
        starttls = env("SMTP_STARTTLS").lower() in ("1", "true", "yes", "on")
    smtp["starttls"] = bool(starttls)
    return smtp


def send_mail(smtp_cfg: dict, subject: str, text_body: str, html_body: str, recipients=None) -> None:
    to = recipients or smtp_cfg["recipients"]
    cc = smtp_cfg.get("cc") or []
    if not to:
        raise SystemExit("没有收件人：请填写 config.json 的 smtp.recipients，或设置环境变量 MAIL_TO")
    if not smtp_cfg.get("host"):
        raise SystemExit("没有 SMTP 服务器：请填写 config.json 的 smtp.host，或设置环境变量 SMTP_HOST")
    if not smtp_cfg.get("user") or not smtp_cfg.get("password"):
        raise SystemExit("缺少 SMTP 账号或授权码：请填写 config.json 的 smtp.user/smtp.password，"
                         "或设置环境变量 SMTP_USER / SMTP_PASSWORD")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = formataddr((str(Header(smtp_cfg.get("from_name") or "Tech Weekly", "utf-8")),
                              smtp_cfg["sender"]))
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = "<tech-weekly-%s@%s>" % (int(time.time()),
                                                 smtp_cfg["sender"].split("@")[-1] or "localhost")
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    host, port = smtp_cfg["host"], int(smtp_cfg["port"])
    if smtp_cfg["ssl"]:
        log("连接 %s:%d (SSL)…" % (host, port))
        server = smtplib.SMTP_SSL(host, port, timeout=45, context=ssl.create_default_context())
    else:
        log("连接 %s:%d…" % (host, port))
        server = smtplib.SMTP(host, port, timeout=45)
        server.ehlo()
        if smtp_cfg["starttls"]:
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
    try:
        server.login(smtp_cfg["user"], smtp_cfg["password"])
        server.sendmail(smtp_cfg["sender"], to + cc, msg.as_string())
    finally:
        try:
            server.quit()
        except Exception:  # noqa: BLE001
            pass
    log("已发送 → %s%s" % (", ".join(to), (" 抄送 " + ", ".join(cc)) if cc else ""))


def build_subject(report: dict, cfg: dict) -> str:
    title = cfg.get("report", {}).get("title", "科技周报")
    total = sum(len(s["articles"]) for s in report["sections"])
    return "%s %s（%d 条）" % (title, report["period_label"], total)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def load_config(path: str) -> dict:
    if not os.path.exists(path):
        raise SystemExit("找不到配置文件：%s" % path)
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def save_outputs(report: dict, cfg: dict, outdir: str):
    os.makedirs(outdir, exist_ok=True)
    text_body = render_text(report, cfg)
    html_body = render_html(report, cfg)
    stamp = report["generated_at"].astimezone(timezone(timedelta(hours=8))).strftime("%Y%m%d")
    paths = {
        "html": os.path.join(outdir, "weekly-%s.html" % stamp),
        "text": os.path.join(outdir, "weekly-%s.txt" % stamp),
    }
    with open(paths["html"], "w", encoding="utf-8") as fh:
        fh.write(html_body)
    with open(paths["text"], "w", encoding="utf-8") as fh:
        fh.write(text_body)
    return text_body, html_body, paths


def cmd_test(cfg: dict) -> int:
    log("开始体检全部信源…")
    _articles, status = collect(cfg)
    ok = [s for s in status if s["ok"]]
    bad = [s for s in status if not s["ok"]]
    print("\n可用 %d / %d" % (len(ok), len(status)))
    if bad:
        print("\n异常信源：")
        for s in bad:
            print("  ✗ [%s] %s → %s" % (s["domain"], s["source"], s.get("error", "")))
    return 0 if not bad else 1


def prepare(cfg: dict, days: int, use_llm: bool = True):
    """抓取 + 排序 +（可选）中文摘要。返回 (report, llm_usage)。"""
    report = build_report(cfg, days)
    usage, translator = translate.translate_report_articles(
        report, cfg, log_fn=log, warn_fn=warn, enabled=use_llm)
    report["llm_usage"] = usage
    report["llm_failed_batches"] = getattr(translator, "failed_batches", 0)
    return report, usage


def cmd_preview(cfg: dict, days: int, outdir: str, use_llm: bool = True) -> int:
    report, _usage = prepare(cfg, days, use_llm)
    _text, _html, paths = save_outputs(report, cfg, outdir)
    total = sum(len(s["articles"]) for s in report["sections"])
    zh = sum(1 for s in report["sections"] for a in s["articles"] if a.translated)
    log("预览已生成：%s（%d 条，其中中文 %d 条）" % (paths["html"], total, zh))
    log("纯文本副本：%s" % paths["text"])
    return 0


def cmd_send(cfg: dict, days: int, outdir: str, recipients=None, use_llm: bool = True) -> int:
    report, _usage = prepare(cfg, days, use_llm)
    text_body, html_body, _paths = save_outputs(report, cfg, outdir)
    articles = [a for s in report["sections"] for a in s["articles"]]
    total = len(articles)
    if total == 0:
        warn("本期没有任何入选条目；为避免发送空周报，已跳过发信（预览文件仍已生成）。")
        return 2

    # 要求了中文却一条都没翻成，说明 Key 或接口有问题：
    # 宁可这次失败，也不要发一封全英文的"中文周报"出去。
    llm = translate.llm_config(cfg)
    if use_llm and llm.get("enabled", True) and llm.get("api_key") \
            and not any(a.translated for a in articles):
        warn("中文摘要全部失败，已中止发信。请先运行 `python main.py llm` 排查接口与 Key。")
        return 3

    smtp_cfg = resolve_mail(cfg, recipients)
    send_mail(smtp_cfg, build_subject(report, cfg), text_body, html_body)
    return 0


def cmd_run(cfg: dict, days: int, outdir: str, use_llm: bool = True) -> int:
    """两段式：先发给 TEST_RECIPIENT，成功后再发给全体收件人。"""
    test_to = [x.strip() for x in re.split(r"[,;\s]+", env("TEST_RECIPIENT")) if x.strip()]
    if test_to:
        log("第一段：试发给 %s" % ", ".join(test_to))
        code = cmd_send(cfg, days, outdir, recipients=test_to, use_llm=use_llm)
        if code != 0:
            warn("试发失败，终止后续发送")
            return code
    else:
        log("未设置 TEST_RECIPIENT，跳过试发环节")
    log("第二段：正式发送")
    return cmd_send(cfg, days, outdir, use_llm=use_llm)


def cmd_llm_test(cfg: dict) -> int:
    """只翻译几条固定样例并把结果打到终端，用来确认 Key、模型名和中文质量。"""
    llm = translate.llm_config(cfg)
    print("中文摘要配置：%s" % translate.describe(llm))
    print("enabled=%s  api_key=%s  batch_size=%s  use_cache=%s"
          % (llm["enabled"], "已配置" if llm["api_key"] else "缺失",
             llm["batch_size"], llm["use_cache"]))
    if not llm["enabled"]:
        warn("config.json 里 llm.enabled=false，中文摘要已关闭。")
        return 1
    if not llm["api_key"]:
        warn("缺少 API Key。两种情况：\n"
             "  1) 你还没设置 —— 运行：setx LLM_API_KEY \"sk-...\"\n"
             "  2) 你刚用 setx 设置过 —— setx 只对\"之后新开\"的窗口生效，\n"
             "     请关掉当前窗口、重新开一个终端再试；\n"
             "     或者在当前窗口临时生效：$env:LLM_API_KEY=\"sk-...\"")
        return 1

    # 固定样例，不联网抓信源，保证这里只测"接口 + 中文质量"
    now = datetime.now(timezone.utc)
    samples = [
        Article(title="Nvidia unveils next-gen GPU with 480GB memory for AI training",
                url="https://example.com/llm-test-1", source="自检样例", domain="chip",
                published=now,
                summary="Nvidia said the new part targets large-scale training clusters and will "
                        "ship to cloud partners later this year.", weight=10),
        Article(title="CRISPR-based therapy lowers tau protein in early Alzheimer's trial",
                url="https://example.com/llm-test-2", source="自检样例", domain="bio",
                published=now,
                summary="A single subcutaneous dose reduced tau levels in cerebrospinal fluid, "
                        "according to interim data from a phase 1 study.", weight=10),
        Article(title="Humanoid robot learns loco-manipulation from human video",
                url="https://example.com/llm-test-3", source="自检样例", domain="robot",
                published=now, summary="", weight=10),
    ]

    translator = translate.Translator(llm, log_fn=lambda _m: None, warn_fn=warn)
    translator.translate_articles(samples)

    ok = 0
    print("\n翻译结果：")
    for i, art in enumerate(samples, 1):
        print("\n  [%d] 原题  ：%s" % (i, art.title))
        print("      中文题：%s" % (art.title_zh or "（失败，未翻译）"))
        if art.summary:
            print("      原摘要：%s" % art.summary)
            print("      中文摘要：%s" % (art.summary_zh or "（失败，未翻译）"))
        else:
            print("      原摘要：（空）→ 空摘要应保持为空：%s"
                  % ("符合" if not art.summary_zh else "不符合 → %s" % art.summary_zh))
        if art.title_zh:
            ok += 1

    print("\n%s" % translator.cost_line())
    print("结果：%d/%d 条成功" % (ok, len(samples)))
    return 0 if ok == len(samples) else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="每周科技周报生成与邮件推送")
    parser.add_argument("command", choices=["test", "llm", "preview", "send", "run"],
                        help="test=信源体检 llm=中文摘要自检 preview=只生成不发 "
                             "send=生成并发送 run=试发+正式发送")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="配置文件路径")
    parser.add_argument("--days", type=int, default=None, help="时间窗口天数（默认取 config）")
    parser.add_argument("--outdir", default=os.path.join(HERE, "out"), help="输出目录")
    parser.add_argument("--to", default=None, help="临时覆盖收件人，逗号分隔")
    parser.add_argument("--no-llm", action="store_true", help="本次不调用大模型，保留英文原文")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    days = args.days or int(cfg.get("report", {}).get("window_days", 7))
    recipients = [x.strip() for x in args.to.split(",") if x.strip()] if args.to else None
    use_llm = not args.no_llm

    if args.command == "test":
        return cmd_test(cfg)
    if args.command == "llm":
        return cmd_llm_test(cfg)
    if args.command == "preview":
        return cmd_preview(cfg, days, args.outdir, use_llm=use_llm)
    if args.command == "send":
        return cmd_send(cfg, days, args.outdir, recipients=recipients, use_llm=use_llm)
    return cmd_run(cfg, days, args.outdir, use_llm=use_llm)


if __name__ == "__main__":
    sys.exit(main())
