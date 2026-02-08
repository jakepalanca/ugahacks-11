"""
Worker Lambda: text->image -> Hunyuan3D -> GLB->vox lambda -> anchored chunked mcfunctions.
"""
from __future__ import annotations

import json
import os
from collections import Counter
import math
import tempfile
import time
import traceback
from typing import Dict, Iterable

import boto3
from boto3.dynamodb.conditions import Attr
from botocore.exceptions import ClientError

from hunyuan_async import run_full_pipeline, split_s3_uri


REGION = os.environ.get("AWS_REGION", "us-east-1")
JOB_TABLE = os.environ["JOB_TABLE"]
TEXT2IMAGE_FUNCTION = os.environ.get("TEXT2IMAGE_FUNCTION", "hackathon_textToImage")
GLB_TO_VOX_FUNCTION = os.environ.get("GLB_TO_VOX_FUNCTION", "createbuild_glb_to_vox")
HUNYUAN_ENDPOINT = os.environ.get("HUNYUAN_ENDPOINT", "hunyuan3d-async-v2")
HUNYUAN_IO_BUCKET = os.environ.get("HUNYUAN_IO_BUCKET", "hackathon-jobs-67")
ARTIFACT_BUCKET = os.environ.get("ARTIFACT_BUCKET", "hackathon-jobs-67")
COMMAND_BUCKET = os.environ.get("COMMAND_BUCKET", ARTIFACT_BUCKET)
COMMAND_PREFIX = os.environ.get("COMMAND_PREFIX", "minecraft-builds")
COMMAND_CHUNK_SIZE = int(os.environ.get("COMMAND_CHUNK_SIZE", "256"))
PLACEMENT_PASSES = max(1, int(os.environ.get("PLACEMENT_PASSES", "2")))
ENABLE_FORCELOAD = os.environ.get("ENABLE_FORCELOAD", "0") != "0"
MAX_FORCELOAD_CHUNKS = max(1, int(os.environ.get("MAX_FORCELOAD_CHUNKS", "256")))
SAGEMAKER_TIMEOUT_SECONDS = int(os.environ.get("SAGEMAKER_TIMEOUT_SECONDS", "1800"))
SAGEMAKER_POLL_SECONDS = int(os.environ.get("SAGEMAKER_POLL_SECONDS", "8"))
ORIENTATION_ROTATE_Y_QUARTER_TURNS = int(os.environ.get("ORIENTATION_ROTATE_Y_QUARTER_TURNS", "0"))
WORKER_LOCK_KEY = os.environ.get("WORKER_LOCK_KEY", "__worker_lock__")
WORKER_LOCK_TTL_SECONDS = int(os.environ.get("WORKER_LOCK_TTL_SECONDS", "7200"))

dynamodb = boto3.resource("dynamodb", region_name=REGION)
table = dynamodb.Table(JOB_TABLE)
lambda_client = boto3.client("lambda", region_name=REGION)
s3 = boto3.client("s3", region_name=REGION)


class UserVisibleError(Exception):
    def __init__(self, user_message: str, internal_message: str | None = None):
        super().__init__(internal_message or user_message)
        self.user_message = user_message
        self.internal_message = internal_message or user_message


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _update_job(job_id: str, fields: Dict):
    if not fields:
        return
    fields = dict(fields)
    fields["updated_at"] = _now_iso()

    names = {}
    values = {}
    updates = []
    for idx, (key, value) in enumerate(fields.items()):
        name_key = f"#k{idx}"
        value_key = f":v{idx}"
        names[name_key] = key
        values[value_key] = value
        updates.append(f"{name_key} = {value_key}")

    table.update_item(
        Key={"job_id": job_id},
        UpdateExpression="SET " + ", ".join(updates),
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
    )


def _set_progress(job_id: str, stage: str, message: str):
    _update_job(
        job_id,
        {
            "progress_stage": str(stage or "").strip(),
            "progress_message": str(message or "").strip(),
        },
    )


def _read_job(job_id: str) -> Dict:
    response = table.get_item(Key={"job_id": job_id})
    item = response.get("Item")
    if not item:
        raise KeyError(f"Job not found: {job_id}")
    return item


def _invoke_worker_async(job_id: str):
    lambda_client.invoke(
        FunctionName=os.environ.get("AWS_LAMBDA_FUNCTION_NAME", "createbuild_worker"),
        InvocationType="Event",
        Payload=json.dumps({"job_id": job_id}).encode("utf-8"),
    )


def _acquire_worker_lock(job_id: str, *, now_epoch: int | None = None) -> bool:
    if now_epoch is None:
        now_epoch = int(time.time())
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


def _release_worker_lock(job_id: str, *, now_epoch: int | None = None):
    if now_epoch is None:
        now_epoch = int(time.time())
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


def _find_next_queued_job(*, exclude_job_id: str = "") -> str:
    filter_expression = Attr("status").eq("QUEUED") & Attr("job_id").ne(WORKER_LOCK_KEY)
    if exclude_job_id:
        filter_expression = filter_expression & Attr("job_id").ne(exclude_job_id)

    scan_kwargs = {
        "ProjectionExpression": "job_id, created_at, #st",
        "ExpressionAttributeNames": {"#st": "status"},
        "FilterExpression": filter_expression,
    }

    candidates = []
    while True:
        response = table.scan(**scan_kwargs)
        candidates.extend(response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
        scan_kwargs["ExclusiveStartKey"] = last_key

    if not candidates:
        return ""

    candidates.sort(key=lambda item: (str(item.get("created_at", "")), str(item.get("job_id", ""))))
    return str(candidates[0].get("job_id", "")).strip()


def _try_start_next_queued_job(*, exclude_job_id: str = "") -> str:
    next_job_id = _find_next_queued_job(exclude_job_id=exclude_job_id)
    if not next_job_id:
        return ""
    if not _acquire_worker_lock(next_job_id):
        return ""

    now_iso = _now_iso()
    try:
        table.update_item(
            Key={"job_id": next_job_id},
            UpdateExpression="SET #st = :starting, updated_at = :updated_at, progress_stage = :progress_stage, progress_message = :progress_message",
            ConditionExpression="attribute_exists(job_id) AND #st = :queued",
            ExpressionAttributeNames={"#st": "status"},
            ExpressionAttributeValues={
                ":starting": "STARTING",
                ":queued": "QUEUED",
                ":updated_at": now_iso,
                ":progress_stage": "image_generation",
                ":progress_message": "Starting image generation from prompt...",
            },
        )
        _invoke_worker_async(next_job_id)
        return next_job_id
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code == "ConditionalCheckFailedException":
            _release_worker_lock(next_job_id)
            return ""
        _release_worker_lock(next_job_id)
        return ""
    except Exception:
        _release_worker_lock(next_job_id)
        return ""


def _transition_job_to_running(job_id: str) -> bool:
    now_iso = _now_iso()
    try:
        table.update_item(
            Key={"job_id": job_id},
            UpdateExpression="SET #st = :running, started_at = :started_at, updated_at = :updated_at",
            ConditionExpression="attribute_exists(job_id) AND (#st = :queued OR #st = :starting)",
            ExpressionAttributeNames={"#st": "status"},
            ExpressionAttributeValues={
                ":running": "RUNNING",
                ":queued": "QUEUED",
                ":starting": "STARTING",
                ":started_at": now_iso,
                ":updated_at": now_iso,
            },
        )
        return True
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code == "ConditionalCheckFailedException":
            return False
        raise


def _decode_lambda_payload(payload: str) -> Dict:
    if not payload:
        return {}
    try:
        decoded = json.loads(payload)
    except json.JSONDecodeError:
        return {"raw_payload": payload[:4000]}
    if isinstance(decoded, dict) and isinstance(decoded.get("body"), str):
        body = decoded.get("body", "")
        if body:
            try:
                body_json = json.loads(body)
                decoded.update(body_json)
            except json.JSONDecodeError:
                pass
    return decoded if isinstance(decoded, dict) else {}


def _normalize_text(value: str) -> str:
    return " ".join(str(value or "").replace("\x00", " ").split()).strip()


def _extract_error_message(value, depth: int = 0) -> str:
    if depth > 5:
        return ""

    if isinstance(value, str):
        return _normalize_text(value)

    if isinstance(value, list):
        parts = []
        for item in value:
            message = _extract_error_message(item, depth + 1)
            if message:
                parts.append(message)
        return " | ".join(parts)

    if isinstance(value, dict):
        priority_keys = (
            "errorMessage",
            "error_message",
            "error",
            "message",
            "detail",
            "details",
            "cause",
            "reason",
            "raw_payload",
            "errorType",
        )
        parts = []
        seen = set()

        for key in priority_keys:
            item = value.get(key)
            message = _extract_error_message(item, depth + 1)
            if message and message not in seen:
                parts.append(message)
                seen.add(message)

        for nested in value.values():
            message = _extract_error_message(nested, depth + 1)
            if message and message not in seen:
                parts.append(message)
                seen.add(message)

        return " | ".join(parts)

    return ""


def _looks_like_content_filter(error_message: str) -> bool:
    text = _normalize_text(error_message).lower()
    if not text:
        return False

    keywords = (
        "content filter",
        "blocked by our content filters",
        "responsible ai policy",
        "blocked this prompt",
        "validationexception",
        "guardrail",
        "safety policy",
        "disallowed content",
    )
    if any(keyword in text for keyword in keywords):
        return True

    return "blocked" in text and "prompt" in text


def _raise_text2image_user_error(status_code, decoded: Dict):
    error_message = _extract_error_message(decoded)
    if _looks_like_content_filter(error_message):
        raise UserVisibleError(
            "Prompt was blocked by safety filters. Try a safer or simpler prompt and run /createbuild again.",
            f"textToImage content-filter error status={status_code}: {_normalize_text(error_message)[:900]}",
        )

    concise = _normalize_text(error_message)
    if concise:
        raise UserVisibleError(
            "Image generation failed for this prompt. Try rewording and run /createbuild again.",
            f"textToImage error status={status_code}: {concise[:900]}",
        )

    raise UserVisibleError(
        "Image generation failed before 3D conversion. Try a different prompt and retry.",
        f"textToImage failure status={status_code}: {str(decoded)[:900]}",
    )


def _extract_first_s3_uri(value, *, keys: Iterable[str]) -> str:
    if isinstance(value, dict):
        for key in keys:
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.startswith("s3://"):
                return candidate
        for nested in value.values():
            found = _extract_first_s3_uri(nested, keys=keys)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _extract_first_s3_uri(item, keys=keys)
            if found:
                return found
    return ""


def _extract_bucket_key_s3_uri(value) -> str:
    bucket_fields = ("bucket", "out_bucket", "outBucket", "s3_bucket", "s3Bucket")
    key_fields = ("key", "out_key", "outKey", "s3_key", "s3Key")

    if isinstance(value, dict):
        for bucket_field in bucket_fields:
            bucket = value.get(bucket_field)
            if not isinstance(bucket, str) or not bucket.strip():
                continue
            for key_field in key_fields:
                key = value.get(key_field)
                if isinstance(key, str) and key.strip():
                    return f"s3://{bucket.strip()}/{key.strip().lstrip('/')}"

        for nested in value.values():
            found = _extract_bucket_key_s3_uri(nested)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _extract_bucket_key_s3_uri(item)
            if found:
                return found
    return ""


def _invoke_text_to_image(prompt: str, size: str, job_id: str) -> str:
    payload = {"prompt": prompt, "size": size, "jobId": job_id}
    response = lambda_client.invoke(
        FunctionName=TEXT2IMAGE_FUNCTION,
        InvocationType="RequestResponse",
        Payload=json.dumps(payload).encode("utf-8"),
    )
    raw = response["Payload"].read().decode("utf-8")
    decoded = _decode_lambda_payload(raw)

    status_code = decoded.get("statusCode")
    if isinstance(status_code, str) and status_code.isdigit():
        status_code = int(status_code)
    if isinstance(status_code, int) and status_code >= 400:
        _raise_text2image_user_error(status_code, decoded)

    image_s3 = _extract_first_s3_uri(
        decoded,
        keys=("image_s3", "imageS3", "output_s3", "outputS3", "s3_uri", "s3Uri"),
    )
    if not image_s3:
        image_s3 = _extract_bucket_key_s3_uri(decoded)
    if not image_s3:
        _raise_text2image_user_error(status_code, decoded)
    return image_s3


def _download_s3_to_file(s3_uri: str, local_path: str):
    bucket, key = split_s3_uri(s3_uri)
    s3.download_file(bucket, key, local_path)


def _detect_mesh_format_from_bytes(head: bytes) -> str:
    if not head:
        return ""
    if head.startswith(b"glTF"):
        return "glb"

    stripped = head.lstrip()
    lowered = stripped.lower()
    if lowered.startswith(b"ply"):
        return "ply"
    if lowered.startswith(b"solid"):
        return "stl"

    ascii_head = head.decode("utf-8", errors="ignore").lower()
    if (
        ascii_head.startswith("o ")
        or ascii_head.startswith("v ")
        or "\nmtllib " in ascii_head
        or "\nv " in ascii_head
        or "\nvt " in ascii_head
        or "\nvn " in ascii_head
        or "\nf " in ascii_head
    ):
        return "obj"

    if lowered.startswith(b"{") or lowered.startswith(b"["):
        return "json"

    return ""


def _probe_s3_mesh_format(s3_uri: str) -> str:
    bucket, key = split_s3_uri(s3_uri)
    response = s3.get_object(Bucket=bucket, Key=key, Range="bytes=0-4095")
    head = response["Body"].read()
    return _detect_mesh_format_from_bytes(head)


def _select_mesh_input(shape_s3: str, textured_s3: str):
    supported = {"glb", "obj", "ply", "stl"}
    textured_format = ""
    shape_format = ""

    try:
        textured_format = _probe_s3_mesh_format(textured_s3)
    except Exception:
        textured_format = ""
    try:
        shape_format = _probe_s3_mesh_format(shape_s3)
    except Exception:
        shape_format = ""

    if textured_format in supported:
        return {
            "selected_s3": textured_s3,
            "selected_stage": "textured",
            "selected_format": textured_format,
            "textured_format": textured_format,
            "shape_format": shape_format,
        }
    if shape_format in supported:
        return {
            "selected_s3": shape_s3,
            "selected_stage": "shape",
            "selected_format": shape_format,
            "textured_format": textured_format,
            "shape_format": shape_format,
        }
    raise UserVisibleError(
        "3D conversion output was invalid. Please retry /createbuild in a moment.",
        f"Unsupported mesh artifacts: textured={textured_s3} ({textured_format}), shape={shape_s3} ({shape_format})",
    )


def _parse_relative_coordinate(token: str) -> int:
    token = token.strip()
    if token.startswith("~"):
        raw = token[1:].strip()
        if not raw:
            return 0
        return int(float(raw))
    return int(float(token))


def _parse_mcfunction_commands(mcfunction_text: str):
    entries = []
    for raw_line in mcfunction_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("/"):
            line = line[1:].strip()
        parts = line.split()
        if len(parts) < 5 or parts[0].lower() != "setblock":
            continue

        rel_x = _parse_relative_coordinate(parts[1])
        rel_y = _parse_relative_coordinate(parts[2])
        rel_z = _parse_relative_coordinate(parts[3])
        block = parts[4]
        entries.append((rel_x, rel_y, rel_z, block))

    if not entries:
        raise ValueError("No valid setblock commands found in mcfunction content")
    return entries


def _rotate_entries_y(entries, quarter_turns: int):
    turns = quarter_turns % 4
    if turns == 0:
        return list(entries)

    rotated = []
    for rel_x, rel_y, rel_z, block in entries:
        if turns == 1:
            out_x, out_z = -rel_z, rel_x
        elif turns == 2:
            out_x, out_z = -rel_x, -rel_z
        else:
            out_x, out_z = rel_z, -rel_x
        rotated.append((out_x, rel_y, out_z, block))
    return rotated


def _anchor_entries_to_commands(entries, anchor: Dict[str, int]) -> Dict:
    entries = _rotate_entries_y(entries, ORIENTATION_ROTATE_Y_QUARTER_TURNS)

    xs = [entry[0] for entry in entries]
    ys = [entry[1] for entry in entries]
    zs = [entry[2] for entry in entries]

    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    min_z, max_z = min(zs), max(zs)

    span_x = max_x - min_x + 1
    span_y = max_y - min_y + 1
    span_z = max_z - min_z + 1

    start_x = int(anchor["x"]) - span_x // 2
    start_y = int(anchor["y"])
    start_z = int(anchor["z"]) - span_z // 2

    block_counts = Counter()
    commands = []

    for rel_x, rel_y, rel_z, block in entries:
        world_x = start_x + (rel_x - min_x)
        world_y = start_y + (rel_y - min_y)
        world_z = start_z + (rel_z - min_z)
        commands.append(f"setblock {world_x} {world_y} {world_z} {block} replace")
        block_counts[block] += 1

    dominant_block = block_counts.most_common(1)[0][0]
    dimensions = {"x": span_x, "y": span_y, "z": span_z}
    bounds = {
        "min_x": int(start_x),
        "max_x": int(start_x + span_x - 1),
        "min_y": int(start_y),
        "max_y": int(start_y + span_y - 1),
        "min_z": int(start_z),
        "max_z": int(start_z + span_z - 1),
    }
    return {
        "commands": commands,
        "dimensions": dimensions,
        "orientation_quarter_turns": ORIENTATION_ROTATE_Y_QUARTER_TURNS % 4,
        "bounds": bounds,
        "block": dominant_block,
    }


def _chunk_commands(commands, chunk_size: int):
    if chunk_size <= 0:
        raise ValueError("chunk_size must be > 0")
    return [commands[index : index + chunk_size] for index in range(0, len(commands), chunk_size)]


def _repeat_placement_commands(commands, passes: int):
    if passes <= 1:
        return list(commands)
    expanded = []
    for _ in range(passes):
        expanded.extend(commands)
    return expanded


def _forceload_commands(bounds: Dict[str, int]):
    chunk_min_x = math.floor(int(bounds["min_x"]) / 16)
    chunk_max_x = math.floor(int(bounds["max_x"]) / 16)
    chunk_min_z = math.floor(int(bounds["min_z"]) / 16)
    chunk_max_z = math.floor(int(bounds["max_z"]) / 16)
    chunk_count = (chunk_max_x - chunk_min_x + 1) * (chunk_max_z - chunk_min_z + 1)
    if chunk_count > MAX_FORCELOAD_CHUNKS:
        return {"enabled": False, "chunk_count": chunk_count, "add_command": "", "remove_command": ""}

    add_command = f"forceload add {chunk_min_x} {chunk_min_z} {chunk_max_x} {chunk_max_z}"
    remove_command = f"forceload remove {chunk_min_x} {chunk_min_z} {chunk_max_x} {chunk_max_z}"
    return {
        "enabled": True,
        "chunk_count": chunk_count,
        "add_command": add_command,
        "remove_command": remove_command,
    }


def _upload_mcfunction_batches(job_id: str, batches) -> Dict:
    normalized_prefix = COMMAND_PREFIX.strip("/").rstrip("/")
    if normalized_prefix:
        normalized_prefix = normalized_prefix + "/"
    key_prefix = f"{normalized_prefix}{job_id}/"

    for index, commands in enumerate(batches, start=1):
        body = ("\n".join(commands) + "\n").encode("utf-8")
        s3.put_object(
            Bucket=COMMAND_BUCKET,
            Key=f"{key_prefix}batch-{index:05d}.mcfunction",
            Body=body,
            ContentType="text/plain",
        )

    return {
        "command_bucket": COMMAND_BUCKET,
        "command_prefix": key_prefix,
        "batch_count": len(batches),
    }


def _invoke_glb_to_vox(*, mesh_s3: str, anchor: Dict[str, int], size: str, job_id: str) -> Dict:
    payload = {
        "s3_uri": mesh_s3,
        "jobId": job_id,
        "size": size,
        "anchor": {
            "x": int(anchor["x"]),
            "y": int(anchor["y"]),
            "z": int(anchor["z"]),
        },
    }
    response = lambda_client.invoke(
        FunctionName=GLB_TO_VOX_FUNCTION,
        InvocationType="RequestResponse",
        Payload=json.dumps(payload).encode("utf-8"),
    )
    raw = response["Payload"].read().decode("utf-8")
    decoded = _decode_lambda_payload(raw)

    status_code = decoded.get("statusCode")
    if isinstance(status_code, int) and status_code >= 400:
        raise RuntimeError(f"glb_to_vox failed with statusCode {status_code}: {decoded}")

    mcfunction_s3 = _extract_first_s3_uri(
        decoded,
        keys=("output_location", "outputLocation", "mcfunction_s3", "mcfunctionS3", "output_s3", "outputS3", "s3_uri", "s3Uri"),
    )
    if not mcfunction_s3:
        mcfunction_s3 = _extract_bucket_key_s3_uri(decoded)
    if not mcfunction_s3:
        raise RuntimeError(f"glb_to_vox response missing mcfunction s3 uri: {decoded}")

    return {
        "mcfunction_s3": mcfunction_s3,
        "block_count": decoded.get("block_count"),
        "raw_response": decoded,
    }


def handler(event, _context):
    job_id = str((event or {}).get("job_id", "")).strip()
    if not job_id:
        raise ValueError("job_id is required")

    job = _read_job(job_id)
    current_status = str(job.get("status", "UNKNOWN")).upper()
    if current_status in {"FAILED", "SUCCEEDED", "CANCELED"}:
        # Ignore duplicate async invokes for terminal jobs.
        return {"job_id": job_id, "status": current_status, "skipped": True}

    if not _acquire_worker_lock(job_id):
        current_status = str(_read_job(job_id).get("status", "UNKNOWN")).upper()
        if current_status in {"QUEUED", "STARTING"}:
            _update_job(
                job_id,
                {
                    "status": "QUEUED",
                    "progress_stage": "queued",
                    "progress_message": "Build queued. Waiting for active build slot...",
                },
            )
            return {"job_id": job_id, "status": "QUEUED", "deferred": True}
        return {"job_id": job_id, "status": current_status, "skipped": True}

    if not _transition_job_to_running(job_id):
        current_status = str(_read_job(job_id).get("status", "UNKNOWN")).upper()
        if current_status != "RUNNING":
            _release_worker_lock(job_id)
            _try_start_next_queued_job(exclude_job_id=job_id)
        processed = False
        return {"job_id": job_id, "status": current_status, "skipped": True}

    processed = True

    try:
        job = _read_job(job_id)
        prompt = str(job["prompt"])
        size = str(job["size"])
        anchor = job["anchor"]

        _set_progress(job_id, "image_generation", "Starting image generation from prompt...")
        image_s3 = _invoke_text_to_image(prompt=prompt, size=size, job_id=job_id)

        def _pipeline_progress(stage: str):
            if stage == "shape_start":
                _set_progress(job_id, "shape_generation", "Image generated. Building 3D mesh...")
            elif stage == "paint_start":
                _set_progress(job_id, "texture_paint", "Mesh ready. Painting 3D texture...")
            elif stage == "paint_fallback":
                _set_progress(
                    job_id,
                    "texture_paint",
                    "Paint stage hit a GPU error. Continuing with mesh-only fallback...",
                )

        outputs = run_full_pipeline(
            input_s3=image_s3,
            output_prefix_s3=f"s3://{ARTIFACT_BUCKET}/jobs/{job_id}",
            job_id=job_id,
            endpoint_name=HUNYUAN_ENDPOINT,
            io_bucket=HUNYUAN_IO_BUCKET,
            region=REGION,
            timeout_seconds=SAGEMAKER_TIMEOUT_SECONDS,
            poll_seconds=SAGEMAKER_POLL_SECONDS,
            progress_hook=_pipeline_progress,
        )
        shape_s3 = outputs["shape_s3"]
        textured_s3 = outputs["textured_s3"]
        paint_fallback = str(outputs.get("paint_fallback", "none"))
        paint_error = str(outputs.get("paint_error", ""))
        selected_mesh = _select_mesh_input(shape_s3=shape_s3, textured_s3=textured_s3)

        _set_progress(job_id, "voxelization", "Texture ready. Converting GLB to voxels...")
        glb_to_vox_result = _invoke_glb_to_vox(
            mesh_s3=selected_mesh["selected_s3"],
            anchor=anchor,
            size=size,
            job_id=job_id,
        )
        source_mcfunction_s3 = glb_to_vox_result["mcfunction_s3"]

        with tempfile.TemporaryDirectory() as tmpdir:
            mcfunction_path = os.path.join(tmpdir, "source.mcfunction")
            _download_s3_to_file(source_mcfunction_s3, mcfunction_path)
            with open(mcfunction_path, "r", encoding="utf-8") as handle:
                source_mcfunction = handle.read()

        entries = _parse_mcfunction_commands(source_mcfunction)
        anchored = _anchor_entries_to_commands(entries, anchor=anchor)
        placement_commands = _repeat_placement_commands(anchored["commands"], passes=PLACEMENT_PASSES)

        forceload = {"enabled": False, "chunk_count": 0, "add_command": "", "remove_command": ""}
        if ENABLE_FORCELOAD:
            forceload = _forceload_commands(anchored["bounds"])
            if forceload["enabled"]:
                placement_commands = [forceload["add_command"]] + placement_commands + [forceload["remove_command"]]

        batches = _chunk_commands(placement_commands, chunk_size=COMMAND_CHUNK_SIZE)

        _set_progress(job_id, "batch_prepare", "Voxel model ready. Preparing Minecraft block commands...")
        batch_info = _upload_mcfunction_batches(job_id=job_id, batches=batches)
        _update_job(
            job_id,
            {
                "status": "SUCCEEDED",
                "image_s3": image_s3,
                "shape_s3": outputs["shape_s3"],
                "textured_s3": textured_s3,
                "voxel_input_s3": selected_mesh["selected_s3"],
                "voxel_input_stage": selected_mesh["selected_stage"],
                "voxel_input_format": selected_mesh["selected_format"],
                "textured_mesh_format": selected_mesh["textured_format"],
                "shape_mesh_format": selected_mesh["shape_format"],
                "color_source": str(glb_to_vox_result.get("raw_response", {}).get("color_source", "")),
                "paint_fallback": paint_fallback,
                "paint_error": paint_error[:1200],
                "source_mcfunction_s3": source_mcfunction_s3,
                "voxel_count": len(anchored["commands"]),
                "placement_command_count": len(placement_commands),
                "placement_passes": PLACEMENT_PASSES,
                "forceload_enabled": bool(forceload["enabled"]),
                "forceload_chunk_count": int(forceload["chunk_count"]),
                "dimensions": anchored["dimensions"],
                "orientation_quarter_turns": anchored["orientation_quarter_turns"],
                "anchor_mode": "center_center_bottom",
                "block": anchored["block"],
                "progress_stage": "ready_to_place",
                "progress_message": "Build ready. Sending block batches to Minecraft...",
                **batch_info,
            },
        )

        return {"job_id": job_id, "status": "SUCCEEDED"}
    except UserVisibleError as exc:
        _update_job(
            job_id,
            {
                "status": "FAILED",
                "error": exc.user_message[:1000],
                "internal_error": exc.internal_message[:2000],
                "progress_stage": "failed",
                "progress_message": "Build failed.",
            },
        )
        return {"job_id": job_id, "status": "FAILED", "error": exc.user_message}
    except Exception as exc:
        trace = traceback.format_exc()
        _update_job(
            job_id,
            {
                "status": "FAILED",
                "error": str(exc)[:1000],
                "traceback": trace[-3500:],
                "progress_stage": "failed",
                "progress_message": "Build failed.",
            },
        )
        # Do not re-raise: this handler is invoked asynchronously, and raising
        # triggers Lambda retries that can re-run the same job_id and leave
        # stale stage artifacts in S3.
        return {"job_id": job_id, "status": "FAILED", "error": str(exc)}
    finally:
        if processed:
            _release_worker_lock(job_id)
            _try_start_next_queued_job(exclude_job_id=job_id)
