#!/bin/bash
set -e

# Configuration
REGION="${AWS_REGION:-us-east-1}"
ACCOUNT_ID="${AWS_ACCOUNT_ID:-418087252133}"
ECR_REPO="hunyuan3d-sagemaker"
IMAGE_TAG="v2"
MODEL_NAME="hunyuan3d-model-v2"
ENDPOINT_CONFIG_NAME="hunyuan3d-async-config-v2"
ENDPOINT_NAME="hunyuan3d-async-v2"
EXECUTION_ROLE_NAME="hunyuan3d-sagemaker-role"

# Instance type: ml.g5.2xlarge (A10G 24GB) or ml.g6.2xlarge (L4 24GB)
INSTANCE_TYPE="${INSTANCE_TYPE:-ml.g5.2xlarge}"

# S3 buckets for async inference
INPUT_BUCKET="hackathon-jobs-67"
OUTPUT_BUCKET="hackathon-jobs-67"

echo "=== Setting up SageMaker Async Inference for Hunyuan3D ==="
echo "Region: $REGION"
echo "Account: $ACCOUNT_ID"
echo "Instance: $INSTANCE_TYPE (scale-to-zero enabled)"

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
    -f Dockerfile.sagemaker -t $ECR_URI --push .

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

aws sagemaker create-model \
    --model-name $MODEL_NAME \
    --primary-container Image=$ECR_URI \
    --execution-role-arn $ROLE_ARN \
    --region $REGION

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

# Delete existing endpoint if it exists
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
echo "To use g6.2xlarge instead, run:"
echo "  INSTANCE_TYPE=ml.g6.2xlarge ./setup-sagemaker.sh"
echo ""
echo "=== Test the endpoint ==="
echo "1. Upload a test image:"
echo "   aws s3 cp your-image.png s3://hackathon-images-67/inputs/test_image.png"
echo ""
echo "2. Run the test (after endpoint is InService):"
echo "   ./test-endpoint.sh"
