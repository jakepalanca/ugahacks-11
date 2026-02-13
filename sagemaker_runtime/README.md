# SageMaker Runtime

This folder contains the SageMaker runtime artifacts for the Hunyuan 3D conversion service used by the CreateBuild pipeline.

## Why this folder exists
This is not the Minecraft mod/plugin folder. It is the containerized AI backend runtime that SageMaker runs for shape/paint inference.

Keeping `Dockerfile` next to `inference_server.py` makes ownership and build context explicit.

## Files
- `Dockerfile`: container recipe for the SageMaker endpoint image
- `inference_server.py`: Flask inference server used by SageMaker async inference
- `iam_role_policy.json`: reference policy for SageMaker runtime permissions

## Related scripts
Operational scripts live under:
- `scripts/sagemaker_runtime/`

Primary entrypoints:
- `scripts/sagemaker_runtime/setup_endpoint.sh`
- `scripts/sagemaker_runtime/update_endpoint.sh`
- `scripts/sagemaker_runtime/test_endpoint.sh`
- `scripts/sagemaker_runtime/teardown_endpoint.sh`
- `scripts/sagemaker_runtime/submit_job.py`
