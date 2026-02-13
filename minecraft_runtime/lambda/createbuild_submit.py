"""
API Lambda: accepts /createbuild requests and queues worker execution.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from typing import Dict, Tuple

import boto3
from botocore.exceptions import ClientError


JOB_TABLE = os.environ["JOB_TABLE"]
WORKER_FUNCTION = os.environ["WORKER_FUNCTION"]
JOB_TTL_SECONDS = int(os.environ.get("JOB_TTL_SECONDS", "604800"))  # 7 days
DEFAULT_WORLD = os.environ.get("DEFAULT_WORLD", "world")
API_TOKEN = os.environ.get("API_TOKEN", "").strip()
ALLOW_UNAUTHENTICATED_REQUESTS = os.environ.get("ALLOW_UNAUTHENTICATED_REQUESTS", "0").strip().lower() in {
    "1",
    "true",
    "yes",
}
WORKER_LOCK_KEY = os.environ.get("WORKER_LOCK_KEY", "__worker_lock__")
WORKER_LOCK_TTL_SECONDS = int(os.environ.get("WORKER_LOCK_TTL_SECONDS", "7200"))
VALID_SIZES = {"small", "medium", "large"}

dynamodb = boto3.resource("dynamodb")
lambda_client = boto3.client("lambda")
table = dynamodb.Table(JOB_TABLE)


def _response(status_code: int, payload: Dict) -> Dict:
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(payload),
    }


def _parse_event(event: Dict) -> Dict:
    body = event.get("body")
    if body is None:
        return event
    if isinstance(body, str):
        return json.loads(body) if body else {}
    if isinstance(body, dict):
        return body
    raise ValueError("Request body must be JSON object")


def _normalize_anchor(anchor: Dict) -> Tuple[int, int, int]:
    if not isinstance(anchor, dict):
        raise ValueError("anchor must be an object with x/y/z")
    try:
        x = int(anchor["x"])
        y = int(anchor["y"])
        z = int(anchor["z"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("anchor must include numeric x, y, z") from exc
    return x, y, z


def _is_authorized(event: Dict) -> bool:
    if ALLOW_UNAUTHENTICATED_REQUESTS:
        return True

    if not API_TOKEN:
        return False

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


def _acquire_worker_lock(job_id: str, *, now_epoch: int) -> bool:
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_epoch))
    try:
        table.update_item(
            Key={"job_id": WORKER_LOCK_KEY},
            UpdateExpression=(
                "SET owner_job_id = :owner, locked_until = :locked_until, "
                "updated_at = :updated_at, created_at = if_not_exists(created_at, :created_at), #st = :st"
            ),
            ConditionExpression=(
                "attribute_not_exists(job_id) OR attribute_not_exists(locked_until) "
                "OR locked_until < :now OR owner_job_id = :owner"
            ),
            ExpressionAttributeNames={"#st": "status"},
            ExpressionAttributeValues={
                ":owner": job_id,
                ":locked_until": now_epoch + WORKER_LOCK_TTL_SECONDS,
                ":updated_at": now_iso,
                ":created_at": now_iso,
                ":st": "LOCKED",
                ":now": now_epoch,
            },
        )
        return True
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code == "ConditionalCheckFailedException":
            return False
        raise


def _release_worker_lock(job_id: str, *, now_epoch: int):
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_epoch))
    try:
        table.update_item(
            Key={"job_id": WORKER_LOCK_KEY},
            UpdateExpression="SET locked_until = :now, updated_at = :updated_at, #st = :st REMOVE owner_job_id",
            ConditionExpression="owner_job_id = :owner",
            ExpressionAttributeNames={"#st": "status"},
            ExpressionAttributeValues={
                ":owner": job_id,
                ":now": now_epoch,
                ":updated_at": now_iso,
                ":st": "IDLE",
            },
        )
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code != "ConditionalCheckFailedException":
            raise


def _invoke_worker(job_id: str):
    lambda_client.invoke(
        FunctionName=WORKER_FUNCTION,
        InvocationType="Event",
        Payload=json.dumps({"job_id": job_id}).encode("utf-8"),
    )


def _try_start_worker(job_id: str, *, now_epoch: int) -> bool:
    if not _acquire_worker_lock(job_id, now_epoch=now_epoch):
        return False

    try:
        table.update_item(
            Key={"job_id": job_id},
            UpdateExpression="SET #st = :st, updated_at = :updated_at",
            ExpressionAttributeNames={"#st": "status"},
            ExpressionAttributeValues={
                ":st": "STARTING",
                ":updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_epoch)),
            },
        )
        _invoke_worker(job_id)
        return True
    except Exception:
        table.update_item(
            Key={"job_id": job_id},
            UpdateExpression="SET #st = :st, updated_at = :updated_at",
            ExpressionAttributeNames={"#st": "status"},
            ExpressionAttributeValues={
                ":st": "QUEUED",
                ":updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_epoch)),
            },
        )
        _release_worker_lock(job_id, now_epoch=now_epoch)
        raise


def handler(event, _context):
    try:
        event = event or {}
        if not API_TOKEN and not ALLOW_UNAUTHENTICATED_REQUESTS:
            return _response(
                503,
                {
                    "error": (
                        "server misconfigured: set API_TOKEN or explicitly set "
                        "ALLOW_UNAUTHENTICATED_REQUESTS=1"
                    )
                },
            )

        if not _is_authorized(event):
            return _response(401, {"error": "unauthorized"})

        request = _parse_event(event)
        prompt = str(request.get("prompt", "")).strip()
        size = str(request.get("size", "")).strip().lower()
        player_uuid = str(request.get("playerUuid", "")).strip()
        player_name = str(request.get("playerName", "")).strip()
        world = str(request.get("world", DEFAULT_WORLD)).strip() or DEFAULT_WORLD
        anchor_x, anchor_y, anchor_z = _normalize_anchor(request.get("anchor", {}))

        if not prompt:
            return _response(400, {"error": "prompt is required"})
        if size not in VALID_SIZES:
            return _response(400, {"error": "size must be one of small, medium, large"})

        now_epoch = int(time.time())
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_epoch))
        job_id = uuid.uuid4().hex[:12]
        item = {
            "job_id": job_id,
            "status": "QUEUED",
            "progress_stage": "queued",
            "progress_message": "Build queued. Waiting for active build slot...",
            "prompt": prompt,
            "size": size,
            "world": world,
            "anchor": {"x": anchor_x, "y": anchor_y, "z": anchor_z},
            "player_uuid": player_uuid,
            "player_name": player_name,
            "created_at": now_iso,
            "updated_at": now_iso,
            "expires_at": now_epoch + JOB_TTL_SECONDS,
        }

        table.put_item(Item=item, ConditionExpression="attribute_not_exists(job_id)")

        started = _try_start_worker(job_id, now_epoch=now_epoch)

        return _response(202, {"jobId": job_id, "status": "QUEUED", "started": started})
    except Exception as exc:
        return _response(500, {"error": str(exc)})
