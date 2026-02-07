#!/bin/bash
set -e

REGION="${AWS_REGION:-us-east-1}"
ENDPOINT_NAME="hunyuan3d-async"
INPUT_BUCKET="hackathon-images-67"
OUTPUT_BUCKET="hackathon-jobs-67"
TEST_IMAGE="inputs/test_image.png"
JOB_ID="test-$(date +%s)"

echo "=== Testing Hunyuan3D SageMaker Endpoint ==="
echo "Endpoint: $ENDPOINT_NAME"
echo "Test image: s3://$INPUT_BUCKET/$TEST_IMAGE"
echo "Job ID: $JOB_ID"
echo ""

# Check endpoint status
echo "Checking endpoint status..."
STATUS=$(aws sagemaker describe-endpoint --endpoint-name $ENDPOINT_NAME --region $REGION --query 'EndpointStatus' --output text 2>/dev/null || echo "NOT_FOUND")

if [ "$STATUS" != "InService" ]; then
    echo "ERROR: Endpoint status is '$STATUS', expected 'InService'"
    echo "Wait for endpoint to be ready or run setup-sagemaker.sh"
    exit 1
fi
echo "Endpoint status: $STATUS"

# Check if test image exists
echo ""
echo "Checking test image exists..."
if ! aws s3 ls "s3://$INPUT_BUCKET/$TEST_IMAGE" --region $REGION >/dev/null 2>&1; then
    echo "ERROR: Test image not found at s3://$INPUT_BUCKET/$TEST_IMAGE"
    echo ""
    echo "Please upload a test image first:"
    echo "  aws s3 cp your-image.png s3://$INPUT_BUCKET/$TEST_IMAGE"
    exit 1
fi
echo "Test image found."

# Check current instance count
echo ""
echo "Checking instance count..."
INSTANCE_COUNT=$(aws cloudwatch get-metric-statistics \
    --namespace "AWS/SageMaker" \
    --metric-name "CPUUtilization" \
    --dimensions Name=EndpointName,Value=$ENDPOINT_NAME Name=VariantName,Value=AllTraffic \
    --start-time "$(date -u -v-5M '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || date -u -d '5 minutes ago' '+%Y-%m-%dT%H:%M:%SZ')" \
    --end-time "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" \
    --period 60 \
    --statistics SampleCount \
    --region $REGION \
    --query 'Datapoints | length(@)' --output text 2>/dev/null || echo "0")

if [ "$INSTANCE_COUNT" = "0" ]; then
    echo "Endpoint is scaled to zero. First request will trigger cold start (~5-10 min)."
else
    echo "Endpoint has active instances."
fi

# Run the test
echo ""
echo "=== Submitting Test Job ==="
echo "This will run both shape and paint stages."
echo ""

python3 "$(dirname "$0")/submit-job.py" \
    --input "s3://$INPUT_BUCKET/$TEST_IMAGE" \
    --output-prefix "s3://$OUTPUT_BUCKET/jobs/$JOB_ID" \
    --endpoint $ENDPOINT_NAME \
    --region $REGION

echo ""
echo "=== Test Complete ==="
echo ""
echo "Output files:"
echo "  Shape:    s3://$OUTPUT_BUCKET/jobs/$JOB_ID/shape.glb"
echo "  Textured: s3://$OUTPUT_BUCKET/jobs/$JOB_ID/textured.glb"
echo ""
echo "Download with:"
echo "  aws s3 cp s3://$OUTPUT_BUCKET/jobs/$JOB_ID/textured.glb ./textured.glb"
