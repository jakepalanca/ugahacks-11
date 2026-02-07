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
        _paint_config = Hunyuan3DPaintConfig(max_num_view=6, resolution=512)
        _paint_pipe = Hunyuan3DPaintPipeline(_paint_config)
        print("Paint pipeline loaded.")
    return _paint_pipe


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
        pipe = get_shape_pipeline()
        mesh = pipe(image=local_input)[0]

        # Save and upload
        local_output = os.path.join(tmpdir, "shape.glb")
        if hasattr(mesh, 'export'):
            mesh.export(local_output)
        else:
            mesh.save(local_output)

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
        pipe = get_paint_pipeline()
        mesh = pipe(local_shape, image_path=local_input)

        # Save and upload
        local_output = os.path.join(tmpdir, "textured.glb")
        if hasattr(mesh, 'export'):
            mesh.export(local_output)
        else:
            mesh.save(local_output)

        upload_to_s3(local_output, output_s3)

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
