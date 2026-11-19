#!/usr/bin/env python3
"""生成公众号正文 HTML 和本地预览页面。

输入 Markdown 或 HTML 定稿，输出：

    article.wechat.html   供推送使用的公众号正文 HTML（本地图片保留为绝对路径）
    preview.html          带封面和手机阅读样式的完整预览页（图片内联）
    meta.json             标题、摘要、字数、图片清单

示例：

    python3 build_article.py \
        --content article.md \
        --title "文章标题" \
        --digest "文章摘要" \
        --cover assets/cover.png \
        --out-dir dist
"""

import argparse
import base64
import html
import json
import mimetypes
import os
import re
import sys
import urllib.parse

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from wechat_draft import markdown_to_html, plain_length  # noqa: E402

IMG_SRC_RE = re.compile(
    r'(<img\b[^>]*?\bsrc\s*=\s*)(["\'])([^"\']+)\2', re.IGNORECASE
)
INLINE_LIMIT = 8 * 1024 * 1024


def fail(message, code=1):
    print(f"[失败] {message}", file=sys.stderr)
    sys.exit(code)


def info(message):
    print(f"[信息] {message}")


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


def is_remote(src):
    return src.startswith(("http://", "https://", "//", "data:"))


def resolve_path(src, base_dir):
    if src.startswith("file://"):
        src = urllib.parse.unquote(src[len("file://"):])
    return src if os.path.isabs(src) else os.path.normpath(
        os.path.join(base_dir, src)
    )


def to_absolute_images(content, base_dir):
    """把正文中的本地图片改成绝对路径，避免输出目录变化后找不到。"""

    def replace(match):
        src = match.group(3).strip()
        if is_remote(src) or src.startswith("#"):
            return match.group(0)
        path = resolve_path(src, base_dir)
        return f"{match.group(1)}{match.group(2)}{path}{match.group(2)}"

    return IMG_SRC_RE.sub(replace, content)


def collect_images(content):
    result = []
    for match in IMG_SRC_RE.finditer(content):
        src = match.group(3).strip()
        if src not in result:
            result.append(src)
    return result


def inline_images(content, label=""):
    """把本地图片转成 data URI，便于预览页脱离原目录打开。"""

    def replace(match):
        src = match.group(3).strip()
        if is_remote(src):
            return match.group(0)
        path = resolve_path(src, os.path.dirname(os.path.abspath(label)) or ".")
        if not os.path.isfile(path):
            info(f"预览中保留原路径（文件不存在）：{src}")
            return match.group(0)
        size = os.path.getsize(path)
        if size > INLINE_LIMIT:
            info(f"预览中保留原路径（图片超过 8MB）：{src}")
            return match.group(0)
        mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
        try:
            with open(path, "rb") as handle:
                encoded = base64.b64encode(handle.read()).decode("ascii")
        except OSError:
            return match.group(0)
        return (
            f"{match.group(1)}{match.group(2)}"
            f"data:{mime};base64,{encoded}{match.group(2)}"
        )

    return IMG_SRC_RE.sub(replace, content)


def cover_data_uri(path):
    if not path:
        return ""
    if is_remote(path):
        return path
    if not os.path.isfile(path):
        fail(f"封面图片不存在：{path}")
    size = os.path.getsize(path)
    if size > INLINE_LIMIT:
        return "file://" + urllib.parse.quote(os.path.abspath(path))
    mime = mimetypes.guess_type(path)[0] or "image/png"
    with open(path, "rb") as handle:
        encoded = base64.b64encode(handle.read()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def build_preview(title, author, digest, cover, body, word_count):
    cover_block = (
        f'<img class="cover" src="{cover}" alt="封面" />' if cover else ""
    )
    digest_block = (
        f'<div class="digest">{html.escape(digest)}</div>' if digest else ""
    )
    author_block = (
        f'<span>{html.escape(author)}</span>' if author else ""
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{html.escape(title)} - 公众号预览</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; background: #e9edf2; color: #222;
    font-family: "WenQuanYi Zen Hei", "Noto Sans CJK SC",
      "Source Han Sans SC", "PingFang SC", "Microsoft YaHei", sans-serif;
  }}
  .phone {{ max-width: 430px; margin: 0 auto; background: #fff; min-height: 100vh; }}
  .cover {{ width: 100%; display: block; }}
  .content {{ padding: 26px 22px 72px; }}
  h1 {{ margin: 0; font-size: 24px; line-height: 1.42; font-weight: 700; }}
  .meta {{
    margin: 14px 0 22px; font-size: 13px; color: #9a9a9a;
    display: flex; gap: 14px; align-items: center;
  }}
  .digest {{
    margin: 0 0 26px; padding: 14px 16px; background: #f7f7f7;
    border-left: 3px solid #07c160; color: #666;
    font-size: 14px; line-height: 1.75;
  }}
  .article {{ font-size: 16px; line-height: 1.8; }}
  .article img {{ max-width: 100%; height: auto; }}
  .footer {{
    margin-top: 40px; padding-top: 18px; border-top: 1px solid #eee;
    font-size: 12px; color: #b0b0b0; text-align: center;
  }}
</style>
</head>
<body>
<div class="phone">
  {cover_block}
  <div class="content">
    <h1>{html.escape(title)}</h1>
    <div class="meta">{author_block}<span>全文约 {word_count} 字</span></div>
    {digest_block}
    <div class="article">
{body}
    </div>
    <div class="footer">公众号预览 · 发布前请核对信息与图片授权</div>
  </div>
</div>
</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser(description="生成公众号正文 HTML 与预览页")
    parser.add_argument("--content", required=True, help="Markdown 或 HTML 定稿")
    parser.add_argument("--title", required=True, help="文章标题")
    parser.add_argument("--digest", default="", help="文章摘要")
    parser.add_argument("--author", default="", help="作者名")
    parser.add_argument("--cover", default="", help="封面图片路径")
    parser.add_argument("--out-dir", default="dist", help="输出目录")
    args = parser.parse_args()

    base_dir = os.path.dirname(os.path.abspath(args.content))
    body, converted = load_content(args.content)
    body = to_absolute_images(body, base_dir)
    images = collect_images(body)
    word_count = plain_length(body)

    os.makedirs(args.out_dir, exist_ok=True)
    wechat_path = os.path.join(args.out_dir, "article.wechat.html")
    preview_path = os.path.join(args.out_dir, "preview.html")
    meta_path = os.path.join(args.out_dir, "meta.json")

    with open(wechat_path, "w", encoding="utf-8") as handle:
        handle.write(body)

    preview_body = inline_images(body, os.path.join(args.out_dir, "preview.html"))
    cover = cover_data_uri(args.cover)
    preview = build_preview(
        args.title, args.author, args.digest, cover, preview_body, word_count
    )
    with open(preview_path, "w", encoding="utf-8") as handle:
        handle.write(preview)

    meta = {
        "title": args.title,
        "digest": args.digest,
        "author": args.author,
        "cover": os.path.abspath(args.cover) if args.cover else "",
        "word_count": word_count,
        "images": images,
        "markdown_converted": converted,
        "files": {
            "wechat_html": wechat_path,
            "preview": preview_path,
        },
    }
    with open(meta_path, "w", encoding="utf-8") as handle:
        json.dump(meta, handle, ensure_ascii=False, indent=2)

    info(f"正文约 {word_count} 字，本地图片 {len(images)} 张")
    info(f"公众号正文：{wechat_path}")
    info(f"预览页面：{preview_path}")
    info("请先打开预览页面核对标题、正文、图片和摘要。")
    print(json.dumps(meta, ensure_ascii=False))


if __name__ == "__main__":
    main()
