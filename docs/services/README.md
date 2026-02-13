# Service Documentation

This folder contains service-level documentation for each deployed backend component.

## Lambda Services
- `docs/services/lambda-createbuild-submit.md`
- `docs/services/lambda-createbuild-status.md`
- `docs/services/lambda-createbuild-worker.md`
- `docs/services/lambda-createbuild-text2image.md`
- `docs/services/lambda-createbuild-glb2vox.md`

## Model Inference
- `docs/services/sagemaker-hunyuan-async-endpoint.md`

## API + Data + Compute
- `docs/services/api-gateway-createbuild-http-api.md`
- `docs/services/dynamodb-createbuild-jobs-table.md`
- `docs/services/s3-buckets.md`
- `docs/services/ec2-minecraft-host.md`
- `docs/services/iam-roles-and-policies.md`

## Source of Truth
- Infrastructure definitions: `infra/cloudformation/template.yaml`
- Runtime Lambda code: `minecraft_runtime/lambda/`
- SageMaker container runtime: `sagemaker_runtime/`
