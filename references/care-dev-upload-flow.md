# Care-Dev Upload Flow

Read this reference only when the conversion needs to upload extracted images through the Care-Dev backend instead of saving them locally or uploading directly to RustFS.

## When To Read This

- The user explicitly wants Care-Dev upload mode
- `--base-url`, `--token`, or upload directory behavior is unclear
- Upload succeeds locally in the editor but fails in the conversion script

## Use This Reference

1. Confirm the user really wants Care-Dev upload mode.
2. Confirm `base-url`, token, and upload directory are all available.
3. Normalize the provided base URL to the upload endpoint form.
4. Send the file with multipart form data and the expected auth headers.
5. If auth is missing, fall back to local-image mode and retry upload later.

## Required Inputs

Care-Dev upload mode needs:

- backend base URL, admin API root, or full upload endpoint
- access token
- upload directory
- optional tenant headers in multi-tenant deployments

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

## Security Notes

- Do not hardcode backend tokens into documentation or committed config files
- Prefer env vars such as `CARE_DOCX_BASE_URL`, `CARE_DOCX_TOKEN`, and `CARE_DOCX_DIRECTORY`
- Only send tenant headers when the deployment actually requires them

## Fallback Guidance

If the user only wants paste-ready Markdown and does not yet have a valid backend token:

1. Run once in local-image mode
2. Confirm the Markdown structure looks correct
3. Re-run with upload enabled after auth becomes available

## Quick Checks

Run these checks before blaming the script:

1. Check whether the same token works in the admin UI.
2. Check whether the directory matches article or temp conventions.
3. Check whether the provided base URL points to backend root, admin API root, or full upload endpoint.
4. Check whether tenant headers are required in the current deployment.
