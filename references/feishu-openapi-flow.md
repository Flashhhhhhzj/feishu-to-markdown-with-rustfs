# Feishu OpenAPI Flow

Read this reference only when direct Feishu link conversion is involved.

## When To Read This

- The input is a Feishu, Lark, or XFChat `/wiki/`, `/docx/`, or `/docs/` link
- Direct OpenAPI conversion fails
- Feishu auth, document lookup, media download, or embedded `sheet` loading is failing

## Use This Reference

1. Confirm the source link is a supported Feishu-style document URL.
2. Confirm app credentials are available through env vars or `./.env.local`.
3. Check whether the Feishu app has document, media, and sheet read permissions.
4. Compare the failing step against the API sequence below.
5. If OpenAPI access is blocked, fall back to local-image mode or browser-cookie mode.

## Supported Inputs

- `https://xxx.feishu.cn/wiki/...`
- `https://xxx.feishu.cn/docx/...`
- `https://xxx.feishu.cn/docs/...`

The script resolves wiki links to the underlying docx token first, then reads the document through the docx block APIs.

## Required Credentials

Set these through env vars or `./.env.local`:

- `CARE_FEISHU_APP_ID` or `FEISHU_APP_ID`
- `CARE_FEISHU_APP_SECRET` or `FEISHU_APP_SECRET`

Optional base URL override:

- `CARE_FEISHU_BASE_URL` or `FEISHU_BASE_URL`

Default:

- `https://open.feishu.cn`

XFChat gateway example:

- `https://open.xfchat.iflytek.com`

## Required Permissions

The Feishu app should have permission to read:

- document content
- images or files
- embedded sheets when sheet expansion is needed

If the document itself is access-controlled, the app also needs document-level access.

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
- `image` -> download binary, then upload or save locally
- `file` -> download binary, then upload or save locally

## Troubleshooting Hints

- `tenant_access_token response was empty`
  Usually means wrong app credentials or an unavailable Feishu gateway.
- `The Feishu wiki link did not resolve to a docx document`
  The source link is not a supported wiki node or access is restricted.
- `warning=sheet_fallback`
  The embedded sheet could not be expanded. Check sheet permissions first.
- Media download failures
  Usually mean missing Drive/file permissions or document-level access.

## Debug Order

Run checks in this order:

1. Check link format.
2. Check app credentials.
3. Check document access.
4. Check media or sheet permissions.
5. Retry in local-image mode if remote upload is not the blocker.

## Important Caveats

- The script is designed for paste-ready Markdown, not for perfect visual reconstruction of every Feishu block type.
- Merged table cells are flattened because standard Markdown tables cannot represent merged cells well.
- Some advanced Feishu block types such as grids, diagrams, or embedded widgets fall back to child content or links.
- Embedded `sheet` blocks require sheet read scope. Without that scope, the script keeps a visible placeholder instead of silently dropping the block.
- Feishu returns embedded `sheet` tokens in `spreadsheetToken_sheetId` form. The script splits that token and reads the non-empty range before converting it into a Markdown table.
- Image URLs are normalized before writing to Markdown. You can rewrite them with `CARE_DOCX_URL_REWRITE_FROM` / `CARE_DOCX_URL_REWRITE_TO` or force `https://` with `CARE_DOCX_FORCE_HTTPS=true`.
