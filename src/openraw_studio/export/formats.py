"""Canonical export format names and paths."""

from __future__ import annotations


EXPORT_FORMATS = ("jpeg", "tiff")


def normalize_export_format(value: str) -> str:
    normalized = value.strip().lower()
    aliases = {
        "jpg": "jpeg",
        "jpeg": "jpeg",
        "tif": "tiff",
        "tiff": "tiff",
    }
    try:
        return aliases[normalized]
    except KeyError as exc:
        raise ValueError(f"Unsupported export format: {value}") from exc


def export_suffix(value: str) -> str:
    return ".jpg" if normalize_export_format(value) == "jpeg" else ".tif"


def export_display_name(value: str) -> str:
    return normalize_export_format(value).upper()


def validate_export_quality(value: int) -> int:
    if isinstance(value, bool) or not 1 <= int(value) <= 100:
        raise ValueError("JPEG quality must be between 1 and 100")
    return int(value)


def validate_export_bit_depth(value: int, *, export_format: str) -> int:
    """Return integer 8/16 for TIFF or 8 for JPEG; otherwise raise ValueError."""

    normalized = normalize_export_format(export_format)
    if isinstance(value, bool) or not isinstance(value, int) or value not in (8, 16):
        raise ValueError("Export bit depth must be an integer: 8 or 16")
    if normalized == "jpeg" and value != 8:
        raise ValueError("JPEG only supports 8-bit export; use TIFF for 16-bit export")
    return value
