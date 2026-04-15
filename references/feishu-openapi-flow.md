# Feishu OpenAPI Flow

Use this reference when the input is a Feishu wiki or docx link.

## Supported Inputs

- `https://xxx.feishu.cn/wiki/...`
- `https://xxx.feishu.cn/docx/...`

The script resolves wiki links to the underlying docx token first, then reads the document through the docx block APIs.

## Required Credentials

Set one of these before running the script:

- `CARE_FEISHU_APP_ID`
- `CARE_FEISHU_APP_SECRET`

Also supported as aliases:

- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`
- `FEISHU_BASE_URL`

The bundled script also auto-loads a local `./.env.local` file from the skill root before parsing CLI arguments.

Optional:

- `CARE_FEISHU_BASE_URL`
  Default: `https://open.feishu.cn`

## API Sequence

1. `POST /open-apis/auth/v3/tenant_access_token/internal`
2. If the source is a wiki link:
   `GET /open-apis/wiki/v2/spaces/get_node?token=...`
3. `GET /open-apis/docx/v1/documents/{document_id}`
4. `GET /open-apis/docx/v1/documents/{document_id}/blocks?page_size=500`
5. For image or file blocks:
   `GET /open-apis/drive/v1/medias/{file_token}/download`
6. For embedded `sheet` blocks:
   `GET /open-apis/sheets/v2/spreadsheets/{spreadsheet_token}/values/{sheet_id}`

## Block Mapping Used By The Script

- `page` -> top-level `# title`
- `heading1..heading9` -> Markdown headings
- `text` -> plain paragraph
- `bullet` -> `- list item`
- `ordered` -> `1. list item`
- `code` -> fenced code block
- `quote` -> blockquote
- `todo` -> task list item
- `divider` -> `---`
- `table` + `table_cell` -> Markdown table
- `sheet` -> Markdown table when Sheets read permission is available
- `image` -> download binary, then upload to Care-Dev or save locally
- `file` -> download binary, then upload to Care-Dev or save locally

## Important Caveats

- The script is designed for paste-ready knowledge-column Markdown, not for perfect pixel-for-pixel reconstruction of every Feishu block type.
- Merged table cells are flattened because standard Markdown tables cannot express merged cells cleanly.
- Some advanced Feishu block types such as grids, diagrams, or embedded widgets fall back to child content or links.
- Direct link mode depends on the Feishu app having permission to read the target document and its media.
- Embedded `sheet` blocks require Sheets or Drive read scope on the Feishu app. Without that scope, the script falls back to a visible `[电子表格: ...]` placeholder instead of silently dropping the block.
- Feishu returns embedded `sheet` tokens in `spreadsheetToken_sheetId` form. The script splits that token and reads the non-empty range of the target sheet before converting it into a Markdown table.
- Image URLs are normalized before writing to Markdown, and you can optionally rewrite them with `CARE_DOCX_URL_REWRITE_FROM` / `CARE_DOCX_URL_REWRITE_TO` or force `https://` with `CARE_DOCX_FORCE_HTTPS=true` when the target storage domain supports TLS.
