"""
SageMaker Async Inference serving script for Hunyuan3D-2.1

Handles two stages:
- shape: Generate 3D mesh from image
- paint: Apply texture to mesh
"""
import os
import sys
import json
import tempfile
import traceback
import gc

# Add Hunyuan3D paths
sys.path.insert(0, '/app/hy3dshape')
sys.path.insert(0, '/app/hy3dpaint')

import boto3
import flask
from PIL import Image

# Lazy-load heavy dependencies
_shape_pipe = None
_paint_pipe = None
_paint_config = None
_rembg = None

app = flask.Flask(__name__)
s3 = boto3.client("s3")

MODEL_PATH = os.environ.get("MODEL_PATH", "tencent/Hunyuan3D-2.1")
UNLOAD_SHAPE_BEFORE_PAINT = os.environ.get("UNLOAD_SHAPE_BEFORE_PAINT", "1") != "0"
UNLOAD_PAINT_BEFORE_SHAPE = os.environ.get("UNLOAD_PAINT_BEFORE_SHAPE", "1") != "0"
KEEP_PAINT_PIPELINE_LOADED = os.environ.get("KEEP_PAINT_PIPELINE_LOADED", "0") != "0"
# Reduce CUDA allocator fragmentation under repeated async requests.
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", os.environ.get("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True"))

PAINT_QUALITY_PRESETS = {
    # Hunyuan3D-Paint 2.1 supported ranges:
    # max_num_view: 6-12, resolution: 512 or 768.
    "low": {"max_num_view": 6, "resolution": 512},
    "medium": {"max_num_view": 8, "resolution": 512},
    "high": {"max_num_view": 9, "resolution": 768},
    "ultra": {"max_num_view": 12, "resolution": 768},
}


def _parse_int_env(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        value = int(str(raw).strip())
    except ValueError:
        print(f"Invalid {name}='{raw}', using default {default}")
        return default
    if value < minimum:
        print(f"{name}={value} below minimum {minimum}; clamping.")
        return minimum
    if value > maximum:
        print(f"{name}={value} above maximum {maximum}; clamping.")
        return maximum
    return value


def resolve_paint_settings():
    quality = os.environ.get("PAINT_QUALITY", "medium").strip().lower()
    preset = PAINT_QUALITY_PRESETS.get(quality, PAINT_QUALITY_PRESETS["medium"])
    if quality not in PAINT_QUALITY_PRESETS:
        print(f"Unknown PAINT_QUALITY '{quality}', using 'medium'.")
        quality = "medium"

    raw_max_num_view = _parse_int_env(
        "PAINT_MAX_NUM_VIEW",
        preset["max_num_view"],
        minimum=6,
        maximum=12,
    )
    if raw_max_num_view < 6:
        max_num_view = 6
    elif raw_max_num_view > 12:
        max_num_view = 12
    else:
        max_num_view = raw_max_num_view

    raw_resolution = _parse_int_env(
        "PAINT_RESOLUTION",
        preset["resolution"],
        minimum=512,
        maximum=768,
    )
    resolution = 768 if raw_resolution >= 640 else 512
    if raw_resolution not in (512, 768):
        print(
            f"PAINT_RESOLUTION={raw_resolution} is unsupported; "
            f"using nearest supported value {resolution}."
        )
    return quality, max_num_view, resolution


def _build_fallback_paint_attempts(base_views: int, base_resolution: int):
    """
    Return progressively lighter paint settings for CUDA runtime retries.
    """
    candidates = [
        (min(12, max(6, base_views)), 768 if base_resolution >= 640 else 512),
        (min(12, max(6, base_views - 1)), 512),
        (6, 512),
    ]

    attempts = []
    seen = set()
    for views, resolution in candidates:
        key = (int(views), int(resolution))
        if key in seen:
            continue
        seen.add(key)
        attempts.append(key)
    return attempts


def get_rembg():
    global _rembg
    if _rembg is None:
        from hy3dshape.rembg import BackgroundRemover
        _rembg = BackgroundRemover()
    return _rembg


def get_shape_pipeline():
    global _shape_pipe
    if _shape_pipe is None:
        print("Loading shape pipeline...")
        from hy3dshape.pipelines import Hunyuan3DDiTFlowMatchingPipeline
        _shape_pipe = Hunyuan3DDiTFlowMatchingPipeline.from_pretrained(MODEL_PATH)
        print("Shape pipeline loaded.")
    return _shape_pipe


def get_paint_pipeline():
    global _paint_pipe, _paint_config
    if _paint_pipe is None:
        print("Loading paint pipeline...")
        from textureGenPipeline import Hunyuan3DPaintPipeline, Hunyuan3DPaintConfig
        quality, max_num_view, resolution = resolve_paint_settings()
        print(
            "Paint quality settings: "
            f"quality={quality}, max_num_view={max_num_view}, resolution={resolution}"
        )
        _paint_config = Hunyuan3DPaintConfig(max_num_view=max_num_view, resolution=resolution)
        _paint_pipe = Hunyuan3DPaintPipeline(_paint_config)
        print("Paint pipeline loaded.")
    return _paint_pipe


def _cuda_cleanup():
    try:
        gc.collect()
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            try:
                torch.cuda.ipc_collect()
            except Exception:
                pass
    except Exception:
        pass


def unload_shape_pipeline():
    global _shape_pipe
    if _shape_pipe is None:
        return
    _shape_pipe = None
    _cuda_cleanup()


def unload_paint_pipeline():
    global _paint_pipe, _paint_config
    if _paint_pipe is None and _paint_config is None:
        return
    _paint_pipe = None
    _paint_config = None
    _cuda_cleanup()


def _is_cuda_runtime_error(exc: Exception) -> bool:
    text = str(exc).lower()
    patterns = (
        "cuda out of memory",
        "outofmemoryerror",
        "cuda driver error",
        "cuda error",
        "invalid argument",
        "cudnn error",
        "cublas_status_alloc_failed",
        "device-side assert",
    )
    return any(pattern in text for pattern in patterns)


def split_s3_uri(uri: str):
    """Parse s3://bucket/key into (bucket, key)"""
    if not uri or not uri.startswith("s3://"):
        raise ValueError(f"Invalid S3 URI: {uri}")
    parts = uri[5:].split("/", 1)
    if len(parts) != 2:
        raise ValueError(f"Invalid S3 URI: {uri}")
    return parts[0], parts[1]


def download_from_s3(s3_uri: str, local_path: str):
    """Download file from S3"""
    bucket, key = split_s3_uri(s3_uri)
    print(f"Downloading s3://{bucket}/{key} -> {local_path}")
    s3.download_file(bucket, key, local_path)


def upload_to_s3(local_path: str, s3_uri: str):
    """Upload file to S3"""
    bucket, key = split_s3_uri(s3_uri)
    print(f"Uploading {local_path} -> s3://{bucket}/{key}")
    s3.upload_file(local_path, bucket, key)


def resolve_output_path(output, desired_ext: str = None) -> str:
    """
    Resolve pipeline output into a local file path.

    Some Hunyuan pipelines return mesh objects, while others return a path string.
    """
    if not isinstance(output, str):
        raise TypeError(f"Unsupported pipeline output type: {type(output).__name__}")

    if desired_ext:
        if output.lower().endswith(desired_ext.lower()) and os.path.exists(output):
            return output
        root, _ = os.path.splitext(output)
        candidate = root + desired_ext
        if os.path.exists(candidate):
            return candidate

    if os.path.exists(output):
        return output

    raise FileNotFoundError(f"Pipeline output path does not exist: {output}")


def _detect_mesh_file_type(local_path: str) -> str:
    try:
        with open(local_path, "rb") as handle:
            head = handle.read(4096)
    except Exception:
        return ""

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
        or ascii_head.startswith("mtllib ")
        or "\nmtllib " in ascii_head
        or "\nv " in ascii_head
        or "\nvt " in ascii_head
        or "\nf " in ascii_head
    ):
        return "obj"
    return ""


def _is_binary_glb(local_path: str) -> bool:
    try:
        with open(local_path, "rb") as handle:
            return handle.read(4) == b"glTF"
    except Exception:
        return False


def _ensure_binary_glb(local_path: str, tmpdir: str, label: str) -> str:
    if _is_binary_glb(local_path):
        return local_path

    detected = _detect_mesh_file_type(local_path)
    if detected not in {"obj", "ply", "stl", "glb"}:
        raise ValueError(f"{label} output is not a valid GLB or supported mesh format: {local_path}")

    print(f"{label} output is '{detected or 'unknown'}'; converting to binary GLB")
    import trimesh

    if detected and detected != "glb":
        loaded = trimesh.load(local_path, file_type=detected, force="scene")
    else:
        loaded = trimesh.load(local_path, force="scene")

    converted = os.path.join(tmpdir, f"{label}.binary.glb")
    loaded.export(converted)

    if not _is_binary_glb(converted):
        raise ValueError(f"{label} conversion did not produce binary GLB")
    return converted


def process_shape(input_s3: str, output_s3: str) -> dict:
    """Generate 3D shape from input image"""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Download input
        local_input = os.path.join(tmpdir, "input.png")
        download_from_s3(input_s3, local_input)

        # Load and preprocess image
        img = Image.open(local_input)
        if img.mode == "RGB":
            print("Removing background...")
            rembg = get_rembg()
            img = rembg(img)
            # Save preprocessed image
            img.save(local_input)
        else:
            img = img.convert("RGBA")
            img.save(local_input)

        # Generate shape - returns list, take first mesh
        print("Generating shape...")
        if UNLOAD_PAINT_BEFORE_SHAPE:
            unload_paint_pipeline()
        pipe = get_shape_pipeline()
        shape_output = pipe(image=local_input, num_inference_steps=20, octree_resolution=128)[0]

        # Save and upload
        local_output = os.path.join(tmpdir, "shape.glb")
        if hasattr(shape_output, 'export'):
            shape_output.export(local_output)
        elif hasattr(shape_output, 'save'):
            shape_output.save(local_output)
        else:
            local_output = resolve_output_path(shape_output, desired_ext=".glb")
        local_output = _ensure_binary_glb(local_output, tmpdir, "shape")

        upload_to_s3(local_output, output_s3)

    return {"status": "success", "stage": "shape", "output": output_s3}


def process_paint(input_s3: str, shape_s3: str, output_s3: str) -> dict:
    """Apply texture/paint to 3D shape"""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Download inputs
        local_input = os.path.join(tmpdir, "input.png")
        local_shape = os.path.join(tmpdir, "shape.glb")
        download_from_s3(input_s3, local_input)
        download_from_s3(shape_s3, local_shape)

        # Preprocess image if needed
        img = Image.open(local_input)
        if img.mode == "RGB":
            print("Removing background...")
            rembg = get_rembg()
            img = rembg(img)
            img.save(local_input)
        else:
            img = img.convert("RGBA")
            img.save(local_input)

        # Generate textured mesh
        print("Applying paint/texture...")
        if UNLOAD_SHAPE_BEFORE_PAINT:
            unload_shape_pipeline()
        _cuda_cleanup()

        try:
            pipe = get_paint_pipeline()
            paint_output = pipe(local_shape, image_path=local_input)
        except Exception as exc:
            if not _is_cuda_runtime_error(exc):
                raise
            print(f"Paint CUDA runtime failure with primary settings ({exc}).")
            unload_paint_pipeline()
            _cuda_cleanup()

            from textureGenPipeline import Hunyuan3DPaintPipeline, Hunyuan3DPaintConfig

            _quality, max_num_view, resolution = resolve_paint_settings()
            fallback_attempts = _build_fallback_paint_attempts(max_num_view, resolution)
            last_err = exc
            paint_output = None

            for attempt_index, (attempt_views, attempt_resolution) in enumerate(fallback_attempts, start=1):
                print(
                    "Retrying paint with fallback settings "
                    f"(attempt {attempt_index}/{len(fallback_attempts)}): "
                    f"max_num_view={attempt_views}, resolution={attempt_resolution}"
                )
                fallback_pipe = None
                try:
                    fallback_config = Hunyuan3DPaintConfig(
                        max_num_view=attempt_views,
                        resolution=attempt_resolution,
                    )
                    fallback_pipe = Hunyuan3DPaintPipeline(fallback_config)
                    paint_output = fallback_pipe(local_shape, image_path=local_input)
                    print(
                        "Fallback paint attempt succeeded with settings "
                        f"max_num_view={attempt_views}, resolution={attempt_resolution}"
                    )
                    break
                except Exception as fallback_exc:
                    if not _is_cuda_runtime_error(fallback_exc):
                        raise
                    last_err = fallback_exc
                    print(
                        "Fallback paint attempt failed with CUDA runtime error "
                        f"(max_num_view={attempt_views}, resolution={attempt_resolution}): {fallback_exc}"
                    )
                finally:
                    if fallback_pipe is not None:
                        del fallback_pipe
                    _cuda_cleanup()

            if paint_output is None:
                raise last_err

        # Save and upload
        local_output = os.path.join(tmpdir, "textured.glb")
        if hasattr(paint_output, 'export'):
            paint_output.export(local_output)
        elif hasattr(paint_output, 'save'):
            paint_output.save(local_output)
        else:
            # Hunyuan3DPaintPipeline returns an .obj path string; it also writes .glb.
            local_output = resolve_output_path(paint_output, desired_ext=".glb")
        local_output = _ensure_binary_glb(local_output, tmpdir, "paint")

        upload_to_s3(local_output, output_s3)

        if not KEEP_PAINT_PIPELINE_LOADED:
            unload_paint_pipeline()

    return {"status": "success", "stage": "paint", "output": output_s3}


@app.route("/ping", methods=["GET"])
def ping():
    """Health check endpoint"""
    return flask.Response(response="\n", status=200, mimetype="application/json")


@app.route("/invocations", methods=["POST"])
def invocations():
    """
    Inference endpoint

    Request JSON format:
    {
        "stage": "shape" | "paint",
        "input_s3": "s3://bucket/input.png",
        "shape_s3": "s3://bucket/shape.glb",  # required for paint stage
        "output_s3": "s3://bucket/output.glb"
    }
    """
    try:
        data = flask.request.get_json(force=True)

        stage = data.get("stage", "shape").lower()
        input_s3 = data.get("input_s3")
        shape_s3 = data.get("shape_s3")
        output_s3 = data.get("output_s3")

        print(f"Processing request: stage={stage}, input={input_s3}, shape={shape_s3}, output={output_s3}")

        if not input_s3:
            return flask.Response(
                response=json.dumps({"error": "input_s3 is required"}),
                status=400,
                mimetype="application/json"
            )

        if not output_s3:
            return flask.Response(
                response=json.dumps({"error": "output_s3 is required"}),
                status=400,
                mimetype="application/json"
            )

        if stage == "shape":
            result = process_shape(input_s3, output_s3)
        elif stage == "paint":
            if not shape_s3:
                return flask.Response(
                    response=json.dumps({"error": "shape_s3 is required for paint stage"}),
                    status=400,
                    mimetype="application/json"
                )
            result = process_paint(input_s3, shape_s3, output_s3)
        else:
            return flask.Response(
                response=json.dumps({"error": f"Unknown stage: {stage}"}),
                status=400,
                mimetype="application/json"
            )

        return flask.Response(
            response=json.dumps(result),
            status=200,
            mimetype="application/json"
        )

    except Exception as e:
        traceback.print_exc()
        return flask.Response(
            response=json.dumps({"error": str(e)}),
            status=500,
            mimetype="application/json"
        )


def start_xvfb():
    """Start virtual framebuffer for headless OpenGL"""
    import subprocess
    try:
        # Check if Xvfb is already running
        result = subprocess.run(['pgrep', 'Xvfb'], capture_output=True)
        if result.returncode != 0:
            print("Starting Xvfb virtual display...")
            subprocess.Popen(['Xvfb', ':99', '-screen', '0', '1024x768x24'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            os.environ['DISPLAY'] = ':99'
            print("Xvfb started on display :99")
    except Exception as e:
        print(f"Warning: Could not start Xvfb: {e}")


if __name__ == "__main__":
    # Start virtual display for headless OpenGL
    start_xvfb()

    # Preload models at startup for faster inference
    if os.environ.get("PRELOAD_MODELS", "0") == "1":
        print("Preloading models...")
        try:
            get_shape_pipeline()
        except Exception as e:
            print(f"Warning: Could not preload shape pipeline: {e}")
        try:
            get_paint_pipeline()
        except Exception as e:
            print(f"Warning: Could not preload paint pipeline: {e}")
        print("Models preloaded.")

    app.run(host="0.0.0.0", port=8080)
