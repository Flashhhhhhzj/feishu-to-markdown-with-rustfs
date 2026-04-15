# Care-Dev Upload Flow

Use this reference when the conversion needs to upload images from a Feishu document into the same RustFS-backed flow the knowledge column editor already uses.

## Current Frontend Behavior

- `care-dev-ui/care-dev-op/apps/web-antd/.env.development` sets `VITE_GLOB_API_URL=http://127.0.0.1:48080/admin-api`
- The same file sets `VITE_UPLOAD_TYPE=server`
- `care-dev-ui/care-dev-op/apps/web-antd/src/components/upload/use-upload.ts` routes image uploads to the backend when upload type is `server`
- `care-dev-ui/care-dev-op/apps/web-antd/src/views/knowledge/column/detail.vue` builds the directory as:
  - `knowledge/column/{columnId}/article/{articleId}` for existing articles
  - `knowledge/column/{columnId}/temp` otherwise
- `detail.vue` strips `?X-Amz-*` style query params and keeps only the stable public URL before inserting Markdown

## Current Backend Behavior

- `POST /infra/file/upload` is implemented in `yudao-module-infra/src/main/java/cn/iocoder/yudao/module/infra/controller/admin/file/FileController.java`
- `FileServiceImpl.createFile(...)` builds `directory/name`, uploads to the master file client, stores a record in `infra_file`, and returns the URL
- `application-local.yaml` shows the local file config defaults to S3-compatible RustFS with public access enabled

## Request Shape

The admin upload request uses multipart form data:

- `file`: binary file
- `directory`: optional upload directory

Auth and headers should match the existing admin UI:

- `Authorization: Bearer <accessToken>`
- `tenant-id`: only when tenant mode is enabled
- `visit-tenant-id`: only when tenant mode is enabled

## Base URL Rules For The Script

The conversion script accepts any of these and normalizes them to the upload endpoint:

- backend root such as `http://127.0.0.1:48080`
- admin API root such as `http://127.0.0.1:48080/admin-api`
- full upload endpoint such as `http://127.0.0.1:48080/admin-api/infra/file/upload`

## Output Rules

- Normalize successful upload URLs to `origin + pathname`
- Emit Markdown image syntax with a default alt text of `image`
- Prefer public stable URLs over temporary signed URLs

## Practical Reminder

If the user only wants paste-ready Markdown and does not yet have a valid backend token, run the conversion in local-image mode first, then re-run with upload enabled once auth is available.
