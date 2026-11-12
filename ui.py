#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tech-weekly 的本地配置界面。

`python ui.py` 之后浏览器打开 http://127.0.0.1:8765 就能改领域、信源、收件人、
模型参数，跑体检和预览，改完一键提交推送。

设计约束（和主项目保持一致）：
1. 只用标准库。http.server 起服务，vanilla JS 做前端，不引入任何框架。
2. config.json 是唯一真实来源。界面只是它的编辑器，不建数据库、不存副本。
3. 密钥不进 git。SMTP 密码和 API Key 只读环境变量，界面里只显示"已配置/未配置"。
4. 只监听 127.0.0.1，并且所有写操作都要带上启动时随机生成的 token——
   防止你浏览器里别的页面顺手 POST 过来。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")
LOCAL_PATH = os.path.join(HERE, "config.local.json")
UI_PATH = os.path.join(HERE, "ui.html")
OUT_DIR = os.path.join(HERE, "out")
MAX_BODY = 2 * 1024 * 1024          # 配置文件不可能超过 2MB，超了就是有问题

# 每种信源类型：界面上的名称、必填字段、新建时的默认值
SOURCE_TYPES = {
    "rss": {"label": "RSS / Atom", "required": ["url"],
            "defaults": {"url": "", "weight": 10}},
    "arxiv": {"label": "arXiv 论文", "required": [],
              "defaults": {"categories": ["cs.AI"], "queries": [], "keywords": [], "weight": 13}},
    "hackernews": {"label": "Hacker News", "required": [],
                   "defaults": {"queries": ["LLM"], "keywords": [], "hn_keywords": [],
                                "min_keyword_hits": 1, "weight": 9}},
    "huggingface_papers": {"label": "HF Daily Papers", "required": [],
                           "defaults": {"limit": 12, "weight": 16}},
    "pubmed": {"label": "PubMed", "required": [],
               "defaults": {"queries": [], "days": 30, "retmax": 12, "weight": 12}},
    "bing_news": {"label": "Bing 新闻", "required": [],
                  "defaults": {"queries": [], "title_keywords": [], "min_title_hits": 1,
                               "weight": 11}},
    "web_search": {"label": "网页搜索", "required": [],
                   "defaults": {"provider": "brave", "api_key": "", "queries": [],
                                "limit": 8, "days": 7, "weight": 10}},
}

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
SECRET_ENVS = {
    "LLM_API_KEY": "大模型 API Key",
    "SMTP_USER": "SMTP 账号",
    "SMTP_PASSWORD": "SMTP 授权码",
    "MAIL_TO": "收件人（环境变量方式）",
}


# --------------------------------------------------------------------------- #
# 配置读写与校验
# --------------------------------------------------------------------------- #
def load_config() -> dict:
    # utf-8-sig：记事本 / PowerShell 保存的 JSON 会带 BOM
    with open(CONFIG_PATH, encoding="utf-8-sig") as fh:
        return json.load(fh)


def save_config(cfg: dict) -> None:
    """原子写，保持 2 空格缩进和中文原样。"""
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, CONFIG_PATH)


# --------------------------------------------------------------------------- #
# 本机私有配置（config.local.json，不进 git）
# --------------------------------------------------------------------------- #
# 界面要能保存每个人的邮箱和 Key，但不能写进 config.json——那要推仓库，是公开的。
# config.local.json 已在 .gitignore 里，main.py 会把它深合到 config.json 之上；
# 环境变量优先级更高，所以 GitHub Actions 里的 Secrets 仍然会盖过本机文件。
SECRET_FIELDS = {
    "smtp_user": ("smtp", "user"),          # SMTP 登录账号
    "smtp_password": ("smtp", "password"),  # 授权码，不是登录密码
    "sender": ("smtp", "sender"),           # 发件人地址，留空则用 SMTP 账号
    "llm_api_key": ("llm", "api_key"),      # 大模型 Key
}


def load_local() -> dict:
    try:
        with open(LOCAL_PATH, encoding="utf-8-sig") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 - 没有就是空的，坏文件也不能拦住界面
        return {}


def save_local(data: dict) -> None:
    tmp = LOCAL_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, LOCAL_PATH)


def local_flags() -> dict:
    """只回"有没有"，绝不把值发回浏览器。"""
    local = load_local()
    out = {}
    for field, (section, key) in SECRET_FIELDS.items():
        out[field] = bool((local.get(section) or {}).get(key))
    return out


def update_local(payload: dict) -> dict:
    """按字段更新本机配置：空字符串表示删除该字段。"""
    local = load_local()
    for field, (section, key) in SECRET_FIELDS.items():
        if field not in payload:
            continue
        box = local.setdefault(section, {})
        value = str(payload.get(field) or "").strip()
        if value:
            box[key] = value
        else:
            box.pop(key, None)
        if not box:
            local.pop(section, None)
    if local:
        save_local(local)
    elif os.path.exists(LOCAL_PATH):
        os.remove(LOCAL_PATH)
    return local_flags()


def _is_int(value, lo, hi) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and lo <= value <= hi


def validate(cfg: dict) -> list:
    """返回错误信息列表，空列表表示可以保存。"""
    errors = []
    if not isinstance(cfg, dict):
        return ["配置必须是一个 JSON 对象"]

    rep = cfg.get("report") or {}
    if not str(rep.get("title") or "").strip():
        errors.append("report.title 不能为空")
    if not _is_int(rep.get("window_days") or 7, 1, 90):
        errors.append("report.window_days 必须是 1-90 的整数")
    if not _is_int(rep.get("max_per_domain") or 6, 1, 30):
        errors.append("report.max_per_domain 必须是 1-30 的整数")
    if not _is_int(rep.get("max_total") or 24, 1, 120):
        errors.append("report.max_total 必须是 1-120 的整数")

    smtp = cfg.get("smtp") or {}
    port = smtp.get("port", 465)
    if not _is_int(port, 1, 65535):
        errors.append("smtp.port 必须是 1-65535 的整数")
    for key in ("recipients", "cc"):
        for addr in smtp.get(key) or []:
            if not EMAIL_RE.match(str(addr)):
                errors.append("smtp.%s 里的 %r 不是合法邮箱" % (key, addr))

    llm = cfg.get("llm") or {}
    if not str(llm.get("model") or "").strip():
        errors.append("llm.model 不能为空")
    base = str(llm.get("base_url") or "")
    if base and not base.startswith("http"):
        errors.append("llm.base_url 必须以 http 开头")
    if llm.get("thinking") not in ("", None, "enabled", "disabled"):
        errors.append("llm.thinking 只能是 enabled / disabled / 留空")

    cur = cfg.get("curation") or {}
    if cur:
        th = cur.get("score_threshold", 6)
        if not isinstance(th, (int, float)) or not 0 <= float(th) <= 10:
            errors.append("curation.score_threshold 必须在 0-10 之间")
        if not _is_int(int(cur.get("pool_per_domain") or 12), 1, 60):
            errors.append("curation.pool_per_domain 必须是 1-60 的整数")
        if float(cur.get("budget_cny") or 0) < 0:
            errors.append("curation.budget_cny 不能为负")

    domains = cfg.get("domains")
    if not isinstance(domains, list) or not domains:
        errors.append("至少要有一个领域")
        return errors

    seen_ids = set()
    for i, dom in enumerate(domains):
        label = "第 %d 个领域" % (i + 1)
        dom_id = str(dom.get("id") or "").strip()
        if not re.match(r"^[a-z0-9_-]+$", dom_id):
            errors.append("%s 的 id %r 只能用小写字母、数字、下划线、短横线" % (label, dom_id))
        elif dom_id in seen_ids:
            errors.append("领域 id %r 重复了" % dom_id)
        seen_ids.add(dom_id)
        if not str(dom.get("name") or "").strip():
            errors.append("%s（%s）缺少 name" % (label, dom_id or "?"))
        if not _is_int(dom.get("max_items") or 6, 1, 30):
            errors.append("%s 的 max_items 必须是 1-30 的整数" % label)

        sources = dom.get("sources")
        if not isinstance(sources, list) or not sources:
            errors.append("%s 至少要有一个信源" % label)
            continue
        src_names = set()
        for j, src in enumerate(sources):
            where = "%s 的第 %d 个信源" % (label, j + 1)
            stype = src.get("type")
            if stype not in SOURCE_TYPES:
                errors.append("%s 的类型 %r 不支持" % (where, stype))
                continue
            name = str(src.get("name") or "").strip()
            if not name:
                errors.append("%s 缺少 name" % where)
            elif name in src_names:
                # 健康度状态按「领域|名字」记，同一领域内重名会互相顶掉
                errors.append("%s 的名字 %r 在本领域内重复了" % (where, name))
            else:
                src_names.add(name)
            if not _is_int(src.get("weight") or 10, 1, 30):
                errors.append("%s（%s）的 weight 必须是 1-30 的整数" % (where, name or "?"))
            for field in SOURCE_TYPES[stype]["required"]:
                if not str(src.get(field) or "").strip():
                    errors.append("%s（%s）缺少 %s" % (where, name or "?", field))
            url = str(src.get("url") or "")
            if url and not url.startswith("http"):
                errors.append("%s（%s）的 url 必须以 http 开头" % (where, name or "?"))
    return errors


# --------------------------------------------------------------------------- #
# 环境与 git 状态
# --------------------------------------------------------------------------- #
def secrets_status(cfg: dict) -> list:
    """只报告"配没配"，绝不把值传回浏览器。"""
    out = []
    smtp = cfg.get("smtp") or {}
    for env_name, label in SECRET_ENVS.items():
        from_env = bool(os.environ.get(env_name))
        out.append({"name": env_name, "label": label, "configured": from_env, "source": "环境变量" if from_env else ""})
    # config 里直接填的（不推荐，但项目支持）
    pairs = [("smtp.user", smtp.get("user")), ("smtp.password", smtp.get("password")),
             ("llm.api_key", (cfg.get("llm") or {}).get("api_key"))]
    for name, value in pairs:
        if value:
            out.append({"name": name, "label": name, "configured": True, "source": "config.json"})
    return out


def _git(*args, timeout=20) -> tuple:
    try:
        proc = subprocess.run(["git"] + list(args), cwd=HERE, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=timeout)
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except FileNotFoundError:
        return 127, "没找到 git 命令"
    except subprocess.TimeoutExpired:
        return 124, "git 命令超时"


def git_status() -> dict:
    code, branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    if code != 0:
        return {"available": False, "error": branch.strip()[:200]}
    _, porcelain = _git("status", "--porcelain")
    _, ahead = _git("rev-list", "--count", "@{u}..HEAD")
    return {
        "available": True,
        "branch": branch.strip(),
        "dirty": bool(porcelain.strip()),
        "changes": [line for line in porcelain.splitlines() if line.strip()][:40],
        "ahead": int(ahead.strip() or 0) if ahead.strip().isdigit() else 0,
    }


# --------------------------------------------------------------------------- #
# GitHub Secrets
# --------------------------------------------------------------------------- #
# GitHub 要求用 libsodium 的 crypto_box_seal 加密后上传。项目零依赖，
# 所以用 nacl_lite 里手写的实现（已用 RFC 7748 向量和 PyNaCl 交叉验证）。
GH_API = "https://api.github.com"


def github_repo() -> dict:
    """从 git remote 里认出 owner/repo，供界面预填。"""
    code, out = _git("remote", "get-url", "origin")
    if code != 0:
        return {"remote": "", "repo": ""}
    remote = out.strip().splitlines()[0] if out.strip() else ""
    match = re.search(r"github\.com[:/]+([^/]+)/([^/]+?)(?:\.git)?$", remote)
    return {"remote": remote, "repo": "%s/%s" % (match.group(1), match.group(2)) if match else ""}


def _gh_request(method: str, path: str, token: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        GH_API + path, data=data, method=method,
        headers={"Accept": "application/vnd.github+json",
                 "Authorization": "Bearer %s" % token,
                 "User-Agent": "tech-weekly-ui/1.0",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode("utf-8", "replace")
    return json.loads(raw) if raw.strip() else {}


def push_secrets(token: str, repo: str, values: dict) -> dict:
    """把 values 写进仓库的 Actions Secrets。返回每个名字的结果。"""
    import nacl_lite

    key_info = _gh_request("GET", "/repos/%s/actions/secrets/public-key" % repo, token)
    public_key, key_id = key_info["key"], key_info["key_id"]
    written, failed = [], {}
    for name, value in values.items():
        if not value:
            continue
        try:
            sealed = nacl_lite.seal_base64(public_key, str(value))
            _gh_request("PUT", "/repos/%s/actions/secrets/%s" % (repo, name), token,
                        {"encrypted_value": sealed, "key_id": key_id})
            written.append(name)
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:200]
            except Exception:  # noqa: BLE001
                pass
            failed[name] = "HTTP %s %s" % (exc.code, detail)
        except Exception as exc:  # noqa: BLE001
            failed[name] = "%s: %s" % (type(exc).__name__, str(exc)[:160])
    return {"written": written, "failed": failed}


# --------------------------------------------------------------------------- #
# 后台任务（体检 / 预览）
# --------------------------------------------------------------------------- #
JOBS = {}
JOB_LOCK = threading.Lock()


def _run_job(job_id: str, args: list) -> None:
    outdir = os.path.join(OUT_DIR, "ui-" + job_id)
    os.makedirs(outdir, exist_ok=True)
    log_path = os.path.join(outdir, "run.log")
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    cmd = [sys.executable, os.path.join(HERE, "main.py")] + args + ["--outdir", outdir]
    started = time.time()
    try:
        # 用文件接收输出而不是管道：管道在部分受限环境里拿不到句柄，
        # 而且日志落盘后还能给用户下载。
        with open(log_path, "w", encoding="utf-8", errors="replace") as fh:
            proc = subprocess.run(cmd, cwd=HERE, stdout=fh, stderr=subprocess.STDOUT,
                                  env=env, timeout=900)
        code = proc.returncode
    except subprocess.TimeoutExpired:
        code = 124
    except Exception as exc:  # noqa: BLE001
        code = 125
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write("\n启动失败：%s\n" % exc)

    html_name = ""
    try:
        candidates = sorted(f for f in os.listdir(outdir) if f.endswith(".html"))
        if candidates:
            html_name = candidates[-1]
    except OSError:
        pass

    with JOB_LOCK:
        job = JOBS.get(job_id) or {}
        job.update({"status": "done", "code": code, "html": html_name,
                    "elapsed": round(time.time() - started, 1)})
        JOBS[job_id] = job
    try:
        with open(log_path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        text = ""
    with JOB_LOCK:
        JOBS[job_id]["log"] = text[-20000:]


def start_job(args: list) -> str:
    job_id = uuid.uuid4().hex[:8]
    with JOB_LOCK:
        JOBS[job_id] = {"status": "running", "log": "", "html": "", "code": None,
                        "started": time.time(), "elapsed": 0}
    threading.Thread(target=_run_job, args=(job_id, args), daemon=True).start()
    return job_id


def job_snapshot(job_id: str) -> dict:
    with JOB_LOCK:
        job = dict(JOBS.get(job_id) or {})
    if not job:
        return {"status": "missing"}
    if job.get("status") == "running":
        try:
            outdir = os.path.join(OUT_DIR, "ui-" + job_id)
            with open(os.path.join(outdir, "run.log"), encoding="utf-8", errors="replace") as fh:
                job["log"] = fh.read()[-20000:]
        except OSError:
            pass
        job["elapsed"] = round(time.time() - job.get("started", time.time()), 1)
    return job


# --------------------------------------------------------------------------- #
# 单个信源体检
# --------------------------------------------------------------------------- #
def test_source(domain_id: str, index: int) -> dict:
    """抓一个信源看返回多少条。在进程内跑，失败信息原样带回。"""
    import main as app

    cfg = load_config()
    dom = next((d for d in cfg.get("domains", []) if d.get("id") == domain_id), None)
    if not dom:
        return {"ok": False, "error": "找不到领域 %s" % domain_id}
    sources = dom.get("sources") or []
    if not (0 <= index < len(sources)):
        return {"ok": False, "error": "信源序号越界"}
    src = sources[index]
    fn = app.FETCHERS.get(src.get("type", "rss"))
    if fn is None:
        return {"ok": False, "error": "未知信源类型 %s" % src.get("type")}

    box = {}

    def run():
        try:
            box["arts"] = fn(src, domain_id, cfg)
        except Exception as exc:  # noqa: BLE001
            box["error"] = "%s: %s" % (type(exc).__name__, str(exc)[:300])

    th = threading.Thread(target=run, daemon=True)
    th.start()
    th.join(timeout=45)
    if th.is_alive():
        return {"ok": False, "error": "超时（45 秒）"}
    if "error" in box:
        return {"ok": False, "error": box["error"]}
    arts = box.get("arts") or []
    return {
        "ok": True,
        "count": len(arts),
        "samples": [{"title": a.title[:120], "published": a.published.isoformat()[:10]} for a in arts[:5]],
    }


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
class Handler(BaseHTTPRequestHandler):
    server_version = "tech-weekly-ui/1.0"
    token = ""

    # ---- 工具 ---------------------------------------------------------- #
    def _json(self, payload: dict, code: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path: str, ctype: str) -> None:
        try:
            with open(path, "rb") as fh:
                body = fh.read()
        except OSError:
            self._json({"ok": False, "error": "文件不存在"}, 404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:  # noqa: BLE001
            return {}

    def _authed(self) -> bool:
        return self.headers.get("X-UI-Token", "") == self.token

    def log_message(self, fmt, *args):        # 别把每个静态请求都打到终端
        if "/api/" in (fmt % args):
            sys.stderr.write("[ui] " + (fmt % args) + "\n")

    # ---- 路由 ---------------------------------------------------------- #
    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html"):
            try:
                with open(UI_PATH, encoding="utf-8") as fh:
                    page = fh.read().replace("__UI_TOKEN__", self.token)
            except OSError:
                return self._json({"ok": False, "error": "找不到 ui.html"}, 500)
            body = page.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/api/config":
            try:
                cfg = load_config()
            except Exception as exc:  # noqa: BLE001
                return self._json({"ok": False, "error": "读取配置失败：%s" % exc}, 500)
            return self._json({
                "ok": True,
                "config": cfg,
                "secrets": secrets_status(cfg),
                "local": {
                    "exists": os.path.exists(LOCAL_PATH),
                    "flags": local_flags(),
                    "path": LOCAL_PATH,
                },
                "git": git_status(),
                "source_types": {k: v["label"] for k, v in SOURCE_TYPES.items()},
                "type_defaults": {k: v["defaults"] for k, v in SOURCE_TYPES.items()},
            })

        if path == "/api/job":
            job_id = (parse_qs(parsed.query).get("id") or [""])[0]
            return self._json({"ok": True, "job": job_snapshot(job_id)})

        if path == "/api/git":
            return self._json({"ok": True, "git": git_status()})

        if path == "/api/github":
            info = github_repo()
            local = load_local()
            smtp = local.get("smtp") or {}
            llm = local.get("llm") or {}
            ready = []
            if llm.get("api_key"):
                ready.append("LLM_API_KEY")
            if smtp.get("user"):
                ready.append("SMTP_USER")
            if smtp.get("password"):
                ready.append("SMTP_PASSWORD")
            if smtp.get("sender") or smtp.get("user"):
                ready.append("MAIL_FROM")
            return self._json({"ok": True, "repo": info["repo"], "remote": info["remote"],
                               "ready": ready})

        if path.startswith("/out/"):
            name = os.path.basename(path[len("/out/"):])
            if not name.endswith(".html"):
                return self._json({"ok": False, "error": "只允许看 html"}, 403)
            if not os.path.isdir(OUT_DIR):
                return self._json({"ok": False, "error": "还没有输出目录"}, 404)
            # 先找 out/ 根目录，再找 UI 任务产生的 out/ui-xxxx/ 子目录
            for entry in [None] + list(os.listdir(OUT_DIR)):
                base = OUT_DIR if entry is None else os.path.join(OUT_DIR, entry)
                if not os.path.isdir(base):
                    continue
                candidate = os.path.join(base, name)
                if os.path.exists(candidate):
                    return self._file(candidate, "text/html; charset=utf-8")
            return self._json({"ok": False, "error": "找不到 %s" % name}, 404)

        return self._json({"ok": False, "error": "not found"}, 404)

    def do_POST(self):  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        if not self._authed():
            return self._json({"ok": False, "error": "token 不对"}, 403)

        if path == "/api/config":
            body = self._body()
            cfg = body.get("config")
            errors = validate(cfg)
            if errors:
                return self._json({"ok": False, "errors": errors}, 400)
            try:
                save_config(cfg)
            except Exception as exc:  # noqa: BLE001
                return self._json({"ok": False, "error": "写入失败：%s" % exc}, 500)
            return self._json({"ok": True, "saved": True, "git": git_status()})

        if path == "/api/secrets":
            body = self._body()
            known = [f for f in SECRET_FIELDS if f in body]
            if not known:
                return self._json({"ok": False, "error": "没有可保存的字段"}, 400)
            try:
                flags = update_local(body)
            except Exception as exc:  # noqa: BLE001
                return self._json({"ok": False, "error": "写入失败：%s" % exc}, 500)
            return self._json({"ok": True, "flags": flags,
                               "exists": os.path.exists(LOCAL_PATH)})

        if path == "/api/secrets/clear":
            try:
                if os.path.exists(LOCAL_PATH):
                    os.remove(LOCAL_PATH)
            except Exception as exc:  # noqa: BLE001
                return self._json({"ok": False, "error": "删除失败：%s" % exc}, 500)
            return self._json({"ok": True, "flags": local_flags(), "exists": False})

        if path == "/api/github/secrets":
            body = self._body()
            token = str(body.get("token") or "").strip()
            repo = str(body.get("repo") or "").strip() or github_repo()["repo"]
            mail_to = str(body.get("mail_to") or "").strip()
            if not token:
                return self._json({"ok": False, "error": "缺少 GitHub Token"}, 400)
            if not re.match(r"^[\w.-]+/[\w.-]+$", repo):
                return self._json({"ok": False, "error": "仓库格式应为 owner/repo"}, 400)

            # 值从本机凭据里取，不经过浏览器
            local = load_local()
            smtp = local.get("smtp") or {}
            llm = local.get("llm") or {}
            values = {}
            if llm.get("api_key"):
                values["LLM_API_KEY"] = llm["api_key"]
            if smtp.get("user"):
                values["SMTP_USER"] = smtp["user"]
            if smtp.get("password"):
                values["SMTP_PASSWORD"] = smtp["password"]
            if smtp.get("sender") or smtp.get("user"):
                values["MAIL_FROM"] = smtp.get("sender") or smtp.get("user")
            if mail_to:
                values["MAIL_TO"] = mail_to
            if not values:
                return self._json({"ok": False,
                                   "error": "本机凭据是空的：先在「收件与投递 / AI」页保存账号和 Key"}, 400)
            try:
                result = push_secrets(token, repo, values)
            except urllib.error.HTTPError as exc:
                detail = ""
                try:
                    detail = exc.read().decode("utf-8", "replace")[:300]
                except Exception:  # noqa: BLE001
                    pass
                if exc.code in (401, 403):
                    hint = "（Token 无效，或没有 repo 权限）"
                elif exc.code == 404:
                    hint = "（仓库不存在，或 Token 没有该仓库的权限）"
                else:
                    hint = ""
                return self._json({"ok": False,
                                   "error": "GitHub 返回 HTTP %s %s %s" % (exc.code, detail, hint)}, 400)
            except Exception as exc:  # noqa: BLE001
                return self._json({"ok": False, "error": "%s: %s" % (type(exc).__name__, str(exc)[:200])}, 500)

            ok = not result["failed"]
            return self._json({"ok": ok, "repo": repo,
                               "written": result["written"], "failed": result["failed"]},
                              200 if ok else 400)

        if path == "/api/source/test":
            body = self._body()
            try:
                result = test_source(str(body.get("domain") or ""), int(body.get("index") or 0))
            except Exception as exc:  # noqa: BLE001
                result = {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}
            return self._json(result)

        if path == "/api/run":
            body = self._body()
            mode = str(body.get("mode") or "preview")
            if mode not in ("preview", "test", "send"):
                return self._json({"ok": False, "error": "只允许 preview / test / send"}, 400)
            if mode == "send" and body.get("confirm") != "send":
                # 发信不可撤回，必须让前端显式确认一次
                return self._json({"ok": False, "error": "发信需要二次确认"}, 400)
            args = [mode]
            if body.get("days"):
                args += ["--days", str(int(body["days"]))]
            if body.get("use_llm") is False and mode == "preview":
                args.append("--no-llm")
            # 临时收件人：只拼进本次命令行，不写 config.json（仓库是公开的）
            to = str(body.get("to") or "").strip()
            if to and mode == "send":
                if not EMAIL_RE.match(to.split(",")[0].strip()):
                    return self._json({"ok": False, "error": "临时收件人不是合法邮箱"}, 400)
                args += ["--to", to]
            return self._json({"ok": True, "job": start_job(args)})

        if path == "/api/git/commit":
            body = self._body()
            message = str(body.get("message") or "").strip() or "chore: 更新周报配置"
            push = bool(body.get("push"))
            code, out = _git("add", "config.json")
            if code != 0:
                return self._json({"ok": False, "error": out.strip()[:500]})
            code, out2 = _git("commit", "-m", message)
            no_change = ("nothing to commit" in out2) or ("no changes added to commit" in out2)
            if code != 0 and not no_change:
                return self._json({"ok": False, "error": (out + out2).strip()[:800]})
            if no_change:
                return self._json({"ok": True, "committed": False, "pushed": False,
                                   "output": "config.json 没有变化，不需要提交"})
            result = {"ok": True, "committed": code == 0, "pushed": False, "output": out2.strip()[-800:]}
            if push:
                pcode, pout = _git("push", timeout=120)
                result["pushed"] = pcode == 0
                result["output"] += "\n" + pout.strip()[-800:]
                if pcode != 0:
                    result["ok"] = False
                    result["error"] = "提交成功，但推送失败（多半是没配凭据）"
            return self._json(result)

        return self._json({"ok": False, "error": "not found"}, 404)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="tech-weekly 本地配置界面")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args(argv)

    if not os.path.exists(UI_PATH):
        print("缺少 ui.html，界面无法启动", file=sys.stderr)
        return 1

    Handler.token = uuid.uuid4().hex
    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = "http://127.0.0.1:%d/" % args.port
    print("tech-weekly 配置界面：%s" % url)
    print("改动会直接写 config.json；点「提交并推送」才会进 git。Ctrl+C 退出。")
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
