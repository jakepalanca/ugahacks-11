#!/usr/bin/env python3
"""Submit async Hunyuan3D jobs to SageMaker.

Examples:
  python scripts/sagemaker_runtime/submit_job.py \
    --input s3://bucket/image.png \
    --output-prefix s3://bucket/jobs/job123

  python scripts/sagemaker_runtime/submit_job.py \
    --stage shape \
    --input s3://bucket/image.png \
    --output s3://bucket/shape.glb

  python scripts/sagemaker_runtime/submit_job.py \
    --stage paint \
    --input s3://bucket/image.png \
    --shape s3://bucket/shape.glb \
    --output s3://bucket/textured.glb
"""

from __future__ import annotations

import argparse
import json
import os
import time
import uuid

import boto3
from botocore.exceptions import ClientError


DEFAULT_ENDPOINT = os.environ.get("ENDPOINT_NAME", "hunyuan3d-async-v2")
DEFAULT_REGION = os.environ.get("AWS_REGION", "us-east-1")
DEFAULT_IO_BUCKET = os.environ.get("ASYNC_IO_BUCKET", "")


def invoke_and_wait(
    *,
    sagemaker_runtime,
    s3,
    endpoint_name: str,
    payload: dict,
    io_bucket: str,
    poll_interval: int = 10,
    max_wait: int = 900,
) -> dict:
    """Upload async payload, invoke endpoint, then poll for output JSON."""
    job_id = str(uuid.uuid4())[:8]
    input_key = f"async-input/{job_id}.json"

    print(f"Uploading request to s3://{io_bucket}/{input_key}")
    s3.put_object(
        Bucket=io_bucket,
        Key=input_key,
        Body=json.dumps(payload),
        ContentType="application/json",
    )

    input_location = f"s3://{io_bucket}/{input_key}"

    print(f"Invoking endpoint: {endpoint_name}")
    response = sagemaker_runtime.invoke_endpoint_async(
        EndpointName=endpoint_name,
        InputLocation=input_location,
        ContentType="application/json",
        Accept="application/json",
        InferenceId=job_id,
    )

    output_location = response["OutputLocation"]
    print(f"Job submitted. Output will be at: {output_location}")

    output_bucket, output_key = output_location.replace("s3://", "", 1).split("/", 1)
    failure_key = output_key.replace("async-output/", "async-failures/")

    print("Waiting for result...")
    elapsed = 0
    while elapsed < max_wait:
        time.sleep(poll_interval)
        elapsed += poll_interval

        try:
            result = s3.get_object(Bucket=output_bucket, Key=output_key)
            body = result["Body"].read().decode("utf-8")
            print("")
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                print(f"Warning: non-JSON response: {body[:200]}")
                return {"status": "success", "raw_response": body}
        except ClientError as exc:
            if exc.response["Error"]["Code"] not in ("NoSuchKey", "404"):
                raise

        try:
            failure = s3.get_object(Bucket=output_bucket, Key=failure_key)
            error_body = failure["Body"].read().decode("utf-8")
            raise RuntimeError(f"Inference failed: {error_body}")
        except ClientError:
            pass

        print(".", end="", flush=True)

    raise TimeoutError(f"Inference did not complete within {max_wait}s")


def run_pipeline(*, input_s3: str, output_prefix: str, endpoint_name: str, region: str, io_bucket: str):
    """Run full shape+paint workflow."""
    sagemaker_runtime = boto3.client("sagemaker-runtime", region_name=region)
    s3 = boto3.client("s3", region_name=region)

    output_prefix = output_prefix.rstrip("/")
    shape_output = f"{output_prefix}/shape.glb"
    paint_output = f"{output_prefix}/textured.glb"

    print("\n=== Stage 1: Generating Shape ===")
    shape_payload = {
        "stage": "shape",
        "input_s3": input_s3,
        "output_s3": shape_output,
    }
    shape_result = invoke_and_wait(
        sagemaker_runtime=sagemaker_runtime,
        s3=s3,
        endpoint_name=endpoint_name,
        payload=shape_payload,
        io_bucket=io_bucket,
    )
    print(f"\nShape complete: {shape_result}")

    shape_bucket, shape_key = shape_output.replace("s3://", "", 1).split("/", 1)
    try:
        s3.head_object(Bucket=shape_bucket, Key=shape_key)
    except ClientError as exc:
        raise RuntimeError(f"Shape file not found at {shape_output}") from exc

    print("\n=== Stage 2: Applying Texture ===")
    paint_payload = {
        "stage": "paint",
        "input_s3": input_s3,
        "shape_s3": shape_output,
        "output_s3": paint_output,
    }
    paint_result = invoke_and_wait(
        sagemaker_runtime=sagemaker_runtime,
        s3=s3,
        endpoint_name=endpoint_name,
        payload=paint_payload,
        io_bucket=io_bucket,
    )
    print(f"\nPaint complete: {paint_result}")

    print("\n=== Pipeline Complete ===")
    print(f"Shape:    {shape_output}")
    print(f"Textured: {paint_output}")


def run_single_stage(
    *,
    stage: str,
    input_s3: str,
    output_s3: str,
    shape_s3: str | None,
    endpoint_name: str,
    region: str,
    io_bucket: str,
):
    """Run a single pipeline stage."""
    sagemaker_runtime = boto3.client("sagemaker-runtime", region_name=region)
    s3 = boto3.client("s3", region_name=region)

    payload = {
        "stage": stage,
        "input_s3": input_s3,
        "output_s3": output_s3,
    }
    if stage == "paint" and shape_s3:
        payload["shape_s3"] = shape_s3

    print(f"\n=== Running {stage} stage ===")
    result = invoke_and_wait(
        sagemaker_runtime=sagemaker_runtime,
        s3=s3,
        endpoint_name=endpoint_name,
        payload=payload,
        io_bucket=io_bucket,
    )
    print(f"\nComplete: {result}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Submit Hunyuan3D jobs to SageMaker Async")
    parser.add_argument(
        "--stage",
        choices=["shape", "paint", "full"],
        default="full",
        help="Stage to run (default: full pipeline)",
    )
    parser.add_argument("--input", "-i", required=True, help="S3 URI of input image")
    parser.add_argument("--output", "-o", help="S3 URI for output (single stage)")
    parser.add_argument("--output-prefix", help="S3 prefix for outputs (full pipeline)")
    parser.add_argument("--shape", help="S3 URI of shape GLB (for paint stage)")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT, help="SageMaker endpoint name")
    parser.add_argument("--region", default=DEFAULT_REGION, help="AWS region")
    parser.add_argument(
        "--io-bucket",
        default=DEFAULT_IO_BUCKET,
        help="Bucket for async input payload objects (or set ASYNC_IO_BUCKET)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.io_bucket:
        print("Error: --io-bucket required (or set ASYNC_IO_BUCKET)")
        return 1

    if args.stage == "full":
        if not args.output_prefix:
            print("Error: --output-prefix required for full pipeline")
            return 1
        run_pipeline(
            input_s3=args.input,
            output_prefix=args.output_prefix,
            endpoint_name=args.endpoint,
            region=args.region,
            io_bucket=args.io_bucket,
        )
        return 0

    if not args.output:
        print("Error: --output required for single stage")
        return 1

    if args.stage == "paint" and not args.shape:
        print("Error: --shape required for paint stage")
        return 1

    run_single_stage(
        stage=args.stage,
        input_s3=args.input,
        output_s3=args.output,
        shape_s3=args.shape,
        endpoint_name=args.endpoint,
        region=args.region,
        io_bucket=args.io_bucket,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
