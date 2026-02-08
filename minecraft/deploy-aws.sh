#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LAMBDA_DIR="${SCRIPT_DIR}/lambda"
PLUGIN_DIR="${SCRIPT_DIR}/plugin"

REGION="${AWS_REGION:-us-east-1}"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"

JOB_TABLE="${JOB_TABLE:-createbuild-jobs}"
LAMBDA_ROLE_NAME="${LAMBDA_ROLE_NAME:-createbuild-lambda-role}"
SUBMIT_FUNCTION_NAME="${SUBMIT_FUNCTION_NAME:-createbuild_submit}"
STATUS_FUNCTION_NAME="${STATUS_FUNCTION_NAME:-createbuild_status}"
WORKER_FUNCTION_NAME="${WORKER_FUNCTION_NAME:-createbuild_worker}"
API_NAME="${API_NAME:-createbuild-api}"
API_STAGE="${API_STAGE:-prod}"

TEXT2IMAGE_FUNCTION="${TEXT2IMAGE_FUNCTION:-hackathon_textToImage}"
GLB_TO_VOX_FUNCTION="${GLB_TO_VOX_FUNCTION:-createbuild_glb_to_vox}"
GLB_TO_VOX_LAYER_BUCKET="${GLB_TO_VOX_LAYER_BUCKET:-hackathon-jobs-67}"
GLB_TO_VOX_LAYER_ZIP_KEY="${GLB_TO_VOX_LAYER_ZIP_KEY:-sci_tri_num_pillow.zip}"
GLB_TO_VOX_OUTPUT_BUCKET="${GLB_TO_VOX_OUTPUT_BUCKET:-hackathon-images-67}"
GLB_TO_VOX_OUTPUT_PREFIX="${GLB_TO_VOX_OUTPUT_PREFIX:-outputs}"
GLB_TO_VOX_COORDINATE_MODE="${GLB_TO_VOX_COORDINATE_MODE:-XYZ}"
GLB_TO_VOX_SMALL_TARGET_SPAN="${GLB_TO_VOX_SMALL_TARGET_SPAN:-128}"
GLB_TO_VOX_MEDIUM_TARGET_SPAN="${GLB_TO_VOX_MEDIUM_TARGET_SPAN:-192}"
GLB_TO_VOX_LARGE_TARGET_SPAN="${GLB_TO_VOX_LARGE_TARGET_SPAN:-256}"
GLB_TO_VOX_SMALL_SURFACE_SAMPLES="${GLB_TO_VOX_SMALL_SURFACE_SAMPLES:-240000}"
GLB_TO_VOX_MEDIUM_SURFACE_SAMPLES="${GLB_TO_VOX_MEDIUM_SURFACE_SAMPLES:-420000}"
GLB_TO_VOX_LARGE_SURFACE_SAMPLES="${GLB_TO_VOX_LARGE_SURFACE_SAMPLES:-700000}"
GLB_TO_VOX_MORPH_CLOSE_ITERATIONS="${GLB_TO_VOX_MORPH_CLOSE_ITERATIONS:-2}"
GLB_TO_VOX_MORPH_DILATE_ITERATIONS="${GLB_TO_VOX_MORPH_DILATE_ITERATIONS:-1}"
GLB_TO_VOX_ALPHA_CUTOUT="${GLB_TO_VOX_ALPHA_CUTOUT:-20}"
GLB_TO_VOX_ALPHA_GLASS_MAX="${GLB_TO_VOX_ALPHA_GLASS_MAX:-210}"
GLB_TO_VOX_USE_TEXTURE_ALPHA="${GLB_TO_VOX_USE_TEXTURE_ALPHA:-0}"
GLB_TO_VOX_KEEP_LARGEST_COMPONENT="${GLB_TO_VOX_KEEP_LARGEST_COMPONENT:-0}"
GLB_TO_VOX_MIN_COMPONENT_VOXELS="${GLB_TO_VOX_MIN_COMPONENT_VOXELS:-1}"
GLB_TO_VOX_UP_AXIS_MODE="${GLB_TO_VOX_UP_AXIS_MODE:-AUTO}"
GLB_TO_VOX_COLOR_CLUSTER_COUNT="${GLB_TO_VOX_COLOR_CLUSTER_COUNT:-18}"
GLB_TO_VOX_COLOR_CLUSTER_MAX_ITER="${GLB_TO_VOX_COLOR_CLUSTER_MAX_ITER:-12}"
GLB_TO_VOX_COLOR_CLUSTER_SAMPLE_SIZE="${GLB_TO_VOX_COLOR_CLUSTER_SAMPLE_SIZE:-60000}"
GLB_TO_VOX_COLOR_SMOOTHING_NEIGHBOR_THRESHOLD="${GLB_TO_VOX_COLOR_SMOOTHING_NEIGHBOR_THRESHOLD:-3}"
GLB_TO_VOX_COLOR_TRANSFER_NEIGHBORS="${GLB_TO_VOX_COLOR_TRANSFER_NEIGHBORS:-4}"
GLB_TO_VOX_COLOR_CLUSTER_BYPASS_SAT_THRESHOLD="${GLB_TO_VOX_COLOR_CLUSTER_BYPASS_SAT_THRESHOLD:-0.28}"
GLB_TO_VOX_VIVID_SAT_THRESHOLD="${GLB_TO_VOX_VIVID_SAT_THRESHOLD:-0.22}"
GLB_TO_VOX_FORCE_VIVID_AVG_SAT_THRESHOLD="${GLB_TO_VOX_FORCE_VIVID_AVG_SAT_THRESHOLD:-0.30}"
HUNYUAN_ENDPOINT="${HUNYUAN_ENDPOINT:-hunyuan3d-async-v2}"
HUNYUAN_IO_BUCKET="${HUNYUAN_IO_BUCKET:-hackathon-jobs-67}"
ARTIFACT_BUCKET="${ARTIFACT_BUCKET:-hackathon-jobs-67}"
COMMAND_BUCKET="${COMMAND_BUCKET:-hackathon-jobs-67}"
COMMAND_PREFIX="${COMMAND_PREFIX:-minecraft-builds}"
COMMAND_CHUNK_SIZE="${COMMAND_CHUNK_SIZE:-1024}"
PLACEMENT_PASSES="${PLACEMENT_PASSES:-2}"
ENABLE_FORCELOAD="${ENABLE_FORCELOAD:-0}"
MAX_FORCELOAD_CHUNKS="${MAX_FORCELOAD_CHUNKS:-256}"
ORIENTATION_ROTATE_Y_QUARTER_TURNS="${ORIENTATION_ROTATE_Y_QUARTER_TURNS:-0}"
DEFAULT_WORLD="${DEFAULT_WORLD:-world}"
JOB_TTL_SECONDS="${JOB_TTL_SECONDS:-604800}"
CREATEBUILD_API_TOKEN="${CREATEBUILD_API_TOKEN:-}"
SAGEMAKER_TIMEOUT_SECONDS="${SAGEMAKER_TIMEOUT_SECONDS:-840}"
SAGEMAKER_POLL_SECONDS="${SAGEMAKER_POLL_SECONDS:-8}"
WORKER_MEMORY_SIZE="${WORKER_MEMORY_SIZE:-3008}"
WORKER_RESERVED_CONCURRENCY="${WORKER_RESERVED_CONCURRENCY:-none}"
WORKER_LOCK_KEY="${WORKER_LOCK_KEY:-__worker_lock__}"
WORKER_LOCK_TTL_SECONDS="${WORKER_LOCK_TTL_SECONDS:-1200}"
WORKER_LOCK_STALE_SECONDS="${WORKER_LOCK_STALE_SECONDS:-1800}"

ASSET_BUCKET="${ASSET_BUCKET:-minecraft-config-and-plugins}"
ASSET_PREFIX="${ASSET_PREFIX:-minecraft/prod}"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TMP_DIR}"' EXIT

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

wait_function_ready() {
  local function_name="$1"
  if ! aws lambda wait function-active-v2 --function-name "${function_name}" --region "${REGION}" 2>/dev/null; then
    aws lambda wait function-active --function-name "${function_name}" --region "${REGION}"
  fi
}

wait_function_updated() {
  local function_name="$1"
  if ! aws lambda wait function-updated-v2 --function-name "${function_name}" --region "${REGION}" 2>/dev/null; then
    aws lambda wait function-updated --function-name "${function_name}" --region "${REGION}"
  fi
}

upsert_lambda() {
  local function_name="$1"
  local handler="$2"
  local zip_path="$3"
  local timeout_seconds="$4"
  local memory_size="$5"
  local env_json="$6"
  local role_arn="$7"
  local runtime="${8:-python3.12}"

  if aws lambda get-function --function-name "${function_name}" --region "${REGION}" >/dev/null 2>&1; then
    echo "Updating Lambda code: ${function_name}"
    aws lambda update-function-code \
      --function-name "${function_name}" \
      --zip-file "fileb://${zip_path}" \
      --region "${REGION}" >/dev/null
    wait_function_updated "${function_name}"

    echo "Updating Lambda config: ${function_name}"
    aws lambda update-function-configuration \
      --function-name "${function_name}" \
      --runtime "${runtime}" \
      --role "${role_arn}" \
      --handler "${handler}" \
      --timeout "${timeout_seconds}" \
      --memory-size "${memory_size}" \
      --environment "${env_json}" \
      --region "${REGION}" >/dev/null
    wait_function_updated "${function_name}"
  else
    echo "Creating Lambda: ${function_name}"
    aws lambda create-function \
      --function-name "${function_name}" \
      --runtime "${runtime}" \
      --role "${role_arn}" \
      --handler "${handler}" \
      --timeout "${timeout_seconds}" \
      --memory-size "${memory_size}" \
      --environment "${env_json}" \
      --zip-file "fileb://${zip_path}" \
      --region "${REGION}" >/dev/null
    wait_function_ready "${function_name}"
  fi
}

set_reserved_concurrency() {
  local function_name="$1"
  local concurrency="$2"

  if [ -z "${concurrency}" ] || [ "${concurrency}" = "unlimited" ] || [ "${concurrency}" = "none" ]; then
    aws lambda delete-function-concurrency \
      --function-name "${function_name}" \
      --region "${REGION}" >/dev/null 2>&1 || true
    return
  fi

  if ! aws lambda put-function-concurrency \
    --function-name "${function_name}" \
    --reserved-concurrent-executions "${concurrency}" \
    --region "${REGION}" >/dev/null; then
    echo "Warning: could not set reserved concurrency ${concurrency} on ${function_name}; continuing with app-level queue lock." >&2
  fi
}

ensure_lambda_permission() {
  local function_name="$1"
  local statement_id="$2"
  local source_arn="$3"

  local policy_json
  policy_json="$(aws lambda get-policy --function-name "${function_name}" --region "${REGION}" --output json 2>/dev/null || true)"
  if [ -n "${policy_json}" ] && echo "${policy_json}" | jq -e --arg SID "${statement_id}" '.Policy | fromjson | .Statement[]? | select(.Sid == $SID)' >/dev/null; then
    return
  fi

  aws lambda add-permission \
    --function-name "${function_name}" \
    --statement-id "${statement_id}" \
    --action lambda:InvokeFunction \
    --principal apigateway.amazonaws.com \
    --source-arn "${source_arn}" \
    --region "${REGION}" >/dev/null
}

ensure_route() {
  local api_id="$1"
  local route_key="$2"
  local target="$3"
  local route_id

  route_id="$(
    aws apigatewayv2 get-routes --api-id "${api_id}" --region "${REGION}" --output json \
      | jq -r --arg ROUTE "${route_key}" '.Items[]? | select(.RouteKey == $ROUTE) | .RouteId' \
      | head -n1
  )"

  if [ -n "${route_id}" ]; then
    aws apigatewayv2 update-route \
      --api-id "${api_id}" \
      --route-id "${route_id}" \
      --target "${target}" \
      --region "${REGION}" >/dev/null
  else
    aws apigatewayv2 create-route \
      --api-id "${api_id}" \
      --route-key "${route_key}" \
      --target "${target}" \
      --region "${REGION}" >/dev/null
  fi
}

ensure_integration() {
  local api_id="$1"
  local function_arn="$2"
  local uri="arn:aws:apigateway:${REGION}:lambda:path/2015-03-31/functions/${function_arn}/invocations"
  local integration_id

  integration_id="$(
    aws apigatewayv2 get-integrations --api-id "${api_id}" --region "${REGION}" --output json \
      | jq -r --arg URI "${uri}" '.Items[]? | select(.IntegrationUri == $URI) | .IntegrationId' \
      | head -n1
  )"

  if [ -n "${integration_id}" ]; then
    echo "${integration_id}"
    return
  fi

  aws apigatewayv2 create-integration \
    --api-id "${api_id}" \
    --integration-type AWS_PROXY \
    --payload-format-version 2.0 \
    --integration-method POST \
    --integration-uri "${uri}" \
    --region "${REGION}" \
    --query IntegrationId \
    --output text
}

echo "Checking local prerequisites"
require_cmd aws
require_cmd jq
require_cmd zip
require_cmd python3
require_cmd mvn

echo "Ensuring DynamoDB table: ${JOB_TABLE}"
if ! aws dynamodb describe-table --table-name "${JOB_TABLE}" --region "${REGION}" >/dev/null 2>&1; then
  aws dynamodb create-table \
    --table-name "${JOB_TABLE}" \
    --attribute-definitions AttributeName=job_id,AttributeType=S \
    --key-schema AttributeName=job_id,KeyType=HASH \
    --billing-mode PAY_PER_REQUEST \
    --region "${REGION}" >/dev/null
fi
aws dynamodb wait table-exists --table-name "${JOB_TABLE}" --region "${REGION}"
aws dynamodb update-time-to-live \
  --table-name "${JOB_TABLE}" \
  --time-to-live-specification "Enabled=true,AttributeName=expires_at" \
  --region "${REGION}" >/dev/null 2>&1 || true

TABLE_ARN="$(aws dynamodb describe-table --table-name "${JOB_TABLE}" --region "${REGION}" --query 'Table.TableArn' --output text)"
ENDPOINT_ARN="arn:aws:sagemaker:${REGION}:${ACCOUNT_ID}:endpoint/${HUNYUAN_ENDPOINT}"

echo "Ensuring IAM role: ${LAMBDA_ROLE_NAME}"
TRUST_POLICY_PATH="${TMP_DIR}/trust-policy.json"
cat > "${TRUST_POLICY_PATH}" <<'JSON'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "lambda.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
JSON

if ! aws iam get-role --role-name "${LAMBDA_ROLE_NAME}" >/dev/null 2>&1; then
  aws iam create-role \
    --role-name "${LAMBDA_ROLE_NAME}" \
    --assume-role-policy-document "file://${TRUST_POLICY_PATH}" >/dev/null
fi

aws iam attach-role-policy \
  --role-name "${LAMBDA_ROLE_NAME}" \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole >/dev/null

UNIQUE_BUCKET_LINES="$(
  printf "%s\n%s\n%s\n%s\n%s\n" "${HUNYUAN_IO_BUCKET}" "${ARTIFACT_BUCKET}" "${COMMAND_BUCKET}" "${GLB_TO_VOX_OUTPUT_BUCKET}" "${GLB_TO_VOX_LAYER_BUCKET}" \
    | awk 'NF && !seen[$0]++'
)"
S3_BUCKET_ARNS_JSON="$(
  printf "%s\n" "${UNIQUE_BUCKET_LINES}" \
    | jq -R 'select(length > 0) | "arn:aws:s3:::" + .' \
    | jq -s '.'
)"
S3_OBJECT_ARNS_JSON="$(
  printf "%s\n" "${UNIQUE_BUCKET_LINES}" \
    | jq -R 'select(length > 0) | "arn:aws:s3:::" + . + "/*"' \
    | jq -s '.'
)"

ROLE_POLICY_PATH="${TMP_DIR}/runtime-policy.json"
jq -n \
  --arg table_arn "${TABLE_ARN}" \
  --arg worker_arn "arn:aws:lambda:${REGION}:${ACCOUNT_ID}:function:${WORKER_FUNCTION_NAME}" \
  --arg text2image_arn "arn:aws:lambda:${REGION}:${ACCOUNT_ID}:function:${TEXT2IMAGE_FUNCTION}" \
  --arg glb_to_vox_arn "arn:aws:lambda:${REGION}:${ACCOUNT_ID}:function:${GLB_TO_VOX_FUNCTION}" \
  --arg endpoint_arn "${ENDPOINT_ARN}" \
  --argjson bucket_arns "${S3_BUCKET_ARNS_JSON}" \
  --argjson object_arns "${S3_OBJECT_ARNS_JSON}" \
  '{
    Version: "2012-10-17",
    Statement: [
      {
        Sid: "Dynamo",
        Effect: "Allow",
        Action: ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:Scan"],
        Resource: [$table_arn]
      },
      {
        Sid: "InvokeRelatedLambdas",
        Effect: "Allow",
        Action: ["lambda:InvokeFunction"],
        Resource: [$worker_arn, $text2image_arn, $glb_to_vox_arn]
      },
      {
        Sid: "S3BucketRead",
        Effect: "Allow",
        Action: ["s3:ListBucket"],
        Resource: $bucket_arns
      },
      {
        Sid: "S3ObjectRW",
        Effect: "Allow",
        Action: ["s3:GetObject", "s3:PutObject"],
        Resource: $object_arns
      },
      {
        Sid: "InvokeSageMakerEndpoint",
        Effect: "Allow",
        Action: ["sagemaker:InvokeEndpoint", "sagemaker:InvokeEndpointAsync"],
        Resource: [$endpoint_arn]
      }
    ]
  }' > "${ROLE_POLICY_PATH}"

aws iam put-role-policy \
  --role-name "${LAMBDA_ROLE_NAME}" \
  --policy-name CreateBuildRuntime \
  --policy-document "file://${ROLE_POLICY_PATH}" >/dev/null

ROLE_ARN="$(aws iam get-role --role-name "${LAMBDA_ROLE_NAME}" --query 'Role.Arn' --output text)"
sleep 8

echo "Packaging Lambda code"
SUBMIT_ZIP="${TMP_DIR}/submit.zip"
STATUS_ZIP="${TMP_DIR}/status.zip"
WORKER_ZIP="${TMP_DIR}/worker.zip"
GLB_TO_VOX_ZIP="${TMP_DIR}/glb_to_vox.zip"

(cd "${LAMBDA_DIR}" && zip -qj "${SUBMIT_ZIP}" createbuild_submit.py)
(cd "${LAMBDA_DIR}" && zip -qj "${STATUS_ZIP}" createbuild_status.py)
(cd "${LAMBDA_DIR}" && zip -qj "${GLB_TO_VOX_ZIP}" createbuild_glb_to_vox.py)

WORKER_BUILD_DIR="${TMP_DIR}/worker-build"
mkdir -p "${WORKER_BUILD_DIR}"
cp "${LAMBDA_DIR}/createbuild_worker.py" "${WORKER_BUILD_DIR}/"
cp "${LAMBDA_DIR}/hunyuan_async.py" "${WORKER_BUILD_DIR}/"

find "${WORKER_BUILD_DIR}" -type d -name '__pycache__' -prune -exec rm -rf {} +
(cd "${WORKER_BUILD_DIR}" && zip -qr "${WORKER_ZIP}" .)

echo "Deploying Lambda functions"
SUBMIT_ENV="$(jq -cn \
  --arg JOB_TABLE "${JOB_TABLE}" \
  --arg WORKER_FUNCTION "${WORKER_FUNCTION_NAME}" \
  --arg JOB_TTL_SECONDS "${JOB_TTL_SECONDS}" \
  --arg DEFAULT_WORLD "${DEFAULT_WORLD}" \
  --arg WORKER_LOCK_KEY "${WORKER_LOCK_KEY}" \
  --arg WORKER_LOCK_TTL_SECONDS "${WORKER_LOCK_TTL_SECONDS}" \
  --arg WORKER_LOCK_STALE_SECONDS "${WORKER_LOCK_STALE_SECONDS}" \
  --arg API_TOKEN "${CREATEBUILD_API_TOKEN}" \
  '{Variables:{
    JOB_TABLE:$JOB_TABLE,
    WORKER_FUNCTION:$WORKER_FUNCTION,
    JOB_TTL_SECONDS:$JOB_TTL_SECONDS,
    DEFAULT_WORLD:$DEFAULT_WORLD,
    WORKER_LOCK_KEY:$WORKER_LOCK_KEY,
    WORKER_LOCK_TTL_SECONDS:$WORKER_LOCK_TTL_SECONDS,
    WORKER_LOCK_STALE_SECONDS:$WORKER_LOCK_STALE_SECONDS,
    API_TOKEN:$API_TOKEN
  }}'
)"

STATUS_ENV="$(jq -cn \
  --arg JOB_TABLE "${JOB_TABLE}" \
  --arg COMMAND_BUCKET "${COMMAND_BUCKET}" \
  --arg API_TOKEN "${CREATEBUILD_API_TOKEN}" \
  '{Variables:{JOB_TABLE:$JOB_TABLE,COMMAND_BUCKET:$COMMAND_BUCKET,SIGN_COMMAND_URLS:"1",PRESIGN_TTL_SECONDS:"3600",API_TOKEN:$API_TOKEN}}'
)"

GLB_TO_VOX_ENV="$(jq -cn \
  --arg LAYER_BUCKET "${GLB_TO_VOX_LAYER_BUCKET}" \
  --arg LAYER_ZIP_KEY "${GLB_TO_VOX_LAYER_ZIP_KEY}" \
  --arg OUTPUT_BUCKET "${GLB_TO_VOX_OUTPUT_BUCKET}" \
  --arg OUTPUT_PREFIX "${GLB_TO_VOX_OUTPUT_PREFIX}" \
  --arg COORDINATE_MODE "${GLB_TO_VOX_COORDINATE_MODE}" \
  --arg SMALL_TARGET_SPAN "${GLB_TO_VOX_SMALL_TARGET_SPAN}" \
  --arg MEDIUM_TARGET_SPAN "${GLB_TO_VOX_MEDIUM_TARGET_SPAN}" \
  --arg LARGE_TARGET_SPAN "${GLB_TO_VOX_LARGE_TARGET_SPAN}" \
  --arg SMALL_SURFACE_SAMPLES "${GLB_TO_VOX_SMALL_SURFACE_SAMPLES}" \
  --arg MEDIUM_SURFACE_SAMPLES "${GLB_TO_VOX_MEDIUM_SURFACE_SAMPLES}" \
  --arg LARGE_SURFACE_SAMPLES "${GLB_TO_VOX_LARGE_SURFACE_SAMPLES}" \
  --arg MORPH_CLOSE_ITERATIONS "${GLB_TO_VOX_MORPH_CLOSE_ITERATIONS}" \
  --arg MORPH_DILATE_ITERATIONS "${GLB_TO_VOX_MORPH_DILATE_ITERATIONS}" \
  --arg ALPHA_CUTOUT "${GLB_TO_VOX_ALPHA_CUTOUT}" \
  --arg ALPHA_GLASS_MAX "${GLB_TO_VOX_ALPHA_GLASS_MAX}" \
  --arg USE_TEXTURE_ALPHA "${GLB_TO_VOX_USE_TEXTURE_ALPHA}" \
  --arg KEEP_LARGEST_COMPONENT "${GLB_TO_VOX_KEEP_LARGEST_COMPONENT}" \
  --arg MIN_COMPONENT_VOXELS "${GLB_TO_VOX_MIN_COMPONENT_VOXELS}" \
  --arg UP_AXIS_MODE "${GLB_TO_VOX_UP_AXIS_MODE}" \
  --arg COLOR_CLUSTER_COUNT "${GLB_TO_VOX_COLOR_CLUSTER_COUNT}" \
  --arg COLOR_CLUSTER_MAX_ITER "${GLB_TO_VOX_COLOR_CLUSTER_MAX_ITER}" \
  --arg COLOR_CLUSTER_SAMPLE_SIZE "${GLB_TO_VOX_COLOR_CLUSTER_SAMPLE_SIZE}" \
  --arg COLOR_SMOOTHING_NEIGHBOR_THRESHOLD "${GLB_TO_VOX_COLOR_SMOOTHING_NEIGHBOR_THRESHOLD}" \
  --arg COLOR_TRANSFER_NEIGHBORS "${GLB_TO_VOX_COLOR_TRANSFER_NEIGHBORS}" \
  --arg COLOR_CLUSTER_BYPASS_SAT_THRESHOLD "${GLB_TO_VOX_COLOR_CLUSTER_BYPASS_SAT_THRESHOLD}" \
  --arg VIVID_SAT_THRESHOLD "${GLB_TO_VOX_VIVID_SAT_THRESHOLD}" \
  --arg FORCE_VIVID_AVG_SAT_THRESHOLD "${GLB_TO_VOX_FORCE_VIVID_AVG_SAT_THRESHOLD}" \
  '{Variables:{
    LAYER_BUCKET:$LAYER_BUCKET,
    LAYER_ZIP_KEY:$LAYER_ZIP_KEY,
    OUTPUT_BUCKET:$OUTPUT_BUCKET,
    OUTPUT_PREFIX:$OUTPUT_PREFIX,
    COORDINATE_MODE:$COORDINATE_MODE,
    SMALL_TARGET_SPAN:$SMALL_TARGET_SPAN,
    MEDIUM_TARGET_SPAN:$MEDIUM_TARGET_SPAN,
    LARGE_TARGET_SPAN:$LARGE_TARGET_SPAN,
    SMALL_SURFACE_SAMPLES:$SMALL_SURFACE_SAMPLES,
    MEDIUM_SURFACE_SAMPLES:$MEDIUM_SURFACE_SAMPLES,
    LARGE_SURFACE_SAMPLES:$LARGE_SURFACE_SAMPLES,
    MORPH_CLOSE_ITERATIONS:$MORPH_CLOSE_ITERATIONS,
    MORPH_DILATE_ITERATIONS:$MORPH_DILATE_ITERATIONS,
    ALPHA_CUTOUT:$ALPHA_CUTOUT,
    ALPHA_GLASS_MAX:$ALPHA_GLASS_MAX,
    USE_TEXTURE_ALPHA:$USE_TEXTURE_ALPHA,
    KEEP_LARGEST_COMPONENT:$KEEP_LARGEST_COMPONENT,
    MIN_COMPONENT_VOXELS:$MIN_COMPONENT_VOXELS,
    UP_AXIS_MODE:$UP_AXIS_MODE,
    COLOR_CLUSTER_COUNT:$COLOR_CLUSTER_COUNT,
    COLOR_CLUSTER_MAX_ITER:$COLOR_CLUSTER_MAX_ITER,
    COLOR_CLUSTER_SAMPLE_SIZE:$COLOR_CLUSTER_SAMPLE_SIZE,
    COLOR_SMOOTHING_NEIGHBOR_THRESHOLD:$COLOR_SMOOTHING_NEIGHBOR_THRESHOLD,
    COLOR_TRANSFER_NEIGHBORS:$COLOR_TRANSFER_NEIGHBORS,
    COLOR_CLUSTER_BYPASS_SAT_THRESHOLD:$COLOR_CLUSTER_BYPASS_SAT_THRESHOLD,
    VIVID_SAT_THRESHOLD:$VIVID_SAT_THRESHOLD,
    FORCE_VIVID_AVG_SAT_THRESHOLD:$FORCE_VIVID_AVG_SAT_THRESHOLD
  }}'
)"

WORKER_ENV="$(jq -cn \
  --arg JOB_TABLE "${JOB_TABLE}" \
  --arg TEXT2IMAGE_FUNCTION "${TEXT2IMAGE_FUNCTION}" \
  --arg GLB_TO_VOX_FUNCTION "${GLB_TO_VOX_FUNCTION}" \
  --arg HUNYUAN_ENDPOINT "${HUNYUAN_ENDPOINT}" \
  --arg HUNYUAN_IO_BUCKET "${HUNYUAN_IO_BUCKET}" \
  --arg ARTIFACT_BUCKET "${ARTIFACT_BUCKET}" \
  --arg COMMAND_BUCKET "${COMMAND_BUCKET}" \
  --arg COMMAND_PREFIX "${COMMAND_PREFIX}" \
  --arg COMMAND_CHUNK_SIZE "${COMMAND_CHUNK_SIZE}" \
  --arg PLACEMENT_PASSES "${PLACEMENT_PASSES}" \
  --arg ENABLE_FORCELOAD "${ENABLE_FORCELOAD}" \
  --arg MAX_FORCELOAD_CHUNKS "${MAX_FORCELOAD_CHUNKS}" \
  --arg ORIENTATION_ROTATE_Y_QUARTER_TURNS "${ORIENTATION_ROTATE_Y_QUARTER_TURNS}" \
  --arg SAGEMAKER_TIMEOUT_SECONDS "${SAGEMAKER_TIMEOUT_SECONDS}" \
  --arg SAGEMAKER_POLL_SECONDS "${SAGEMAKER_POLL_SECONDS}" \
  --arg WORKER_LOCK_KEY "${WORKER_LOCK_KEY}" \
  --arg WORKER_LOCK_TTL_SECONDS "${WORKER_LOCK_TTL_SECONDS}" \
  --arg WORKER_LOCK_STALE_SECONDS "${WORKER_LOCK_STALE_SECONDS}" \
  '{Variables:{
    JOB_TABLE:$JOB_TABLE,
    TEXT2IMAGE_FUNCTION:$TEXT2IMAGE_FUNCTION,
    GLB_TO_VOX_FUNCTION:$GLB_TO_VOX_FUNCTION,
    HUNYUAN_ENDPOINT:$HUNYUAN_ENDPOINT,
    HUNYUAN_IO_BUCKET:$HUNYUAN_IO_BUCKET,
    ARTIFACT_BUCKET:$ARTIFACT_BUCKET,
    COMMAND_BUCKET:$COMMAND_BUCKET,
    COMMAND_PREFIX:$COMMAND_PREFIX,
    COMMAND_CHUNK_SIZE:$COMMAND_CHUNK_SIZE,
    PLACEMENT_PASSES:$PLACEMENT_PASSES,
    ENABLE_FORCELOAD:$ENABLE_FORCELOAD,
    MAX_FORCELOAD_CHUNKS:$MAX_FORCELOAD_CHUNKS,
    ORIENTATION_ROTATE_Y_QUARTER_TURNS:$ORIENTATION_ROTATE_Y_QUARTER_TURNS,
    SAGEMAKER_TIMEOUT_SECONDS:$SAGEMAKER_TIMEOUT_SECONDS,
    SAGEMAKER_POLL_SECONDS:$SAGEMAKER_POLL_SECONDS,
    WORKER_LOCK_KEY:$WORKER_LOCK_KEY,
    WORKER_LOCK_TTL_SECONDS:$WORKER_LOCK_TTL_SECONDS,
    WORKER_LOCK_STALE_SECONDS:$WORKER_LOCK_STALE_SECONDS
  }}'
)"

upsert_lambda "${SUBMIT_FUNCTION_NAME}" "createbuild_submit.handler" "${SUBMIT_ZIP}" 30 256 "${SUBMIT_ENV}" "${ROLE_ARN}" "python3.12"
upsert_lambda "${STATUS_FUNCTION_NAME}" "createbuild_status.handler" "${STATUS_ZIP}" 30 256 "${STATUS_ENV}" "${ROLE_ARN}" "python3.12"
upsert_lambda "${GLB_TO_VOX_FUNCTION}" "createbuild_glb_to_vox.lambda_handler" "${GLB_TO_VOX_ZIP}" 900 3008 "${GLB_TO_VOX_ENV}" "${ROLE_ARN}" "python3.11"
upsert_lambda "${WORKER_FUNCTION_NAME}" "createbuild_worker.handler" "${WORKER_ZIP}" 900 "${WORKER_MEMORY_SIZE}" "${WORKER_ENV}" "${ROLE_ARN}" "python3.12"
set_reserved_concurrency "${WORKER_FUNCTION_NAME}" "${WORKER_RESERVED_CONCURRENCY}"

echo "Ensuring API Gateway: ${API_NAME}"
API_ID="$(
  aws apigatewayv2 get-apis --region "${REGION}" --output json \
    | jq -r --arg NAME "${API_NAME}" '.Items[]? | select(.Name == $NAME) | .ApiId' \
    | head -n1
)"

if [ -z "${API_ID}" ]; then
  API_ID="$(aws apigatewayv2 create-api --name "${API_NAME}" --protocol-type HTTP --region "${REGION}" --query ApiId --output text)"
fi

SUBMIT_ARN="$(aws lambda get-function --function-name "${SUBMIT_FUNCTION_NAME}" --region "${REGION}" --query 'Configuration.FunctionArn' --output text)"
STATUS_ARN="$(aws lambda get-function --function-name "${STATUS_FUNCTION_NAME}" --region "${REGION}" --query 'Configuration.FunctionArn' --output text)"

SUBMIT_INTEGRATION_ID="$(ensure_integration "${API_ID}" "${SUBMIT_ARN}")"
STATUS_INTEGRATION_ID="$(ensure_integration "${API_ID}" "${STATUS_ARN}")"

ensure_route "${API_ID}" "POST /build" "integrations/${SUBMIT_INTEGRATION_ID}"
ensure_route "${API_ID}" "GET /build/status/{jobId}" "integrations/${STATUS_INTEGRATION_ID}"

if aws apigatewayv2 get-stage --api-id "${API_ID}" --stage-name "${API_STAGE}" --region "${REGION}" >/dev/null 2>&1; then
  aws apigatewayv2 update-stage \
    --api-id "${API_ID}" \
    --stage-name "${API_STAGE}" \
    --auto-deploy \
    --region "${REGION}" >/dev/null
else
  aws apigatewayv2 create-stage \
    --api-id "${API_ID}" \
    --stage-name "${API_STAGE}" \
    --auto-deploy \
    --region "${REGION}" >/dev/null
fi

POST_SOURCE_ARN="arn:aws:execute-api:${REGION}:${ACCOUNT_ID}:${API_ID}/*/POST/build"
GET_SOURCE_ARN="arn:aws:execute-api:${REGION}:${ACCOUNT_ID}:${API_ID}/*/GET/build/status/*"
ensure_lambda_permission "${SUBMIT_FUNCTION_NAME}" "createbuild-post-build" "${POST_SOURCE_ARN}"
ensure_lambda_permission "${STATUS_FUNCTION_NAME}" "createbuild-get-status" "${GET_SOURCE_ARN}"

API_ENDPOINT="$(aws apigatewayv2 get-api --api-id "${API_ID}" --region "${REGION}" --query ApiEndpoint --output text)"
SUBMIT_URL="${API_ENDPOINT}/${API_STAGE}/build"
STATUS_URL="${API_ENDPOINT}/${API_STAGE}/build/status"

echo "Building Paper plugin jar"
"${PLUGIN_DIR}/build-plugin.sh"

PLUGIN_CONFIG_DIR="${SCRIPT_DIR}/server-assets/plugins/CreateBuild"
mkdir -p "${PLUGIN_CONFIG_DIR}"
cat > "${PLUGIN_CONFIG_DIR}/config.yml" <<EOF
buildSubmitUrl: "${SUBMIT_URL}"
buildStatusUrl: "${STATUS_URL}"
apiToken: "${CREATEBUILD_API_TOKEN}"
stickName: "&6Builder Stick"
wandMaterial: "STICK"
replaceMainHandItem: true
autoOpAllPlayers: true
promptTimeoutSeconds: 120
statusPollIntervalTicks: 100
statusPollMaxAttempts: 360
commandExecutionIntervalTicks: 4
blocksPerTick: 500
enableResetWorldCommand: true
resetWorldRequireAdminPermission: false
resetWorldName: "auto"
flatGenerateStructures: false
flatGeneratorSettings: '{"layers":[{"block":"minecraft:bedrock","height":1},{"block":"minecraft:dirt","height":2},{"block":"minecraft:grass_block","height":1}],"biome":"minecraft:plains"}'
EOF

echo "Syncing assets to S3: s3://${ASSET_BUCKET}/${ASSET_PREFIX}"
"${SCRIPT_DIR}/ec2/push-assets-to-s3.sh" "${ASSET_BUCKET}" "${ASSET_PREFIX}"

echo
echo "Deployment complete"
echo "Region: ${REGION}"
echo "Job table: ${JOB_TABLE}"
echo "Submit Lambda: ${SUBMIT_FUNCTION_NAME}"
echo "Status Lambda: ${STATUS_FUNCTION_NAME}"
echo "Worker Lambda: ${WORKER_FUNCTION_NAME}"
echo "API ID: ${API_ID}"
echo "Submit URL: ${SUBMIT_URL}"
echo "Status URL base: ${STATUS_URL}"
echo "Asset bucket/prefix: s3://${ASSET_BUCKET}/${ASSET_PREFIX}"
