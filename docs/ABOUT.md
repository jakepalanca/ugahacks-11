# ABOUT.md

## Project Overview

SageMaker async inference deployment for **Hunyuan3D-2.1**. The pipeline converts a 2D image into a textured 3D GLB in two stages:

1. **Shape** - generate mesh
2. **Paint** - apply texture

The endpoint is configured for scale-to-zero (`MinInstanceCount=0`) to reduce idle cost, with cold starts that can take several minutes.

For service-by-service runbooks, see:
- `docs/services/README.md`

## Commands

```bash
# Deploy/update SageMaker runtime resources
export HF_TOKEN=<token> # optional but recommended for model downloads
export ASYNC_IO_BUCKET=<pipeline-bucket>
./scripts/sagemaker_runtime/setup_endpoint.sh
INSTANCE_TYPE=ml.g6.2xlarge ./scripts/sagemaker_runtime/setup_endpoint.sh
CLEAR_CREATEBUILD_QUEUE=0 ./scripts/sagemaker_runtime/setup_endpoint.sh

# Submit a full pipeline job (shape + paint)
python scripts/sagemaker_runtime/submit_job.py \
  --input s3://<images-bucket>/inputs/image.png \
  --output-prefix s3://<pipeline-bucket>/jobs/job123 \
  --io-bucket <pipeline-bucket>

# Submit single stage
python scripts/sagemaker_runtime/submit_job.py \
  --stage shape \
  --input s3://<images-bucket>/inputs/image.png \
  --output s3://<pipeline-bucket>/jobs/job123/shape.glb \
  --io-bucket <pipeline-bucket>

python scripts/sagemaker_runtime/submit_job.py \
  --stage paint \
  --input s3://<images-bucket>/inputs/image.png \
  --shape s3://<pipeline-bucket>/jobs/job123/shape.glb \
  --output s3://<pipeline-bucket>/jobs/job123/textured.glb \
  --io-bucket <pipeline-bucket>

# Integration test (requires TEST_INPUT_BUCKET/INPUT_BUCKET and IO bucket env)
INPUT_BUCKET=<images-bucket> OUTPUT_BUCKET=<pipeline-bucket> ASYNC_IO_BUCKET=<pipeline-bucket> \
./scripts/sagemaker_runtime/test_endpoint.sh

# Check endpoint status
aws sagemaker describe-endpoint --endpoint-name ${ENDPOINT_NAME:-hunyuan3d-async-v2} --region ${AWS_REGION:-us-east-1}

# Clear queue + release worker lock
AWS_REGION=<region> JOB_TABLE=<jobs-table> ./minecraft_runtime/scripts/clear_createbuild_queue.sh

# On EC2 (SSH): sync latest assets and restart Paper server
sudo /usr/local/bin/minecraft-sync-assets.sh
sudo systemctl restart minecraft.service

# Tear down SageMaker runtime resources
./scripts/sagemaker_runtime/teardown_endpoint.sh
```

## Architecture

```text
scripts/sagemaker_runtime/submit_job.py -> SageMaker Async Endpoint -> container (sagemaker_runtime/inference_server.py)
                                               |-/ping
                                               '-/invocations (shape|paint)
```

- `sagemaker_runtime/inference_server.py`: Flask server inside SageMaker container.
- `scripts/sagemaker_runtime/submit_job.py`: async invocation client and poller.
- `sagemaker_runtime/Dockerfile`: image build for inference runtime.

## AWS Resources

Resource names are configurable via environment variables and CloudFormation parameters. Typical defaults:

- ECR repo: `hunyuan3d-sagemaker`
- SageMaker model: `hunyuan3d-model-v2`
- SageMaker endpoint config: `hunyuan3d-async-config-v2`
- SageMaker endpoint: `hunyuan3d-async-v2`
- SageMaker execution role: `hunyuan3d-sagemaker-role`

## S3 Buckets

Use two logical buckets:

### `<images-bucket>`
User-facing inputs, for example:
- `inputs/test_image.png`

### `<pipeline-bucket>`
Async I/O and generated artifacts:
- `async-input/`
- `async-output/`
- `async-failures/`
- `jobs/<job-id>/shape.glb`
- `jobs/<job-id>/textured.glb`

## Minecraft Flow

Implementation assets live under:

- `minecraft_runtime/plugin`
- `minecraft_runtime/lambda`
- `minecraft_runtime/ec2`
- `minecraft_runtime/server-assets`

End-to-end runtime:

1. Player runs `/createbuild` and sets an anchor.
2. Plugin gathers prompt + size.
3. Submit Lambda enqueues work in DynamoDB.
4. Worker Lambda calls:
   - text-to-image Lambda
   - SageMaker async endpoint
   - GLB-to-vox Lambda
5. Worker writes command batches to S3.
6. Plugin polls status Lambda and executes command batches.
