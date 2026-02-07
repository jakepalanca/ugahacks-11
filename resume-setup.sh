#!/bin/bash
set -e
export AWS_PAGER=""

# Configuration (same as setup-sagemaker.sh)
REGION="${AWS_REGION:-us-east-1}"
ACCOUNT_ID="${AWS_ACCOUNT_ID:-418087252133}"
ECR_REPO="hunyuan3d-sagemaker"
IMAGE_TAG="v2"
MODEL_NAME="hunyuan3d-model-v2"
ENDPOINT_CONFIG_NAME="hunyuan3d-async-config-v2"
ENDPOINT_NAME="hunyuan3d-async-v2"
EXECUTION_ROLE_NAME="hunyuan3d-sagemaker-role"
INSTANCE_TYPE="${INSTANCE_TYPE:-ml.g5.2xlarge}"
INPUT_BUCKET="hackathon-jobs-67"
OUTPUT_BUCKET="hackathon-jobs-67"
ECR_URI="$ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/$ECR_REPO:$IMAGE_TAG"
ROLE_ARN="arn:aws:iam::$ACCOUNT_ID:role/$EXECUTION_ROLE_NAME"

echo "=== Resuming SageMaker setup from Step 3 ==="
echo "Region: $REGION"
echo "Account: $ACCOUNT_ID"
echo "Instance: $INSTANCE_TYPE"
echo "ECR URI: $ECR_URI"

# Step 3: Create SageMaker execution role
echo ""
echo "=== Step 3: SageMaker Execution Role ==="

if ! aws iam get-role --role-name $EXECUTION_ROLE_NAME > /dev/null 2>&1; then
    echo "Creating execution role..."

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

aws sagemaker delete-model --model-name $MODEL_NAME --region $REGION 2>/dev/null || true

aws sagemaker create-model \
    --model-name $MODEL_NAME \
    --primary-container Image=$ECR_URI \
    --execution-role-arn $ROLE_ARN \
    --region $REGION

echo "Model created: $MODEL_NAME"

# Step 5: Create Async Endpoint Configuration
echo ""
echo "=== Step 5: Endpoint Configuration (Async + Scale-to-Zero) ==="

aws sagemaker delete-endpoint-config --endpoint-config-name $ENDPOINT_CONFIG_NAME --region $REGION 2>/dev/null || true

echo "Instance type: $INSTANCE_TYPE"

aws sagemaker create-endpoint-config \
    --endpoint-config-name $ENDPOINT_CONFIG_NAME \
    --production-variants '[
        {
            "VariantName": "AllTraffic",
            "ModelName": "'"$MODEL_NAME"'",
            "InstanceType": "'"$INSTANCE_TYPE"'",
            "InitialInstanceCount": 1,
            "ManagedInstanceScaling": {
                "Status": "ENABLED",
                "MinInstanceCount": 0,
                "MaxInstanceCount": 1
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

aws sagemaker delete-endpoint --endpoint-name $ENDPOINT_NAME --region $REGION 2>/dev/null || true
echo "Waiting for old endpoint to be deleted..."
sleep 30

aws sagemaker create-endpoint \
    --endpoint-name $ENDPOINT_NAME \
    --endpoint-config-name $ENDPOINT_CONFIG_NAME \
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
echo "  - Endpoint starts with 0 instances (no cost when idle)"
echo "  - First request triggers scale-up (~5-10 min cold start)"
echo "  - Scales back to 0 after ~10-15 min of no requests"
echo "  - Use submit-job.py to submit inference requests"
echo ""
echo "=== Test the endpoint ==="
echo "1. Upload a test image:"
echo "   aws s3 cp your-image.png s3://hackathon-images-67/inputs/test_image.png"
echo ""
echo "2. Run the test (after endpoint is InService):"
echo "   ./test-endpoint.sh"
