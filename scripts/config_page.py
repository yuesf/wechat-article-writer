#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
公众号草稿箱凭据配置页（一次性）
-----------------------------------------------------------
用法:
  python3 config_page.py [--port N]  # token 由脚本随机生成并打印

行为:
  - GET  /<token>/           出表单向导（AppID / AppSecret）
  - POST /<token>/           校验并持久化凭据，成功后页面上显示结果，90 秒后自动退出
  - 其余路径一律 404
说明:
  凭据以 export 形式写入 CRED_FILE（chmod 600），不回显、不记录日志；
  校验阶段调用微信官方 token 与草稿接口（只调不写），失败时页面会给出错误码与处理建议。
  若 args 端口不合法或供外部调用，请保持监听在随机 token 后。
"""

import html
import json
import os
import re
import secrets
import sys
import time
import threading
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CRED_FILE = os.environ.get(
    "WECHAT_MP_CRED_FILE",
    os.path.join(os.path.expanduser("~"), ".config", "wechat-article-writer", "wechat_mp.env"),
)
LOG = os.environ.get("WECHAT_MP_CONFIG_LOG", "/tmp/wxcfg/access.log")
PORT = 0
TOKEN = ""

STYLE = """
<style>
*{box-sizing:border-box}
body{margin:0;min-height:100vh;background:#0b0f14;color:#e8edf3;
 font:16px/1.55 -apple-system,BlinkMacSystemFont,"PingFang SC","Noto Sans CJK SC",system-ui,sans-serif;
 padding:24px 18px calc(24px + env(safe-area-inset-bottom))}
.card{max-width:560px;margin:0 auto;background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.09);
 border-radius:18px;padding:22px 20px}
h1{margin:0 0 6px;font-size:21px;letter-spacing:.3px}
.sub{color:#8b9bad;font-size:13px;margin-bottom:22px}
h2.error-red{margin:14px 0 0;font-size:15px;color:#ff9d9d}
label{display:block;font-size:13px;color:#9fb0c2;margin:18px 0 7px}
input{width:100%;padding:13px 14px;border-radius:12px;border:1px solid rgba(255,255,255,.14);
 background:rgba(0,0,0,.35);color:#e8edf3;font-size:15px;font-family:ui-monospace,Menlo,monospace}
input:focus{outline:none;border-color:#2ea3ff;box-shadow:0 0 0 3px rgba(46,163,255,.15)}
button{width:100%;margin-top:24px;padding:14px;border:none;border-radius:12px;background:#2ea3ff;color:#04121d;
 font-size:16px;font-weight:700;letter-spacing:.5px}
button:active{transform:translateY(1px)}
.note{margin-top:18px;padding:12px 14px;border-radius:12px;background:rgba(46,163,255,.08);
 border:1px solid rgba(46,163,255,.2);font-size:12.5px;color:#9db8cf;line-height:1.7}
.note b{color:#e8edf3}
.ok{color:#24e08a}.bad{color:#ff6b6b}
.res{margin-top:16px;padding:14px;border-radius:12px;font-size:13.5px;line-height:1.8;
 background:rgba(0,0,0,.35);border:1px solid rgba(255,255,255,.08);
 font-family:ui-monospace,Menlo,monospace;white-space:pre-wrap;word-break:break-all}
</style>
"""

FORM = """<!doctype html><html lang=\"zh-CN\"><meta charset=\"utf-8\">
<meta name=\"viewport\" content=\"width=device-width,initial-scale=1,viewport-fit=cover\">
<title>公众号草稿配置</title>%s
<div class=\"card\">
<h1>公众号草稿箱配置</h1>
<div class=\"sub\">填一次即可，密钥只写进本机文件（600 权限），不回显。</div>
<form method=\"POST\">
<label>AppID</label>
<input name=\"appid\" placeholder=\"wx0f1a2b3c4d5e6f7g\" autocomplete=\"off\" autocapitalize=\"off\" spellcheck=\"false\">
<label>AppSecret</label>
<input name=\"secret\" type=\"password\" placeholder=\"32 位密钥\" autocomplete=\"off\" autocapitalize=\"off\" spellcheck=\"false\">
<button type=\"submit\">保存并验证连通</button>
</form>
<div class=\"note\">
提交后会立刻：① 写进本机凭据文件 ② 微信接口换 access_token ③ 读取一次草稿列表。<br>
若提示 IP 不在白名单，请在后台「设置与开发 → 基本配置 → IP 白名单」加入 <b>122.51.217.209</b>。
</div>
</div></html>"""

HINTS = {
    40013: "AppID 不正确，检查是否复制完整。",
    40125: "AppSecret 不正确，去后台重置后重新复制。",
    40164: "出口 IP 不在白名单：后台「设置与开发 → 基本配置 → IP 白名单」加入 122.51.217.209。",
    89503: "API 调用需管理员确认，请到公众平台查看待确认记录。",
    48001: "该公众号没有草稿接口权限（未认证订阅号不支持草稿接口）。",
}


def page(body, status=200):
    doc = ("<!doctype html><html lang=\"zh-CN\"><meta charset=\"utf-8\">"
           "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1,viewport-fit=cover\">"
           "<title>公众号草稿配置</title>" + STYLE + '<div class="card">' + body + "</div></html>")
    return status, doc.encode()


def obtain_token(appid, secret):
    q = urllib.parse.urlencode({"grant_type": "client_credential", "appid": appid, "secret": secret})
    with urllib.request.urlopen("https://api.weixin.qq.com/cgi-bin/token?" + q, timeout=15) as r:
        return json.load(r)


def verify_draft(token):
    req = urllib.request.Request(
        "https://api.weixin.qq.com/cgi-bin/draft/batchget?access_token=" + token,
        data=json.dumps({"offset": 0, "count": 1, "no_content": 1}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *a):
        try:
            os.makedirs(os.path.dirname(LOG), exist_ok=True)
            with open(LOG, "a") as f:
                f.write("%s %s %s\n" % (time.strftime("%F %T"), self.client_address[0], fmt % a))
        except Exception:
            pass

    def do_GET(self):
        if self.path.rstrip("/").endswith(TOKEN):
            self._ok(*page(FORM % STYLE))
        else:
            self._ok(*page("<h1>链接失效</h1><div class=\"sub\">链接无效或已过期。</div>", 404))

    def do_POST(self):
        if not self.path.rstrip("/").endswith(TOKEN):
            self._ok(*page("<h1>404</h1>", 404))
            return
        n = int(self.headers.get("Content-Length") or 0)
        data = urllib.parse.parse_qs(self.rfile.read(n).decode("utf-8", "replace"))
        appid = (data.get("appid", [""])[0] or "").strip()
        secret = (data.get("secret", [""])[0] or "").strip()
        if not re.fullmatch(r"wx[0-9a-zA-Z]{10,}", appid) or len(secret) < 20:
            self._ok(*page("<h2 class=\"error-red\">格式不对：AppID 应以 wx 开头且长度为 18 位，"
                           "AppSecret 通常为 32 位。</h2>"))
            return
        try:
            tk = obtain_token(appid, secret)
        except Exception as e:  # noqa
            self._ok(*page("<h1 class=\"bad\">连不上微信接口</h1><div class=\"res\">%s</div>" % html.escape(str(e))))
            return
        if "access_token" not in tk:
            code = tk.get("errcode")
            msg = HINTS.get(code, "") + (" 微信：" + str(tk.get("errmsg")) if tk.get("errmsg") else "")
            self._ok(*page("<h1 class=\"bad\">校验未通过</h1>"
                           "<div class=\"res\">errcode %s\n%s</div>" % (code, html.escape(msg))))
            return
        try:
            d = verify_draft(tk["access_token"])
            draft_info = ("草稿接口正常，当前草稿数：%s" % d["total_count"]) if "total_count" in d else \
                "草稿接口返回：%s %s" % (d.get("errcode"), d.get("errmsg"))
        except Exception as e:  # noqa
            draft_info = "草稿接口异常：%s" % e
        os.makedirs(os.path.dirname(CRED_FILE), exist_ok=True)
        with open(CRED_FILE, "w") as f:
            f.write("# 微信公众号凭据（仅本机运行环境使用，勿提交、勿外发）\n"
                    "export WECHAT_MP_APPID='%s'\n"
                    "export WECHAT_MP_APPSECRET='%s'\n" % (appid, secret))
        os.chmod(CRED_FILE, 0o600)
        lines = ["凭据已保存到本机（权限 600）", "access_token 获取成功", draft_info, "", "返回对话告我一声，我就收尾。"]
        self._ok(*page("<h1 class=\"ok\">配置完成</h1><div class=\"res\">%s</div>" % html.escape("\n".join(lines))))
        threading.Timer(90, lambda: os._exit(0)).start()

    def _ok(self, status, body):
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def main():
    global PORT, TOKEN
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 0  # 0 = 由系统分配
    TOKEN = sys.argv[2] if len(sys.argv) > 2 else secrets.token_urlsafe(9)
    os.makedirs("/tmp/wxcfg", exist_ok=True)
    with open("/tmp/wxcfg/token.txt", "w") as f:
        f.write(TOKEN)
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    PORT = srv.server_address[1]
    print("token=%s port=%d" % (TOKEN, PORT), flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
