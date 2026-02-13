# Normalized Naming Schema

This project uses a single normalized schema for all AWS resource families.

## Core Pattern

`<name-prefix>-<component>-<resource-type>`

Where:
- `name-prefix` is the environment-scoped base (example: `createbuild-prod`)
- `component` describes the domain owner (`submit`, `worker`, `minecraft`, `sagemaker`, etc.)
- `resource-type` is explicit (`lambda`, `role`, `api`, `bucket`, `table`, `sg`, `profile`, etc.)

## Global Rules
- lowercase letters, numbers, and hyphens only
- no ambiguous short names (`app`, `main`, `temp`)
- resource name alone should communicate purpose and blast radius
- one `name-prefix` per deployed environment

## Resource Family Examples

### Lambda Functions
- `${NamePrefix}-submit-lambda`
- `${NamePrefix}-status-lambda`
- `${NamePrefix}-worker-lambda`
- `${NamePrefix}-text2image-lambda`
- `${NamePrefix}-glb2vox-lambda`

### IAM Roles / Policies
- `${NamePrefix}-submit-role`
- `${NamePrefix}-status-role`
- `${NamePrefix}-worker-role`
- `${NamePrefix}-text2image-role`
- `${NamePrefix}-glb2vox-role`
- `${NamePrefix}-minecraft-role`
- `${NamePrefix}-sagemaker-role`

### API Gateway
- `${NamePrefix}-http-api`
- stage name from `ApiStageName` (default `prod`)

### DynamoDB
- `${NamePrefix}-jobs`

### S3
- `${NamePrefix}-${AccountId}-${Region}-assets`
- `${NamePrefix}-${AccountId}-${Region}-pipeline`
- packaging bucket (outside stack): `${NamePrefix}-${AccountId}-${Region}-cfn-artifacts`

### SageMaker
- `${NamePrefix}-hunyuan-model`
- `${NamePrefix}-hunyuan-async-config`
- `${NamePrefix}-hunyuan-async`

### Network + Compute
- `${NamePrefix}-vpc`
- `${NamePrefix}-public-a`
- `${NamePrefix}-mc-sg`
- `${NamePrefix}-minecraft-instance`
- `${NamePrefix}-minecraft-role`
- `${NamePrefix}-minecraft-profile`

## Why This Schema
- stable, predictable names across environments
- easier IAM auditing and cost allocation
- deterministic integration wiring (logs, alarms, policy targeting, scripts)
- clean teardown and drift detection
