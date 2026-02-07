#!/bin/bash
set -e

REGION="${AWS_REGION:-us-east-1}"
ENDPOINT_NAME="hunyuan3d-async"
ENDPOINT_CONFIG_NAME="hunyuan3d-async-config"
MODEL_NAME="hunyuan3d-model"

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
