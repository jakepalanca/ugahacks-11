# Minecraft Build Pipeline

This folder adds the requested hackathon `createbuild` flow:

1. Player runs `/createbuild <prompt>` and receives a custom enchanted builder stick.
2. Player taps a block to set anchor.
3. Plugin asks for prompt + size (`small`, `medium`, `large`).
4. Plugin calls submit Lambda.
5. Worker Lambda calls:
   - `hackathon_textToImage` Lambda (prompt -> image S3)
   - Hunyuan3D SageMaker async endpoint (image -> textured GLB)
   - `hackathon_glb_to_vox` Lambda (textured GLB -> source `.mcfunction`)
   - Anchor translation to tapped block (`center/center/bottom`) + chunked `.mcfunction` batches
6. Plugin polls status Lambda, downloads `.mcfunction` batches, and executes the contained `setblock` commands.

## Folder Layout

- `plugin/`: Paper plugin source, config, and build script
- `lambda/`: submit/status/worker lambdas + Hunyuan async + mcfunction translation helpers
- `ec2/`: EC2 user-data bootstrap and S3 sync script
- `server-assets/`: server/plugin/custom artifacts to sync into S3 and then to the Paper server
- `deploy-aws.sh`: one-command deploy for DynamoDB + Lambda + API Gateway + plugin config + S3 assets

## One-Command Deploy (Recommended)

```bash
cd /Users/jake/hunyuan3d-batch/minecraft
./deploy-aws.sh
```

Defaults target the live hackathon environment in `us-east-1`:

- S3 assets: `s3://minecraft-config-and-plugins/minecraft/prod/`
- text-to-image Lambda: `hackathon_textToImage`
- GLB-to-vox Lambda: `hackathon_glb_to_vox`
- Hunyuan endpoint: `hunyuan3d-async-v2`

Useful overrides:

```bash
AWS_REGION=us-east-1 \
ASSET_BUCKET=minecraft-config-and-plugins \
ASSET_PREFIX=minecraft/prod \
CREATEBUILD_API_TOKEN=your-shared-token \
./deploy-aws.sh
```

## Plugin Build

```bash
cd /Users/jake/hunyuan3d-batch/minecraft/plugin
./build-plugin.sh
```

This writes `CreateBuildPlugin.jar` to:

- `/Users/jake/hunyuan3d-batch/minecraft/server-assets/plugins/CreateBuildPlugin.jar`

## Lambda Deploy Notes

Create three Lambda handlers:

- submit: `createbuild_submit.handler`
- status: `createbuild_status.handler`
- worker: `createbuild_worker.handler`

Minimum worker env vars:

- `JOB_TABLE`
- `TEXT2IMAGE_FUNCTION=hackathon_textToImage`
- `GLB_TO_VOX_FUNCTION=hackathon_glb_to_vox`
- `HUNYUAN_ENDPOINT=hunyuan3d-async-v2`
- `HUNYUAN_IO_BUCKET=hackathon-jobs-67`
- `ARTIFACT_BUCKET=hackathon-jobs-67`
- `COMMAND_BUCKET=<minecraft-command-bucket>`
- `COMMAND_PREFIX=minecraft-builds`

API Gateway routing example:

- `POST /build` -> submit lambda
- `GET /build/status/{jobId}` -> status lambda

## EC2 User Data + S3 Assets

1. Upload assets:

```bash
cd /Users/jake/hunyuan3d-batch/minecraft/ec2
./push-assets-to-s3.sh <bucket> <prefix>
```

2. In your EC2 launch template user-data, use:

- `/Users/jake/hunyuan3d-batch/minecraft/ec2/paper-user-data.sh`

3. Set at launch:

- `ASSET_BUCKET`
- `ASSET_PREFIX`
- `CREATEBUILD_SUBMIT_URL`
- `CREATEBUILD_STATUS_URL`
- `CREATEBUILD_API_TOKEN` (optional)

The user-data script installs Java + Paper, syncs `server/`, `plugins/`, and `custom/` from S3, and keeps them updated with a systemd timer.
