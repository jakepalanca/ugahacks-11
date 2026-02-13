# CloudFormation Deployment (Full Pipeline + Minecraft Server)

This folder contains a full-stack Infrastructure-as-Code deployment for the CreateBuild system.

It provisions:
- API + backend pipeline: `submit`, `status`, `worker`, `text2image`, `glb2vox` Lambdas
- API Gateway HTTP API (`POST /build`, `GET /build/status/{jobId}`)
- DynamoDB jobs table (with TTL)
- IAM roles/policies for Lambda, EC2, and (optionally) SageMaker
- S3 buckets for assets and pipeline artifacts
- VPC + subnet + security group + EC2 Minecraft server host
- Optional SageMaker endpoint/model/config (if `SAGEMAKER_IMAGE_URI` is provided)

## Naming
All resources use the normalized naming contract documented in:
- [`docs/NAMING.md`](docs/NAMING.md)

## File Layout
- `template.yaml`: full CloudFormation template
- `scripts/deploy.sh`: package + deploy + asset sync workflow
- `scripts/destroy.sh`: empty buckets + delete stack workflow
- `build/`: generated packaging artifacts (safe to delete)
- service-level docs: `../../docs/services/README.md`

## Prerequisites
- AWS CLI v2 authenticated (`aws sts get-caller-identity` works)
- Maven (`mvn`) for plugin build
- Existing Bedrock access to Titan Image Generator v2
- GLB voxel dependency zip (`sci_tri_num_pillow.zip`) either:
  - already in S3, or
  - available locally to upload during deploy

## Deploy
```bash
cd infra/cloudformation
./scripts/deploy.sh
```

Useful overrides:
```bash
AWS_REGION=us-east-1 \
NAME_PREFIX=createbuild-prod \
STACK_NAME=createbuild-prod-stack \
API_TOKEN=replace-with-strong-random-token \
MINECRAFT_INGRESS_CIDR=0.0.0.0/0 \
ADMIN_INGRESS_CIDR=0.0.0.0/0 \
GLB_LAYER_ZIP_LOCAL_PATH=/absolute/path/to/sci_tri_num_pillow.zip \
./scripts/deploy.sh
```

Security default:
- `API_TOKEN` is required by default.
- To intentionally disable auth for short-lived testing only, set `ALLOW_UNAUTHENTICATED_API=true`.

You can start from:
- `infra/cloudformation/env.example`

To let the stack create/manage the SageMaker endpoint:
```bash
SAGEMAKER_IMAGE_URI=<account-id>.dkr.ecr.<region>.amazonaws.com/hunyuan3d-sagemaker:v2 \
./scripts/deploy.sh
```

If `SAGEMAKER_IMAGE_URI` is empty, worker Lambda targets `EXISTING_SAGEMAKER_ENDPOINT_NAME` (default: `hunyuan3d-async-v2`).

Minecraft connection uses the `MinecraftPublicIp` stack output.

- If `ALLOCATE_ELASTIC_IP=true` (default), this is the Elastic IP.
- Connect from Minecraft Java client to `<MinecraftPublicIp>:25565`.

## Destroy Everything
```bash
cd infra/cloudformation
./scripts/destroy.sh
```

Optional: also delete the CloudFormation packaging bucket:
```bash
DELETE_PACKAGING_BUCKET=true ./scripts/destroy.sh
```

## How Deploy Script Works
1. Builds `CreateBuildPlugin.jar`.
2. Stages Lambda source into `build/lambda/*`.
3. Runs `aws cloudformation package` to upload Lambda artifacts.
4. Deploys stack with `CAPABILITY_NAMED_IAM`.
5. Reads stack outputs.
6. Syncs `minecraft_runtime/server-assets` + bootstrap script to stack assets bucket.
7. Optionally uploads GLB dependency layer zip.

## Notes
- `destroy.sh` empties stack buckets before deleting stack so teardown completes cleanly.
- Script and Python entrypoints were normalized to underscore naming and centralized paths.
