# Lambda: CreateBuild Submit

## Purpose
Receives new build requests (`POST /build`), validates payload/auth, stores a new job row, and attempts to start worker execution.

## CloudFormation Resource
- Function name pattern: `${NamePrefix}-submit-lambda`
- Handler: `createbuild_submit.handler`
- Runtime: `python3.12`
- Source: `minecraft_runtime/lambda/createbuild_submit.py`

## Trigger
- API Gateway route: `POST /build`

## Request Payload
JSON body fields:
- `prompt` (required string)
- `size` (required: `small|medium|large`)
- `anchor` (required object with numeric `x`, `y`, `z`)
- `world` (optional; default from env)
- `playerUuid`, `playerName` (optional metadata)

Authorization:
- Requires `Authorization: Bearer <token>` by default.
- If `ALLOW_UNAUTHENTICATED_REQUESTS=1`, auth checks are bypassed (testing only).

## Response
- `202` with `{ jobId, status, started }` on accepted request.
- `400` for validation failures.
- `401` when token auth fails.
- `503` when auth is required but `API_TOKEN` is not configured.
- `500` for server-side errors.

## Key Environment Variables
- `JOB_TABLE`
- `WORKER_FUNCTION`
- `JOB_TTL_SECONDS`
- `DEFAULT_WORLD`
- `WORKER_LOCK_KEY`
- `WORKER_LOCK_TTL_SECONDS`
- `API_TOKEN`
- `ALLOW_UNAUTHENTICATED_REQUESTS`

## IAM Permissions Required
- DynamoDB: `GetItem`, `PutItem`, `UpdateItem` on jobs table.
- Lambda: `InvokeFunction` on worker Lambda.
