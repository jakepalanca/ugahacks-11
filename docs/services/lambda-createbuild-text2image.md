# Lambda: CreateBuild Text-to-Image

## Purpose
Generates a transparent-background PNG from prompt text using Titan Image Generator v2.

Pipeline:
1. Titan TEXT_IMAGE generation
2. Titan BACKGROUND_REMOVAL
3. Upload PNG to pipeline bucket

## CloudFormation Resource
- Function name pattern: `${NamePrefix}-text2image-lambda`
- Handler: `createbuild_text_to_image.lambda_handler`
- Runtime: `python3.12`
- Source: `minecraft_runtime/lambda/createbuild_text_to_image.py`

## Trigger
- Synchronous invoke from worker Lambda.

## Input
Common fields:
- `prompt` (or `input`)
- optional tuning: `seed`, `width`, `height`, `cfgScale`, `negativeText`

## Output
Returns:
- `bucket`
- `key`

Worker converts that to `s3://bucket/key` for downstream SageMaker stages.

## Key Environment Variables
- `BEDROCK_REGION`
- `OUT_BUCKET`
- `OUT_PREFIX`
- `MODEL_ID` (optional override; default Titan v2)

## IAM Permissions Required
- Bedrock: `bedrock:InvokeModel` for Titan Image Generator v2.
- S3: `PutObject` into pipeline bucket.
