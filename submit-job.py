#!/usr/bin/env python3
"""
Submit jobs to SageMaker Async Inference endpoint for Hunyuan3D

Usage:
    # Full pipeline (shape + paint)
    python submit-job.py --input s3://bucket/image.png --output-prefix s3://bucket/jobs/job123

    # Shape only
    python submit-job.py --stage shape --input s3://bucket/image.png --output s3://bucket/shape.glb

    # Paint only (requires existing shape)
    python submit-job.py --stage paint --input s3://bucket/image.png --shape s3://bucket/shape.glb --output s3://bucket/textured.glb
"""
import argparse
import json
import time
import uuid
import boto3
from botocore.exceptions import ClientError

ENDPOINT_NAME = "hunyuan3d-async-v2"
REGION = "us-east-1"


def submit_async_inference(sagemaker_runtime, payload: dict) -> str:
    """Submit async inference request, returns output location"""
    response = sagemaker_runtime.invoke_endpoint_async(
        EndpointName=ENDPOINT_NAME,
        ContentType="application/json",
        InputLocation=None,  # We'll use direct invocation
        Accept="application/json",
    )
    # For direct payload, use invoke_endpoint_async with InferenceId
    return response


def invoke_and_wait(sagemaker_runtime, s3, payload: dict, poll_interval: int = 10) -> dict:
    """
    Invoke async endpoint and wait for result.

    For async inference, we need to:
    1. Upload input to S3
    2. Invoke with S3 location
    3. Poll output location until result appears
    """
    # Generate unique job ID
    job_id = str(uuid.uuid4())[:8]
    input_bucket = "hackathon-jobs-67"
    input_key = f"async-input/{job_id}.json"

    # Upload payload to S3
    print(f"Uploading request to s3://{input_bucket}/{input_key}")
    s3.put_object(
        Bucket=input_bucket,
        Key=input_key,
        Body=json.dumps(payload),
        ContentType="application/json"
    )

    input_location = f"s3://{input_bucket}/{input_key}"

    # Invoke async endpoint
    print(f"Invoking endpoint: {ENDPOINT_NAME}")
    response = sagemaker_runtime.invoke_endpoint_async(
        EndpointName=ENDPOINT_NAME,
        InputLocation=input_location,
        ContentType="application/json",
        Accept="application/json",
        InferenceId=job_id
    )

    output_location = response["OutputLocation"]
    print(f"Job submitted. Output will be at: {output_location}")

    # Poll for result
    print("Waiting for result...")
    output_parts = output_location.replace("s3://", "").split("/", 1)
    output_bucket = output_parts[0]
    output_key = output_parts[1]
    failure_key = output_key.replace("async-output/", "async-failures/")

    max_wait = 900  # 15 minutes max
    elapsed = 0
    while elapsed < max_wait:
        time.sleep(poll_interval)
        elapsed += poll_interval

        # Check for success output
        try:
            result = s3.get_object(Bucket=output_bucket, Key=output_key)
            body = result["Body"].read().decode("utf-8")
            print("")
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                print(f"Warning: Got non-JSON response: {body[:200]}")
                return {"status": "success", "raw_response": body}
        except ClientError as e:
            if e.response["Error"]["Code"] not in ("NoSuchKey", "404"):
                raise

        # Check for failure output
        try:
            failure = s3.get_object(Bucket=output_bucket, Key=failure_key)
            error_body = failure["Body"].read().decode("utf-8")
            raise RuntimeError(f"Inference failed: {error_body}")
        except ClientError:
            pass

        print(".", end="", flush=True)

    raise TimeoutError(f"Inference did not complete within {max_wait}s")


def run_pipeline(input_s3: str, output_prefix: str):
    """Run full shape + paint pipeline"""
    sagemaker_runtime = boto3.client("sagemaker-runtime", region_name=REGION)
    s3 = boto3.client("s3", region_name=REGION)

    # Ensure output_prefix ends without slash
    output_prefix = output_prefix.rstrip("/")

    shape_output = f"{output_prefix}/shape.glb"
    paint_output = f"{output_prefix}/textured.glb"

    # Stage 1: Shape
    print("\n=== Stage 1: Generating Shape ===")
    shape_payload = {
        "stage": "shape",
        "input_s3": input_s3,
        "output_s3": shape_output
    }

    result = invoke_and_wait(sagemaker_runtime, s3, shape_payload)
    print(f"\nShape complete: {result}")

    # Verify shape file exists on S3
    shape_parts = shape_output.replace("s3://", "").split("/", 1)
    try:
        s3.head_object(Bucket=shape_parts[0], Key=shape_parts[1])
        print(f"Verified shape exists: {shape_output}")
    except ClientError:
        raise RuntimeError(f"Shape file not found at {shape_output} after inference completed")

    # Stage 2: Paint
    print("\n=== Stage 2: Applying Texture ===")
    paint_payload = {
        "stage": "paint",
        "input_s3": input_s3,
        "shape_s3": shape_output,
        "output_s3": paint_output
    }

    result = invoke_and_wait(sagemaker_runtime, s3, paint_payload)
    print(f"\nPaint complete: {result}")

    print(f"\n=== Pipeline Complete ===")
    print(f"Shape:    {shape_output}")
    print(f"Textured: {paint_output}")


def run_single_stage(stage: str, input_s3: str, output_s3: str, shape_s3: str = None):
    """Run single stage"""
    sagemaker_runtime = boto3.client("sagemaker-runtime", region_name=REGION)
    s3 = boto3.client("s3", region_name=REGION)

    payload = {
        "stage": stage,
        "input_s3": input_s3,
        "output_s3": output_s3
    }

    if stage == "paint" and shape_s3:
        payload["shape_s3"] = shape_s3

    print(f"\n=== Running {stage} stage ===")
    result = invoke_and_wait(sagemaker_runtime, s3, payload)
    print(f"\nComplete: {result}")


def main():
    global ENDPOINT_NAME, REGION
    parser = argparse.ArgumentParser(description="Submit Hunyuan3D jobs to SageMaker Async")
    parser.add_argument("--stage", choices=["shape", "paint", "full"], default="full",
                       help="Stage to run (default: full pipeline)")
    parser.add_argument("--input", "-i", required=True, help="S3 URI of input image")
    parser.add_argument("--output", "-o", help="S3 URI for output (single stage)")
    parser.add_argument("--output-prefix", help="S3 prefix for outputs (full pipeline)")
    parser.add_argument("--shape", help="S3 URI of shape GLB (for paint stage)")
    parser.add_argument("--endpoint", default=ENDPOINT_NAME, help="SageMaker endpoint name")
    parser.add_argument("--region", default=REGION, help="AWS region")

    args = parser.parse_args()

    ENDPOINT_NAME = args.endpoint
    REGION = args.region

    if args.stage == "full":
        if not args.output_prefix:
            print("Error: --output-prefix required for full pipeline")
            return 1
        run_pipeline(args.input, args.output_prefix)
    else:
        if not args.output:
            print("Error: --output required for single stage")
            return 1
        if args.stage == "paint" and not args.shape:
            print("Error: --shape required for paint stage")
            return 1
        run_single_stage(args.stage, args.input, args.output, args.shape)

    return 0


if __name__ == "__main__":
    exit(main())
