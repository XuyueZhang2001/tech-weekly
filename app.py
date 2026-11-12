#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tech-weekly 桌面启动器：把配置界面包成一个独立窗口。

它做的事和 `python ui.py` 完全一样，只是多了两件：

1. 用 Chromium 的 `--app=` 模式开窗口——没有地址栏、没有标签页、没有前进后退，
   任务栏里是一个独立窗口，看不出是网页。
2. 用一个单独的 `--user-data-dir`，和日常浏览的 Edge/Chrome 隔离：
   不会有你的收藏夹、扩展、登录态，也不会因为浏览器已在运行而复用进程
   （复用的话我们监听不到窗口关闭，服务就会一直挂着）。

零依赖：只用了标准库和系统自带的 Edge/Chrome。没有浏览器的话退回默认浏览器打开。

用法：
    python app.py                    # 起服务并开窗口
    python app.py --no-window        # 只起服务，自己用浏览器开
    python app.py --port 9000        # 换端口
    python app.py --install-shortcut # 在桌面建一个快捷方式（双击即开，无黑框）
"""

from __future__ import annotations

import argparse
import base64
import os
import re
import socket
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import ui  # noqa: E402

BROWSERS = [
    ("Edge", [
        os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                     r"Microsoft\Edge\Application\msedge.exe"),
        os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                     r"Microsoft\Edge\Application\msedge.exe"),
    ]),
    ("Chrome", [
        os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                     r"Google\Chrome\Application\chrome.exe"),
        os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                     r"Google\Chrome\Application\chrome.exe"),
    ]),
]


def find_browser() -> tuple:
    for name, paths in BROWSERS:
        for path in paths:
            if os.path.exists(path):
                return name, path
    return "", ""


def port_busy(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.4)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def start_server(port: int):
    from http.server import ThreadingHTTPServer
    ui.Handler.token = ui.uuid.uuid4().hex
    httpd = ThreadingHTTPServer(("127.0.0.1", port), ui.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def open_window(exe: str, url: str, profile_dir: str) -> subprocess.Popen:
    os.makedirs(profile_dir, exist_ok=True)
    args = [
        exe,
        "--app=" + url,
        "--user-data-dir=" + profile_dir,
        "--window-size=1320,900",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=Translate,MediaRouter",
        "--disable-sync",
        # 窗口里没有地址栏，这些限制都是为了让界面像个应用而不是网页
        "--bookmark-bar-state=hidden",
    ]
    return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def install_shortcut(dest_dir: str) -> int:
    """在桌面建 .lnk，指向 pythonw app.py —— 双击不会弹黑色控制台窗口。"""
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.exists(pythonw):
        print("找不到 pythonw.exe，改用 python.exe（双击会留一个黑窗口）")
        pythonw = sys.executable

    os.makedirs(dest_dir, exist_ok=True)
    # 文件名刻意用 ASCII：WScript.Shell 保存 .lnk 时会把路径按 ANSI 代码页转一遍，
    # 中文文件名会变成 "??"（而 ? 是非法文件名字符），结果是 FileNotFoundException。
    lnk = os.path.join(dest_dir, "tech-weekly.lnk")

    def q(text: str) -> str:
        return "'" + str(text).replace("'", "''") + "'"

    script = "\n".join([
        "$shell = New-Object -ComObject WScript.Shell",
        "$sc = $shell.CreateShortcut(%s)" % q(lnk),
        "$sc.TargetPath = %s" % q(pythonw),
        "$sc.Arguments = %s" % q('"%s"' % os.path.join(HERE, "app.py")),
        "$sc.WorkingDirectory = %s" % q(HERE),
        "$sc.Description = 'tech-weekly config UI'",
        "$sc.Save()",
    ])
    try:
        # 必须用 -EncodedCommand：直接传 -Command 时，中文路径会按控制台代码页
        # 转换，Windows PowerShell 5.1 会把它变成 "??" 导致找不到目录。
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        proc = subprocess.run(["powershell", "-NoProfile", "-EncodedCommand", encoded],
                              capture_output=True, text=True, timeout=30,
                              encoding="utf-8", errors="replace")
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "")
            # PowerShell 会把错误包成 CLIXML，直接读原文太吵，挑出 Error 段
            if "CLIXML" in detail:
                parts = re.findall(r'<S S="Error">(.*?)</S>', detail, re.S)
                detail = " ".join(parts) or detail
                detail = detail.replace("_x000D_", "").replace("_x000A_", "\n").replace("&gt;", ">").replace("&lt;", "<")
            print("创建快捷方式失败：%s" % detail.strip()[:600])
            return 1
    except Exception as exc:  # noqa: BLE001
        print("创建快捷方式失败：%s" % exc)
        return 1
    if not os.path.exists(lnk):
        print("命令执行了，但没生成 %s" % lnk)
        return 1
    print("已创建快捷方式：%s" % lnk)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="tech-weekly 桌面启动器")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-window", action="store_true", help="只起服务，不开窗口")
    parser.add_argument("--install-shortcut", action="store_true", help="在桌面创建快捷方式")
    parser.add_argument("--shortcut-dir", default="", help="快捷方式目录（默认桌面）")
    args = parser.parse_args(argv)

    if args.install_shortcut:
        dest = args.shortcut_dir or os.path.join(os.path.expanduser("~"), "Desktop")
        return install_shortcut(dest)

    url = "http://127.0.0.1:%d/" % args.port

    # 已经开着一个了：直接开窗口，不重复起服务
    reused = port_busy(args.port)
    httpd = None
    if not reused:
        try:
            httpd = start_server(args.port)
        except OSError as exc:
            print("端口 %d 起不来：%s" % (args.port, exc))
            return 1
        time.sleep(0.3)

    name, exe = find_browser()
    if args.no_window or not exe:
        if not exe:
            print("没找到 Edge / Chrome，用默认浏览器打开标签页。")
        print("配置界面：%s" % url)
        if args.no_window:
            print("Ctrl+C 退出。")
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                pass
            return 0
        import webbrowser
        webbrowser.open(url)
        return 0

    profile = os.path.join(HERE, "cache", "app-window")
    print("正在打开 %s 窗口……" % name)
    proc = open_window(exe, url, profile)

    # Chromium 有两种行为：自己就是窗口进程（等它退出即可），或者把窗口交给
    # 另一个进程后自己立刻退出。还有一些环境（没有交互式桌面的会话、被安全
    # 软件拦截）根本开不出窗口。等 3 秒再判断，避免误判后把服务关掉。
    time.sleep(3)
    window_alive = proc.poll() is None
    if not window_alive:
        print("窗口进程已退出——如果没看到窗口，可能是当前会话没有交互式桌面。")
        print("改用默认浏览器打开：%s" % url)
        import webbrowser
        webbrowser.open(url)

    try:
        if window_alive:
            proc.wait()
        else:
            print("服务仍在运行：%s（Ctrl+C 退出）" % url)
            while True:
                time.sleep(1)
    except KeyboardInterrupt:
        if window_alive:
            proc.terminate()
    finally:
        if httpd is not None:
            httpd.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
