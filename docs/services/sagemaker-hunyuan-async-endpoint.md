# SageMaker: Hunyuan Async Endpoint

## Purpose
Runs heavy 3D inference stages that are too large for Lambda packaging/runtime limits.

Stages:
- `shape`: image -> mesh
- `paint`: mesh + image -> textured mesh

## CloudFormation Resources
Conditional on `SageMakerImageUri` being set:
- `${NamePrefix}-hunyuan-model`
- `${NamePrefix}-hunyuan-async-config`
- `${NamePrefix}-hunyuan-async`
- `${NamePrefix}-sagemaker-role`
- autoscaling target + policies + alarm for endpoint variant `AllTraffic` (min `0`, max `1`)

If `SageMakerImageUri` is empty, worker uses existing endpoint name from `ExistingSageMakerEndpointName`.

## Runtime Source
- Dockerfile: `sagemaker_runtime/Dockerfile`
- Inference server: `sagemaker_runtime/inference_server.py`
- Ops scripts: `scripts/sagemaker_runtime/`

## Async I/O Contract
Input payload object in S3:
- `stage`
- `input_s3`
- `output_s3`
- `shape_s3` (paint stage only)

Configured output paths:
- `s3://<pipeline-bucket>/async-output/`
- `s3://<pipeline-bucket>/async-failures/`

## Operational Scripts
- setup: `scripts/sagemaker_runtime/setup_endpoint.sh`
- update: `scripts/sagemaker_runtime/update_endpoint.sh`
- test: `scripts/sagemaker_runtime/test_endpoint.sh`
- teardown: `scripts/sagemaker_runtime/teardown_endpoint.sh`
