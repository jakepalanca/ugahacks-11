# Repository Structure

This repo now uses a clearer split between application code and infrastructure lifecycle code.

## Top-Level Map
- `minecraft_runtime/`
  - plugin source (`plugin/`)
  - Lambda runtime source (`lambda/`)
  - EC2 bootstrap + asset sync helpers (`ec2/`)
  - server files synced to Minecraft host (`server-assets/`)
- `sagemaker_runtime/`
  - SageMaker container artifacts (`Dockerfile`, `inference_server.py`, IAM policy)
- `scripts/sagemaker_runtime/`
  - endpoint setup/update/test/teardown and async submit CLI scripts
- `infra/cloudformation/`
  - full-stack CloudFormation template and deploy/destroy scripts
  - naming and architecture docs
  - generated packaging artifacts under `build/`
- `docs/`
  - cross-cutting project docs (including this structure map and `ABOUT.md`)
  - per-service operational docs under `docs/services/`

## Deployment Paths
- New recommended path:
  - `infra/cloudformation/scripts/deploy.sh`
  - `infra/cloudformation/scripts/destroy.sh`

## Design Intent
- keep runtime source (`minecraft_runtime/`) stable
- keep SageMaker runtime isolated under `sagemaker_runtime/` so it is clearly separate from the Minecraft plugin/mod assets
- centralize IaC and lifecycle operations under `infra/cloudformation/`
- keep naming and architecture docs close to the template they describe
