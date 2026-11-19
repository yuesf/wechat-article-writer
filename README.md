# wechat-article-writer

一个面向 AI Agent（也适合手动使用）的**微信公众号写作助手**：

选主题 → 收集并核验素材 → 找官方配图或生成配图 → 出标题候选 → 搭结构写正文 →
生成可核对的 HTML 预览页 → 定稿后通过公众号官方接口写入**草稿箱**。

最后一步只写草稿，不自动群发、不自动发表，是否发布由你自己在公众号后台决定。

## 特性

- **取材优先官方**：官网 / 官方公众号 / 官方新闻稿 / 发布会资料，事实与数字必须落到来源。
- **配图三条路**：从官方页面抓图、无头 Chromium 截官方页面、本机生成封面 / 金句卡 / 要点卡。
- **发布前可核对**：产出 `preview.html` 手机阅读预览页，确认无误再推草稿。
- **只写草稿**：调用 `draft/add` 接口，返回 `media_id`，不碰群发接口。
- **零第三方依赖**：全部脚本只用 Python 3 标准库；只有截图与配图渲染需要本机有 Chromium。
- **凭据不进代码**：AppID / AppSecret 只从环境变量或本地凭据文件读取，不写进正文、脚本、仓库和对话。

## 目录结构

```
wechat-article-writer/
├── SKILL.md                 # Agent 技能说明：完整流程、步骤、边界
├── scripts/
│   ├── assets.py            # 配图：fetch（抓官方图）/ capture（截页面）/ generate（生成图）
│   ├── build_article.py     # Markdown → 公众号正文 HTML + 预览页 + meta.json
│   ├── wechat_draft.py      # 调公众号接口写草稿（支持 --dry-run）
│   ├── config_page.py       # 一次性凭据配置页（本地表单 + 临时隧道）
│   └── push.sh              # 加载凭据后调用 wechat_draft.py 的包装脚本
└── examples/
    └── article.md           # 示例定稿 Markdown
```

## 快速开始

### 1. 生成配图（可选）

```bash
# 从官方页面抓图
python3 scripts/assets.py fetch --url "https://example.com/news" --out assets/official

# 截取官方页面
python3 scripts/assets.py capture --url "https://example.com/news" --out assets/page.png

# 本机生成封面（900×383）、金句卡、要点卡
python3 scripts/assets.py generate --template cover \
  --title "主标题" --subtitle "副标题" --author "署名" \
  --theme dark --out assets/cover.png
```

主题色：`green` / `blue` / `orange` / `dark`。

### 2. 写定稿 Markdown

见 `examples/article.md`：小标题用 `##` / `###`，图片用 `![图注](本地路径)` 引用。

### 3. 生成正文与预览页

```bash
python3 scripts/build_article.py \
  --content article.md \
  --title "文章标题" \
  --digest "文章摘要" \
  --author "作者名" \
  --cover assets/cover.png \
  --out-dir dist
```

输出 `dist/article.wechat.html`（推送用）、`dist/preview.html`（核对用）、`dist/meta.json`。

### 4. 配置凭据（一次性）

公众号草稿接口需要 AppID 与 AppSecret，只能从公众号后台「设置与开发 → 基本配置」拿到。

方式一，环境变量：

```bash
export WECHAT_MP_APPID='wx...'
export WECHAT_MP_APPSECRET='...'
```

方式二，凭据文件（推荐，`chmod 600`）：

```bash
mkdir -p .secrets
cat > .secrets/wechat_mp.env <<'EOF'
export WECHAT_MP_APPID='wx...'
export WECHAT_MP_APPSECRET='...'
EOF
chmod 600 .secrets/wechat_mp.env
```

方式三，用配置页在手机上填（适合模型侧拿不到密钥的场景）：

```bash
python3 scripts/config_page.py      # 打印 token=<token> port=<port>
```

再把本地端口通过临时隧道暴露成 HTTPS，用手机浏览器打开 `<https 地址>/<token>` 填表提交；
页面会校验凭据、换一次 access_token、读一次草稿列表，成功后自动落盘并 90 秒后自清理。
凭据默认写到 `~/.config/wechat-article-writer/wechat_mp.env`，可用 `WECHAT_MP_CRED_FILE` 覆盖。

### 5. 写草稿

```bash
# 先本地检查，不调接口
python3 scripts/wechat_draft.py \
  --title "文章标题" --content dist/article.wechat.html \
  --cover assets/cover.png --digest "摘要" --author "作者名" --dry-run

# 正式写入草稿箱
./scripts/push.sh \
  --title "文章标题" --content dist/article.wechat.html \
  --cover assets/cover.png --digest "摘要" --author "作者名"
```

成功后返回 `media_id`，到公众号后台「草稿箱」检查排版、封面、摘要，再自行群发。

## 前提与限制

- 公众号需已认证并具备草稿接口权限（否则报 `48001`）。
- 公众号后台「IP 白名单」必须包含运行环境的出口 IP（否则报 `40164`）。
- 封面为本地 jpg/png，建议 900×383（约 2.35:1），不超过 10MB。
- 截图与配图渲染需要本机安装 Chromium（`chromium` 或 `chromium-browser`）。
- 只调用草稿接口，不做群发、不做定时发表。

## 常见错误码

| 错误码 | 含义 | 处理 |
| --- | --- | --- |
| 40013 | AppID 不正确 | 复制完整 AppID |
| 40125 | AppSecret 不正确 | 后台重置后重填 |
| 40164 | 出口 IP 不在白名单 | 后台把运行环境出口 IP 加进白名单 |
| 48001 | 公众号未认证、无草稿权限 | 完成认证或换有权限的号 |
| 89503 | 需管理员确认 | 让管理员在后台确认 |

## 作为 Agent Skill 使用

本仓库根目录的 `SKILL.md` 就是技能定义（YAML front matter + 完整流程），
可以直接放进支持 skill 的 Agent 环境（例如放到 `skills/wechat-article-writer/` 下），
由 Agent 按步骤执行：选题 → 取材 → 配图 → 结构 → 正文 → 摘要 → 自检 → 出 HTML → 核对 → 推草稿 → 回报。

## 边界

- 不编造事实、数据、案例、引语、来源；无法核实的删掉或标注待补充。
- 自制图不冒充官方图，官方截图不篡改内容。
- 不搬运受版权保护的图片与文章，引用控制在合理范围并注明来源。
- 不生成诱导分享、夸大疗效、虚假承诺、低俗标题。
- 医疗 / 金融 / 法律等专业内容提示由具备资质的人审核。

## License

MIT
