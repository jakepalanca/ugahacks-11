# EC2: Minecraft Server Host

## Purpose
Runs Paper Minecraft server and the CreateBuild plugin that calls backend APIs.

## CloudFormation Resources
- VPC/networking resources (`${NamePrefix}-vpc`, `${NamePrefix}-public-a`, `${NamePrefix}-mc-sg`)
- instance role/profile (`${NamePrefix}-minecraft-role`, `${NamePrefix}-minecraft-profile`)
- instance (`${NamePrefix}-minecraft-instance`)
- optional EIP (`${NamePrefix}-minecraft-eip`)

Connection target:
- `MinecraftPublicIp` stack output (`<ip>:25565`).
- When `AllocateElasticIp=true`, this output is the Elastic IP.

## Bootstrap Flow
User-data does the following:
1. install AWS CLI
2. fetch bootstrap script from assets bucket
3. execute script with API URLs and token env vars

Canonical bootstrap source:
- `minecraft_runtime/ec2/paper_user_data.sh`

## Runtime Asset Sync
EC2 periodically pulls assets from S3 using:
- `minecraft_runtime/ec2/push_assets_to_s3.sh` (upload side)
- `/usr/local/bin/minecraft-sync-assets.sh` (instance side)

## Required IAM Access
Instance role grants read-only access to assets bucket:
- `s3:ListBucket`
- `s3:GetObject`
