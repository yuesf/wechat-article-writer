#!/usr/bin/env python3
"""把公众号文章写入公众号草稿箱。

依赖 Python 3 标准库。凭据只从环境变量读取，不写入文件、不打印：

    WECHAT_MP_APPID      公众号 AppID
    WECHAT_MP_APPSECRET  公众号 AppSecret

用法示例：

    python3 wechat_draft.py \
        --title "文章标题" \
        --content article.html \
        --cover cover.jpg \
        --digest "文章摘要" \
        --author "作者名"

    # 只做本地检查，不调用微信接口
    python3 wechat_draft.py --title "标题" --content article.md --cover cover.jpg --dry-run
"""

import argparse
import html
import json
import mimetypes
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

API = "https://api.weixin.qq.com/cgi-bin"
TOKEN_CACHE = {}

ERROR_HINTS = {
    40001: "AppSecret 不正确或 access_token 已失效",
    40013: "AppID 无效",
    40125: "AppSecret 无效",
    40164: "调用接口的 IP 不在公众号后台的 IP 白名单中",
    45009: "接口调用次数超过当日限额",
    48001: "该公众号没有此接口权限，通常是未认证公众号",
    40007: "media_id 无效",
    40009: "图片大小超过限制",
    40005: "文件类型不支持",
    40003: "openid 无效",
    44002: "请求体为空",
}


def fail(message, code=1):
    print(f"[失败] {message}", file=sys.stderr)
    sys.exit(code)


def info(message):
    print(f"[信息] {message}")


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def http_request(url, data=None, headers=None, method=None, timeout=60):
    request = urllib.request.Request(
        url, data=data, headers=headers or {}, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        # 微信接口出错时仍会返回 JSON，交给解析逻辑处理。
        return exc.read()
    except urllib.error.URLError as exc:
        fail(f"网络请求失败：{exc.reason}")
    except TimeoutError:
        fail("网络请求超时，请检查网络后重试")


def parse_json(raw):
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        fail("微信接口返回了无法解析的内容，请稍后重试")


def api_error(response):
    code = response.get("errcode")
    if not code:
        return
    message = response.get("errmsg", "")
    hint = ERROR_HINTS.get(code, "")
    detail = f"{code}: {message}"
    if hint:
        detail += f"（{hint}）"
    fail(detail)


def multipart_post(url, file_path, field_name="media", extra_fields=None):
    """用 multipart/form-data 上传一个文件。"""
    filename = os.path.basename(file_path)
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    try:
        with open(file_path, "rb") as handle:
            payload = handle.read()
    except OSError as exc:
        fail(f"无法读取文件 {file_path}：{exc}")

    boundary = "----WechatDraft" + uuid.uuid4().hex
    chunks = []
    for key, value in (extra_fields or {}).items():
        chunks.append(f"--{boundary}\r\n".encode())
        chunks.append(
            f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode()
        )
        chunks.append(str(value).encode("utf-8"))
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}\r\n".encode())
    chunks.append(
        (
            f'Content-Disposition: form-data; name="{field_name}"; '
            f'filename="{filename}"\r\n'
        ).encode("utf-8")
    )
    chunks.append(f"Content-Type: {content_type}\r\n\r\n".encode())
    chunks.append(payload)
    chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())

    body = b"".join(chunks)
    headers = {
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Content-Length": str(len(body)),
    }
    return parse_json(http_request(url, data=body, headers=headers))


def get_token(appid, secret, force=False):
    cached = TOKEN_CACHE.get("token")
    if cached and not force:
        return cached
    query = urllib.parse.urlencode(
        {
            "grant_type": "client_credential",
            "appid": appid,
            "secret": secret,
        }
    )
    response = parse_json(http_request(f"{API}/token?{query}"))
    token = response.get("access_token")
    if not token:
        api_error(response)
        fail("未能获取 access_token，请检查 AppID 和 AppSecret")
    TOKEN_CACHE["token"] = token
    return token


# ---------------------------------------------------------------------------
# Markdown -> 公众号 HTML
# ---------------------------------------------------------------------------

P_STYLE = (
    "margin:0 0 24px;font-size:16px;line-height:1.8;"
    "color:#333;letter-spacing:0.5px;"
)
H2_STYLE = (
    "margin:36px 0 16px;padding-left:12px;border-left:4px solid #07c160;"
    "font-size:20px;line-height:1.5;color:#222;font-weight:bold;"
)
H3_STYLE = (
    "margin:28px 0 12px;font-size:17px;line-height:1.6;"
    "color:#222;font-weight:bold;"
)
QUOTE_STYLE = (
    "margin:0 0 24px;padding:16px 18px;background:#f7f7f7;"
    "border-left:3px solid #d9d9d9;font-size:15px;line-height:1.8;color:#666;"
)
LI_STYLE = "margin:0 0 14px;font-size:16px;line-height:1.9;color:#333;"
UL_STYLE = "margin:16px 0 26px;padding-left:22px;"
OL_STYLE = "margin:16px 0 26px;padding-left:24px;"
TABLE_WRAP_STYLE = "margin:16px 0 26px;overflow-x:auto;"
TABLE_STYLE = (
    "width:100%;border-collapse:collapse;font-size:14px;"
    "line-height:1.7;color:#333;"
)
TH_STYLE = (
    "border:1px solid #e5e5e5;padding:9px 10px;background:#f5f7f6;"
    "font-weight:bold;text-align:left;font-size:14px;color:#222;"
)
TD_STYLE = "border:1px solid #e5e5e5;padding:9px 10px;text-align:left;"
IMG_STYLE = (
    "max-width:100%;height:auto;display:block;margin:24px auto;"
    "border-radius:4px;"
)
CODE_STYLE = (
    "padding:2px 6px;background:#f2f2f2;border-radius:3px;"
    "font-size:14px;color:#c7254e;font-family:Consolas,Monaco,monospace;"
)
PRE_STYLE = (
    "margin:0 0 24px;padding:16px;background:#f7f7f7;border-radius:6px;"
    "font-size:13px;line-height:1.7;color:#333;overflow-x:auto;"
    "white-space:pre-wrap;word-break:break-word;"
)


def inline(text):
    text = html.escape(text, quote=False)
    text = re.sub(
        r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)",
        lambda m: (
            f'<img src="{m.group(2)}" alt="{m.group(1)}" style="{IMG_STYLE}" />'
        ),
        text,
    )
    text = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        r'<a href="\2" style="color:#576b95;text-decoration:none;">\1</a>',
        text,
    )
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", text)
    text = re.sub(
        r"`([^`]+)`", rf'<code style="{CODE_STYLE}">\1</code>', text
    )
    return text


def split_table_row(line):
    text = line.strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|"):
        text = text[:-1]
    return [cell.strip() for cell in text.split("|")]


def is_table_separator(line):
    cells = split_table_row(line)
    if not cells:
        return False
    return all(re.match(r"^:?-{1,}:?$", cell) for cell in cells)


def render_table(header, rows):
    head = "".join(f'<th style="{TH_STYLE}">{inline(cell)}</th>' for cell in header)
    body = []
    for row in rows:
        cells = row + [""] * (len(header) - len(row))
        body.append(
            "<tr>"
            + "".join(f'<td style="{TD_STYLE}">{inline(c)}</td>' for c in cells)
            + "</tr>"
        )
    return (
        f'<section style="{TABLE_WRAP_STYLE}">'
        f'<table style="{TABLE_STYLE}">'
        f"<thead><tr>{head}</tr></thead>"
        f"<tbody>{''.join(body)}</tbody>"
        "</table></section>"
    )


def markdown_to_html(markdown_text):
    lines = markdown_text.replace("\r\n", "\n").split("\n")
    out = []
    paragraph = []
    list_kind = None
    in_code = False
    code_lines = []

    def flush_paragraph():
        if paragraph:
            out.append(f'<p style="{P_STYLE}">{inline("".join(paragraph))}</p>')
            paragraph.clear()

    def close_list():
        nonlocal list_kind
        if list_kind == "ul":
            out.append("</ul>")
        elif list_kind == "ol":
            out.append("</ol>")
        list_kind = None

    index = 0
    total = len(lines)
    while index < total:
        raw_line = lines[index]
        line = raw_line.rstrip()
        stripped = line.strip()

        if stripped.startswith("```"):
            if in_code:
                out.append(
                    f'<pre style="{PRE_STYLE}"><code>{html.escape("".join(code_lines))}</code></pre>'
                )
                code_lines = []
                in_code = False
            else:
                flush_paragraph()
                close_list()
                in_code = True
            index += 1
            continue

        if in_code:
            code_lines.append(raw_line + "\n")
            index += 1
            continue

        if not stripped:
            flush_paragraph()
            close_list()
            index += 1
            continue

        heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading:
            flush_paragraph()
            close_list()
            level = min(len(heading.group(1)), 3)
            style = H2_STYLE if level <= 2 else H3_STYLE
            tag = "h2" if level <= 2 else "h3"
            out.append(f'<{tag} style="{style}">{inline(heading.group(2))}</{tag}>')
            index += 1
            continue

        if re.match(r"^([-*_])\1{2,}$", stripped):
            flush_paragraph()
            close_list()
            out.append(
                '<hr style="border:none;border-top:1px solid #e5e5e5;margin:32px 0;" />'
            )
            index += 1
            continue

        if (
            stripped.startswith("|")
            and index + 1 < total
            and is_table_separator(lines[index + 1])
        ):
            flush_paragraph()
            close_list()
            header = split_table_row(stripped)
            rows = []
            cursor = index + 2
            while cursor < total and lines[cursor].strip().startswith("|"):
                rows.append(split_table_row(lines[cursor]))
                cursor += 1
            out.append(render_table(header, rows))
            index = cursor
            continue

        quote = re.match(r"^>\s?(.*)$", stripped)
        if quote:
            flush_paragraph()
            close_list()
            out.append(
                f'<blockquote style="{QUOTE_STYLE}">{inline(quote.group(1))}</blockquote>'
            )
            index += 1
            continue

        bullet = re.match(r"^[-*+]\s+(.*)$", stripped)
        if bullet:
            flush_paragraph()
            if list_kind != "ul":
                close_list()
                out.append(f'<ul style="{UL_STYLE}">')
                list_kind = "ul"
            out.append(f'<li style="{LI_STYLE}">{inline(bullet.group(1))}</li>')
            index += 1
            continue

        ordered = re.match(r"^\d+[.)]\s+(.*)$", stripped)
        if ordered:
            flush_paragraph()
            if list_kind != "ol":
                close_list()
                out.append(f'<ol style="{OL_STYLE}">')
                list_kind = "ol"
            out.append(f'<li style="{LI_STYLE}">{inline(ordered.group(1))}</li>')
            index += 1
            continue

        if list_kind:
            close_list()
        paragraph.append(stripped)
        index += 1

    flush_paragraph()
    close_list()
    if in_code:
        out.append(
            f'<pre style="{PRE_STYLE}"><code>{html.escape("".join(code_lines))}</code></pre>'
        )
    return "\n".join(out)


# ---------------------------------------------------------------------------
# 图片上传与正文处理
# ---------------------------------------------------------------------------

IMG_SRC_RE = re.compile(
    r'(<img\b[^>]*?\bsrc\s*=\s*)(["\'])([^"\']+)\2', re.IGNORECASE
)


def upload_inline_image(token, path):
    url = f"{API}/media/uploadimg?access_token={urllib.parse.quote(token)}"
    response = multipart_post(url, path, field_name="media")
    image_url = response.get("url")
    if not image_url:
        api_error(response)
        fail("正文图片上传失败，未返回图片地址")
    return image_url


def upload_cover(token, path):
    url = (
        f"{API}/material/add_material?access_token={urllib.parse.quote(token)}"
        "&type=image"
    )
    response = multipart_post(url, path, field_name="media")
    media_id = response.get("media_id")
    if not media_id:
        api_error(response)
        fail("封面上传失败，未返回 media_id")
    return media_id


def resolve_local_image(src, base_dir):
    if src.startswith("file://"):
        src = urllib.parse.unquote(src[len("file://"):])
    if re.match(r"^(https?:)?//", src) or src.startswith("data:"):
        return None
    if src.startswith("#"):
        return None
    path = src if os.path.isabs(src) else os.path.join(base_dir, src)
    path = os.path.normpath(path)
    if not os.path.isfile(path):
        fail(f"正文图片不存在：{src}")
    return path


def rewrite_local_images(content, base_dir, token, upload=True):
    """把正文里的本地图片换成微信可访问的地址。"""
    count = 0

    def replace(match):
        nonlocal count
        src = match.group(3).strip()
        path = resolve_local_image(src, base_dir)
        if not path:
            return match.group(0)
        count += 1
        if not upload:
            return match.group(0)
        new_url = upload_inline_image(token, path)
        return f"{match.group(1)}{match.group(2)}{new_url}{match.group(2)}"

    return IMG_SRC_RE.sub(replace, content), count


# ---------------------------------------------------------------------------
# 主体
# ---------------------------------------------------------------------------

def load_content(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = handle.read()
    except OSError as exc:
        fail(f"无法读取正文文件 {path}：{exc}")

    extension = os.path.splitext(path)[1].lower()
    if extension in {".md", ".markdown"}:
        return markdown_to_html(raw), True
    if extension in {".html", ".htm"}:
        return raw, False
    fail("正文文件只支持 .md、.markdown、.html、.htm")


def plain_length(content_html):
    text = re.sub(r"<[^>]+>", "", content_html)
    text = html.unescape(text)
    return len(re.sub(r"\s+", "", text))


def main():
    parser = argparse.ArgumentParser(
        description="把公众号文章写入公众号草稿箱（不群发）。"
    )
    parser.add_argument("--title", required=True, help="文章标题")
    parser.add_argument(
        "--content", required=True, help="正文文件：.md 或 .html"
    )
    parser.add_argument("--cover", required=True, help="本地封面图片路径")
    parser.add_argument("--digest", default="", help="摘要，建议 120 字以内")
    parser.add_argument("--author", default="", help="作者名")
    parser.add_argument("--source-url", default="", help="原文链接，可留空")
    parser.add_argument(
        "--comment",
        action="store_true",
        help="允许读者评论（默认关闭）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只做本地检查，不调用微信接口",
    )
    args = parser.parse_args()

    if not args.title.strip():
        fail("标题不能为空")
    if len(args.title) > 64:
        fail("标题过长：微信草稿标题建议不超过 64 字")
    if not os.path.isfile(args.cover):
        fail(f"封面图片不存在：{args.cover}")

    cover_size = os.path.getsize(args.cover)
    if cover_size > 10 * 1024 * 1024:
        fail("封面图片超过 10MB，请压缩后再上传")

    content_html, converted = load_content(args.content)
    base_dir = os.path.dirname(os.path.abspath(args.content))
    length = plain_length(content_html)
    if length == 0:
        fail("正文为空")
    if length > 20000:
        fail(f"正文约 {length} 字，超过草稿接口的 20000 字限制，请精简")

    has_credentials = bool(
        os.environ.get("WECHAT_MP_APPID") and os.environ.get("WECHAT_MP_APPSECRET")
    )

    info(f"标题：{args.title}")
    info(f"正文：约 {length} 字" + ("（已从 Markdown 转换）" if converted else ""))
    info(f"摘要：{len(args.digest)} 字")
    info(f"封面：{args.cover}（{cover_size / 1024:.0f} KB）")
    info("凭据：" + ("已从环境变量读取" if has_credentials else "未设置"))

    if args.dry_run:
        _, image_count = rewrite_local_images(
            content_html, base_dir, token=None, upload=False
        )
        info(f"正文本地图片：{image_count} 张（正式运行时会自动上传）")
        info("dry-run 完成，未调用微信接口。")
        return

    if not has_credentials:
        fail(
            "缺少凭据。请先设置环境变量 WECHAT_MP_APPID 和 "
            "WECHAT_MP_APPSECRET，再重新运行。"
        )

    appid = os.environ["WECHAT_MP_APPID"]
    secret = os.environ["WECHAT_MP_APPSECRET"]

    token = get_token(appid, secret)
    info("已获取 access_token")

    content_html, image_count = rewrite_local_images(
        content_html, base_dir, token
    )
    if image_count:
        info(f"已上传 {image_count} 张正文图片")

    thumb_media_id = upload_cover(token, args.cover)
    info("封面上传完成")

    article = {
        "article_type": "news",
        "title": args.title,
        "author": args.author,
        "digest": args.digest,
        "content": content_html,
        "content_source_url": args.source_url,
        "thumb_media_id": thumb_media_id,
        "need_open_comment": 1 if args.comment else 0,
        "only_fans_can_comment": 0,
    }
    payload = json.dumps(
        {"articles": [article]}, ensure_ascii=False
    ).encode("utf-8")

    url = f"{API}/draft/add?access_token={urllib.parse.quote(token)}"
    response = parse_json(
        http_request(
            url,
            data=payload,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
    )

    if response.get("errcode") in {40001, 42001}:
        token = get_token(appid, secret, force=True)
        url = f"{API}/draft/add?access_token={urllib.parse.quote(token)}"
        response = parse_json(
            http_request(
                url,
                data=payload,
                headers={"Content-Type": "application/json; charset=utf-8"},
                method="POST",
            )
        )

    media_id = response.get("media_id")
    if not media_id:
        api_error(response)
        fail("草稿创建失败，未返回 media_id")

    print(json.dumps({"media_id": media_id}, ensure_ascii=False))
    info("已写入公众号草稿箱。请到公众号后台「草稿箱」检查并决定是否群发。")


if __name__ == "__main__":
    main()
