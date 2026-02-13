"""
Text-to-image Lambda used by the CreateBuild pipeline.

Flow:
1. Generate an isometric image from text with Titan Image Generator v2.
2. Remove the background with Titan v2 background removal.
3. Write final PNG to S3 and return its bucket/key.
"""
from __future__ import annotations

import base64
import json
import os
import uuid

import boto3


BEDROCK_REGION = os.environ.get("BEDROCK_REGION", "us-east-1")
OUT_BUCKET = os.environ.get("OUT_BUCKET", "")
OUT_PREFIX = os.environ.get("OUT_PREFIX", "images/")

PROMPT_TEMPLATE = (
    "Isometric 3D render of {user_input}, centered, fully visible, no cropping. "
    "Orthographic camera 30 degrees, neutral gray background, even lighting, "
    "sharp focus, high detail, realistic materials. No people, text, watermark, blur, or distortion."
)

DEFAULT_NEGATIVE = (
    "blurry, low quality, lowres, jpeg artifacts, cropped, cut off, out of frame, "
    "text, watermark, logo, signature, people, person, face, hands, fingers, "
    "motion blur, fog, depth of field, bokeh, fisheye, extreme perspective, distortion"
)

# Titan v2 model ID used for both generation and background removal.
MODEL_ID = os.environ.get("MODEL_ID", "amazon.titan-image-generator-v2:0")

bedrock = boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)
s3 = boto3.client("s3")


def _invoke_bedrock(payload: dict) -> dict:
    response = bedrock.invoke_model(
        modelId=MODEL_ID,
        contentType="application/json",
        accept="application/json",
        body=json.dumps(payload),
    )
    return json.loads(response["body"].read())


def _extract_body(event: dict) -> dict:
    if not isinstance(event, dict):
        return {}

    body = event.get("body")
    if isinstance(body, str):
        try:
            parsed = json.loads(body)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return {}
    if isinstance(body, dict):
        return body

    return event


def lambda_handler(event, _context):
    if not OUT_BUCKET:
        raise ValueError("Set OUT_BUCKET environment variable.")

    body = _extract_body(event)

    prompt = str(body.get("prompt") or body.get("input") or "").strip()
    if not prompt:
        raise ValueError("Missing required field: prompt (or input).")

    allow_people = bool(body.get("allowPeople", False))
    negative_text = str(body.get("negativeText") or DEFAULT_NEGATIVE).strip()

    if allow_people and negative_text:
        for term in ("people", "person", "face", "hands", "fingers"):
            negative_text = negative_text.replace(term, "")
        negative_text = ", ".join([part.strip() for part in negative_text.split(",") if part.strip()])

    seed = int(body.get("seed", 0))
    width = int(body.get("width", 1024))
    height = int(body.get("height", 1024))
    cfg_scale = float(body.get("cfgScale", 8.0))

    out_key = str(body.get("outKey") or f"{OUT_PREFIX}{uuid.uuid4().hex}.png")
    formatted_prompt = PROMPT_TEMPLATE.format(user_input=prompt)

    gen_payload = {
        "taskType": "TEXT_IMAGE",
        "textToImageParams": {
            "text": formatted_prompt,
        },
        "imageGenerationConfig": {
            "numberOfImages": 1,
            "height": height,
            "width": width,
            "cfgScale": cfg_scale,
            "seed": seed,
        },
    }
    if negative_text:
        gen_payload["textToImageParams"]["negativeText"] = negative_text

    gen_response = _invoke_bedrock(gen_payload)
    if gen_response.get("error") is not None:
        raise RuntimeError(f"Titan generation error: {gen_response['error']}")

    generated_b64 = (gen_response.get("images") or [None])[0]
    if not generated_b64:
        raise RuntimeError(f"Titan generation returned no image: {gen_response}")

    remove_bg_payload = {
        "taskType": "BACKGROUND_REMOVAL",
        "backgroundRemovalParams": {
            "image": generated_b64,
        },
    }

    remove_bg_response = _invoke_bedrock(remove_bg_payload)
    if remove_bg_response.get("error") is not None:
        raise RuntimeError(f"Titan background removal error: {remove_bg_response['error']}")

    final_b64 = (remove_bg_response.get("images") or [None])[0]
    if not final_b64:
        raise RuntimeError(f"Titan background removal returned no image: {remove_bg_response}")

    final_png = base64.b64decode(final_b64)

    s3.put_object(
        Bucket=OUT_BUCKET,
        Key=out_key,
        Body=final_png,
        ContentType="image/png",
    )

    return {
        "bucket": OUT_BUCKET,
        "key": out_key,
    }
