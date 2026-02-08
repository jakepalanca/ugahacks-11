"""
Helpers for invoking the existing Hunyuan3D SageMaker async endpoint.
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Dict, Tuple

import boto3
from botocore.exceptions import ClientError


def split_s3_uri(s3_uri: str) -> Tuple[str, str]:
    if not s3_uri.startswith("s3://"):
        raise ValueError(f"Invalid S3 URI: {s3_uri}")
    bucket, key = s3_uri[5:].split("/", 1)
    return bucket, key


def _is_paint_oom_error(exc: Exception) -> bool:
    text = str(exc or "").lower()
    return "cuda out of memory" in text or "outofmemoryerror" in text


def _poll_async_output(
    s3_client,
    output_location: str,
    timeout_seconds: int,
    poll_seconds: int,
) -> Dict:
    out_bucket, out_key = split_s3_uri(output_location)
    failure_keys = []
    base_failure_key = out_key.replace("async-output/", "async-failures/")
    failure_keys.append(base_failure_key)
    if base_failure_key.endswith(".out"):
        failure_keys.append(base_failure_key[:-4] + "-error.out")

    started = time.time()
    while time.time() - started < timeout_seconds:
        try:
            response = s3_client.get_object(Bucket=out_bucket, Key=out_key)
            body = response["Body"].read().decode("utf-8")
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                return {"raw_response": body}
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code not in {"NoSuchKey", "404"}:
                raise

        found_failure = None
        for failure_key in failure_keys:
            try:
                response = s3_client.get_object(Bucket=out_bucket, Key=failure_key)
                body = response["Body"].read().decode("utf-8")
                found_failure = body
                break
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                if code not in {"NoSuchKey", "404"}:
                    raise

        if found_failure is not None:
            raise RuntimeError(f"SageMaker async failure: {found_failure}")

        time.sleep(poll_seconds)

    raise TimeoutError(f"Async endpoint output not found within {timeout_seconds} seconds")


def _invoke_stage(
    *,
    stage: str,
    endpoint_name: str,
    input_s3: str,
    output_s3: str,
    io_bucket: str,
    job_id: str,
    region: str,
    timeout_seconds: int,
    poll_seconds: int,
    shape_s3: str | None = None,
) -> Dict:
    s3_client = boto3.client("s3", region_name=region)
    runtime = boto3.client("sagemaker-runtime", region_name=region)

    payload = {
        "stage": stage,
        "input_s3": input_s3,
        "output_s3": output_s3,
    }
    if shape_s3:
        payload["shape_s3"] = shape_s3

    # Clear any stale artifact for this stage key before invoking SageMaker.
    # Lambda async retries can re-run the same job_id/stage and otherwise leave
    # an older output object in place, which makes diagnostics confusing.
    out_bucket, out_key = split_s3_uri(output_s3)
    try:
        s3_client.delete_object(Bucket=out_bucket, Key=out_key)
    except Exception:
        # Deletion failures here should not block new stage execution.
        pass

    request_key = f"async-input/{job_id}-{stage}-{uuid.uuid4().hex[:8]}.json"
    request_body = json.dumps(payload).encode("utf-8")
    s3_client.put_object(
        Bucket=io_bucket,
        Key=request_key,
        Body=request_body,
        ContentType="application/json",
    )

    response = runtime.invoke_endpoint_async(
        EndpointName=endpoint_name,
        InputLocation=f"s3://{io_bucket}/{request_key}",
        ContentType="application/json",
        Accept="application/json",
        InferenceId=f"{job_id}-{stage}",
    )
    result = _poll_async_output(
        s3_client=s3_client,
        output_location=response["OutputLocation"],
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
    )

    # Ensure target artifact really exists after this stage invocation.
    s3_client.head_object(Bucket=out_bucket, Key=out_key)
    return result


def run_full_pipeline(
    *,
    input_s3: str,
    output_prefix_s3: str,
    job_id: str,
    endpoint_name: str,
    io_bucket: str,
    region: str,
    timeout_seconds: int = 1800,
    poll_seconds: int = 8,
    progress_hook=None,
) -> Dict[str, str]:
    output_prefix_s3 = output_prefix_s3.rstrip("/")
    shape_s3 = f"{output_prefix_s3}/shape.glb"
    textured_s3 = f"{output_prefix_s3}/textured.glb"

    if callable(progress_hook):
        progress_hook("shape_start")
    _invoke_stage(
        stage="shape",
        endpoint_name=endpoint_name,
        input_s3=input_s3,
        output_s3=shape_s3,
        io_bucket=io_bucket,
        job_id=job_id,
        region=region,
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
    )
    if callable(progress_hook):
        progress_hook("shape_done")
        progress_hook("paint_start")
    try:
        _invoke_stage(
            stage="paint",
            endpoint_name=endpoint_name,
            input_s3=input_s3,
            output_s3=textured_s3,
            shape_s3=shape_s3,
            io_bucket=io_bucket,
            job_id=job_id,
            region=region,
            timeout_seconds=timeout_seconds,
            poll_seconds=poll_seconds,
        )
        if callable(progress_hook):
            progress_hook("paint_done")
        return {
            "shape_s3": shape_s3,
            "textured_s3": textured_s3,
            "paint_fallback": "none",
            "paint_error": "",
        }
    except Exception as exc:
        if not _is_paint_oom_error(exc):
            raise
        if callable(progress_hook):
            progress_hook("paint_oom_fallback")
        return {
            "shape_s3": shape_s3,
            "textured_s3": shape_s3,
            "paint_fallback": "shape_mesh_due_to_paint_oom",
            "paint_error": str(exc)[:1200],
        }
