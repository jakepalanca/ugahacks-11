#!/bin/bash
set -euo pipefail
export AWS_PAGER=""

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CFN_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
ROOT_DIR="$(cd "${CFN_DIR}/../.." && pwd)"
TEMPLATE_FILE="${CFN_DIR}/template.yaml"
PACKAGED_TEMPLATE="${CFN_DIR}/build/packaged-template.yaml"
LAMBDA_BUILD_ROOT="${CFN_DIR}/build/lambda"

REGION="${AWS_REGION:-us-east-1}"
NAME_PREFIX="${NAME_PREFIX:-createbuild-prod}"
STACK_NAME="${STACK_NAME:-${NAME_PREFIX}-stack}"
ASSETS_PREFIX="${ASSETS_PREFIX:-minecraft/prod}"
API_STAGE_NAME="${API_STAGE_NAME:-prod}"
API_TOKEN="${API_TOKEN:-}"
ALLOW_UNAUTHENTICATED_API="${ALLOW_UNAUTHENTICATED_API:-false}"

EXISTING_SAGEMAKER_ENDPOINT_NAME="${EXISTING_SAGEMAKER_ENDPOINT_NAME:-hunyuan3d-async-v2}"
SAGEMAKER_IMAGE_URI="${SAGEMAKER_IMAGE_URI:-}"
SAGEMAKER_INSTANCE_TYPE="${SAGEMAKER_INSTANCE_TYPE:-ml.g5.2xlarge}"

WORKER_MEMORY_SIZE="${WORKER_MEMORY_SIZE:-3008}"
WORKER_RESERVED_CONCURRENCY="${WORKER_RESERVED_CONCURRENCY:--1}"

MINECRAFT_INSTANCE_TYPE="${MINECRAFT_INSTANCE_TYPE:-t3a.large}"
MINECRAFT_ROOT_VOLUME_GIB="${MINECRAFT_ROOT_VOLUME_GIB:-64}"
MINECRAFT_INGRESS_CIDR="${MINECRAFT_INGRESS_CIDR:-0.0.0.0/0}"
ADMIN_INGRESS_CIDR="${ADMIN_INGRESS_CIDR:-0.0.0.0/0}"
EC2_KEY_PAIR_NAME="${EC2_KEY_PAIR_NAME:-}"
ALLOCATE_ELASTIC_IP="${ALLOCATE_ELASTIC_IP:-true}"

GLB_LAYER_BUCKET_NAME="${GLB_LAYER_BUCKET_NAME:-}"
GLB_LAYER_OBJECT_KEY="${GLB_LAYER_OBJECT_KEY:-layers/sci_tri_num_pillow.zip}"
GLB_LAYER_ZIP_LOCAL_PATH="${GLB_LAYER_ZIP_LOCAL_PATH:-}"

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

stack_output() {
  local key="$1"
  aws cloudformation describe-stacks \
    --region "${REGION}" \
    --stack-name "${STACK_NAME}" \
    --query "Stacks[0].Outputs[?OutputKey=='${key}'].OutputValue | [0]" \
    --output text
}

ensure_bucket() {
  local bucket="$1"
  if aws s3api head-bucket --bucket "${bucket}" >/dev/null 2>&1; then
    return 0
  fi

  if [ "${REGION}" = "us-east-1" ]; then
    aws s3api create-bucket --bucket "${bucket}" --region "${REGION}" >/dev/null
  else
    aws s3api create-bucket \
      --bucket "${bucket}" \
      --region "${REGION}" \
      --create-bucket-configuration "LocationConstraint=${REGION}" >/dev/null
  fi

  aws s3api put-public-access-block \
    --bucket "${bucket}" \
    --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true >/dev/null
}

prepare_lambda_sources() {
  local source_dir="${ROOT_DIR}/minecraft_runtime/lambda"

  rm -rf "${LAMBDA_BUILD_ROOT}"
  mkdir -p \
    "${LAMBDA_BUILD_ROOT}/submit" \
    "${LAMBDA_BUILD_ROOT}/status" \
    "${LAMBDA_BUILD_ROOT}/worker" \
    "${LAMBDA_BUILD_ROOT}/text_to_image" \
    "${LAMBDA_BUILD_ROOT}/glb_to_vox"

  cp "${source_dir}/createbuild_submit.py" "${LAMBDA_BUILD_ROOT}/submit/"
  cp "${source_dir}/createbuild_status.py" "${LAMBDA_BUILD_ROOT}/status/"
  cp "${source_dir}/createbuild_worker.py" "${LAMBDA_BUILD_ROOT}/worker/"
  cp "${source_dir}/hunyuan_async.py" "${LAMBDA_BUILD_ROOT}/worker/"
  cp "${source_dir}/createbuild_text_to_image.py" "${LAMBDA_BUILD_ROOT}/text_to_image/"
  cp "${source_dir}/createbuild_glb_to_vox.py" "${LAMBDA_BUILD_ROOT}/glb_to_vox/"
}

require_cmd aws
require_cmd jq
require_cmd mvn

if [ -z "${API_TOKEN}" ] && [ "${ALLOW_UNAUTHENTICATED_API}" != "true" ]; then
  echo "API_TOKEN is required unless ALLOW_UNAUTHENTICATED_API=true." >&2
  echo "Set API_TOKEN to a strong random token before deploying." >&2
  exit 1
fi

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text --region "${REGION}")"
PACKAGING_BUCKET="${PACKAGING_BUCKET:-${NAME_PREFIX}-${ACCOUNT_ID}-${REGION}-cfn-artifacts}"

echo "==> Building Minecraft plugin jar"
"${ROOT_DIR}/minecraft_runtime/plugin/build_plugin.sh"

echo "==> Preparing Lambda source bundles"
prepare_lambda_sources

echo "==> Ensuring CloudFormation packaging bucket: ${PACKAGING_BUCKET}"
ensure_bucket "${PACKAGING_BUCKET}"

echo "==> Packaging CloudFormation template"
aws cloudformation package \
  --region "${REGION}" \
  --template-file "${TEMPLATE_FILE}" \
  --s3-bucket "${PACKAGING_BUCKET}" \
  --s3-prefix "${NAME_PREFIX}/cloudformation" \
  --output-template-file "${PACKAGED_TEMPLATE}"

echo "==> Deploying stack: ${STACK_NAME}"
aws cloudformation deploy \
  --region "${REGION}" \
  --stack-name "${STACK_NAME}" \
  --template-file "${PACKAGED_TEMPLATE}" \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
    "NamePrefix=${NAME_PREFIX}" \
    "AssetsPrefix=${ASSETS_PREFIX}" \
    "ApiStageName=${API_STAGE_NAME}" \
    "ApiToken=${API_TOKEN}" \
    "AllowUnauthenticatedApi=${ALLOW_UNAUTHENTICATED_API}" \
    "ExistingSageMakerEndpointName=${EXISTING_SAGEMAKER_ENDPOINT_NAME}" \
    "SageMakerImageUri=${SAGEMAKER_IMAGE_URI}" \
    "SageMakerInstanceType=${SAGEMAKER_INSTANCE_TYPE}" \
    "WorkerMemorySize=${WORKER_MEMORY_SIZE}" \
    "WorkerReservedConcurrency=${WORKER_RESERVED_CONCURRENCY}" \
    "MinecraftInstanceType=${MINECRAFT_INSTANCE_TYPE}" \
    "MinecraftRootVolumeGiB=${MINECRAFT_ROOT_VOLUME_GIB}" \
    "MinecraftIngressCidr=${MINECRAFT_INGRESS_CIDR}" \
    "AdminIngressCidr=${ADMIN_INGRESS_CIDR}" \
    "Ec2KeyPairName=${EC2_KEY_PAIR_NAME}" \
    "AllocateElasticIp=${ALLOCATE_ELASTIC_IP}" \
    "GlbLayerBucketName=${GLB_LAYER_BUCKET_NAME}" \
    "GlbLayerObjectKey=${GLB_LAYER_OBJECT_KEY}"

ASSET_BUCKET="$(stack_output AssetsBucketName)"
PIPELINE_BUCKET="$(stack_output PipelineBucketName)"
SUBMIT_URL="$(stack_output SubmitUrl)"
STATUS_URL_BASE="$(stack_output StatusUrlBase)"
MC_PUBLIC_IP="$(stack_output MinecraftPublicIp)"
SAGEMAKER_ENDPOINT_IN_USE="$(stack_output SageMakerEndpointNameInUse)"

echo "==> Syncing Minecraft server assets to stack bucket"
AWS_REGION="${REGION}" "${ROOT_DIR}/minecraft_runtime/ec2/push_assets_to_s3.sh" "${ASSET_BUCKET}" "${ASSETS_PREFIX}"

if [ -n "${GLB_LAYER_ZIP_LOCAL_PATH}" ]; then
  if [ ! -f "${GLB_LAYER_ZIP_LOCAL_PATH}" ]; then
    echo "GLB_LAYER_ZIP_LOCAL_PATH does not exist: ${GLB_LAYER_ZIP_LOCAL_PATH}" >&2
    exit 1
  fi

  TARGET_LAYER_BUCKET="${GLB_LAYER_BUCKET_NAME:-${PIPELINE_BUCKET}}"
  echo "==> Uploading GLB voxel dependency layer zip to s3://${TARGET_LAYER_BUCKET}/${GLB_LAYER_OBJECT_KEY}"
  aws s3 cp "${GLB_LAYER_ZIP_LOCAL_PATH}" "s3://${TARGET_LAYER_BUCKET}/${GLB_LAYER_OBJECT_KEY}" --region "${REGION}"
else
  TARGET_LAYER_BUCKET="${GLB_LAYER_BUCKET_NAME:-${PIPELINE_BUCKET}}"
  if ! aws s3api head-object --bucket "${TARGET_LAYER_BUCKET}" --key "${GLB_LAYER_OBJECT_KEY}" --region "${REGION}" >/dev/null 2>&1; then
    echo "WARN: GLB layer zip not found at s3://${TARGET_LAYER_BUCKET}/${GLB_LAYER_OBJECT_KEY}" >&2
    echo "      Upload it with GLB_LAYER_ZIP_LOCAL_PATH=/absolute/path/to/sci_tri_num_pillow.zip and rerun deploy." >&2
  fi
fi

echo
echo "Deployment complete"
echo "  Stack:               ${STACK_NAME}"
echo "  Region:              ${REGION}"
echo "  Assets bucket:       ${ASSET_BUCKET}"
echo "  Pipeline bucket:     ${PIPELINE_BUCKET}"
echo "  Submit URL:          ${SUBMIT_URL}"
echo "  Status URL base:     ${STATUS_URL_BASE}"
echo "  SageMaker endpoint:  ${SAGEMAKER_ENDPOINT_IN_USE}"
echo "  Minecraft public IP: ${MC_PUBLIC_IP}"
echo
echo "Next actions:"
echo "  1) Wait 2-5 minutes for EC2 first boot and plugin sync."
echo "  2) Connect Minecraft client to: ${MC_PUBLIC_IP}:25565"
echo "  3) Optional SSH: ssh ec2-user@${MC_PUBLIC_IP}"
echo "  4) Destroy everything: ${CFN_DIR}/scripts/destroy.sh"
