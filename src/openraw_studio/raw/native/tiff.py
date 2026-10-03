"""TIFF export helper for OpenRAW Native."""

from __future__ import annotations

import os
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

    if image.bit_depth != 8:
        raise ValueError("RGB8 TIFF writer requires an 8-bit image")
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


def write_tiff_rgb16(pixels: Any, output_path: Path, *, tiffinfo: Any | None = None) -> Path:
    """Write genuine uint16 RGB samples without passing through an RGB8 encoder."""
    import numpy as np
    import tifffile

    if output_path.suffix.lower() not in {".tif", ".tiff"}:
        raise ValueError("TIFF output path must end in .tif or .tiff")
    pixels = np.asarray(pixels)
    if pixels.dtype.kind != "u" or pixels.dtype.itemsize != 2:
        raise ValueError("16-bit TIFF requires unsigned 16-bit pixels")
    if pixels.ndim != 3 or pixels.shape[2] != 3 or min(pixels.shape[:2]) <= 0:
        raise ValueError("16-bit TIFF requires a nonempty H x W x 3 RGB image")

    # Only photographic derivative tags are forwarded, never source/GPS/MakerNotes.
    allowed = {271, 272, 274, 306, 33434, 33437, 34855, 36867, 36868, 37386, 40961, 42036}
    tags = []
    for code, value in (tiffinfo or {}).items():
        if code not in allowed:
            continue
        kind = tiffinfo.tagtype[code]
        if kind == 5:
            value = (value.numerator, value.denominator)
        tags.append((code, kind, 1, value, False))
    software = tiffinfo.get(305) if tiffinfo is not None else "OpenRAW Studio"
    with atomic_output_path(output_path) as temporary_path:
        tifffile.imwrite(
            temporary_path, pixels, photometric="rgb", metadata=None,
            compression="deflate", compressionargs={"level": 1},
            rowsperstrip=64, maxworkers=min(4, os.cpu_count() or 1), byteorder="<",
            software=software, extratags=tags,
        )
    return output_path
