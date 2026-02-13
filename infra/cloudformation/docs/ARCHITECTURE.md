# Architecture and Runtime Flow

## End-to-End Runtime
1. Player uses `/createbuild` in Minecraft.
2. Plugin calls `POST /build`.
3. Submit Lambda writes a job row in DynamoDB and kicks worker Lambda.
4. Worker Lambda pipeline:
- invoke text-to-image Lambda (Titan v2 + background removal)
- call SageMaker async endpoint (image -> textured GLB)
- invoke GLB-to-vox Lambda (mesh -> `.mcfunction` source)
- anchor/translate/chunk commands and upload batch artifacts to S3
5. Plugin polls `GET /build/status/{jobId}`.
6. Status Lambda returns signed command batch URLs.
7. Plugin downloads and executes block commands.

## Stack Topology
- `S3 assets bucket`: server config/plugins/custom/bootstrap script
- `S3 pipeline bucket`: images, async payloads/results, voxel outputs, command batches
- `DynamoDB jobs table`: state machine + progress tracking
- `API Gateway`: ingress for plugin requests
- `Lambdas`: orchestration + model adapters
- `SageMaker` (optional in stack): model + endpoint config + endpoint
- `EC2 Minecraft host`: Paper server with periodic S3 asset sync

## EC2 Bootstrap Contract
The EC2 instance user-data is minimal and pulls the canonical bootstrap script from S3:
- `s3://<assets-bucket>/<assets-prefix>/bootstrap/paper_user_data.sh`

That script installs Java/Paper, syncs server assets, writes plugin config with API URLs, and starts systemd units.

## Teardown Behavior
`destroy.sh` does two important things before deleting stack:
1. empties assets and pipeline buckets
2. runs CloudFormation stack deletion

This avoids the common S3 non-empty deletion failure and provides a full clean teardown path.
