#!/bin/bash
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
ENDPOINT_NAME="${ENDPOINT_NAME:-hunyuan3d-async-v2}"
ENDPOINT_CONFIG_NAME="${ENDPOINT_CONFIG_NAME:-hunyuan3d-async-config-v2}"
MODEL_NAME="${MODEL_NAME:-hunyuan3d-model-v2}"
VARIANT_NAME="${VARIANT_NAME:-AllTraffic}"
RESOURCE_ID="endpoint/${ENDPOINT_NAME}/variant/${VARIANT_NAME}"
SCALE_OUT_POLICY_NAME="${ENDPOINT_NAME}-scaleout-from-zero"
TARGET_POLICY_NAME="${ENDPOINT_NAME}-backlog-target-tracking"
SCALE_OUT_ALARM_NAME="${ENDPOINT_NAME}-has-backlog-without-capacity"

echo "=== Cleaning up SageMaker resources ==="

# Wait for endpoint to be in a deletable state
echo "Checking endpoint status..."
STATUS=$(aws sagemaker describe-endpoint --endpoint-name $ENDPOINT_NAME --region $REGION --query 'EndpointStatus' --output text 2>/dev/null || echo "NotFound")

if [ "$STATUS" = "Creating" ] || [ "$STATUS" = "Updating" ]; then
    echo "Endpoint is $STATUS. Waiting for it to finish..."
    while true; do
        sleep 30
        STATUS=$(aws sagemaker describe-endpoint --endpoint-name $ENDPOINT_NAME --region $REGION --query 'EndpointStatus' --output text 2>/dev/null || echo "NotFound")
        echo "  Status: $STATUS"
        if [ "$STATUS" != "Creating" ] && [ "$STATUS" != "Updating" ]; then
            break
        fi
    done
fi

# Remove autoscaling resources
echo "Deleting autoscaling alarm/policies/target (if present)..."
aws cloudwatch delete-alarms --alarm-names "$SCALE_OUT_ALARM_NAME" --region $REGION 2>/dev/null || true
aws application-autoscaling delete-scaling-policy \
    --service-namespace sagemaker \
    --resource-id "$RESOURCE_ID" \
    --scalable-dimension sagemaker:variant:DesiredInstanceCount \
    --policy-name "$SCALE_OUT_POLICY_NAME" \
    --region $REGION 2>/dev/null || true
aws application-autoscaling delete-scaling-policy \
    --service-namespace sagemaker \
    --resource-id "$RESOURCE_ID" \
    --scalable-dimension sagemaker:variant:DesiredInstanceCount \
    --policy-name "$TARGET_POLICY_NAME" \
    --region $REGION 2>/dev/null || true
aws application-autoscaling deregister-scalable-target \
    --service-namespace sagemaker \
    --resource-id "$RESOURCE_ID" \
    --scalable-dimension sagemaker:variant:DesiredInstanceCount \
    --region $REGION 2>/dev/null || true

# Delete endpoint
echo "Deleting endpoint: $ENDPOINT_NAME"
aws sagemaker delete-endpoint --endpoint-name $ENDPOINT_NAME --region $REGION 2>/dev/null || echo "  Endpoint not found or already deleted"

echo "Waiting for endpoint deletion..."
sleep 30

# Delete endpoint config
echo "Deleting endpoint config: $ENDPOINT_CONFIG_NAME"
aws sagemaker delete-endpoint-config --endpoint-config-name $ENDPOINT_CONFIG_NAME --region $REGION 2>/dev/null || echo "  Config not found or already deleted"

# Delete model
echo "Deleting model: $MODEL_NAME"
aws sagemaker delete-model --model-name $MODEL_NAME --region $REGION 2>/dev/null || echo "  Model not found or already deleted"

echo ""
echo "=== Cleanup complete ==="
