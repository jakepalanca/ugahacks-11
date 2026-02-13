# Lambda: CreateBuild Worker

## Purpose
Executes the full build pipeline for one job:
1. text prompt -> image (text2image Lambda)
2. image -> 3D mesh (SageMaker async endpoint)
3. mesh -> `.mcfunction` commands (glb2vox Lambda)
4. anchor/chunk commands and upload command batches
5. write final status and metadata to DynamoDB

## CloudFormation Resource
- Function name pattern: `${NamePrefix}-worker-lambda`
- Handler: `createbuild_worker.handler`
- Runtime: `python3.12`
- Source: `minecraft_runtime/lambda/createbuild_worker.py`

## Trigger
- Asynchronous invoke from submit Lambda.
- Can also self-invoke to drain queued jobs (single-active-worker locking model).

## Input Event
- `{ "job_id": "<id>" }`

## Key Outputs
Persists to DynamoDB job row:
- `status`: `QUEUED|STARTING|RUNNING|SUCCEEDED|FAILED`
- `shape_s3`, `textured_s3`, `source_mcfunction_s3`
- command batch metadata (`command_bucket`, `command_prefix`, `batch_count`)
- dimensions, voxel count, and placement metadata

## Key Environment Variables
- `JOB_TABLE`
- `TEXT2IMAGE_FUNCTION`
- `GLB_TO_VOX_FUNCTION`
- `HUNYUAN_ENDPOINT`
- `HUNYUAN_IO_BUCKET`
- `ARTIFACT_BUCKET`
- `COMMAND_BUCKET`, `COMMAND_PREFIX`, `COMMAND_CHUNK_SIZE`
- `PLACEMENT_PASSES`
- `ENABLE_FORCELOAD`, `MAX_FORCELOAD_CHUNKS`
- `ORIENTATION_ROTATE_Y_QUARTER_TURNS`
- `SAGEMAKER_TIMEOUT_SECONDS`, `SAGEMAKER_POLL_SECONDS`
- `WORKER_LOCK_KEY`, `WORKER_LOCK_TTL_SECONDS`

## IAM Permissions Required
- DynamoDB: `GetItem`, `UpdateItem`, `Scan` on jobs table.
- Lambda: invoke worker/text2image/glb2vox functions.
- S3: list/get/put on pipeline bucket.
- SageMaker: `InvokeEndpoint`, `InvokeEndpointAsync` on target endpoint.

## Notes
- Enforces one active worker with a DynamoDB lock item (`WORKER_LOCK_KEY`).
- Handles paint-stage GPU failures by falling back to shape-only mesh output.
