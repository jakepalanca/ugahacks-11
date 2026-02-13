# S3: Assets and Pipeline Buckets

## Assets Bucket
Name pattern:
- `${NamePrefix}-${AccountId}-${Region}-assets`

Purpose:
- stores Minecraft server bootstrap and synced assets
- consumed by EC2 bootstrap and periodic sync

Typical key layout:
- `${AssetsPrefix}/bootstrap/paper_user_data.sh`
- `${AssetsPrefix}/plugins/...`
- `${AssetsPrefix}/config/...`

## Pipeline Bucket
Name pattern:
- `${NamePrefix}-${AccountId}-${Region}-pipeline`

Purpose:
- stores image, mesh, async payloads, voxel outputs, and command batches

Typical key layout:
- `async-input/`
- `async-output/`
- `async-failures/`
- `jobs/<jobId>/shape.glb`
- `jobs/<jobId>/textured.glb`
- `${CommandPrefix}/<jobId>/batch-00001.mcfunction`

## Security Defaults
Both buckets are provisioned with:
- public access blocks enabled
- SSE-S3 encryption (`AES256`)
- bucket owner enforced ownership controls
