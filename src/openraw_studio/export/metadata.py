"""Safe photographic metadata for OpenRAW derivative files."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction
import math
from typing import Any, Mapping

from openraw_studio import __version__


@dataclass(frozen=True)
class DerivativePhotoMetadata:
    make: str | None = None
    model: str | None = None
    lens_model: str | None = None
    captured_at: str | None = None
    iso: int | None = None
    exposure_time: Fraction | None = None
    aperture: Fraction | None = None
    focal_length: Fraction | None = None


def extract_derivative_photo_metadata(recipe: Mapping[str, Any]) -> DerivativePhotoMetadata:
    source = recipe.get("source")
    source = source if isinstance(source, Mapping) else {}
    metadata = source.get("metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    return DerivativePhotoMetadata(
        make=_ascii_text(_first(metadata, "camera_make", "make")),
        model=_ascii_text(_first(metadata, "camera_model", "model", "unique_camera_model")),
        lens_model=_ascii_text(metadata.get("lens_model")),
        captured_at=_exif_datetime(metadata.get("captured_at")),
        iso=_positive_int(metadata.get("iso")),
        exposure_time=_positive_fraction(metadata.get("exposure_time")),
        aperture=_positive_fraction(metadata.get("aperture")),
        focal_length=_positive_fraction(metadata.get("focal_length_mm")),
    )


def build_jpeg_exif(recipe: Mapping[str, Any]) -> Any:
    """Build a Pillow Exif object for an oriented sRGB JPEG derivative."""

    try:
        from PIL import Image, TiffImagePlugin
    except ImportError as exc:
        raise RuntimeError("Pillow is required for derivative EXIF metadata") from exc

    photo = extract_derivative_photo_metadata(recipe)
    exif = Image.Exif()
    _set_base_tags(exif, photo)
    exif_ifd: dict[int, Any] = {40961: 1}
    _set_photo_tags(exif_ifd, photo, TiffImagePlugin.IFDRational)
    exif[34665] = exif_ifd
    return exif


def build_tiff_info(recipe: Mapping[str, Any]) -> Any:
    """Build flat TIFF tags for an oriented sRGB TIFF derivative."""

    try:
        from PIL import TiffImagePlugin
    except ImportError as exc:
        raise RuntimeError("Pillow is required for derivative TIFF metadata") from exc

    photo = extract_derivative_photo_metadata(recipe)
    info = TiffImagePlugin.ImageFileDirectory_v2()
    _set_base_tags(info, photo)
    info[40961] = 1
    _set_photo_tags(info, photo, TiffImagePlugin.IFDRational)
    return info


def _set_base_tags(target: Any, photo: DerivativePhotoMetadata) -> None:
    if photo.make is not None:
        target[271] = photo.make
    if photo.model is not None:
        target[272] = photo.model
    target[274] = 1
    target[305] = f"OpenRAW Studio {__version__}"
    if photo.captured_at is not None:
        target[306] = photo.captured_at


def _set_photo_tags(target: Any, photo: DerivativePhotoMetadata, rational_type: Any) -> None:
    if photo.exposure_time is not None:
        target[33434] = rational_type(photo.exposure_time.numerator, photo.exposure_time.denominator)
    if photo.aperture is not None:
        target[33437] = rational_type(photo.aperture.numerator, photo.aperture.denominator)
    if photo.iso is not None:
        target[34855] = photo.iso
    if photo.captured_at is not None:
        target[36867] = photo.captured_at
        target[36868] = photo.captured_at
    if photo.focal_length is not None:
        target[37386] = rational_type(photo.focal_length.numerator, photo.focal_length.denominator)
    if photo.lens_model is not None:
        target[42036] = photo.lens_model


def _first(values: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = values.get(key)
        if value is not None and str(value).strip():
            return value
    return None


def _ascii_text(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip().encode("ascii", errors="replace").decode("ascii")


def _exif_datetime(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if len(text) >= 19 and text[4] == ":" and text[7] == ":":
        return text[:19]
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.strftime("%Y:%m:%d %H:%M:%S")


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if result > 0 else None


def _positive_fraction(value: Any) -> Fraction | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        if isinstance(value, (tuple, list)) and len(value) == 2:
            result = Fraction(int(value[0]), int(value[1]))
        else:
            result = Fraction(str(value)).limit_denominator(1_000_000)
            if not math.isfinite(float(result)):
                return None
    except (TypeError, ValueError, ZeroDivisionError, OverflowError):
        return None
    return result if result > 0 else None
