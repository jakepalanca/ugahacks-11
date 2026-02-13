import os
import sys
import boto3
from pathlib import Path
from PIL import Image

# Make repo modules importable (repo root is /app)
sys.path.insert(0, "./hy3dshape")
sys.path.insert(0, "./hy3dpaint")
sys.path.insert(0, ".")

def split_s3_uri(uri: str):
    if not uri or not uri.startswith("s3://"):
        raise ValueError(f"Invalid S3 URI: {uri}")
    rest = uri[5:]
    parts = rest.split("/", 1)
    if len(parts) != 2:
        raise ValueError(f"Invalid S3 URI: {uri}")
    return parts[0], parts[1]

def s3_download(s3, s3_uri: str, local_path: str):
    b, k = split_s3_uri(s3_uri)
    Path(local_path).parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {s3_uri} -> {local_path}")
    s3.download_file(b, k, local_path)

def s3_upload(s3, local_path: str, s3_uri: str):
    b, k = split_s3_uri(s3_uri)
    print(f"Uploading {local_path} -> {s3_uri}")
    s3.upload_file(local_path, b, k)


def detect_mesh_file_type(local_path: str) -> str:
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


def is_binary_glb(local_path: str) -> bool:
    try:
        with open(local_path, "rb") as handle:
            return handle.read(4) == b"glTF"
    except Exception:
        return False


def ensure_binary_glb(local_path: str, fallback_out: str, label: str) -> str:
    if is_binary_glb(local_path):
        return local_path

    detected = detect_mesh_file_type(local_path)
    if detected not in {"obj", "ply", "stl", "glb"}:
        raise RuntimeError(f"{label} output is not binary GLB or convertible mesh: {local_path}")

    print(f"{label} output is '{detected or 'unknown'}'; converting to binary GLB")
    import trimesh
    if detected and detected != "glb":
        loaded = trimesh.load(local_path, file_type=detected, force="scene")
    else:
        loaded = trimesh.load(local_path, force="scene")
    loaded.export(fallback_out)
    if not is_binary_glb(fallback_out):
        raise RuntimeError(f"{label} conversion did not produce binary GLB")
    return fallback_out

def main():
    stage = os.environ.get("STAGE", "shape").strip().lower()
    input_s3 = os.environ.get("INPUT_S3")
    shape_s3 = os.environ.get("SHAPE_S3")
    output_s3 = os.environ.get("OUTPUT_S3")

    model_path = os.environ.get("MODEL_PATH", "tencent/Hunyuan3D-2.1")
    skip_rembg = os.environ.get("SKIP_REMBG", "0") == "1"

    print("STAGE:", stage)
    print("INPUT_S3:", input_s3)
    print("SHAPE_S3:", shape_s3)
    print("OUTPUT_S3:", output_s3)
    print("MODEL_PATH:", model_path)

    if not input_s3:
        print("ERROR: INPUT_S3 not set")
        return 2
    if stage == "shape" and not output_s3:
        print("ERROR: OUTPUT_S3 not set for shape stage")
        return 2
    if stage == "paint" and (not shape_s3 or not output_s3):
        print("ERROR: SHAPE_S3 and OUTPUT_S3 must be set for paint stage")
        return 2

    # Optional torchvision compatibility fix (if present)
    try:
        from torchvision_fix import apply_fix
        apply_fix()
    except ImportError:
        pass
    except Exception as e:
        print(f"Warning: torchvision_fix failed: {e}")

    s3 = boto3.client("s3")

    local_img = "/tmp/input.png"
    s3_download(s3, input_s3, local_img)

    # Load image
    img = Image.open(local_img)
    # If image has no alpha channel, convert to RGB then possibly rembg -> RGBA
    if img.mode not in ("RGBA", "LA"):
        img = img.convert("RGB")
        if not skip_rembg:
            from hy3dshape.rembg import BackgroundRemover
            print("Running background removal (rembg)...")
            rembg = BackgroundRemover()
            img = rembg(img)  # should return RGBA
        else:
            img = img.convert("RGBA")
    else:
        img = img.convert("RGBA")

    if stage == "shape":
        from hy3dshape.pipelines import Hunyuan3DDiTFlowMatchingPipeline

        local_shape = "/tmp/shape.glb"

        print("Loading shape pipeline...")
        pipe = Hunyuan3DDiTFlowMatchingPipeline.from_pretrained(model_path)

        # Try GPU if supported
        try:
            import torch
            if torch.cuda.is_available():
                print("CUDA:", torch.cuda.get_device_name(0))
                try:
                    pipe = pipe.to("cuda")
                except Exception as e:
                    print("pipe.to('cuda') skipped:", e)
        except Exception as e:
            print("torch check skipped:", e)

        print("Generating mesh...")
        mesh = pipe(image=img)[0]

        print("Exporting GLB...")
        mesh.export(local_shape)
        local_shape = ensure_binary_glb(local_shape, "/tmp/shape.binary.glb", "shape")

        s3_upload(s3, local_shape, output_s3)
        print("DONE: shape")
        return 0

    if stage == "paint":
        # Download the shape glb
        local_shape = "/tmp/shape.glb"
        s3_download(s3, shape_s3, local_shape)

        # Paint config
        max_num_view = int(os.environ.get("PAINT_MAX_NUM_VIEW", "6"))
        resolution = int(os.environ.get("PAINT_RESOLUTION", "512"))

        realesrgan_ckpt_path = os.environ.get("REALESRGAN_CKPT_PATH", "hy3dpaint/ckpt/RealESRGAN_x4plus.pth")
        multiview_cfg_path = os.environ.get("PAINT_CFG_PATH", "hy3dpaint/cfgs/hunyuan-paint-pbr.yaml")
        custom_pipeline = os.environ.get("PAINT_CUSTOM_PIPELINE", "hy3dpaint/hunyuanpaintpbr")

        # Optionally download RealESRGAN ckpt from S3 if not present
        realesrgan_ckpt_s3 = os.environ.get("REALESRGAN_CKPT_S3")
        if not Path(realesrgan_ckpt_path).exists():
            if realesrgan_ckpt_s3:
                s3_download(s3, realesrgan_ckpt_s3, realesrgan_ckpt_path)
            else:
                print(f"ERROR: RealESRGAN ckpt not found at {realesrgan_ckpt_path} and REALESRGAN_CKPT_S3 not set")
                return 3

        if not Path(multiview_cfg_path).exists():
            print(f"ERROR: paint cfg not found at {multiview_cfg_path}")
            return 3

        from textureGenPipeline import Hunyuan3DPaintPipeline, Hunyuan3DPaintConfig

        conf = Hunyuan3DPaintConfig(max_num_view, resolution)
        conf.realesrgan_ckpt_path = realesrgan_ckpt_path
        conf.multiview_cfg_path = multiview_cfg_path
        conf.custom_pipeline = custom_pipeline

        print("Loading paint pipeline...")
        paint_pipeline = Hunyuan3DPaintPipeline(conf)

        local_out = "/tmp/textured.glb"
        print("Painting mesh...")
        paint_pipeline(
            mesh_path=local_shape,
            image_path=local_img,
            output_mesh_path=local_out
        )
        local_out = ensure_binary_glb(local_out, "/tmp/textured.binary.glb", "paint")

        s3_upload(s3, local_out, output_s3)
        print("DONE: paint")
        return 0

    print(f"ERROR: unknown STAGE={stage}")
    return 2

if __name__ == "__main__":
    sys.exit(main())
