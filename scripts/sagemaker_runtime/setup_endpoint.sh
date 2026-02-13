#!/bin/bash
set -euo pipefail
export AWS_PAGER=""

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Configuration
REGION="${AWS_REGION:-us-east-1}"
ACCOUNT_ID="${AWS_ACCOUNT_ID:-$(aws sts get-caller-identity --query Account --output text --region "$REGION" 2>/dev/null || true)}"
ECR_REPO="${ECR_REPO:-hunyuan3d-sagemaker}"
IMAGE_TAG="${IMAGE_TAG:-v2}"
MODEL_NAME="${MODEL_NAME:-hunyuan3d-model-v2}"
ENDPOINT_CONFIG_NAME="${ENDPOINT_CONFIG_NAME:-hunyuan3d-async-config-v2}"
ENDPOINT_NAME="${ENDPOINT_NAME:-hunyuan3d-async-v2}"
EXECUTION_ROLE_NAME="${EXECUTION_ROLE_NAME:-hunyuan3d-sagemaker-role}"
PAINT_QUALITY="${PAINT_QUALITY:-medium}"
PAINT_MAX_NUM_VIEW="${PAINT_MAX_NUM_VIEW:-6}"
PAINT_RESOLUTION="${PAINT_RESOLUTION:-512}"
PYTORCH_CUDA_ALLOC_CONF="${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}"
UNLOAD_SHAPE_BEFORE_PAINT="${UNLOAD_SHAPE_BEFORE_PAINT:-1}"
UNLOAD_PAINT_BEFORE_SHAPE="${UNLOAD_PAINT_BEFORE_SHAPE:-1}"
KEEP_PAINT_PIPELINE_LOADED="${KEEP_PAINT_PIPELINE_LOADED:-0}"
DISABLE_CUDNN_FOR_PAINT="${DISABLE_CUDNN_FOR_PAINT:-1}"
SKIP_MESH_INPAINT="${SKIP_MESH_INPAINT:-1}"
CLEAR_CREATEBUILD_QUEUE="${CLEAR_CREATEBUILD_QUEUE:-1}"
JOB_TABLE="${JOB_TABLE:-createbuild-jobs}"
WORKER_LOCK_KEY="${WORKER_LOCK_KEY:-__worker_lock__}"

# Instance type: ml.g5.2xlarge (A10G 24GB) or ml.g6.2xlarge (L4 24GB)
INSTANCE_TYPE="${INSTANCE_TYPE:-ml.g5.2xlarge}"
VARIANT_NAME="${VARIANT_NAME:-AllTraffic}"
MIN_INSTANCE_COUNT="${MIN_INSTANCE_COUNT:-0}"
MAX_INSTANCE_COUNT="${MAX_INSTANCE_COUNT:-1}"
BACKLOG_TARGET_VALUE="${BACKLOG_TARGET_VALUE:-1}"
SCALE_IN_COOLDOWN_SECONDS="${SCALE_IN_COOLDOWN_SECONDS:-600}"
SCALE_OUT_COOLDOWN_SECONDS="${SCALE_OUT_COOLDOWN_SECONDS:-60}"
WAIT_FOR_ENDPOINT="${WAIT_FOR_ENDPOINT:-1}"

# S3 buckets for async inference
ASYNC_IO_BUCKET="${ASYNC_IO_BUCKET:-createbuild-${ACCOUNT_ID}-${REGION}-pipeline}"
INPUT_BUCKET="${INPUT_BUCKET:-${ASYNC_IO_BUCKET}}"
OUTPUT_BUCKET="${OUTPUT_BUCKET:-${ASYNC_IO_BUCKET}}"
TEST_IMAGE_BUCKET="${TEST_IMAGE_BUCKET:-${INPUT_BUCKET}}"
TEST_IMAGE_KEY="${TEST_IMAGE_KEY:-inputs/test_image.png}"

if [ -z "${ACCOUNT_ID}" ] || [ "${ACCOUNT_ID}" = "None" ]; then
    echo "ERROR: could not determine AWS account id. Set AWS_ACCOUNT_ID or configure AWS CLI credentials." >&2
    exit 1
fi

echo "=== Setting up SageMaker Async Inference for Hunyuan3D ==="
echo "Region: $REGION"
echo "Account: $ACCOUNT_ID"
echo "Instance: $INSTANCE_TYPE (autoscaling ${MIN_INSTANCE_COUNT}-${MAX_INSTANCE_COUNT})"

if [ "${CLEAR_CREATEBUILD_QUEUE}" = "1" ]; then
    echo ""
    echo "=== Step 0: Clear CreateBuild Queue ==="
    if [ -x "${ROOT_DIR}/minecraft_runtime/scripts/clear_createbuild_queue.sh" ]; then
        if ! AWS_REGION="${REGION}" JOB_TABLE="${JOB_TABLE}" WORKER_LOCK_KEY="${WORKER_LOCK_KEY}" \
            "${ROOT_DIR}/minecraft_runtime/scripts/clear_createbuild_queue.sh"; then
            echo "WARN: queue clear failed; continuing SageMaker setup." >&2
        fi
    else
        echo "WARN: ${ROOT_DIR}/minecraft_runtime/scripts/clear_createbuild_queue.sh not found; skipping queue clear." >&2
    fi
fi

# Step 1: Create ECR repository if it doesn't exist
echo ""
echo "=== Step 1: ECR Repository ==="
aws ecr describe-repositories --repository-names $ECR_REPO --region $REGION 2>/dev/null || \
    aws ecr create-repository --repository-name $ECR_REPO --region $REGION

ECR_URI="$ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/$ECR_REPO:$IMAGE_TAG"
echo "ECR URI: $ECR_URI"

# Step 2: Build and push Docker image
echo ""
echo "=== Step 2: Build & Push Docker Image ==="
aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin "$ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com"

# Build for x86_64 and push directly to ECR
# Use provenance=false to avoid OCI format (SageMaker requires Docker manifest v2)
docker buildx build --platform linux/amd64 --provenance=false --sbom=false \
    -f "${ROOT_DIR}/sagemaker_runtime/Dockerfile" -t $ECR_URI --push "${ROOT_DIR}"

# Step 3: Create SageMaker execution role
echo ""
echo "=== Step 3: SageMaker Execution Role ==="

ROLE_ARN="arn:aws:iam::$ACCOUNT_ID:role/$EXECUTION_ROLE_NAME"

# Check if role exists
if ! aws iam get-role --role-name $EXECUTION_ROLE_NAME 2>/dev/null; then
    echo "Creating execution role..."

    # Trust policy for SageMaker
    cat > /tmp/sagemaker-trust-policy.json << 'EOF'
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {
                "Service": "sagemaker.amazonaws.com"
            },
            "Action": "sts:AssumeRole"
        }
    ]
}
EOF

    aws iam create-role \
        --role-name $EXECUTION_ROLE_NAME \
        --assume-role-policy-document file:///tmp/sagemaker-trust-policy.json

    # Attach policies
    aws iam attach-role-policy \
        --role-name $EXECUTION_ROLE_NAME \
        --policy-arn arn:aws:iam::aws:policy/AmazonSageMakerFullAccess

    aws iam attach-role-policy \
        --role-name $EXECUTION_ROLE_NAME \
        --policy-arn arn:aws:iam::aws:policy/AmazonS3FullAccess

    aws iam attach-role-policy \
        --role-name $EXECUTION_ROLE_NAME \
        --policy-arn arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly

    echo "Waiting for role to propagate..."
    sleep 10
else
    echo "Role already exists: $ROLE_ARN"
fi

# Step 4: Create SageMaker Model
echo ""
echo "=== Step 4: SageMaker Model ==="

# Delete existing model if it exists
aws sagemaker delete-model --model-name $MODEL_NAME --region $REGION 2>/dev/null || true

# Build primary container definition with optional HF token for authenticated Hub access.
PRIMARY_CONTAINER_JSON="$(mktemp /tmp/hy3d-primary-container.XXXXXX.json)"
python3 - "$ECR_URI" "${HF_TOKEN:-}" "${PAINT_QUALITY}" "${PAINT_MAX_NUM_VIEW}" "${PAINT_RESOLUTION}" "${PYTORCH_CUDA_ALLOC_CONF}" "${UNLOAD_SHAPE_BEFORE_PAINT}" "${UNLOAD_PAINT_BEFORE_SHAPE}" "${KEEP_PAINT_PIPELINE_LOADED}" "${DISABLE_CUDNN_FOR_PAINT}" "${SKIP_MESH_INPAINT}" > "$PRIMARY_CONTAINER_JSON" <<'PY'
import json
import sys

image = sys.argv[1]
hf_token = sys.argv[2]
paint_quality = sys.argv[3]
paint_max_num_view = sys.argv[4]
paint_resolution = sys.argv[5]
cuda_alloc_conf = sys.argv[6]
unload_shape_before_paint = sys.argv[7]
unload_paint_before_shape = sys.argv[8]
keep_paint_pipeline_loaded = sys.argv[9]
disable_cudnn_for_paint = sys.argv[10]
skip_mesh_inpaint = sys.argv[11]

environment = {
    "PAINT_QUALITY": paint_quality,
    "PAINT_MAX_NUM_VIEW": paint_max_num_view,
    "PAINT_RESOLUTION": paint_resolution,
    "PYTORCH_CUDA_ALLOC_CONF": cuda_alloc_conf,
    "UNLOAD_SHAPE_BEFORE_PAINT": unload_shape_before_paint,
    "UNLOAD_PAINT_BEFORE_SHAPE": unload_paint_before_shape,
    "KEEP_PAINT_PIPELINE_LOADED": keep_paint_pipeline_loaded,
    "DISABLE_CUDNN_FOR_PAINT": disable_cudnn_for_paint,
    "SKIP_MESH_INPAINT": skip_mesh_inpaint,
}
if hf_token:
    environment["HF_TOKEN"] = hf_token

container = {"Image": image, "Environment": environment}

print(json.dumps(container))
PY

if [ -n "${HF_TOKEN:-}" ]; then
    echo "Using HF_TOKEN for authenticated Hugging Face downloads."
else
    echo "HF_TOKEN not set; relying on model artifacts baked into the image cache."
fi
echo "Paint settings: PAINT_QUALITY=${PAINT_QUALITY}, PAINT_MAX_NUM_VIEW=${PAINT_MAX_NUM_VIEW}, PAINT_RESOLUTION=${PAINT_RESOLUTION}"
echo "Memory settings: PYTORCH_CUDA_ALLOC_CONF=${PYTORCH_CUDA_ALLOC_CONF}, UNLOAD_SHAPE_BEFORE_PAINT=${UNLOAD_SHAPE_BEFORE_PAINT}, UNLOAD_PAINT_BEFORE_SHAPE=${UNLOAD_PAINT_BEFORE_SHAPE}, KEEP_PAINT_PIPELINE_LOADED=${KEEP_PAINT_PIPELINE_LOADED}, DISABLE_CUDNN_FOR_PAINT=${DISABLE_CUDNN_FOR_PAINT}, SKIP_MESH_INPAINT=${SKIP_MESH_INPAINT}"

aws sagemaker create-model \
    --model-name $MODEL_NAME \
    --primary-container "file://$PRIMARY_CONTAINER_JSON" \
    --execution-role-arn $ROLE_ARN \
    --region $REGION

rm -f "$PRIMARY_CONTAINER_JSON"

echo "Model created: $MODEL_NAME"

# Step 5: Create Async Endpoint Configuration
echo ""
echo "=== Step 5: Endpoint Configuration (Async + Scale-to-Zero) ==="

# Delete existing endpoint config if it exists
aws sagemaker delete-endpoint-config --endpoint-config-name $ENDPOINT_CONFIG_NAME --region $REGION 2>/dev/null || true

# Instance type: g5.2xlarge (24GB A10G) or g6.2xlarge (24GB L4)
# Using g5.2xlarge - change to ml.g6.2xlarge if preferred
INSTANCE_TYPE="${INSTANCE_TYPE:-ml.g5.2xlarge}"
echo "Instance type: $INSTANCE_TYPE"

# Create endpoint config with async inference + managed scaling (scale to zero)
# InitialInstanceCount must be >= 1, but managed scaling can scale down to 0
aws sagemaker create-endpoint-config \
    --endpoint-config-name $ENDPOINT_CONFIG_NAME \
    --production-variants '[
        {
            "VariantName": "'"$VARIANT_NAME"'",
            "ModelName": "'"$MODEL_NAME"'",
            "InstanceType": "'"$INSTANCE_TYPE"'",
            "InitialInstanceCount": 1,
            "ManagedInstanceScaling": {
                "Status": "ENABLED",
                "MinInstanceCount": '"$MIN_INSTANCE_COUNT"',
                "MaxInstanceCount": '"$MAX_INSTANCE_COUNT"'
            }
        }
    ]' \
    --async-inference-config '{
        "OutputConfig": {
            "S3OutputPath": "s3://'"$OUTPUT_BUCKET"'/async-output/",
            "S3FailurePath": "s3://'"$OUTPUT_BUCKET"'/async-failures/"
        },
        "ClientConfig": {
            "MaxConcurrentInvocationsPerInstance": 1
        }
    }' \
    --region $REGION

echo "Endpoint config created: $ENDPOINT_CONFIG_NAME"
echo "  - Scale to zero enabled (MinInstanceCount=0)"
echo "  - Cold start: ~5-10 minutes when scaling from 0"

# Step 6: Create Endpoint
echo ""
echo "=== Step 6: Create Endpoint ==="

# Delete existing endpoint if it exists
aws sagemaker delete-endpoint --endpoint-name $ENDPOINT_NAME --region $REGION 2>/dev/null || true
echo "Waiting for old endpoint to be deleted..."
sleep 30

aws sagemaker create-endpoint \
    --endpoint-name $ENDPOINT_NAME \
    --endpoint-config-name $ENDPOINT_CONFIG_NAME \
    --region $REGION

# Step 7: Configure explicit autoscaling policies
echo ""
echo "=== Step 7: Configure explicit autoscaling policies ==="

if [ "${WAIT_FOR_ENDPOINT}" = "1" ]; then
    echo "Waiting for endpoint to reach InService before autoscaling registration..."
    aws sagemaker wait endpoint-in-service \
        --endpoint-name $ENDPOINT_NAME \
        --region $REGION
fi

RESOURCE_ID="endpoint/${ENDPOINT_NAME}/variant/${VARIANT_NAME}"
SCALE_OUT_POLICY_NAME="${ENDPOINT_NAME}-scaleout-from-zero"
TARGET_POLICY_NAME="${ENDPOINT_NAME}-backlog-target-tracking"
SCALE_OUT_ALARM_NAME="${ENDPOINT_NAME}-has-backlog-without-capacity"

aws application-autoscaling register-scalable-target \
    --service-namespace sagemaker \
    --resource-id "$RESOURCE_ID" \
    --scalable-dimension sagemaker:variant:DesiredInstanceCount \
    --min-capacity $MIN_INSTANCE_COUNT \
    --max-capacity $MAX_INSTANCE_COUNT \
    --region $REGION

SCALE_OUT_POLICY_ARN=$(
    aws application-autoscaling put-scaling-policy \
        --service-namespace sagemaker \
        --resource-id "$RESOURCE_ID" \
        --scalable-dimension sagemaker:variant:DesiredInstanceCount \
        --policy-name "$SCALE_OUT_POLICY_NAME" \
        --policy-type StepScaling \
        --step-scaling-policy-configuration '{
            "AdjustmentType": "ChangeInCapacity",
            "MetricAggregationType": "Maximum",
            "Cooldown": '"$SCALE_OUT_COOLDOWN_SECONDS"',
            "StepAdjustments": [
                {
                    "MetricIntervalLowerBound": 0,
                    "ScalingAdjustment": 1
                }
            ]
        }' \
        --region $REGION \
        --query 'PolicyARN' \
        --output text
)

TARGET_TRACKING_JSON="$(mktemp /tmp/sm-target-tracking.XXXXXX.json)"
cat > "$TARGET_TRACKING_JSON" <<EOF
{
  "TargetValue": ${BACKLOG_TARGET_VALUE},
  "ScaleInCooldown": ${SCALE_IN_COOLDOWN_SECONDS},
  "ScaleOutCooldown": ${SCALE_OUT_COOLDOWN_SECONDS},
  "CustomizedMetricSpecification": {
    "Namespace": "AWS/SageMaker",
    "MetricName": "ApproximateBacklogSizePerInstance",
    "Statistic": "Average",
    "Dimensions": [
      { "Name": "EndpointName", "Value": "${ENDPOINT_NAME}" }
    ]
  }
}
EOF

aws application-autoscaling put-scaling-policy \
    --service-namespace sagemaker \
    --resource-id "$RESOURCE_ID" \
    --scalable-dimension sagemaker:variant:DesiredInstanceCount \
    --policy-name "$TARGET_POLICY_NAME" \
    --policy-type TargetTrackingScaling \
    --target-tracking-scaling-policy-configuration "file://$TARGET_TRACKING_JSON" \
    --region $REGION >/dev/null

rm -f "$TARGET_TRACKING_JSON"

aws cloudwatch put-metric-alarm \
    --alarm-name "$SCALE_OUT_ALARM_NAME" \
    --alarm-description "Scale ${ENDPOINT_NAME} from zero when backlog appears without capacity." \
    --namespace AWS/SageMaker \
    --metric-name HasBacklogWithoutCapacity \
    --dimensions Name=EndpointName,Value="$ENDPOINT_NAME" \
    --statistic Maximum \
    --period 60 \
    --evaluation-periods 2 \
    --datapoints-to-alarm 2 \
    --threshold 1 \
    --comparison-operator GreaterThanOrEqualToThreshold \
    --treat-missing-data notBreaching \
    --alarm-actions "$SCALE_OUT_POLICY_ARN" \
    --region $REGION

echo ""
echo "=== Endpoint creation started! ==="
echo "Endpoint name: $ENDPOINT_NAME"
echo "Instance type: $INSTANCE_TYPE"
echo ""
echo "Monitor progress with:"
echo "  aws sagemaker describe-endpoint --endpoint-name $ENDPOINT_NAME --region $REGION"
echo ""
echo "Wait for status to become 'InService'"
echo ""
echo "=== Scale-to-Zero Behavior ==="
echo "  - Explicit autoscaling target: min=${MIN_INSTANCE_COUNT}, max=${MAX_INSTANCE_COUNT}"
echo "  - Backlog target tracking metric: ApproximateBacklogSizePerInstance"
echo "  - Scale-from-zero alarm: ${SCALE_OUT_ALARM_NAME}"
echo "  - Endpoint can scale to 0 when idle; first new request can incur cold start"
echo "  - Use scripts/sagemaker_runtime/submit_job.py to submit inference requests"
echo ""
echo "To use g6.2xlarge instead, run:"
echo "  INSTANCE_TYPE=ml.g6.2xlarge ${ROOT_DIR}/scripts/sagemaker_runtime/setup_endpoint.sh"
echo ""
echo "=== Test the endpoint ==="
echo "1. Upload a test image:"
echo "   aws s3 cp your-image.png s3://$TEST_IMAGE_BUCKET/$TEST_IMAGE_KEY"
echo ""
echo "2. Run the test (after endpoint is InService):"
echo "   ${ROOT_DIR}/scripts/sagemaker_runtime/test_endpoint.sh"
echo ""
echo "Queue clear command:"
echo "  AWS_REGION=$REGION JOB_TABLE=$JOB_TABLE ${ROOT_DIR}/minecraft_runtime/scripts/clear_createbuild_queue.sh"
echo "EC2 sync/restart (run via SSH):"
echo "  sudo /usr/local/bin/minecraft-sync-assets.sh && sudo systemctl restart minecraft.service"
