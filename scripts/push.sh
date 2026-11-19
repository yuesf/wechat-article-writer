#!/bin/bash
# 公众号草稿推送包装脚本：加载凭据后调用同目录下的 wechat_draft.py
#
# 用法:
#   ./push.sh --title "标题" --content dist/article.wechat.html \
#             --cover assets/cover.png --digest "摘要" --author "作者" [--dry-run]
#
# 凭据文件位置（按顺序取第一个存在的）:
#   1. $WECHAT_MP_CRED_FILE
#   2. ./.secrets/wechat_mp.env（相对当前工作目录）
#   3. ~/.config/wechat-article-writer/wechat_mp.env
#
# 凭据文件内容形如:
#   export WECHAT_MP_APPID='wx...'
#   export WECHAT_MP_APPSECRET='...'
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

CRED_FILE="${WECHAT_MP_CRED_FILE:-}"
if [ -z "$CRED_FILE" ]; then
  if [ -f "./.secrets/wechat_mp.env" ]; then
    CRED_FILE="./.secrets/wechat_mp.env"
  else
    CRED_FILE="$HOME/.config/wechat-article-writer/wechat_mp.env"
  fi
fi

if [ -f "$CRED_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$CRED_FILE"
  set +a
else
  echo "[warn] 未找到凭据文件：$CRED_FILE" >&2
  echo "[warn] 可直接用环境变量 WECHAT_MP_APPID / WECHAT_MP_APPSECRET，或先运行 scripts/config_page.py 配置。" >&2
fi

exec python3 "$SCRIPT_DIR/wechat_draft.py" "$@"
