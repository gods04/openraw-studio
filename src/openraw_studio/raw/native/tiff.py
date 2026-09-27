"""TIFF export helper for OpenRAW Native."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openraw_studio.core.files import atomic_output_path
from openraw_studio.raw.native.tone import PreviewRgbImage


def write_tiff_rgb8(
    image: PreviewRgbImage,
    output_path: Path,
    *,
    tiffinfo: Any | None = None,
) -> Path:
    """Write an 8-bit sRGB image as a lossless Deflate-compressed TIFF."""

    if output_path.suffix.lower() not in {".tif", ".tiff"}:
        raise ValueError("TIFF output path must end in .tif or .tiff")
    if image.width <= 0 or image.height <= 0:
        raise ValueError("TIFF image dimensions must be positive")
    if len(image.pixels) != image.width * image.height:
        raise ValueError("TIFF pixel count does not match image dimensions")

    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("Pillow is required for TIFF export") from exc

    payload = bytes(channel for pixel in image.pixels for channel in pixel)
    encoded = Image.frombytes("RGB", (image.width, image.height), payload)
    options: dict[str, Any] = {"compression": "tiff_deflate"}
    if tiffinfo is not None:
        options["tiffinfo"] = tiffinfo
    with atomic_output_path(output_path) as temporary_path:
        encoded.save(temporary_path, format="TIFF", **options)
    return output_path
