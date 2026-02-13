# Lambda: CreateBuild Status

## Purpose
Returns job status (`GET /build/status/{jobId}`), progress metadata, and command batch URLs when ready.

## CloudFormation Resource
- Function name pattern: `${NamePrefix}-status-lambda`
- Handler: `createbuild_status.handler`
- Runtime: `python3.12`
- Source: `minecraft_runtime/lambda/createbuild_status.py`

## Trigger
- API Gateway route: `GET /build/status/{jobId}`

## Request
- `jobId` from path parameter (`/build/status/{jobId}`) or query string.
- Requires bearer auth by default.
- If `ALLOW_UNAUTHENTICATED_REQUESTS=1`, auth checks are bypassed (testing only).

## Response
Includes current job fields:
- `status`, `progressStage`, `progressMessage`, `error`
- output pointers like `texturedGlbS3`, `sourceMcfunctionS3`
- on success: `commandBatchUrls` and counts

If signing is enabled (`SIGN_COMMAND_URLS=1`), URLs are presigned; otherwise S3 URIs are returned.

Auth failures return `401`. Misconfigured auth (required but missing token) returns `503`.

## Key Environment Variables
- `JOB_TABLE`
- `COMMAND_BUCKET`
- `SIGN_COMMAND_URLS`
- `PRESIGN_TTL_SECONDS`
- `API_TOKEN`
- `ALLOW_UNAUTHENTICATED_REQUESTS`

## IAM Permissions Required
- DynamoDB: `GetItem` on jobs table.
- S3: `ListBucket`, `GetObject` on pipeline bucket.
