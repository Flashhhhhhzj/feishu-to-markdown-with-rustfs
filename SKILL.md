---
name: feishu-to-markdown-with-rustfs
description: Convert Feishu or Lark cloud document links into Markdown and upload extracted images to RustFS for stable public URLs while preserving headings, tables, lists, links, and code blocks as much as possible.
---

# Feishu Cloud Doc to Markdown with RustFS

## 技能概述

该 skill 是一套面向知识沉淀与内容工程化交付场景打造的高保真文档转译能力模块，可将飞书、Lark 或 XFChat 云文档链接解析为结构化 Markdown，尽可能保留标题层级、自动编号、列表、表格、代码块、超链接、图片及嵌入式 sheet 等核心语义。其内置 Feishu OpenAPI 读取链路与 RustFS 图床上传机制，可自动抽取媒资、完成对象存储落盘与公网链接回写，输出可直接用于知识库、专栏后台、研发归档与 AI 工作流接入的标准化内容成果，同时支持本地草稿模式、统一脚本入口及 Codex、Claude Code、Cursor、Gemini、终端等多环境协同调用。

这个 skill 有两种入口：

- 在 Codex 里直接使用 `$feishu-to-markdown-with-rustfs`
- 在 Claude Code、Cursor、Gemini、终端里直接运行 `scripts/feishu_link_to_markdown.py`

建议始终显式指定：

- 输出 Markdown 路径
- 图片上传目录

这样转换完成后最不容易找不到文件。

## 使用流程

### 1. 先拿到 RustFS 的 AK/SK

RustFS 这一侧需要的是 S3 兼容对象存储凭证，也就是下面这几项：

- `CARE_DEV_FILE_S3_ENDPOINT`
- `CARE_DEV_FILE_S3_DOMAIN`
- `CARE_DEV_FILE_S3_BUCKET`
- `CARE_DEV_FILE_S3_ACCESS_KEY`
- `CARE_DEV_FILE_S3_ACCESS_SECRET`

常见获取方式：

- 找 RustFS / MinIO / 对象存储管理后台里的 Access Key 页面
- 找项目当前环境里已经在用的文件存储配置
- 直接找后端或运维同学要当前环境的 endpoint、domain、bucket、AK、SK

如果你们项目已经能把图片上传到 RustFS，那这些值通常已经存在于后端环境变量、部署配置，或者对象存储控制台里。

### 2. 再拿到飞书的 App ID / App Secret

飞书这边严格来说不是 AK/SK 这套叫法，而是：

- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`

常见获取方式：

- 打开飞书开放平台或你们当前使用的开放平台网关后台
- 进入目标应用的“凭证与基础信息”
- 复制 App ID 和 App Secret

如果你们走的是讯飞这套网关，通常还需要：

- `FEISHU_BASE_URL="https://open.xfchat.iflytek.com"`

同时记得给应用开好至少这些权限：

- 文档读取权限
- 图片/文件读取权限
- `sheet` 读取权限，例如 `sheets:spreadsheet:readonly`

### 3. 先复制 `.env.example`，再把这些值填进 `.env.local`

分享包里会附带一份不含真实值的 `.env.example`。推荐先复制一份：

- `cp .env.example .env.local`

然后这个 skill 会自动读取 skill 根目录下的 `.env.local`：

- `./.env.local`

推荐填法如下：

```env
FEISHU_APP_ID="cli_xxx"
FEISHU_APP_SECRET="xxx"
FEISHU_BASE_URL="https://open.xfchat.iflytek.com"

CARE_DEV_FILE_CONFIG_ENABLED=true
CARE_DEV_FILE_S3_ENDPOINT="http://你的 RustFS 地址"
CARE_DEV_FILE_S3_DOMAIN="http://你的访问域名/care-markdown"
CARE_DEV_FILE_S3_BUCKET="care-markdown"
CARE_DEV_FILE_S3_ACCESS_KEY="你的 RustFS AK"
CARE_DEV_FILE_S3_ACCESS_SECRET="你的 RustFS SK"
CARE_DEV_FILE_S3_PATH_STYLE=true
CARE_DEV_FILE_S3_PUBLIC_ACCESS=true
CARE_DEV_FILE_S3_REGION="us-east-1"

CARE_DOCX_DIRECTORY="knowledge/column/temp"
```

字段说明：

- `CARE_DOCX_DIRECTORY`
  默认上传目录。已有文章建议改成 `knowledge/column/{columnId}/article/{articleId}`。
- `CARE_DEV_FILE_S3_DOMAIN`
  Markdown 里最终写入的图片访问前缀。
- `CARE_DEV_FILE_S3_ENDPOINT`
  实际上传用的对象存储服务地址。

如果你不想把真实值写进 `SKILL.md`，就把真实值只放在 `.env.local` 里，不要提交到仓库。

### 4. 先确定上传目录

开始转换前，先确定图片应该放到哪里：

- 已有文章：`knowledge/column/{columnId}/article/{articleId}`
- 草稿文章：`knowledge/column/{columnId}/temp`

如果你不传 `--directory`，脚本会优先使用 `.env.local` 里的 `CARE_DOCX_DIRECTORY`。  
如果这里也没填，但 RustFS 配置存在，脚本会回退到 `knowledge/column/temp`。

### 5. 在 Codex 里使用

直接在 Codex 对话里这样说：

```text
Use $feishu-to-markdown-with-rustfs to convert this Feishu link into Markdown:
https://www.xfchat.iflytek.com/wiki/xxxx

Write the result to /tmp/article.md
Upload images to knowledge/column/12/article/101
```

如果你只想先出草稿，不上传图片：

```text
Use $feishu-to-markdown-with-rustfs to convert this Feishu link into Markdown only.
Do not upload images.
Write the result to /tmp/article.md
https://www.xfchat.iflytek.com/wiki/xxxx
```

### 6. 在 Claude Code（cc）里使用

直接给 Claude Code 这样的中文提示即可：

```text
请运行 $CODEX_HOME/skills/feishu-to-markdown-with-rustfs/scripts/feishu_link_to_markdown.py，
把这个飞书链接转换成 Markdown：
https://www.xfchat.iflytek.com/wiki/xxxx

输出到 /tmp/article.md
图片上传到 knowledge/column/12/article/101
完成后告诉我生成文件的位置。
```

### 7. 在 Cursor 里使用

如果 Cursor Agent 能执行本地命令，就直接这样说：

```text
请调用本地脚本 $CODEX_HOME/skills/feishu-to-markdown-with-rustfs/scripts/feishu_link_to_markdown.py，
把这个飞书文档链接转换成 Markdown：
https://www.xfchat.iflytek.com/wiki/xxxx

输出文件：/tmp/article.md
上传目录：knowledge/column/12/article/101
如果上传失败，就回退成不上传图片的草稿模式。
```

### 8. 在 Gemini 里使用

如果是 Gemini CLI 或其他能执行命令的 Gemini 环境，可以直接让它执行完整命令：

```text
请直接执行下面这条命令，把飞书文档转换成 Markdown：
python3 "$CODEX_HOME/skills/feishu-to-markdown-with-rustfs/scripts/feishu_link_to_markdown.py" "https://www.xfchat.iflytek.com/wiki/xxxx" --directory "knowledge/column/12/article/101" --output /tmp/article.md
```

### 9. 在终端里使用

直接运行命令：

```bash
python3 "$CODEX_HOME/skills/feishu-to-markdown-with-rustfs/scripts/feishu_link_to_markdown.py" \
  "https://www.xfchat.iflytek.com/wiki/xxxx" \
  --directory "knowledge/column/12/article/101" \
  --output /tmp/article.md
```

如果你想只生成草稿，不上传图片：

```bash
python3 "$CODEX_HOME/skills/feishu-to-markdown-with-rustfs/scripts/feishu_link_to_markdown.py" \
  "https://www.xfchat.iflytek.com/wiki/xxxx" \
  --no-upload \
  --output /tmp/article.md \
  --assets-dir /tmp/article-assets
```

### 10. 导出的 Markdown 存在哪里

这件事分 3 种情况：

- 如果你显式传了 `--output /tmp/article.md`
  最终 Markdown 就在 `/tmp/article.md`
- 如果你在 Codex、cc、Cursor、Gemini 的提示词里明确要求“写到某个路径”
  结果就会按你给的路径写入
- 如果你没有传 `--output`
  脚本会把 Markdown 写到当前工作目录，默认文件名是 `<文档 token>.md`

图片的落点也分两种：

- 开启上传：图片会上传到 RustFS 指定目录，Markdown 里写 RustFS 链接
- 关闭上传：图片会保存在输出 Markdown 同级目录下的 `<输出文件名>-assets/`

## 其他说明

### 输入要求

- 一条飞书 `/wiki/` 或 `/docx/` 文档链接
- 一个输出 `.md` 路径，或者允许脚本使用默认输出位置
- 一组可用的飞书 `app_id` / `app_secret`
- 一组可用的 RustFS 配置，或者你明确使用 `--no-upload`
- 一个符合知识专栏约定的上传目录

### 脚本能力

这个 skill 自带的脚本可以：

- 接收飞书 `/wiki/` 或 `/docx/` 文档链接
- 通过 Feishu OpenAPI 把 wiki 链接解析成底层文档
- 在链接模式下，直接读取飞书 block 和媒体资源
- 把标题样式映射成 Markdown 标题
- 把连续代码段合并成 fenced code block
- 把表格转换成 Markdown 表格
- 在权限正常时，把嵌入的 `sheet` 块展开成 Markdown 表格
- 尽量保留链接、列表和基础行内样式
- 提取图片并上传到 `/admin-api/infra/file/upload` 或 `/infra/file/upload`
- 规范化返回图片链接，只保留 `origin + pathname`
- 在上传配置缺失时，自动回退到本地图片保存模式
- 通过 `scripts/feishu_link_to_markdown.py` 对外提供更稳定、更通用的脚本入口，方便其他 AI 工具直接调用

### 快速检查清单

转换完成后，建议快速检查：

- 标题是否已经变成 `#`、`##`、`###`
- `1.`、`1.1` 这类编号层级是否还在
- 代码示例是否已经变成 fenced code block
- 表格是否已经是 Markdown 表格
- 图片是否已经是 RustFS 链接，或者草稿模式下的本地路径
- `sheet` 是否已经展开成 Markdown 表格

### 上传约定

- `--base-url` 可以传后端根地址，例如 `http://127.0.0.1:48080`
- 也可以传管理后台 API 根地址，例如 `http://127.0.0.1:48080/admin-api`
- 也可以直接传完整上传接口
- `--token` 既支持原始 token，也支持 `Bearer ...`
- 只有在多租户部署下，才需要 `--tenant-id` 和 `--visit-tenant-id`
- 脚本会自动给图片名加上文档前缀，尽量减少同目录重名冲突
- 如果你想重写图片访问域名，可设置 `CARE_DOCX_URL_REWRITE_FROM` 和 `CARE_DOCX_URL_REWRITE_TO`
- 如果页面是 HTTPS，而文件域名还是 HTTP，可尝试设置 `CARE_DOCX_FORCE_HTTPS=true`

### 常见 warning

脚本常见 warning 含义如下：

- `warning=sheet_fallback`
  说明文档里有嵌入式 sheet，但当前应用还没真正读到它。优先检查 Sheets 权限和文档侧授权。
- `warning=mixed_content_risk`
  说明生成的图片地址还是 `http://...`。如果知识专栏页面是 HTTPS，浏览器预览可能会拦截这些图片。
- `warning=rustfs_key_fallback`
  说明 RustFS 第一次接收对象后，读回校验失败。脚本已经自动换了一个新的文件名重新上传。

### 故障排查

- 如果标题层级不对，优先检查飞书 block 类型和编号层级映射
- 如果代码块没有正确识别，优先检查飞书返回的 block 类型和代码语言字段
- 如果表格表现不好，先以第一行作为表头，再手动处理合并单元格边角情况
- 如果嵌入式 `sheet` 还显示为 `[电子表格: ...]`，优先检查飞书应用的 Sheets 权限和文档侧授权
- 如果图片在浏览器预览里打不开，但 `curl` 能访问，优先排查 HTTPS 页面加载 HTTP 图片的 mixed content 问题
- 如果上传失败，检查 token、目录、后端地址，以及 `references/care-dev-upload-flow.md`
- 如果飞书链接模式失败，检查飞书应用凭证、文档权限，以及 `references/feishu-openapi-flow.md`

### 参考资源

- `scripts/feishu_link_to_markdown.py`：推荐给 Gemini、Claude Code、Cursor 等工具使用的通用入口
- `scripts/feishu_docx_to_markdown.py`：核心转换脚本
- `references/care-dev-upload-flow.md`：项目内上传链路、默认配置和文件定位
- `references/feishu-openapi-flow.md`：飞书链接转换流程和接口假设
