"""
GLB -> Minecraft .mcfunction converter with broad block palette and transparency support.

This function dynamically loads scientific libraries from an S3 zip (numpy/scipy/trimesh),
voxelizes the mesh, samples texture/vertex colors, and maps voxels to Minecraft blocks.
"""
from __future__ import annotations

import json
import os
import struct
import sys
import time
import uuid
import zipfile
import zlib
from collections import Counter
from typing import Dict, Iterable, List, Tuple
from urllib.parse import urlparse

import boto3


s3 = boto3.client("s3")

LAYER_BUCKET = os.environ.get("LAYER_BUCKET", "hackathon-jobs-67")
LAYER_ZIP_KEY = os.environ.get("LAYER_ZIP_KEY", "sci_tri_num_pillow.zip")
OUTPUT_BUCKET = os.environ.get("OUTPUT_BUCKET", "hackathon-images-67")
OUTPUT_PREFIX = os.environ.get("OUTPUT_PREFIX", "outputs")
COORDINATE_MODE = os.environ.get("COORDINATE_MODE", "XYZ").strip().upper()
ALPHA_CUTOUT = int(os.environ.get("ALPHA_CUTOUT", "20"))
ALPHA_GLASS_MAX = int(os.environ.get("ALPHA_GLASS_MAX", "210"))
MORPH_CLOSE_ITERATIONS = int(os.environ.get("MORPH_CLOSE_ITERATIONS", "1"))
MORPH_DILATE_ITERATIONS = int(os.environ.get("MORPH_DILATE_ITERATIONS", "0"))
KEEP_LARGEST_COMPONENT = os.environ.get("KEEP_LARGEST_COMPONENT", "1") != "0"
MIN_COMPONENT_VOXELS = int(os.environ.get("MIN_COMPONENT_VOXELS", "1"))
UP_AXIS_MODE = os.environ.get("UP_AXIS_MODE", "AUTO").strip().upper()
USE_TEXTURE_ALPHA = os.environ.get("USE_TEXTURE_ALPHA", "0") != "0"
COLOR_CLUSTER_COUNT = int(os.environ.get("COLOR_CLUSTER_COUNT", "14"))
COLOR_CLUSTER_MAX_ITER = int(os.environ.get("COLOR_CLUSTER_MAX_ITER", "10"))
COLOR_CLUSTER_SAMPLE_SIZE = int(os.environ.get("COLOR_CLUSTER_SAMPLE_SIZE", "50000"))
COLOR_SMOOTHING_NEIGHBOR_THRESHOLD = int(os.environ.get("COLOR_SMOOTHING_NEIGHBOR_THRESHOLD", "4"))
COLOR_TRANSFER_NEIGHBORS = int(os.environ.get("COLOR_TRANSFER_NEIGHBORS", "4"))
COLOR_CLUSTER_BYPASS_SAT_THRESHOLD = float(os.environ.get("COLOR_CLUSTER_BYPASS_SAT_THRESHOLD", "0.28"))
VIVID_SAT_THRESHOLD = float(os.environ.get("VIVID_SAT_THRESHOLD", "0.22"))
FORCE_VIVID_AVG_SAT_THRESHOLD = float(os.environ.get("FORCE_VIVID_AVG_SAT_THRESHOLD", "0.30"))
BOUNDARY_PADDING_VOXELS = max(0, int(os.environ.get("BOUNDARY_PADDING_VOXELS", "1")))

SIZE_TARGET_SPAN = {
    "small": int(os.environ.get("SMALL_TARGET_SPAN", "128")),
    "medium": int(os.environ.get("MEDIUM_TARGET_SPAN", "192")),
    "large": int(os.environ.get("LARGE_TARGET_SPAN", "256")),
}

SIZE_SURFACE_SAMPLES = {
    "small": int(os.environ.get("SMALL_SURFACE_SAMPLES", "120000")),
    "medium": int(os.environ.get("MEDIUM_SURFACE_SAMPLES", "200000")),
    "large": int(os.environ.get("LARGE_SURFACE_SAMPLES", "320000")),
}


def _install_and_find_libraries():
    safe_key = "".join(ch if ch.isalnum() else "_" for ch in LAYER_ZIP_KEY)
    if len(safe_key) > 80:
        safe_key = safe_key[-80:]
    extract_path = f"/tmp/libs_{safe_key}"
    if not os.path.exists(extract_path):
        s3.download_file(LAYER_BUCKET, LAYER_ZIP_KEY, "/tmp/layer.zip")
        with zipfile.ZipFile("/tmp/layer.zip", "r") as zip_ref:
            zip_ref.extractall(extract_path)
        os.remove("/tmp/layer.zip")

    library_root = None
    for root, dirs, _files in os.walk(extract_path):
        if "scipy" in dirs and "numpy" in dirs:
            library_root = root
            break

    if library_root:
        if library_root not in sys.path:
            sys.path.insert(0, library_root)
    elif extract_path not in sys.path:
        sys.path.insert(0, extract_path)


def _parse_request(event) -> Dict:
    payload: Dict = {}
    if isinstance(event, dict):
        payload.update(event)
        body = event.get("body")
        if isinstance(body, str) and body.strip():
            try:
                parsed = json.loads(body)
                if isinstance(parsed, dict):
                    payload.update(parsed)
            except json.JSONDecodeError:
                pass
        elif isinstance(body, dict):
            payload.update(body)
    elif isinstance(event, str):
        stripped = event.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            try:
                parsed = json.loads(stripped)
                if isinstance(parsed, dict):
                    payload.update(parsed)
            except json.JSONDecodeError:
                payload["s3_uri"] = stripped
        else:
            payload["s3_uri"] = stripped
    return payload


def _get_required_s3_uri(payload: Dict) -> str:
    for key in ("s3_uri", "file_content", "input_s3", "inputS3"):
        value = payload.get(key)
        if isinstance(value, str) and value.startswith("s3://"):
            return value
    raise ValueError("Missing required s3_uri")


def _get_optional_s3_uri(payload: Dict, keys: Iterable[str]) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.startswith("s3://"):
            return value
    return ""


def _split_s3_uri(s3_uri: str) -> Tuple[str, str]:
    parsed = urlparse(s3_uri)
    if parsed.scheme != "s3" or not parsed.netloc or not parsed.path:
        raise ValueError(f"Invalid S3 URI: {s3_uri}")
    return parsed.netloc, parsed.path.lstrip("/")


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

    # Common OBJ signatures.
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

    # JSON usually means an error payload leaked into artifact path.
    if lowered.startswith(b"{") or lowered.startswith(b"["):
        return "json"

    return ""


def _choose_size(payload: Dict) -> str:
    size = str(payload.get("size", "medium")).strip().lower()
    if size not in SIZE_TARGET_SPAN:
        size = "medium"
    return size


def _load_s3_image_rgba(s3_uri: str, np):
    if not s3_uri:
        return None
    bucket, key = _split_s3_uri(s3_uri)
    local_path = f"/tmp/source-{uuid.uuid4().hex[:10]}.img"
    try:
        s3.download_file(bucket, key, local_path)
    except Exception:
        return None

    try:
        from PIL import Image

        with Image.open(local_path) as image:
            return np.asarray(image.convert("RGBA"), dtype=np.float64)
    except Exception:
        pass

    try:
        import imageio.v2 as imageio

        image = imageio.imread(local_path)
        image_np = np.asarray(image, dtype=np.float64)
        if image_np.ndim == 3 and image_np.shape[2] == 3:
            alpha = np.full((image_np.shape[0], image_np.shape[1], 1), 255.0, dtype=np.float64)
            image_np = np.concatenate([image_np, alpha], axis=2)
        if image_np.ndim == 2:
            image_np = np.stack([image_np, image_np, image_np, np.full_like(image_np, 255)], axis=2)
        return image_np[:, :, :4]
    except Exception:
        pass

    try:
        with open(local_path, "rb") as handle:
            payload = handle.read()
        return _decode_png_rgba(payload, np)
    except Exception:
        return None


def _decode_png_rgba(payload: bytes, np):
    signature = b"\x89PNG\r\n\x1a\n"
    if not payload.startswith(signature):
        raise ValueError("Only PNG source image fallback is supported")

    pos = len(signature)
    width = height = None
    bit_depth = color_type = interlace_method = None
    idat_parts = []
    palette = None
    trns = None

    while pos + 8 <= len(payload):
        length = struct.unpack(">I", payload[pos : pos + 4])[0]
        chunk_type = payload[pos + 4 : pos + 8]
        chunk_data_start = pos + 8
        chunk_data_end = chunk_data_start + length
        if chunk_data_end + 4 > len(payload):
            break
        chunk_data = payload[chunk_data_start:chunk_data_end]
        pos = chunk_data_end + 4

        if chunk_type == b"IHDR":
            width, height, bit_depth, color_type, _comp, _filt, interlace_method = struct.unpack(">IIBBBBB", chunk_data)
        elif chunk_type == b"PLTE":
            palette = chunk_data
        elif chunk_type == b"tRNS":
            trns = chunk_data
        elif chunk_type == b"IDAT":
            idat_parts.append(chunk_data)
        elif chunk_type == b"IEND":
            break

    if width is None or height is None:
        raise ValueError("PNG missing IHDR")
    if interlace_method != 0:
        raise ValueError("Interlaced PNG is not supported in fallback decoder")
    if bit_depth != 8:
        raise ValueError("Only 8-bit PNG is supported in fallback decoder")

    if color_type == 6:
        channels = 4
    elif color_type == 2:
        channels = 3
    elif color_type == 0:
        channels = 1
    elif color_type == 3:
        channels = 1
        if palette is None:
            raise ValueError("Indexed PNG missing PLTE")
    else:
        raise ValueError(f"Unsupported PNG color type: {color_type}")

    raw = zlib.decompress(b"".join(idat_parts))
    stride = width * channels
    expected = height * (stride + 1)
    if len(raw) < expected:
        raise ValueError("PNG decode failed: truncated image data")

    def paeth(a, b, c):
        p = a + b - c
        pa = abs(p - a)
        pb = abs(p - b)
        pc = abs(p - c)
        if pa <= pb and pa <= pc:
            return a
        if pb <= pc:
            return b
        return c

    rows = np.zeros((height, stride), dtype=np.uint8)
    offset = 0
    for y in range(height):
        filter_type = raw[offset]
        offset += 1
        row = np.frombuffer(raw[offset : offset + stride], dtype=np.uint8).astype(np.int16)
        offset += stride

        if filter_type == 1:
            for i in range(stride):
                left = row[i - channels] if i >= channels else 0
                row[i] = (row[i] + left) & 0xFF
        elif filter_type == 2:
            up = rows[y - 1] if y > 0 else np.zeros(stride, dtype=np.uint8)
            row = (row + up) & 0xFF
        elif filter_type == 3:
            up = rows[y - 1] if y > 0 else np.zeros(stride, dtype=np.uint8)
            for i in range(stride):
                left = row[i - channels] if i >= channels else 0
                row[i] = (row[i] + ((left + int(up[i])) // 2)) & 0xFF
        elif filter_type == 4:
            up = rows[y - 1] if y > 0 else np.zeros(stride, dtype=np.uint8)
            for i in range(stride):
                left = row[i - channels] if i >= channels else 0
                up_left = int(up[i - channels]) if (y > 0 and i >= channels) else 0
                row[i] = (row[i] + paeth(int(left), int(up[i]), up_left)) & 0xFF
        elif filter_type != 0:
            raise ValueError(f"Unsupported PNG filter: {filter_type}")

        rows[y] = row.astype(np.uint8)

    if color_type == 6:
        image = rows.reshape(height, width, 4).astype(np.float64)
    elif color_type == 2:
        rgb = rows.reshape(height, width, 3).astype(np.float64)
        alpha = np.full((height, width, 1), 255.0, dtype=np.float64)
        image = np.concatenate([rgb, alpha], axis=2)
    elif color_type == 0:
        gray = rows.reshape(height, width, 1).astype(np.float64)
        alpha = np.full((height, width, 1), 255.0, dtype=np.float64)
        image = np.concatenate([gray, gray, gray, alpha], axis=2)
    else:
        # color_type 3 indexed
        palette_np = np.frombuffer(palette, dtype=np.uint8).reshape(-1, 3)
        idx = rows.reshape(height, width).astype(np.int32)
        rgb = palette_np[idx].astype(np.float64)
        if trns is not None:
            alpha_lut = np.full(palette_np.shape[0], 255, dtype=np.uint8)
            alpha_lut[: len(trns)] = np.frombuffer(trns, dtype=np.uint8)
            alpha = alpha_lut[idx].astype(np.float64)[..., None]
        else:
            alpha = np.full((height, width, 1), 255.0, dtype=np.float64)
        image = np.concatenate([rgb, alpha], axis=2)

    return image


def _project_image_colors(points, image_rgba, np):
    if image_rgba is None or image_rgba.ndim != 3 or image_rgba.shape[2] < 3:
        return None
    mins = points.min(axis=0).astype(np.float64)
    maxs = points.max(axis=0).astype(np.float64)
    spans = np.maximum(maxs - mins, 1.0)

    u = (points[:, 0] - mins[0]) / spans[0]
    v = 1.0 - ((points[:, 1] - mins[1]) / spans[1])

    h, w = image_rgba.shape[:2]
    px = np.clip((u * (w - 1)).astype(np.int64), 0, w - 1)
    py = np.clip((v * (h - 1)).astype(np.int64), 0, h - 1)
    projected = image_rgba[py, px, :4].astype(np.float64)
    projected[:, 3] = 255.0
    return projected


def _remap_points_up_axis(points, np):
    mode = UP_AXIS_MODE
    if mode not in {"AUTO", "X", "Y", "Z"}:
        mode = "AUTO"

    target_axis = 1
    if mode == "X":
        up_axis = 0
    elif mode == "Y":
        up_axis = 1
    elif mode == "Z":
        up_axis = 2
    else:
        # glTF spec mandates Y-up; default to that instead of guessing.
        up_axis = 1

    if up_axis == target_axis:
        return points
    if up_axis == 0:
        return points[:, [1, 0, 2]]
    return points[:, [0, 2, 1]]


def _collect_meshes(loaded, trimesh) -> List:
    meshes = []
    if isinstance(loaded, trimesh.Scene):
        # Try concatenated dump first to preserve UV/texture data.
        try:
            concatenated = loaded.dump(concatenate=True)
            if isinstance(concatenated, trimesh.Trimesh) and len(concatenated.vertices) > 0:
                meshes.append(concatenated)
                return meshes
        except Exception:
            pass
        dumped = loaded.dump(concatenate=False)
        if not isinstance(dumped, list):
            dumped = [dumped]
        for candidate in dumped:
            if isinstance(candidate, trimesh.Trimesh) and len(candidate.vertices) > 0:
                meshes.append(candidate.copy())
    elif isinstance(loaded, trimesh.Trimesh):
        if len(loaded.vertices) > 0:
            meshes.append(loaded.copy())
    return meshes


_last_color_source = "neutral_gray_fallback"


def _sample_face_colors(mesh, sample_points, face_indices, np):
    global _last_color_source
    neutral = np.tile(np.array([140.0, 140.0, 140.0, 255.0], dtype=np.float64), (len(face_indices), 1))
    if len(face_indices) == 0:
        _last_color_source = "neutral_gray_fallback"
        return neutral

    # Path 1: sample color directly from the GLB texture image via UVs.
    try:
        uv = np.asarray(getattr(mesh.visual, "uv", []), dtype=np.float64)
        material = getattr(mesh.visual, "material", None)
        image = None
        if material is not None:
            image = getattr(material, "baseColorTexture", None)  # PBR path
            if image is None:
                image = getattr(material, "image", None)         # SimpleMaterial path
            elif not hasattr(image, "convert"):
                # Some trimesh materials wrap PIL image under .image.
                image = getattr(image, "image", image)
        if (
            uv.ndim == 2
            and uv.shape[0] >= len(mesh.vertices)
            and uv.shape[1] >= 2
            and sample_points is not None
            and len(sample_points) == len(face_indices)
        ):
            if image is not None:
                if hasattr(image, "convert"):
                    image = image.convert("RGBA")
                image_np = np.asarray(image, dtype=np.float64)
            else:
                image_np = None

            if image_np is not None and image_np.ndim == 3 and image_np.shape[2] >= 3:
                if image_np.shape[2] == 3:
                    alpha = np.full((image_np.shape[0], image_np.shape[1], 1), 255.0, dtype=np.float64)
                    image_np = np.concatenate([image_np, alpha], axis=2)
                faces = mesh.faces[face_indices]
                tri_xyz = mesh.vertices[faces]
                tri_uv = uv[faces][:, :, :2]
                p = np.asarray(sample_points, dtype=np.float64)

                a = tri_xyz[:, 0, :]
                b = tri_xyz[:, 1, :]
                c = tri_xyz[:, 2, :]
                v0 = b - a
                v1 = c - a
                v2 = p - a
                d00 = np.einsum("ij,ij->i", v0, v0)
                d01 = np.einsum("ij,ij->i", v0, v1)
                d11 = np.einsum("ij,ij->i", v1, v1)
                d20 = np.einsum("ij,ij->i", v2, v0)
                d21 = np.einsum("ij,ij->i", v2, v1)
                denom = d00 * d11 - d01 * d01

                # Numerical fallback for degenerate triangles.
                safe = np.abs(denom) > 1e-12
                v_coord = np.zeros(len(face_indices), dtype=np.float64)
                w_coord = np.zeros(len(face_indices), dtype=np.float64)
                v_coord[safe] = (d11[safe] * d20[safe] - d01[safe] * d21[safe]) / denom[safe]
                w_coord[safe] = (d00[safe] * d21[safe] - d01[safe] * d20[safe]) / denom[safe]
                u_coord = 1.0 - v_coord - w_coord

                bary = np.stack([u_coord, v_coord, w_coord], axis=1)
                bary = np.clip(bary, 0.0, 1.0)
                bary_sum = np.maximum(bary.sum(axis=1, keepdims=True), 1e-12)
                bary = bary / bary_sum
                face_uv = np.sum(tri_uv * bary[:, :, None], axis=1)

                # Clamp instead of wrapping to avoid color bleeding across UV seams.
                u = np.clip(face_uv[:, 0], 0.0, 1.0)
                v = np.clip(face_uv[:, 1], 0.0, 1.0)
                h, w = image_np.shape[:2]
                px = np.clip((u * (w - 1)).astype(np.int64), 0, w - 1)
                py = np.clip(((1.0 - v) * (h - 1)).astype(np.int64), 0, h - 1)
                sampled = image_np[py, px, :4]
                if not USE_TEXTURE_ALPHA:
                    sampled[:, 3] = 255.0
                _last_color_source = "uv_texture"
                return sampled
    except Exception:
        pass

    # Path 1b: PBR baseColorFactor (uniform material color, no texture).
    try:
        material = getattr(mesh.visual, "material", None)
        if material is not None:
            factor = getattr(material, "baseColorFactor", None)
            if factor is not None:
                factor = np.asarray(factor, dtype=np.float64).ravel()
                if len(factor) >= 3:
                    # baseColorFactor is typically [0..1] floats; normalize to [0..255].
                    rgb = factor[:3].astype(np.float64)
                    if rgb.max() <= 1.0:
                        rgb = rgb * 255.0
                    alpha = float(factor[3]) if len(factor) >= 4 else 1.0
                    if alpha <= 1.0:
                        alpha *= 255.0
                    rgba = np.clip(np.array([rgb[0], rgb[1], rgb[2], alpha], dtype=np.float64), 0.0, 255.0)
                    if not USE_TEXTURE_ALPHA:
                        rgba[3] = 255.0
                    _last_color_source = "baseColorFactor"
                    return np.tile(rgba, (len(face_indices), 1))
    except Exception:
        pass

    # Path 2: to_color() face colors.
    try:
        visual = mesh.visual.to_color()
        face_colors = np.asarray(getattr(visual, "face_colors", []), dtype=np.float64)
        if face_colors.ndim == 2 and face_colors.shape[0] == len(mesh.faces) and face_colors.shape[1] >= 3:
            if face_colors.shape[1] == 3:
                alpha = np.full((face_colors.shape[0], 1), 255.0, dtype=np.float64)
                face_colors = np.concatenate([face_colors, alpha], axis=1)
            result = face_colors[face_indices][:, :4]
            if not USE_TEXTURE_ALPHA and result.shape[1] >= 4:
                result[:, 3] = 255.0
            # Skip if colors are uniform defaults (std < 1.0 per channel).
            if np.mean(np.std(result[:, :3], axis=0)) >= 1.0:
                _last_color_source = "to_color_face_colors"
                return result
    except Exception:
        pass

    # Path 3: vertex colors averaged per face.
    try:
        vertex_colors = np.asarray(getattr(mesh.visual, "vertex_colors", []), dtype=np.float64)
        if vertex_colors.ndim == 2 and vertex_colors.shape[0] >= len(mesh.vertices) and vertex_colors.shape[1] >= 3:
            if vertex_colors.shape[1] == 3:
                alpha = np.full((vertex_colors.shape[0], 1), 255.0, dtype=np.float64)
                vertex_colors = np.concatenate([vertex_colors, alpha], axis=1)
            face_vertices = mesh.faces[face_indices]
            result = vertex_colors[face_vertices][:, :, :4].mean(axis=1)
            if not USE_TEXTURE_ALPHA and result.shape[1] >= 4:
                result[:, 3] = 255.0
            # Skip if colors are uniform defaults (std < 1.0 per channel).
            if np.mean(np.std(result[:, :3], axis=0)) >= 1.0:
                _last_color_source = "vertex_colors"
                return result
    except Exception:
        pass

    _last_color_source = "neutral_gray_fallback"
    return neutral


def _nearest_palette_indices(rgb_values, palette_colors, np):
    # rgb_values: [N,3], palette_colors: [P,3]
    diff = rgb_values[:, None, :] - palette_colors[None, :, :]
    weights = np.array([2.0, 4.0, 3.0], dtype=np.float32)
    dist = np.sum(diff * diff * weights[None, None, :], axis=2)
    return np.argmin(dist, axis=1)


def _rgb_to_hsv_np(rgb_values, np):
    rgb = np.clip(rgb_values.astype(np.float32) / 255.0, 0.0, 1.0)
    r = rgb[:, 0]
    g = rgb[:, 1]
    b = rgb[:, 2]

    cmax = np.maximum(np.maximum(r, g), b)
    cmin = np.minimum(np.minimum(r, g), b)
    delta = cmax - cmin

    hue = np.zeros_like(cmax, dtype=np.float32)
    nonzero = delta > 1e-6

    r_mask = nonzero & (cmax == r)
    g_mask = nonzero & (cmax == g)
    b_mask = nonzero & (cmax == b)

    hue[r_mask] = ((g[r_mask] - b[r_mask]) / delta[r_mask]) % 6.0
    hue[g_mask] = ((b[g_mask] - r[g_mask]) / delta[g_mask]) + 2.0
    hue[b_mask] = ((r[b_mask] - g[b_mask]) / delta[b_mask]) + 4.0
    hue = hue / 6.0

    sat = np.zeros_like(cmax, dtype=np.float32)
    nonzero_cmax = cmax > 1e-6
    sat[nonzero_cmax] = delta[nonzero_cmax] / cmax[nonzero_cmax]
    val = cmax

    return np.stack([hue, sat, val], axis=1)


def _nearest_hsv_palette_indices(hsv_values, palette_hsv, np):
    if hsv_values.shape[0] == 0:
        return np.empty(0, dtype=np.int32)
    hue_diff = np.abs(hsv_values[:, None, 0] - palette_hsv[None, :, 0])
    hue_diff = np.minimum(hue_diff, 1.0 - hue_diff)
    sat_diff = hsv_values[:, None, 1] - palette_hsv[None, :, 1]
    val_diff = hsv_values[:, None, 2] - palette_hsv[None, :, 2]
    dist = (hue_diff * 3.4) ** 2 + (sat_diff * 1.2) ** 2 + (val_diff * 0.7) ** 2
    return np.argmin(dist, axis=1).astype(np.int32)


def _assign_centroids(rgb, centroids, np, chunk_size: int = 50000):
    labels = np.empty(rgb.shape[0], dtype=np.int32)
    for start in range(0, rgb.shape[0], chunk_size):
        end = min(start + chunk_size, rgb.shape[0])
        chunk = rgb[start:end]
        diff = chunk[:, None, :] - centroids[None, :, :]
        dist = np.sum(diff * diff, axis=2)
        labels[start:end] = np.argmin(dist, axis=1)
    return labels


def _cluster_and_smooth_colors(points_local, colors_rgba, np):
    n = colors_rgba.shape[0]
    if n == 0:
        return colors_rgba, 0

    k = max(2, int(COLOR_CLUSTER_COUNT))
    if n < (k * 8):
        return colors_rgba, 0
    k = min(k, n)

    rgb = colors_rgba[:, :3].astype(np.float32)
    rng = np.random.default_rng(42)

    sample_count = min(int(COLOR_CLUSTER_SAMPLE_SIZE), n)
    if sample_count < k:
        sample_count = k

    if sample_count < n:
        sample_idx = rng.choice(n, size=sample_count, replace=False)
        sample = rgb[sample_idx]
    else:
        sample = rgb

    init_idx = rng.choice(sample.shape[0], size=k, replace=False)
    centroids = sample[init_idx].copy()

    max_iter = max(1, int(COLOR_CLUSTER_MAX_ITER))
    for _ in range(max_iter):
        sample_labels = _assign_centroids(sample, centroids, np, chunk_size=20000)
        updated = centroids.copy()
        for ci in range(k):
            mask = sample_labels == ci
            if np.any(mask):
                updated[ci] = sample[mask].mean(axis=0)
        if float(np.max(np.abs(updated - centroids))) < 0.6:
            centroids = updated
            break
        centroids = updated

    labels = _assign_centroids(rgb, centroids, np)

    threshold = int(COLOR_SMOOTHING_NEIGHBOR_THRESHOLD)
    if threshold > 0 and points_local.shape[0] == labels.shape[0]:
        neighbors = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))
        points_int = points_local.astype(np.int32, copy=False)
        coord_to_index = {
            (int(p[0]), int(p[1]), int(p[2])): idx
            for idx, p in enumerate(points_int)
        }
        smoothed = labels.copy()
        for idx, p in enumerate(points_int):
            counts = {}
            px = int(p[0])
            py = int(p[1])
            pz = int(p[2])
            for dx, dy, dz in neighbors:
                j = coord_to_index.get((px + dx, py + dy, pz + dz))
                if j is None:
                    continue
                lab = int(labels[j])
                counts[lab] = counts.get(lab, 0) + 1
            if not counts:
                continue
            best_label = labels[idx]
            best_count = 0
            for lab, cnt in counts.items():
                if cnt > best_count:
                    best_count = cnt
                    best_label = lab
            if best_count >= threshold:
                smoothed[idx] = best_label
        labels = smoothed

    clustered = colors_rgba.copy()
    clustered[:, :3] = centroids[labels]
    unique_clusters = int(np.unique(labels).shape[0])
    return clustered, unique_clusters


def _map_colors_to_blocks(colors_rgba, np, *, force_vivid: bool = False):
    concrete = (
        ((207, 213, 214), "minecraft:white_concrete"),
        ((121, 42, 172), "minecraft:purple_concrete"),
        ((44, 46, 143), "minecraft:blue_concrete"),
        ((71, 79, 82), "minecraft:gray_concrete"),
        ((8, 10, 15), "minecraft:black_concrete"),
        ((142, 32, 32), "minecraft:red_concrete"),
        ((224, 97, 0), "minecraft:orange_concrete"),
        ((241, 175, 21), "minecraft:yellow_concrete"),
        ((94, 168, 24), "minecraft:lime_concrete"),
        ((21, 119, 136), "minecraft:cyan_concrete"),
        ((169, 48, 159), "minecraft:magenta_concrete"),
        ((214, 101, 143), "minecraft:pink_concrete"),
        ((36, 137, 199), "minecraft:light_blue_concrete"),
        ((125, 74, 48), "minecraft:brown_concrete"),
        ((83, 109, 27), "minecraft:green_concrete"),
        ((125, 125, 125), "minecraft:light_gray_concrete"),
    )
    wool = (
        ((234, 236, 237), "minecraft:white_wool"),
        ((22, 22, 26), "minecraft:black_wool"),
        ((161, 39, 34), "minecraft:red_wool"),
        ((240, 118, 19), "minecraft:orange_wool"),
        ((248, 198, 39), "minecraft:yellow_wool"),
        ((112, 185, 25), "minecraft:lime_wool"),
        ((20, 180, 133), "minecraft:cyan_wool"),
        ((60, 68, 170), "minecraft:blue_wool"),
        ((121, 42, 172), "minecraft:purple_wool"),
        ((189, 68, 179), "minecraft:magenta_wool"),
        ((58, 175, 217), "minecraft:light_blue_wool"),
        ((237, 141, 172), "minecraft:pink_wool"),
        ((141, 145, 146), "minecraft:light_gray_wool"),
        ((62, 68, 71), "minecraft:gray_wool"),
        ((114, 71, 40), "minecraft:brown_wool"),
        ((84, 109, 27), "minecraft:green_wool"),
    )
    terracotta = (
        ((209, 178, 161), "minecraft:white_terracotta"),
        ((61, 41, 35), "minecraft:black_terracotta"),
        ((143, 61, 46), "minecraft:red_terracotta"),
        ((161, 83, 37), "minecraft:orange_terracotta"),
        ((186, 133, 35), "minecraft:yellow_terracotta"),
        ((103, 117, 52), "minecraft:lime_terracotta"),
        ((86, 91, 91), "minecraft:cyan_terracotta"),
        ((74, 59, 91), "minecraft:blue_terracotta"),
        ((118, 70, 86), "minecraft:purple_terracotta"),
        ((150, 88, 109), "minecraft:magenta_terracotta"),
        ((113, 108, 137), "minecraft:light_blue_terracotta"),
        ((162, 78, 79), "minecraft:pink_terracotta"),
        ((135, 107, 98), "minecraft:light_gray_terracotta"),
        ((58, 42, 36), "minecraft:brown_terracotta"),
        ((76, 83, 42), "minecraft:green_terracotta"),
        ((152, 94, 68), "minecraft:terracotta"),
    )
    natural = (
        ((121, 192, 90), "minecraft:grass_block"),
        ((134, 96, 67), "minecraft:dirt"),
        ((232, 228, 220), "minecraft:birch_planks"),
        ((143, 119, 72), "minecraft:oak_planks"),
        ((114, 84, 48), "minecraft:spruce_planks"),
        ((154, 110, 77), "minecraft:jungle_planks"),
        ((170, 91, 51), "minecraft:acacia_planks"),
        ((68, 47, 32), "minecraft:dark_oak_planks"),
        ((247, 233, 163), "minecraft:sandstone"),
        ((183, 96, 27), "minecraft:red_sandstone"),
        ((123, 123, 123), "minecraft:stone"),
        ((77, 77, 79), "minecraft:deepslate"),
        ((137, 98, 79), "minecraft:granite"),
        ((186, 187, 182), "minecraft:diorite"),
        ((131, 133, 133), "minecraft:andesite"),
        ((173, 173, 173), "minecraft:iron_block"),
        ((249, 236, 78), "minecraft:gold_block"),
        ((167, 110, 79), "minecraft:copper_block"),
        ((111, 121, 111), "minecraft:oxidized_copper"),
        ((32, 32, 32), "minecraft:coal_block"),
        ((20, 18, 29), "minecraft:obsidian"),
        ((229, 229, 229), "minecraft:quartz_block"),
        ((127, 204, 177), "minecraft:prismarine"),
        ((173, 201, 190), "minecraft:sea_lantern"),
        ((217, 210, 184), "minecraft:end_stone"),
    )
    glass = (
        ((255, 255, 255), "minecraft:white_stained_glass"),
        ((30, 30, 30), "minecraft:black_stained_glass"),
        ((161, 39, 34), "minecraft:red_stained_glass"),
        ((240, 118, 19), "minecraft:orange_stained_glass"),
        ((248, 198, 39), "minecraft:yellow_stained_glass"),
        ((112, 185, 25), "minecraft:lime_stained_glass"),
        ((20, 180, 133), "minecraft:cyan_stained_glass"),
        ((60, 68, 170), "minecraft:blue_stained_glass"),
        ((121, 42, 172), "minecraft:purple_stained_glass"),
        ((189, 68, 179), "minecraft:magenta_stained_glass"),
        ((58, 175, 217), "minecraft:light_blue_stained_glass"),
        ((237, 141, 172), "minecraft:pink_stained_glass"),
        ((142, 142, 142), "minecraft:light_gray_stained_glass"),
        ((62, 68, 71), "minecraft:gray_stained_glass"),
        ((114, 71, 40), "minecraft:brown_stained_glass"),
        ((84, 109, 27), "minecraft:green_stained_glass"),
    )

    vivid_palette = tuple(concrete) + tuple(wool)
    vivid_hue_wheel = (
        ((142, 32, 32), "minecraft:red_concrete"),
        ((224, 97, 0), "minecraft:orange_concrete"),
        ((241, 175, 21), "minecraft:yellow_concrete"),
        ((94, 168, 24), "minecraft:lime_concrete"),
        ((83, 109, 27), "minecraft:green_concrete"),
        ((21, 119, 136), "minecraft:cyan_concrete"),
        ((36, 137, 199), "minecraft:light_blue_concrete"),
        ((44, 46, 143), "minecraft:blue_concrete"),
        ((121, 42, 172), "minecraft:purple_concrete"),
        ((169, 48, 159), "minecraft:magenta_concrete"),
        ((214, 101, 143), "minecraft:pink_concrete"),
    )
    muted_palette = tuple(terracotta) + tuple(natural) + (
        ((207, 213, 214), "minecraft:white_concrete"),
        ((125, 125, 125), "minecraft:light_gray_concrete"),
        ((71, 79, 82), "minecraft:gray_concrete"),
        ((8, 10, 15), "minecraft:black_concrete"),
    )
    glass_palette = tuple(glass)

    vivid_rgb = np.array([rgb for rgb, _name in vivid_palette], dtype=np.float32)
    vivid_names = np.array([name for _rgb, name in vivid_palette], dtype=object)
    vivid_hsv = _rgb_to_hsv_np(vivid_rgb, np)
    vivid_wheel_rgb = np.array([rgb for rgb, _name in vivid_hue_wheel], dtype=np.float32)
    vivid_wheel_names = np.array([name for _rgb, name in vivid_hue_wheel], dtype=object)
    vivid_wheel_hsv = _rgb_to_hsv_np(vivid_wheel_rgb, np)

    muted_rgb = np.array([rgb for rgb, _name in muted_palette], dtype=np.float32)
    muted_names = np.array([name for _rgb, name in muted_palette], dtype=object)

    glass_rgb = np.array([rgb for rgb, _name in glass_palette], dtype=np.float32)
    glass_names = np.array([name for _rgb, name in glass_palette], dtype=object)
    glass_hsv = _rgb_to_hsv_np(glass_rgb, np)

    n = colors_rgba.shape[0]
    mapped = np.empty(n, dtype=object)
    mapped[:] = ""

    alpha = colors_rgba[:, 3] if colors_rgba.shape[1] >= 4 else np.full(n, 255.0, dtype=np.float32)
    if not USE_TEXTURE_ALPHA:
        alpha = np.full(n, 255.0, dtype=np.float32)
    rgb = colors_rgba[:, :3].astype(np.float32)

    mask_air = alpha < float(ALPHA_CUTOUT)
    mask_glass = (alpha >= float(ALPHA_CUTOUT)) & (alpha < float(ALPHA_GLASS_MAX))
    mask_opaque = alpha >= float(ALPHA_GLASS_MAX)

    chunk = 4096
    for start in range(0, n, chunk):
        end = min(start + chunk, n)
        sl = slice(start, end)
        chunk_indices = np.arange(start, end)
        chunk_rgb = rgb[sl]

        opaque_mask = mask_opaque[sl]
        glass_mask = mask_glass[sl]
        air_mask = mask_air[sl]

        if np.any(opaque_mask):
            sub_rgb = chunk_rgb[opaque_mask]
            sub_hsv = _rgb_to_hsv_np(sub_rgb, np)
            sub_sat = sub_hsv[:, 1]
            if force_vivid:
                # In vivid mode, keep chromatic hues chromatic.
                chroma_mask = sub_sat >= 0.10
                if np.any(chroma_mask):
                    indices = _nearest_hsv_palette_indices(sub_hsv[chroma_mask], vivid_wheel_hsv, np)
                    mapped[chunk_indices[opaque_mask][chroma_mask]] = vivid_wheel_names[indices]
                if np.any(~chroma_mask):
                    indices = _nearest_palette_indices(sub_rgb[~chroma_mask], muted_rgb, np)
                    mapped[chunk_indices[opaque_mask][~chroma_mask]] = muted_names[indices]
            else:
                vivid_mask = sub_sat >= float(VIVID_SAT_THRESHOLD)
                if np.any(vivid_mask):
                    indices = _nearest_hsv_palette_indices(sub_hsv[vivid_mask], vivid_hsv, np)
                    mapped[chunk_indices[opaque_mask][vivid_mask]] = vivid_names[indices]
                if np.any(~vivid_mask):
                    indices = _nearest_palette_indices(sub_rgb[~vivid_mask], muted_rgb, np)
                    mapped[chunk_indices[opaque_mask][~vivid_mask]] = muted_names[indices]

        if np.any(glass_mask):
            sub = chunk_rgb[glass_mask]
            sub_hsv = _rgb_to_hsv_np(sub, np)
            indices = _nearest_hsv_palette_indices(sub_hsv, glass_hsv, np)
            mapped[chunk_indices[glass_mask]] = glass_names[indices]

        if np.any(air_mask):
            mapped[chunk_indices[air_mask]] = ""

    return mapped.tolist()


def _build_commands(points, blocks: Iterable[str]) -> List[str]:
    commands: List[str] = []
    for (x, y, z), block in zip(points, blocks):
        if not block:
            continue
        if COORDINATE_MODE == "XYZ":
            commands.append(f"setblock ~{int(x)} ~{int(y)} ~{int(z)} {block}")
        else:
            # Default keeps vertical axis aligned with legacy pipeline behavior.
            commands.append(f"setblock ~{int(x)} ~{int(z)} ~{int(y)} {block}")
    return commands


def lambda_handler(event, _context):
    started = time.time()
    try:
        global _last_color_source
        _last_color_source = "neutral_gray_fallback"
        _install_and_find_libraries()
        import importlib

        importlib.invalidate_caches()

        import numpy as np
        import trimesh
        from scipy import ndimage
        from scipy.spatial import cKDTree

        payload = _parse_request(event)
        input_s3 = _get_required_s3_uri(payload)
        size = _choose_size(payload)
        job_id = str(payload.get("jobId") or payload.get("job_id") or uuid.uuid4().hex[:12]).strip()
        if not job_id:
            job_id = uuid.uuid4().hex[:12]

        input_bucket, input_key = _split_s3_uri(input_s3)
        input_path = "/tmp/input.glb"
        s3.download_file(input_bucket, input_key, input_path)

        detected_input_format = _detect_mesh_file_type(input_path)
        if detected_input_format == "json":
            raise ValueError("Input mesh artifact is JSON/error payload, not mesh geometry")

        # Try force="mesh" first to get a single concatenated Trimesh that
        # preserves UV coordinates and texture material for color sampling.
        try:
            if detected_input_format:
                mesh = trimesh.load(input_path, file_type=detected_input_format, force="mesh")
            else:
                mesh = trimesh.load(input_path, force="mesh")
            if not isinstance(mesh, trimesh.Trimesh) or len(mesh.vertices) == 0:
                raise ValueError("fallback to scene")
            meshes = [mesh]
        except Exception:
            if detected_input_format:
                loaded = trimesh.load(input_path, file_type=detected_input_format, force="scene")
            else:
                loaded = trimesh.load(input_path, force="scene")
            meshes = _collect_meshes(loaded, trimesh)
        if not meshes:
            raise ValueError("No meshes found in input mesh")

        vertices = np.vstack([mesh.vertices for mesh in meshes if len(mesh.vertices) > 0])
        extents = vertices.max(axis=0) - vertices.min(axis=0)
        max_extent = float(np.max(extents))
        if max_extent <= 0:
            raise ValueError("Invalid mesh extents")

        target_span = max(8, int(SIZE_TARGET_SPAN.get(size, SIZE_TARGET_SPAN["medium"])))
        scale = float(target_span) / max_extent
        for mesh in meshes:
            mesh.apply_scale(scale)

        total_samples = max(10000, int(SIZE_SURFACE_SAMPLES.get(size, SIZE_SURFACE_SAMPLES["medium"])))
        area_sum = sum(max(float(mesh.area), 1e-6) for mesh in meshes)

        sampled_points_list = []
        sampled_colors_list = []
        for mesh in meshes:
            weight = max(float(mesh.area), 1e-6) / area_sum
            mesh_samples = max(1500, int(total_samples * weight))
            points, face_indices = trimesh.sample.sample_surface(mesh, mesh_samples)
            colors = _sample_face_colors(mesh, points, face_indices, np)
            sampled_points_list.append(points)
            sampled_colors_list.append(colors)

        sampled_points = np.vstack(sampled_points_list)
        sampled_colors = np.vstack(sampled_colors_list)
        if sampled_points.shape[0] == 0:
            raise ValueError("No surface points sampled from GLB")
        sampled_points = _remap_points_up_axis(sampled_points, np)

        voxel_indices = np.round(sampled_points).astype(np.int32)
        min_v = voxel_indices.min(axis=0) - int(BOUNDARY_PADDING_VOXELS)
        max_v = voxel_indices.max(axis=0) + int(BOUNDARY_PADDING_VOXELS)
        dims = max_v - min_v + 1

        # Safety bound for memory usage in Lambda.
        if int(dims[0]) * int(dims[1]) * int(dims[2]) > 128_000_000:
            raise ValueError(f"Voxel grid too large: {dims.tolist()}")

        grid = np.zeros(dims, dtype=bool)
        shifted = voxel_indices - min_v
        shifted = np.clip(shifted, 0, dims - 1)
        grid[shifted[:, 0], shifted[:, 1], shifted[:, 2]] = True

        surface_grid = grid
        if MORPH_CLOSE_ITERATIONS > 0:
            surface_grid = ndimage.binary_closing(
                surface_grid,
                structure=np.ones((3, 3, 3), dtype=bool),
                iterations=MORPH_CLOSE_ITERATIONS,
            )
        if MORPH_DILATE_ITERATIONS > 0:
            surface_grid = ndimage.binary_dilation(
                surface_grid,
                structure=np.ones((3, 3, 3), dtype=bool),
                iterations=MORPH_DILATE_ITERATIONS,
            )
        solid_grid = ndimage.binary_fill_holes(surface_grid)
        if KEEP_LARGEST_COMPONENT or MIN_COMPONENT_VOXELS > 0:
            labels, count = ndimage.label(solid_grid, structure=np.ones((3, 3, 3), dtype=bool))
            if count > 1:
                sizes = np.bincount(labels.ravel())
                min_component_voxels = max(1, int(MIN_COMPONENT_VOXELS))
                keep_mask = np.zeros(sizes.shape[0], dtype=bool)
                if sizes.size > 1:
                    keep_mask[1:] = sizes[1:] >= min_component_voxels

                if np.any(keep_mask[1:]):
                    solid_grid = keep_mask[labels]
                elif KEEP_LARGEST_COMPONENT and sizes.size > 1:
                    largest_label = int(np.argmax(sizes[1:]) + 1)
                    solid_grid = labels == largest_label
        solid_points_local = np.argwhere(solid_grid)
        if solid_points_local.shape[0] == 0:
            solid_points_local = np.argwhere(grid)
        if solid_points_local.shape[0] == 0:
            raise ValueError("No solid voxels after morphology")
        solid_points_global = solid_points_local + min_v

        tree = cKDTree(sampled_points)
        neighbor_count = max(1, int(COLOR_TRANSFER_NEIGHBORS))
        neighbor_count = min(neighbor_count, int(sampled_points.shape[0]))
        if neighbor_count == 1:
            _dist, nearest = tree.query(solid_points_global)
            solid_colors = sampled_colors[nearest]
        else:
            dist, nearest = tree.query(solid_points_global, k=neighbor_count)
            if nearest.ndim == 1:
                nearest = nearest[:, None]
                dist = dist[:, None]
            weights = 1.0 / np.maximum(dist.astype(np.float64), 1e-3)
            weights = weights / np.maximum(weights.sum(axis=1, keepdims=True), 1e-12)
            solid_colors = np.sum(sampled_colors[nearest] * weights[:, :, None], axis=1)
        solid_colors = np.clip(solid_colors, 0.0, 255.0)
        if not USE_TEXTURE_ALPHA and solid_colors.shape[1] >= 4:
            solid_colors[:, 3] = 255.0

        color_std_before = float(np.mean(np.std(solid_colors[:, :3], axis=0)))
        avg_sat_before = float(np.mean(_rgb_to_hsv_np(solid_colors[:, :3], np)[:, 1]))
        used_clusters = 0
        if avg_sat_before < float(COLOR_CLUSTER_BYPASS_SAT_THRESHOLD):
            clustered_colors, used_clusters = _cluster_and_smooth_colors(solid_points_local, solid_colors, np)
            solid_colors = clustered_colors
        color_std_after = float(np.mean(np.std(solid_colors[:, :3], axis=0)))
        force_vivid_palette = avg_sat_before >= float(FORCE_VIVID_AVG_SAT_THRESHOLD)

        mapped_blocks = _map_colors_to_blocks(solid_colors, np, force_vivid=force_vivid_palette)
        if mapped_blocks:
            air_ratio = float(sum(1 for block in mapped_blocks if not block)) / float(len(mapped_blocks))
        else:
            air_ratio = 1.0

        if air_ratio > 0.18:
            boosted_colors = solid_colors.copy()
            if boosted_colors.shape[1] >= 4:
                boosted_colors[:, 3] = np.maximum(boosted_colors[:, 3], float(ALPHA_CUTOUT + 5))
            remapped = _map_colors_to_blocks(boosted_colors, np)
            remapped_air_ratio = float(sum(1 for block in remapped if not block)) / float(len(remapped))
            if remapped_air_ratio < air_ratio:
                mapped_blocks = remapped
                air_ratio = remapped_air_ratio

        commands = _build_commands(solid_points_global, mapped_blocks)

        basename = os.path.splitext(os.path.basename(input_key))[0] or "model"
        output_key = f"{OUTPUT_PREFIX.strip('/').rstrip('/')}/{job_id}/{basename}.mcfunction"
        output_location = f"s3://{OUTPUT_BUCKET}/{output_key}"
        s3.put_object(
            Bucket=OUTPUT_BUCKET,
            Key=output_key,
            Body=("\n".join(commands) + "\n").encode("utf-8"),
            ContentType="text/plain",
        )

        used_blocks = [block for block in mapped_blocks if block]
        if used_clusters > 0:
            _last_color_source = f"{_last_color_source}+clustered_{used_clusters}"
        top_blocks = Counter(used_blocks).most_common(20)

        response = {
            "status": "success",
            "message": "Voxelized and uploaded",
            "output_location": output_location,
            "block_count": len(commands),
            "solid_voxel_count": int(solid_points_global.shape[0]),
            "size": size,
            "input_mesh_format": detected_input_format or "auto",
            "target_span": target_span,
            "grid_dims": [int(dims[0]), int(dims[1]), int(dims[2])],
            "elapsed_seconds": round(time.time() - started, 3),
            "color_std_before": round(color_std_before, 3),
            "color_std_after": round(color_std_after, 3),
            "avg_sat_before": round(avg_sat_before, 3),
            "palette_clusters_used": used_clusters,
            "force_vivid_palette": force_vivid_palette,
            "color_source": _last_color_source,
            "air_ratio": round(air_ratio, 4),
            "top_blocks": top_blocks,
        }
        return {"statusCode": 200, "body": json.dumps(response)}
    except Exception as exc:
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(exc), "type": type(exc).__name__}),
        }
