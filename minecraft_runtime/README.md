# Minecraft Build Pipeline

This folder contains the Minecraft-specific runtime components for the CreateBuild flow.

## Runtime Flow
1. Player runs `/createbuild` and gets the builder wand.
2. Player selects anchor block, prompt, and size.
3. Plugin calls backend API.
4. Backend pipeline generates image -> 3D mesh -> voxel commands.
5. Plugin polls job status and executes returned command batches.

## Canonical Script Names
- Queue clear: `minecraft_runtime/scripts/clear_createbuild_queue.sh`
- Plugin build: `minecraft_runtime/plugin/build_plugin.sh`
- Asset sync upload: `minecraft_runtime/ec2/push_assets_to_s3.sh`
- EC2 bootstrap script: `minecraft_runtime/ec2/paper_user_data.sh`

## Recommended Deploy Path
Use the stack lifecycle under:
- `infra/cloudformation/scripts/deploy.sh`
- `infra/cloudformation/scripts/destroy.sh`

## Folder Layout
- `plugin/`: Paper plugin source and build script
- `lambda/`: submit/status/worker/text-to-image/GLB-to-vox Lambda handlers
- `scripts/`: operational scripts for queue and job maintenance
- `ec2/`: bootstrap + S3 sync helpers
- `server-assets/`: server/plugin/custom files synced to the Minecraft host

## Common Operations

Build plugin jar:
```bash
cd minecraft_runtime/plugin
./build_plugin.sh
```

Clear queue and release worker lock:
```bash
AWS_REGION=us-east-1 JOB_TABLE=createbuild-jobs minecraft_runtime/scripts/clear_createbuild_queue.sh
```

Upload server assets to S3:
```bash
minecraft_runtime/ec2/push_assets_to_s3.sh <bucket> <prefix>
```

On EC2, sync and restart Paper:
```bash
sudo /usr/local/bin/minecraft-sync-assets.sh
sudo systemctl restart minecraft.service
sudo systemctl status minecraft.service --no-pager
```
