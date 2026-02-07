# ABOUT.md

## Project Overview

SageMaker async inference deployment for **Hunyuan3D-2.1** (Tencent's 3D model generation system). Converts 2D images into textured 3D GLB models via a two-stage pipeline:

1. **Shape** — generates a 3D mesh from an input image
2. **Paint** — applies realistic texturing to the mesh

Uses scale-to-zero auto-scaling (MinInstanceCount=0) so there's no cost when idle, with ~5-10 minute cold starts.

## Commands

```bash
# Deploy infrastructure (builds Docker image, pushes to ECR, creates SageMaker endpoint)
./setup-sagemaker.sh
INSTANCE_TYPE=ml.g6.2xlarge ./setup-sagemaker.sh   # use L4 GPU instead of A10G

# Submit a full pipeline job (shape + paint)
python submit-job.py --input s3://bucket/image.png --output-prefix s3://bucket/jobs/job123

# Submit single stage
python submit-job.py --stage shape --input s3://bucket/image.png --output s3://bucket/shape.glb
python submit-job.py --stage paint --input s3://bucket/image.png --shape s3://bucket/shape.glb --output s3://bucket/textured.glb

# Integration test (requires test image at s3://hackathon-images-67/inputs/test_image.png)
./test-endpoint.sh

# Check endpoint status
aws sagemaker describe-endpoint --endpoint-name hunyuan3d-async-v2 --region us-east-1

# Tear down all SageMaker resources
./cleanup-sagemaker.sh
```

## Architecture

```
submit-job.py ──► SageMaker Async Endpoint ──► Docker container (serve.py)
                  (invoke_endpoint_async)        ├── /ping (health check)
                                                 └── /invocations (inference)
                                                      ├── process_shape() → GLB mesh
                                                      └── process_paint() → textured GLB
```

**`serve.py`** — Flask server running inside the SageMaker container on port 8080. Lazy-loads ML pipelines on first use (shape via `Hunyuan3DDiTFlowMatchingPipeline`, paint via `Hunyuan3DPaintPipeline`). Automatically removes backgrounds from RGB images using `rembg`. All I/O goes through S3; request payload specifies `stage`, `input_s3`, `output_s3`, and optionally `shape_s3`.

**`submit-job.py`** — CLI client that uploads request JSON to S3, invokes the async endpoint, and polls for results. For full pipeline mode, runs shape then paint sequentially. Uses `hackathon-jobs-67` bucket for async I/O.

**`Dockerfile.sagemaker`** — Based on `nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04`. Clones the official Hunyuan3D-2.1 repo, patches `bpy` and `pymeshlab` versions, installs PyTorch 2.5.1 with CUDA 12.4.

## AWS Resources

- **S3 buckets**: `hackathon-images-67` (input images), `hackathon-jobs-67` (async I/O, job outputs)
- **ECR repo**: `hunyuan3d-sagemaker`
- **SageMaker model**: `hunyuan3d-model-v2`
- **SageMaker endpoint config**: `hunyuan3d-async-config-v2`
- **SageMaker endpoint**: `hunyuan3d-async-v2` on `ml.g5.2xlarge` (24GB A10G GPU)
- **IAM role**: `hunyuan3d-sagemaker-role`
- **Default region**: `us-east-1`, account `418087252133`
