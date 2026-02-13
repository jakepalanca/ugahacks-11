# DynamoDB: CreateBuild Jobs Table

## Purpose
Stores job lifecycle state, progress, output references, and worker lock coordination.

## CloudFormation Resource
- Table name pattern: `${NamePrefix}-jobs`
- Partition key: `job_id` (string)
- Billing: on-demand (`PAY_PER_REQUEST`)
- TTL attribute: `expires_at`

## Data Shape
Common job row fields:
- `job_id`, `status`, `created_at`, `updated_at`, `expires_at`
- input metadata: `prompt`, `size`, `anchor`, player info
- progress fields: `progress_stage`, `progress_message`
- output references: `image_s3`, `shape_s3`, `textured_s3`, `source_mcfunction_s3`

Special lock row:
- `job_id = __worker_lock__` (default key)
- tracks `owner_job_id` and `locked_until`

## Access Patterns
- submit Lambda: create job and attempt start
- worker Lambda: lock + status transitions + output updates + queue scan
- status Lambda: point reads for poll responses
