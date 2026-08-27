"""JPEG export helper for OpenRAW Native."""

from __future__ import annotations

from pathlib import Path

from openraw_studio.raw.native.tone import PreviewRgbImage


def write_jpeg(image: PreviewRgbImage, output_path: Path, *, quality: int = 92) -> Path:
    """Write an 8-bit RGB image as a JPEG file."""

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

    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = bytes(channel for pixel in image.pixels for channel in pixel)
    encoded = Image.frombytes("RGB", (image.width, image.height), payload)
    encoded.save(output_path, format="JPEG", quality=quality, optimize=False, progressive=False)
    return output_path
