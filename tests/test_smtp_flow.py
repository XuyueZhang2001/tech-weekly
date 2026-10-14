"""端到端验证 send_mail：用标准库自己实现的极小 SMTP 假服务器收信，
检查 MIME 结构、主题编码、正文、收件人与抄送。不接触任何真实邮箱。
"""
import base64
import email
import os
import socketserver
import sys
import threading
import time
from email.header import decode_header, make_header

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import main as app  # noqa: E402

RECEIVED = []
AUTH_SEEN = []


def decode(value):
    return str(make_header(decode_header(value or "")))


class FakeSMTPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class FakeSMTPHandler(socketserver.StreamRequestHandler):
    def reply(self, line):
        self.wfile.write((line + "\r\n").encode("utf-8"))
        self.wfile.flush()

    def handle(self):
        self.reply("220 fake.local ESMTP ready")
        mail_from = ""
        rcpt_tos = []
        while True:
            raw = self.rfile.readline()
            if not raw:
                return
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            cmd = line.split(" ", 1)[0].upper()

            if cmd == "EHLO":
                self.reply("250-fake.local greets you")
                self.reply("250 AUTH LOGIN PLAIN")
            elif cmd == "HELO":
                self.reply("250 fake.local")
            elif cmd == "AUTH":
                chunk = line[5:].strip()
                AUTH_SEEN.append(chunk.split(" ")[0].upper() or "UNKNOWN")
                if chunk.upper().startswith("LOGIN") and len(chunk.split()) > 1:
                    self.reply("334 UGFzc3dvcmQ6")            # 已带用户名，再要密码
                    self.rfile.readline()
                else:
                    self.reply("334 VXNlcm5hbWU6")            # 要用户名
                    self.rfile.readline()
                    self.reply("334 UGFzc3dvcmQ6")            # 要密码
                    self.rfile.readline()
                self.reply("235 2.7.0 Authentication successful")
            elif cmd == "MAIL":
                mail_from = line.split(":", 1)[1].strip().strip("<>")
                self.reply("250 OK")
            elif cmd == "RCPT":
                rcpt_tos.append(line.split(":", 1)[1].strip().strip("<>"))
                self.reply("250 OK")
            elif cmd == "DATA":
                self.reply("354 End data with <CR><LF>.<CR><LF>")
                lines = []
                while True:
                    dl = self.rfile.readline()
                    if not dl:
                        break
                    s = dl.decode("utf-8", "replace")
                    if s.rstrip("\r\n") == ".":
                        break
                    if s.startswith(".."):                     # 反转义
                        s = s[1:]
                    lines.append(s)
                RECEIVED.append({
                    "mail_from": mail_from,
                    "rcpt_tos": list(rcpt_tos),
                    "content": "".join(lines).encode("utf-8"),
                })
                rcpt_tos = []
                self.reply("250 Message accepted for delivery")
            elif cmd == "RSET":
                mail_from, rcpt_tos = "", []
                self.reply("250 OK")
            elif cmd == "NOOP":
                self.reply("250 OK")
            elif cmd == "QUIT":
                self.reply("221 Bye")
                return
            else:
                self.reply("250 OK")


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    cfg = app.load_config(os.path.join(os.path.dirname(here), "config.json"))
    report = app.build_report(cfg, 7)
    total = sum(len(s["articles"]) for s in report["sections"])
    text_body = app.render_text(report, cfg)
    html_body = app.render_html(report, cfg)
    subject = app.build_subject(report, cfg)
    print("入选条目 = %d" % total)
    print("主题     = %s" % subject)
    assert total > 0, "没有入选条目，无法测试发信"

    server = FakeSMTPServer(("127.0.0.1", 2525), FakeSMTPHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    time.sleep(0.3)
    try:
        smtp_cfg = {
            "host": "127.0.0.1", "port": 2525, "ssl": False, "starttls": False,
            "user": "someone@example.com", "password": "dummy-auth-code",
            "sender": "someone@example.com", "from_name": "科技周报",
            "recipients": ["reader-a@example.com"], "cc": ["reader-b@example.com"],
        }
        app.send_mail(smtp_cfg, subject, text_body, html_body)
    finally:
        server.shutdown()
        server.server_close()

    assert len(RECEIVED) == 1, "服务器收到 %d 封邮件，期望 1 封" % len(RECEIVED)
    got = RECEIVED[0]
    msg = email.message_from_bytes(got["content"])

    parts = {}
    for part in msg.walk():
        if part.get_content_type() in ("text/plain", "text/html"):
            parts[part.get_content_type()] = part.get_payload(decode=True).decode(
                part.get_content_charset() or "utf-8")

    plain = parts.get("text/plain", "")
    htmlp = parts.get("text/html", "")
    first = report["sections"][0]["articles"][0]

    checks = [
        ("SMTP 登录被调用", bool(AUTH_SEEN), ",".join(AUTH_SEEN)),
        ("发件人信封", got["mail_from"] == "someone@example.com", got["mail_from"]),
        ("收件人含抄送", set(got["rcpt_tos"]) == {"reader-a@example.com", "reader-b@example.com"},
         ",".join(got["rcpt_tos"])),
        ("Subject 可解码", decode(msg["Subject"]) == subject, decode(msg["Subject"])),
        ("From 中文名可解码", "科技周报" in decode(msg["From"]), decode(msg["From"])),
        ("To 头正确", "reader-a@example.com" in (msg["To"] or ""), msg["To"]),
        ("Cc 头正确", "reader-b@example.com" in (msg["Cc"] or ""), msg["Cc"]),
        ("Date / Message-ID", bool(msg["Date"]) and bool(msg["Message-ID"]), msg["Message-ID"]),
        ("两种正文都存在", set(parts) == {"text/plain", "text/html"}, ",".join(sorted(parts))),
        ("纯文本正文可用", "科技周报" in plain and len(plain) > 500, "len=%d" % len(plain)),
        ("HTML 正文可用", "</html>" in htmlp and len(htmlp) > 2000, "len=%d" % len(htmlp)),
        ("中文无乱码", "\ufffd" not in plain and "\ufffd" not in htmlp, "no U+FFFD"),
        ("真实条目链接入正文", (first.url in htmlp) or (first.url.replace("&", "&amp;") in htmlp),
         first.url[:64]),
        ("正文长度合理", len(htmlp) < 900_000, "html=%d bytes" % len(htmlp)),
    ]

    print()
    ok = True
    for name, passed, detail in checks:
        print("%s %-24s %s" % ("PASS" if passed else "FAIL", name, detail))
        ok = ok and passed
    print()
    print("SMTP 端到端：%s" % ("全部通过" if ok else "存在失败项"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
