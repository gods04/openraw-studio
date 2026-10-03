"""JPEG export helper for OpenRAW Native."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openraw_studio.core.files import atomic_output_path
from openraw_studio.raw.native.tone import PreviewRgbImage


def write_jpeg(
    image: PreviewRgbImage,
    output_path: Path,
    *,
    quality: int = 92,
    exif: Any | None = None,
) -> Path:
    """Write an 8-bit RGB image as a JPEG file."""

    if image.bit_depth != 8:
        raise ValueError("JPEG writer requires an 8-bit image")
    if output_path.suffix.lower() not in {".jpg", ".jpeg"}:
        raise ValueError("JPEG output path must end in .jpg or .jpeg")
    if quality < 1 or quality > 100:
        raise ValueError("JPEG quality must be between 1 and 100")
    if image.width <= 0 or image.height <= 0:
        raise ValueError("JPEG image dimensions must be positive")
    if len(image.pixels) != image.width * image.height:
        raise ValueError("JPEG pixel count does not match image dimensions")

    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("Pillow is required for JPEG export") from exc

    payload = bytes(channel for pixel in image.pixels for channel in pixel)
    encoded = Image.frombytes("RGB", (image.width, image.height), payload)
    options: dict[str, Any] = {
        "quality": quality,
        "optimize": False,
        "progressive": False,
    }
    if exif is not None:
        options["exif"] = exif
    with atomic_output_path(output_path) as temporary_path:
        encoded.save(temporary_path, format="JPEG", **options)
    return output_path
