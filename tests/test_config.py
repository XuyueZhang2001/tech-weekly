"""配置层测试：本机私有配置的合并、BOM 容忍、环境变量优先级。

不联网。重点覆盖：
  1. config.local.json 深合到 config.json（字典逐层、列表整体替换）
  2. 本机文件缺失 / 损坏时，主配置不受影响
  3. 记事本 / PowerShell 存出来的 UTF-8 BOM 不会让程序崩掉
  4. 收件人优先级：--to > 环境变量 MAIL_TO > config

用法：python tests/test_config.py
"""
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import main as app  # noqa: E402

RESULTS = []


def check(name, passed, detail=""):
    RESULTS.append((name, passed))
    print(("  PASS  " if passed else "  FAIL  ") + name + (("  <- " + detail) if detail and not passed else ""))


def write(path, data, encoding="utf-8"):
    with open(path, "w", encoding=encoding) as fh:
        json.dump(data, fh, ensure_ascii=False)


BASE = {
    "smtp": {"host": "smtp.163.com", "port": 465, "user": "", "password": "", "recipients": []},
    "llm": {"model": "deepseek-flash", "api_key": ""},
    "report": {"window_days": 7},
    "domains": [{"id": "ai", "name": "AI", "max_items": 6, "sources": [{"type": "rss", "name": "A", "url": "https://a"}]}],
}


def test_overlay(tmp):
    print("[1] 本机配置合并")
    cfg_path = os.path.join(tmp, "config.json")
    local_path = os.path.join(tmp, "config.local.json")
    write(cfg_path, BASE)

    cfg = app.load_config(cfg_path)
    check("没有本机文件时保持原样", cfg["smtp"]["user"] == "")

    write(local_path, {"smtp": {"user": "me@163.com", "password": "auth"}})
    cfg = app.load_config(cfg_path)
    check("合并后取到本机值", cfg["smtp"]["user"] == "me@163.com" and cfg["smtp"]["password"] == "auth")
    check("未覆盖的字段保持原值", cfg["smtp"]["host"] == "smtp.163.com" and cfg["smtp"]["port"] == 465)
    check("其它段落不受影响", cfg["llm"]["model"] == "deepseek-flash")

    write(local_path, {"domains": [{"id": "x", "name": "X", "max_items": 3, "sources": []}]})
    cfg = app.load_config(cfg_path)
    check("列表整体替换而不是拼接", [d["id"] for d in cfg["domains"]] == ["x"])


def test_broken_local(tmp):
    print("[2] 本机文件损坏时不拖垮主流程")
    cfg_path = os.path.join(tmp, "config.json")
    local_path = os.path.join(tmp, "config.local.json")
    write(cfg_path, BASE)
    with open(local_path, "w", encoding="utf-8") as fh:
        fh.write("{不是合法 JSON")
    cfg = app.load_config(cfg_path)
    check("坏文件被忽略，主配置仍可用", cfg["smtp"]["host"] == "smtp.163.com")


def test_bom(tmp):
    print("[3] BOM 容忍")
    cfg_path = os.path.join(tmp, "config-bom.json")
    local_path = os.path.join(tmp, "config.local.json")
    if os.path.exists(local_path):
        os.remove(local_path)
    write(cfg_path, BASE, encoding="utf-8-sig")
    check("主配置带 BOM 也能读", app.load_config(cfg_path)["smtp"]["host"] == "smtp.163.com")
    write(local_path, {"smtp": {"user": "bom@test"}}, encoding="utf-8-sig")
    check("本机配置带 BOM 也能读", app.load_config(cfg_path)["smtp"]["user"] == "bom@test")


def test_recipient_priority():
    print("[4] 收件人优先级")
    cfg = {"smtp": {"host": "h", "port": 465, "recipients": ["config@test"]}}
    old = os.environ.pop("MAIL_TO", None)
    try:
        check("没有环境变量时用配置里的",
              app.resolve_mail(cfg)["recipients"] == ["config@test"])
        os.environ["MAIL_TO"] = "env@test, env2@test"
        check("环境变量盖过配置",
              app.resolve_mail(cfg)["recipients"] == ["env@test", "env2@test"])
        check("--to 盖过环境变量",
              app.resolve_mail(cfg, ["cli@test"])["recipients"] == ["cli@test"])
    finally:
        os.environ.pop("MAIL_TO", None)
        if old is not None:
            os.environ["MAIL_TO"] = old


def main_test():
    base = os.path.join(ROOT, "out")
    os.makedirs(base, exist_ok=True)
    tmp = os.path.join(base, "tw-config-test-%d" % os.getpid())
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    try:
        test_overlay(tmp)
        test_broken_local(tmp)
        test_bom(tmp)
        test_recipient_priority()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    passed = sum(1 for _, ok in RESULTS if ok)
    total = len(RESULTS)
    print("\n配置层测试：%d/%d 通过" % (passed, total))
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main_test())
