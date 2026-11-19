#!/usr/bin/env python3
"""公众号配图素材工具：从官方页面取图、截取官方页面、生成本地配图。

只依赖 Python 3 标准库；截图与配图渲染需要本机安装 Chromium。

子命令：

    fetch     从官方页面提取并下载图片候选（og:image、正文图片）
    capture   用无头 Chromium 截取官方页面
    generate  生成封面、金句卡或要点卡

示例：

    python3 assets.py fetch --url "https://example.com/news" --out assets/official
    python3 assets.py capture --url "https://example.com/news" --out assets/page.png
    python3 assets.py generate --template cover \
        --title "文章标题" --subtitle "文章副标题" \
        --out assets/cover.png
"""

import argparse
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)
MAX_IMAGE_BYTES = 15 * 1024 * 1024
THEMES = {
    "green": {"accent": "#07c160", "deep": "#053f2b", "soft": "#e8f8ef"},
    "blue": {"accent": "#2f6bff", "deep": "#0a2357", "soft": "#eaf0ff"},
    "orange": {"accent": "#ff6b35", "deep": "#4a1d0b", "soft": "#fff0e9"},
    "dark": {"accent": "#f5c542", "deep": "#111827", "soft": "#2a2f3a"},
}


def fail(message, code=1):
    print(f"[失败] {message}", file=sys.stderr)
    sys.exit(code)


def info(message):
    print(f"[信息] {message}")


def http_get(url, referer=None, timeout=30):
    headers = {"User-Agent": USER_AGENT}
    if referer:
        headers["Referer"] = referer
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read(), response.headers.get_content_type()
    except urllib.error.HTTPError as exc:
        fail(f"请求失败（HTTP {exc.code}）：{url}")
    except urllib.error.URLError as exc:
        fail(f"网络请求失败：{exc.reason}")
    except TimeoutError:
        fail(f"请求超时：{url}")


# ---------------------------------------------------------------------------
# 从官方页面取图
# ---------------------------------------------------------------------------

META_RE = re.compile(
    r'<meta\b[^>]*?(?:property|name)\s*=\s*["\']([^"\']+)["\'][^>]*?'
    r'content\s*=\s*["\']([^"\']*)["\'][^>]*>',
    re.IGNORECASE,
)
META_RE_REVERSED = re.compile(
    r'<meta\b[^>]*?content\s*=\s*["\']([^"\']*)["\'][^>]*?'
    r'(?:property|name)\s*=\s*["\']([^"\']+)["\'][^>]*>',
    re.IGNORECASE,
)
IMG_RE = re.compile(r'<img\b[^>]*>', re.IGNORECASE)
SRC_RE = re.compile(
    r'\b(?:data-src|data-original|src)\s*=\s*["\']([^"\']+)["\']',
    re.IGNORECASE,
)
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)


def clean_text(value):
    value = re.sub(r"<[^>]+>", "", value or "")
    return html.unescape(re.sub(r"\s+", " ", value)).strip()


def extract_meta(page):
    meta = {}
    for pattern, order in ((META_RE, (1, 2)), (META_RE_REVERSED, (2, 1))):
        for match in pattern.finditer(page):
            key = match.group(order[0]).lower()
            value = match.group(order[1])
            meta.setdefault(key, value)
    return meta


def extract_image_candidates(page, page_url):
    candidates = []
    meta = extract_meta(page)

    for key in ("og:image", "og:image:url", "twitter:image", "twitter:image:src"):
        value = meta.get(key)
        if value:
            candidates.append(urllib.parse.urljoin(page_url, html.unescape(value)))

    for tag in IMG_RE.findall(page):
        src_match = SRC_RE.search(tag)
        if not src_match:
            continue
        src = html.unescape(src_match.group(1).strip())
        if not src or src.startswith("data:"):
            continue
        candidates.append(urllib.parse.urljoin(page_url, src))

    seen = set()
    result = []
    for url in candidates:
        if not url.startswith(("http://", "https://")):
            continue
        if url in seen:
            continue
        seen.add(url)
        result.append(url)
    return result


def extension_for(content_type, url):
    mapping = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/bmp": ".bmp",
        "image/svg+xml": ".svg",
    }
    if content_type in mapping:
        return mapping[content_type]
    suffix = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower()
    return suffix if suffix in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg"} else ".jpg"


def command_fetch(args):
    os.makedirs(args.out, exist_ok=True)
    info(f"读取官方页面：{args.url}")
    page_bytes, _ = http_get(args.url)
    try:
        page = page_bytes.decode("utf-8", errors="replace")
    except Exception:
        fail("无法解析页面内容")

    meta = extract_meta(page)
    title = clean_text(meta.get("og:title")) or clean_text(
        (TITLE_RE.search(page) or [None, ""])[1]
    )
    description = clean_text(
        meta.get("og:description") or meta.get("description") or ""
    )
    if not title:
        h1 = H1_RE.search(page)
        title = clean_text(h1.group(1)) if h1 else ""

    candidates = extract_image_candidates(page, args.url)
    if not candidates:
        info("页面中没有发现图片候选。")
        write_manifest(args.out, args.url, title, description, [])
        return

    images = []
    for url in candidates:
        if len(images) >= args.max:
            break
        try:
            data, content_type = http_get(url, referer=args.url)
        except SystemExit:
            info(f"跳过无法访问的图片：{url}")
            continue
        if not content_type.startswith("image/"):
            continue
        if len(data) > MAX_IMAGE_BYTES:
            info(f"跳过超过 15MB 的图片：{url}")
            continue
        if content_type == "image/svg+xml" and not args.allow_svg:
            info(f"跳过 SVG 图片（公众号不接受 SVG）：{url}")
            continue

        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
        filename = digest + extension_for(content_type, url)
        path = os.path.join(args.out, filename)
        with open(path, "wb") as handle:
            handle.write(data)
        images.append(
            {
                "url": url,
                "file": path,
                "content_type": content_type,
                "bytes": len(data),
                "origin_page": args.url,
            }
        )
        info(f"已保存：{path}（{len(data) / 1024:.0f} KB）")

    write_manifest(args.out, args.url, title, description, images)
    if not images:
        info("没有下载到可用图片，可改用截取官方页面或自行生成配图。")


def write_manifest(out_dir, page_url, title, description, images):
    manifest = {
        "official_page": page_url,
        "page_title": title,
        "page_description": description,
        "images": images,
    }
    path = os.path.join(out_dir, "sources.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
    print(json.dumps({"manifest": path, "count": len(images)}, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Chromium 截图
# ---------------------------------------------------------------------------

def find_chromium():
    candidates = [
        os.environ.get("CHROME_BIN"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        shutil.which("google-chrome"),
        shutil.which("google-chrome-stable"),
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    fail(
        "没有找到 Chromium。请安装 Chromium，或用 CHROME_BIN 指定可执行文件路径。"
    )


def screenshot(chromium, url, out_path, width, height, scale=1, timeout=60):
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    command = [
        chromium,
        "--headless=new",
        "--disable-gpu",
        "--no-sandbox",
        "--hide-scrollbars",
        "--disable-dev-shm-usage",
        f"--window-size={width},{height}",
        f"--force-device-scale-factor={scale}",
        "--virtual-time-budget=6000",
        f"--screenshot={os.path.abspath(out_path)}",
        url,
    ]
    result = subprocess.run(
        command, capture_output=True, text=True, timeout=timeout
    )
    if not os.path.isfile(out_path) or os.path.getsize(out_path) == 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        hint = detail[-1] if detail else "未知错误"
        fail(f"截图失败：{hint}")
    return out_path


def command_capture(args):
    chromium = find_chromium()
    info(f"截取官方页面：{args.url}")
    screenshot(
        chromium,
        args.url,
        args.out,
        args.width,
        args.height,
        scale=args.scale,
    )
    print(json.dumps({"file": args.out, "source": args.url}, ensure_ascii=False))
    info(f"已保存截图：{args.out}")


# ---------------------------------------------------------------------------
# 生成配图
# ---------------------------------------------------------------------------

BASE_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body { width: 100%; height: 100%; }
body {
  font-family: "WenQuanYi Zen Hei", "Noto Sans CJK SC",
    "Source Han Sans SC", "PingFang SC", "Microsoft YaHei", sans-serif;
  -webkit-font-smoothing: antialiased;
}
"""


def fit_size(text, base, minimum, longest):
    """按字数收缩字号，避免长标题溢出。"""
    length = max(len(text), 1)
    if length <= longest:
        return base
    return max(minimum, int(base * longest / length))


def render_cover(args, theme):
    title_size = fit_size(args.title, 64, 34, 14)
    subtitle_size = fit_size(args.subtitle, 26, 18, 28)
    return f"""<!doctype html>
<html><head><meta charset="utf-8" /><style>
{BASE_CSS}
body {{
  width: 900px; height: 383px;
  background: linear-gradient(135deg, {theme['deep']} 0%, {theme['deep']} 55%, {theme['accent']} 160%);
  color: #fff; position: relative; overflow: hidden;
}}
.glow {{
  position: absolute; width: 420px; height: 420px; border-radius: 50%;
  background: {theme['accent']}; opacity: .22; right: -120px; top: -160px;
}}
.grid {{
  position: absolute; inset: 0;
  background-image: linear-gradient(rgba(255,255,255,.05) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255,255,255,.05) 1px, transparent 1px);
  background-size: 36px 36px;
}}
.wrap {{
  position: relative; z-index: 2; padding: 54px 64px;
  height: 100%; display: flex; flex-direction: column; justify-content: center;
}}
.bar {{ width: 64px; height: 6px; background: {theme['accent']}; margin-bottom: 26px; }}
h1 {{ font-size: {title_size}px; line-height: 1.25; letter-spacing: 1px; font-weight: 700; }}
p {{ margin-top: 18px; font-size: {subtitle_size}px; line-height: 1.5; color: rgba(255,255,255,.78); }}
.foot {{
  position: absolute; z-index: 2; left: 64px; bottom: 34px;
  font-size: 15px; letter-spacing: 2px; color: rgba(255,255,255,.55);
}}
</style></head><body>
<div class="grid"></div><div class="glow"></div>
<div class="wrap">
  <div class="bar"></div>
  <h1>{html.escape(args.title)}</h1>
  {f'<p>{html.escape(args.subtitle)}</p>' if args.subtitle else ''}
</div>
<div class="foot">{html.escape(args.author or '')}</div>
</body></html>"""


def render_quote(args, theme):
    quote_size = fit_size(args.title, 60, 30, 22)
    return f"""<!doctype html>
<html><head><meta charset="utf-8" /><style>
{BASE_CSS}
body {{
  width: 1080px; height: 1080px; background: {theme['soft']};
  color: #1f2937; display: flex; align-items: center; justify-content: center;
  position: relative; overflow: hidden;
}}
.circle {{
  position: absolute; width: 620px; height: 620px; border-radius: 50%;
  border: 2px solid {theme['accent']}; opacity: .25; right: -220px; bottom: -260px;
}}
.mark {{
  position: absolute; top: 96px; left: 86px;
  font-size: 180px; line-height: 1; color: {theme['accent']}; opacity: .35;
  font-family: Georgia, serif;
}}
.wrap {{ position: relative; z-index: 2; width: 820px; text-align: center; }}
h1 {{ font-size: {quote_size}px; line-height: 1.55; font-weight: 700; letter-spacing: 1px; }}
.line {{ width: 72px; height: 5px; background: {theme['accent']}; margin: 46px auto 0; }}
p {{ margin-top: 30px; font-size: 24px; color: #6b7280; letter-spacing: 2px; }}
</style></head><body>
<div class="circle"></div><div class="mark">“</div>
<div class="wrap">
  <h1>{html.escape(args.title)}</h1>
  <div class="line"></div>
  <p>{html.escape(args.author or args.subtitle or '')}</p>
</div>
</body></html>"""


def render_points(args, theme):
    bullets = [item.strip() for item in args.bullets if item.strip()]
    if not bullets:
        bullets = ["要点一", "要点二", "要点三"]
    title_size = fit_size(args.title, 56, 32, 18)
    items = "\n".join(
        f"""<div class="item">
  <div class="num">{index:02d}</div>
  <div class="text">{html.escape(text)}</div>
</div>"""
        for index, text in enumerate(bullets[:6], start=1)
    )
    return f"""<!doctype html>
<html><head><meta charset="utf-8" /><style>
{BASE_CSS}
body {{
  width: 1080px; height: 1440px; background: #fff; color: #1f2937;
  padding: 84px 80px 120px; position: relative; overflow: hidden;
  display: flex; flex-direction: column;
}}
.inner {{
  flex: 1; display: flex; flex-direction: column; justify-content: center;
  position: relative; z-index: 2;
}}
.accent {{ position: absolute; top: 0; left: 0; width: 100%; height: 12px; background: {theme['accent']}; }}
.orb {{
  position: absolute; width: 460px; height: 460px; border-radius: 50%;
  background: {theme['soft']}; right: -160px; top: -120px;
}}
h1 {{ position: relative; z-index: 2; font-size: {title_size}px; line-height: 1.35; font-weight: 700; }}
.sub {{ position: relative; z-index: 2; margin-top: 18px; font-size: 24px; color: #6b7280; }}
.list {{ position: relative; z-index: 2; margin-top: 58px; }}
.item {{ display: flex; align-items: flex-start; gap: 24px; margin-bottom: 38px; }}
.num {{
  flex: 0 0 58px; height: 58px; border-radius: 16px; background: {theme['accent']};
  color: #fff; font-size: 24px; font-weight: 700;
  display: flex; align-items: center; justify-content: center;
}}
.text {{ flex: 1; font-size: 32px; line-height: 1.55; padding-top: 6px; }}
.foot {{
  position: absolute; left: 80px; bottom: 64px;
  font-size: 20px; letter-spacing: 2px; color: #9ca3af;
}}
</style></head><body>
<div class="accent"></div><div class="orb"></div>
<div class="inner">
  <h1>{html.escape(args.title)}</h1>
  {f'<div class="sub">{html.escape(args.subtitle)}</div>' if args.subtitle else ''}
  <div class="list">{items}</div>
</div>
<div class="foot">{html.escape(args.author or '')}</div>
</body></html>"""


def command_generate(args):
    theme = THEMES[args.theme]
    if args.template == "cover":
        width, height = 900, 383
        document = render_cover(args, theme)
    elif args.template == "quote":
        width, height = 1080, 1080
        document = render_quote(args, theme)
    else:
        width, height = 1080, 1440
        document = render_points(args, theme)

    chromium = find_chromium()
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".html", delete=False, encoding="utf-8"
    )
    try:
        handle.write(document)
        handle.close()
        page_url = "file://" + urllib.parse.quote(os.path.abspath(handle.name))
        screenshot(
            chromium,
            page_url,
            args.out,
            width,
            height,
            scale=args.scale,
        )
    finally:
        try:
            os.unlink(handle.name)
        except OSError:
            pass

    print(
        json.dumps(
            {
                "file": args.out,
                "template": args.template,
                "width": width * args.scale,
                "height": height * args.scale,
                "method": "generated",
            },
            ensure_ascii=False,
        )
    )
    info(f"已生成配图：{args.out}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="公众号配图素材工具")
    subparsers = parser.add_subparsers(dest="command", required=True)

    fetch = subparsers.add_parser("fetch", help="从官方页面提取并下载图片")
    fetch.add_argument("--url", required=True, help="官方页面地址")
    fetch.add_argument("--out", required=True, help="图片保存目录")
    fetch.add_argument("--max", type=int, default=8, help="最多下载几张，默认 8")
    fetch.add_argument(
        "--allow-svg", action="store_true", help="允许下载 SVG（公众号不支持）"
    )
    fetch.set_defaults(func=command_fetch)

    capture = subparsers.add_parser("capture", help="截取官方页面")
    capture.add_argument("--url", required=True, help="官方页面地址")
    capture.add_argument("--out", required=True, help="截图保存路径")
    capture.add_argument("--width", type=int, default=1280)
    capture.add_argument("--height", type=int, default=1600)
    capture.add_argument("--scale", type=int, default=1)
    capture.set_defaults(func=command_capture)

    generate = subparsers.add_parser("generate", help="生成配图")
    generate.add_argument(
        "--template",
        choices=["cover", "quote", "points"],
        default="cover",
        help="cover 封面、quote 金句卡、points 要点卡",
    )
    generate.add_argument("--title", required=True, help="主要文字")
    generate.add_argument("--subtitle", default="", help="副标题")
    generate.add_argument("--author", default="", help="署名或底部文字")
    generate.add_argument(
        "--bullets",
        default="",
        help="要点卡的条目，用 | 分隔",
    )
    generate.add_argument(
        "--theme", choices=sorted(THEMES), default="green", help="主题色"
    )
    generate.add_argument("--out", required=True, help="输出 PNG 路径")
    generate.add_argument("--scale", type=int, default=1, help="像素倍率")
    generate.set_defaults(func=command_generate)

    args = parser.parse_args()
    if args.command == "generate":
        args.bullets = [item for item in args.bullets.split("|")]
    args.func(args)


if __name__ == "__main__":
    main()
