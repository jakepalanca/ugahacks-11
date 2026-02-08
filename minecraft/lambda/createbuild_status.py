"""
API Lambda: fetch build status and return command batch URLs.
"""
from __future__ import annotations

import json
import os
from decimal import Decimal
from typing import Dict, List, Sequence

import boto3


JOB_TABLE = os.environ["JOB_TABLE"]
DEFAULT_COMMAND_BUCKET = os.environ.get("COMMAND_BUCKET", "")
SIGN_COMMAND_URLS = os.environ.get("SIGN_COMMAND_URLS", "1") != "0"
PRESIGN_TTL_SECONDS = int(os.environ.get("PRESIGN_TTL_SECONDS", "3600"))
API_TOKEN = os.environ.get("API_TOKEN", "").strip()

dynamodb = boto3.resource("dynamodb")
s3 = boto3.client("s3")
table = dynamodb.Table(JOB_TABLE)


def _json_safe(value):
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        return float(value)
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def _response(status_code: int, payload: Dict) -> Dict:
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(_json_safe(payload)),
    }


def _get_job_id(event: Dict) -> str:
    path = event.get("pathParameters") or {}
    query = event.get("queryStringParameters") or {}
    job_id = str(path.get("jobId") or query.get("jobId") or "").strip()
    return job_id


def _is_authorized(event: Dict) -> bool:
    if not API_TOKEN:
        return True

    headers = event.get("headers") or {}
    if not isinstance(headers, dict):
        return False

    auth = ""
    for key, value in headers.items():
        if key and key.lower() == "authorization":
            auth = str(value or "").strip()
            break

    if not auth:
        return False

    if auth.lower().startswith("bearer "):
        auth = auth[7:].strip()
    return auth == API_TOKEN


def _list_keys(bucket: str, prefix: str, suffixes: Sequence[str]) -> List[str]:
    allowed = tuple(suffix.lower() for suffix in suffixes)
    keys: List[str] = []
    continuation = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": prefix}
        if continuation:
            kwargs["ContinuationToken"] = continuation
        response = s3.list_objects_v2(**kwargs)
        for item in response.get("Contents", []):
            key = item.get("Key")
            if key and key.lower().endswith(allowed):
                keys.append(key)
        if not response.get("IsTruncated"):
            break
        continuation = response.get("NextContinuationToken")
    keys.sort()
    return keys


def _to_url(bucket: str, key: str) -> str:
    if SIGN_COMMAND_URLS:
        return s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=PRESIGN_TTL_SECONDS,
        )
    return f"s3://{bucket}/{key}"


def handler(event, _context):
    try:
        event = event or {}
        if not _is_authorized(event):
            return _response(401, {"error": "unauthorized"})

        job_id = _get_job_id(event)
        if not job_id:
            return _response(400, {"error": "jobId is required"})

        response = table.get_item(Key={"job_id": job_id})
        item = response.get("Item")
        if not item:
            return _response(404, {"error": f"job not found: {job_id}"})

        payload = {
            "jobId": item.get("job_id"),
            "status": item.get("status", "UNKNOWN"),
            "error": item.get("error", ""),
            "createdAt": item.get("created_at"),
            "updatedAt": item.get("updated_at"),
            "progressStage": item.get("progress_stage", ""),
            "progressMessage": item.get("progress_message", ""),
            "voxelCount": item.get("voxel_count"),
            "dimensions": item.get("dimensions"),
            "block": item.get("block"),
            "texturedGlbS3": item.get("textured_s3"),
            "sourceMcfunctionS3": item.get("source_mcfunction_s3"),
        }

        if item.get("status") == "SUCCEEDED":
            bucket = item.get("command_bucket") or DEFAULT_COMMAND_BUCKET
            prefix = item.get("command_prefix", "")
            payload["commandBatchS3Prefix"] = f"s3://{bucket}/{prefix}" if bucket and prefix else ""

            if bucket and prefix:
                json_keys = _list_keys(bucket=bucket, prefix=prefix, suffixes=(".json",))
                mcfunction_keys = _list_keys(bucket=bucket, prefix=prefix, suffixes=(".mcfunction",))

                payload["commandBatchUrls"] = [_to_url(bucket, key) for key in json_keys]
                payload["commandBatchCount"] = len(json_keys)
                payload["mcfunctionUrls"] = [_to_url(bucket, key) for key in mcfunction_keys]
                payload["mcfunctionCount"] = len(mcfunction_keys)

        return _response(200, payload)
    except Exception as exc:
        return _response(500, {"error": str(exc)})
