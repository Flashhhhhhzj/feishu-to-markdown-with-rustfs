---
name: feishu-to-markdown-with-rustfs
version: 1.0.0
description: Convert Feishu, Lark, or XFChat cloud docs to Markdown with optional RustFS or Care-Dev upload. Use when users provide a doc link. Triggers: 'feishu to markdown'.
trigger: |
  TRIGGER when:
  - User provides a Feishu, Lark, or XFChat `/wiki/`, `/docx/`, or `/docs/` link
  - User asks to convert a cloud document into Markdown
  - User asks to preserve images and optionally upload them to RustFS or Care-Dev
  - User asks for paste-ready Markdown for a knowledge base, column backend, or article editor

  DO NOT TRIGGER when:
  - The input is only local text or a local `.docx` file
  - The task is general web scraping
  - The user asks for PDF processing
  - The user only wants Markdown editing or copywriting help
references:
  - references/feishu-openapi-flow.md
  - references/care-dev-upload-flow.md
allowed-tools:
  - Read
  - Edit
  - Bash
---

# 飞书文档转 Markdown

将公司飞书、Lark 或 XFChat 文档链接转换为标准 Markdown。自动提取文字、标题、列表、表格、代码块、图片和部分嵌入式 sheet 内容；可将图片上传到 RustFS 或 Care-Dev，也可回退为本地 assets 目录。

## 使用方式

`/feishu-to-markdown-with-rustfs`

优先使用仓库内稳定入口：

- `scripts/feishu_link_to_markdown.py`

不要优先直接调用核心脚本，除非需要核对实现细节：

- `scripts/feishu_docx_to_markdown.py`

## 适用场景

当用户出现以下诉求时使用本 skill：

- “把这个飞书文档转成 Markdown”
- “把 wiki/docx/docs 链接转成可粘贴的 md”
- “保留图片并上传到 RustFS”
- “输出给知识库 / 专栏 / 文章后台”

## Trigger / When to use

满足以下任一条件时触发：

- 用户提供 Feishu、Lark、XFChat 的 `/wiki/`、`/docx/`、`/docs/` 链接
- 用户要求把飞书云文档转换为 Markdown
- 用户要求保留图片，并上传到 RustFS 或 Care-Dev
- 用户要求输出为适合知识库、专栏后台或文章系统粘贴的 Markdown

## Do not use

仅适用于飞书、Lark、XFChat 云文档链接，不适用于：

- 本地 `.docx` 文件
- 普通网页抓取
- PDF 转换
- 没有云文档链接、只给出纯文本内容的场景

## 输入校验

执行前检查以下约束：

- 输入链接必须是 Feishu、Lark 或 XFChat 的文档链接
- 链接路径必须包含 `/wiki/`、`/docx/` 或 `/docs/`
- 远程上传模式下必须提供上传目录
- Care-Dev 上传模式下必须提供后端地址和鉴权信息
- 若用户明确要求“不上传图片”，必须使用本地 assets 模式

## 权限与安全

- 不要在 `SKILL.md` 中写入真实 `App Secret`、Access Key、Secret Key 或 Token
- 优先从 `./.env.local` 或环境变量读取敏感配置
- 命令示例中只使用环境变量占位，不写任何疑似真实密钥
- 仅在用户明确要求上传时，才使用远程上传能力
- 如果缺少凭据或权限不足，先说明缺失项，再回退到 `--no-upload`

## 操作指令

执行任务时遵循下面这些明确动作：

1. 先校验输入链接是否为 Feishu、Lark 或 XFChat 的 `/wiki/`、`/docx/`、`/docs/` 链接。
2. 再确认输出路径、上传模式和上传目录。
3. 优先运行 `scripts/feishu_link_to_markdown.py`，不要默认直接调用核心脚本。
4. 若远程上传条件不完整，立即回退到 `--no-upload` 本地 assets 模式。
5. 转换完成后，必须检查 Markdown 输出路径、图片落点和 warning。
6. 若需要额外背景信息，再按需读取 `references/`，不要默认全部加载。

## 执行流程

### Step 1：收集信息

先收集以下信息：

- 飞书文档链接，必填
  支持格式：`https://xxx.feishu.cn/docx/Xxx`、`/docs/Xxx`、`/wiki/Xxx`
- 输出路径，选填
  建议显式传 `.md` 路径，避免产物位置不明确
- 上传模式，选填
  可选：
  - 上传到 RustFS
  - 上传到 Care-Dev
  - 不上传，仅保留本地图片
- 上传目录，远程上传时必填
  常见值：
  - `knowledge/column/{columnId}/article/{articleId}`
  - `knowledge/column/{columnId}/temp`

如果用户没有提供输出路径，脚本默认写到当前工作目录，文件名默认为 `<document-token>.md`。

### Step 2：检查环境

先确认 Python 可用，再确认依赖是否存在：

```bash
python --version 2>/dev/null || python3 --version
python3 -c "import requests" 2>/dev/null && echo "ok" || echo "missing"
```

如果 `requests` 缺失，提示：

```bash
pip install requests
```

如果用户要使用 `--browser-cookies`，再额外检查：

```bash
python3 -c "import browser_cookie3" 2>/dev/null && echo "ok" || echo "missing"
```

### Step 3：准备凭据

先读取 skill 根目录下的：

- `./.env.local`

不要假设仓库里一定存在 `.env.example`。只有它真实存在时才引用它。

#### 飞书 OpenAPI 凭据

优先读取这些环境变量：

- `CARE_FEISHU_APP_ID` 或 `FEISHU_APP_ID`
- `CARE_FEISHU_APP_SECRET` 或 `FEISHU_APP_SECRET`
- `CARE_FEISHU_BASE_URL` 或 `FEISHU_BASE_URL`

默认 `FEISHU_BASE_URL` 为：

- `https://open.feishu.cn`

如果是 XFChat 私有网关，可使用：

- `https://open.xfchat.iflytek.com`

#### Care-Dev 上传参数

当用户要通过后端上传接口传图时，读取：

- `CARE_DOCX_BASE_URL`
- `CARE_DOCX_TOKEN`
- `CARE_DOCX_DIRECTORY`
- `CARE_DOCX_TENANT_ID` 可选
- `CARE_DOCX_VISIT_TENANT_ID` 可选

#### RustFS 直传参数

当用户要直接上传到 S3 兼容对象存储时，读取：

- `CARE_DEV_FILE_S3_ENDPOINT`
- `CARE_DEV_FILE_S3_DOMAIN`
- `CARE_DEV_FILE_S3_BUCKET`
- `CARE_DEV_FILE_S3_ACCESS_KEY`
- `CARE_DEV_FILE_S3_ACCESS_SECRET`
- `CARE_DEV_FILE_S3_REGION` 可选，默认 `us-east-1`
- `CARE_DEV_FILE_S3_PATH_STYLE` 可选

如果 RustFS 配置已存在，但未显式传 `--directory`，脚本会回退到：

- `knowledge/column/temp`

### Step 4：执行转换

根据用户需求，选择下面一种方式执行。

#### 方案 A：仅生成 Markdown，本地保存图片

```bash
python3 scripts/feishu_link_to_markdown.py \
  "https://www.xfchat.iflytek.com/wiki/xxxx" \
  --no-upload \
  --output /tmp/article.md \
  --assets-dir /tmp/article-assets
```

#### 方案 B：通过 Care-Dev 接口上传图片

```bash
python3 scripts/feishu_link_to_markdown.py \
  "https://www.xfchat.iflytek.com/wiki/xxxx" \
  --base-url "$CARE_DOCX_BASE_URL" \
  --token "$CARE_DOCX_TOKEN" \
  --directory "knowledge/column/12/article/101" \
  --output /tmp/article.md
```

说明：

- `--base-url` 支持后端根地址
- `--base-url` 支持 `admin-api` 根地址
- `--base-url` 也支持完整上传接口地址
- `--token` 支持原始 token 或 `Bearer ...`

#### 方案 C：直接上传到 RustFS

当 `.env.local` 已包含 RustFS 配置时执行：

```bash
python3 scripts/feishu_link_to_markdown.py \
  "https://www.xfchat.iflytek.com/wiki/xxxx" \
  --directory "knowledge/column/12/article/101" \
  --output /tmp/article.md
```

#### 方案 D：读取本地浏览器 Cookie 抓取页面

仅在 OpenAPI 不方便使用、且本机已有登录态时使用：

```bash
python3 scripts/feishu_link_to_markdown.py \
  "https://www.feishu.cn/wiki/xxxx" \
  --browser-cookies \
  --browser chrome \
  --no-upload \
  --output /tmp/article.md
```

### Step 5：验证输出

转换完成后，至少执行以下检查：

1. 确认 Markdown 文件已生成
2. 确认图片是远程 URL 或本地 assets 路径
3. 预览 Markdown 前几十行，确认标题和正文结构正常
4. 如果有表格、代码块、sheet，抽查转换效果

可执行：

```bash
ls -l /tmp/article.md
```

如果是本地图片模式，可执行：

```bash
ls -l /tmp/article-assets
```

如需快速预览 Markdown，可执行：

```bash
sed -n '1,40p' /tmp/article.md
```

## Examples

示例 1：用户说“把这个飞书 wiki 链接转成 Markdown，图片不要上传，输出到 `/tmp/article.md`”。

执行方式：

```bash
python3 scripts/feishu_link_to_markdown.py \
  "https://www.feishu.cn/wiki/xxxx" \
  --no-upload \
  --output /tmp/article.md \
  --assets-dir /tmp/article-assets
```

示例 2：用户说“把这个文档转成可发专栏的 Markdown，图片上传到 `knowledge/column/12/article/101`”。

执行方式：

```bash
python3 scripts/feishu_link_to_markdown.py \
  "https://www.xfchat.iflytek.com/wiki/xxxx" \
  --directory "knowledge/column/12/article/101" \
  --output /tmp/article.md
```

示例 3：用户说“用 Care-Dev 接口上传图片，输出 Markdown 到固定路径”。

执行方式：

```bash
python3 scripts/feishu_link_to_markdown.py \
  "https://www.xfchat.iflytek.com/wiki/xxxx" \
  --base-url "$CARE_DOCX_BASE_URL" \
  --token "$CARE_DOCX_TOKEN" \
  --directory "$CARE_DOCX_DIRECTORY" \
  --output /tmp/article.md
```

## 错误处理

常见问题与建议：

| 错误关键词 | 常见原因 | 处理建议 |
| --- | --- | --- |
| `This script only accepts a Feishu wiki/docx URL` | 链接格式不支持 | 换成原始 `/wiki/`、`/docx/`、`/docs/` 链接 |
| `tenant_access_token response was empty` | 飞书凭据错误或网关不通 | 检查 `FEISHU_APP_ID`、`FEISHU_APP_SECRET`、`FEISHU_BASE_URL` |
| `--base-url or CARE_DOCX_BASE_URL is required when upload is enabled` | 开启上传但未提供后端地址 | 补充 `--base-url` 或环境变量 |
| `--token or CARE_DOCX_TOKEN is required when upload is enabled` | 开启 Care-Dev 上传但未提供 token | 补充 `--token` 或环境变量 |
| `--directory or CARE_DOCX_DIRECTORY is required when upload is enabled` | 上传目录缺失 | 提供文章目录或草稿目录 |
| `warning=sheet_fallback` | 无法展开嵌入 sheet | 检查飞书应用的 Sheets 权限和文档授权 |
| `warning=mixed_content_risk` | 图片还是 `http://` 链接 | HTTPS 页面预览可能被浏览器拦截 |
| 上传失败 | token、目录、接口地址或 RustFS 配置不正确 | 优先检查上传链路配置，必要时回退 `--no-upload` |

## 回退策略

除非用户明确要求“必须远程上传图片”，否则失败时按下面顺序回退：

1. 优先使用用户指定的上传方式
2. 若另一种远程上传方式已具备条件，可尝试切换
3. 最后回退到 `--no-upload`，将图片保存到本地 assets 目录

如果用户明确说“不要上传图片”，始终使用：

- `--no-upload`

## 输出规范

成功后至少向用户说明：

- Markdown 输出路径
- 图片是远程上传还是本地保存
- 如果是本地保存，assets 目录在哪里
- 是否出现影响发布或预览的 warning

典型输出形态：

```text
<输出路径>/article.md
<输出路径>/article-assets/
```

如果启用远程上传，Markdown 中的图片应为远程 URL。  
如果未启用上传，Markdown 中的图片应引用本地 assets 目录下的文件。

## 前置条件

- Python 3 可用
- 已安装 `requests`
- 若使用浏览器 Cookie 模式，已安装 `browser_cookie3`
- 飞书开放平台应用具备文档读取能力
- 若要上传图片，具备 Care-Dev 或 RustFS 对应配置
- 若要展开 `sheet`，应用还需要相应的表格读取权限

## 限制 / 不触发条件

- 不负责创建飞书应用或开通权限，只消费已有配置
- 不保证 100% 还原复杂排版、合并单元格或所有嵌入对象
- 没有可用凭据时，不应伪造上传成功结果
- 用户未提供云文档链接时，不应触发本 skill
- 若用户需求只是“解释一段 Markdown”或“润色文案”，不应触发本 skill

## References / See also

只在需要时读取，不要默认全部加载：

- `references/feishu-openapi-flow.md`
  当飞书鉴权、文档解析、sheet 拉取异常时查看
- `references/care-dev-upload-flow.md`
  当 Care-Dev 上传链路、目录约定、接口路径不清楚时查看

## 写给 Agent 的规则

- 优先给出明确输出路径，避免生成后找不到文件
- 优先使用 `scripts/feishu_link_to_markdown.py`
- 指令要短、直给、可执行，不要写成长篇产品说明
- 不要承诺仓库中不存在的文件或能力
- 不要默认加载全部 references，按需读取
