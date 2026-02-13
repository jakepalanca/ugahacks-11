# Lambda: CreateBuild GLB-to-Vox

## Purpose
Converts mesh output (`.glb/.obj/.ply/.stl`) into Minecraft `setblock` commands and uploads `.mcfunction` output.

## CloudFormation Resource
- Function name pattern: `${NamePrefix}-glb2vox-lambda`
- Handler: `createbuild_glb_to_vox.lambda_handler`
- Runtime: `python3.11`
- Source: `minecraft_runtime/lambda/createbuild_glb_to_vox.py`

## Trigger
- Synchronous invoke from worker Lambda.

## Input
Payload keys accepted:
- `s3_uri` (required mesh URI)
- `size` (`small|medium|large`)
- `jobId`
- optional `source_image_s3`/equivalent image references

## Output
Returns a response containing S3 location of generated `.mcfunction` file plus block metadata.

## Key Environment Variables
- layer and output: `LAYER_BUCKET`, `LAYER_ZIP_KEY`, `OUTPUT_BUCKET`, `OUTPUT_PREFIX`
- sizing: `SMALL_TARGET_SPAN`, `MEDIUM_TARGET_SPAN`, `LARGE_TARGET_SPAN`
- sampling: `SMALL_SURFACE_SAMPLES`, `MEDIUM_SURFACE_SAMPLES`, `LARGE_SURFACE_SAMPLES`
- morphology/color/transparency tuning values (see template env block)

## IAM Permissions Required
- S3 read/write on pipeline bucket.
- S3 get-object on dependency layer zip (`GlbLayerObjectKey`).

## Notes
- Lambda dynamically loads scientific dependencies from an S3 zip at runtime.
- Supports alpha-aware material mapping and component cleanup before voxel emission.
