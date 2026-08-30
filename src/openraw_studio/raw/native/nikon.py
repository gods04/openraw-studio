"""Nikon MakerNote helpers for OpenRAW Native."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openraw_studio.raw.native.dng import DngMetadata, DngMetadataError, DngMetadataReader, TiffIfd


MAKER_NOTE_TAG = 37500
NIKON_MAKER_PREFIX = b"Nikon\x00"
NIKON_MAKER_TIFF_OFFSET = 10


@dataclass(frozen=True)
class NikonMakerNoteSummary:
    """Small product-safe summary of Nikon MakerNote fields we understand."""

    kind: str
    byte_order: str
    tag_count: int
    version: str | None = None
    active_area: tuple[int, ...] | None = None
    crop_info: tuple[int, ...] | None = None
    compression_mode: int | None = None
    curve_byte_count: int | None = None
    curve_prefix: str | None = None
    compression_table_byte_count: int | None = None
    compression_table_prefix: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "byte_order": self.byte_order,
            "tag_count": self.tag_count,
            "version": self.version,
            "active_area": self.active_area,
            "crop_info": self.crop_info,
            "compression_mode": self.compression_mode,
            "curve_byte_count": self.curve_byte_count,
            "curve_prefix": self.curve_prefix,
            "compression_table_byte_count": self.compression_table_byte_count,
            "compression_table_prefix": self.compression_table_prefix,
        }


def summarize_nikon_makernote(metadata: DngMetadata) -> NikonMakerNoteSummary | None:
    """Parse a Nikon Type 2 MakerNote summary when one is present."""

    value = _first_tag_value(metadata.ifds, MAKER_NOTE_TAG)
    if value is None:
        return None
    payload = _undefined_bytes(value)
    if payload is None:
        return None
    return summarize_nikon_makernote_payload(payload)


def summarize_nikon_makernote_payload(payload: bytes) -> NikonMakerNoteSummary | None:
    """Parse a standalone Nikon MakerNote payload into a compact summary."""

    tiff_offset, kind = _makernote_tiff_offset(payload)
    if tiff_offset is None:
        return None
    try:
        byte_order, _endian, ifds = DngMetadataReader()._read_structure(payload[tiff_offset:])
    except DngMetadataError:
        return None
    if not ifds:
        return None

    ifd = ifds[0]
    curve_payload = _tag_bytes(ifd, 0x008C)
    compression_table_payload = _tag_bytes(ifd, 0x0096)
    return NikonMakerNoteSummary(
        kind=kind,
        byte_order=byte_order,
        tag_count=len(ifd.tags),
        version=_tag_ascii(ifd, 0x0001),
        crop_info=_tag_int_tuple(ifd, 0x001B),
        active_area=_tag_int_tuple(ifd, 0x0045),
        compression_mode=_tag_int(ifd, 0x0093),
        curve_byte_count=len(curve_payload) if curve_payload is not None else None,
        curve_prefix=_ascii_prefix(curve_payload) if curve_payload is not None else None,
        compression_table_byte_count=len(compression_table_payload) if compression_table_payload is not None else None,
        compression_table_prefix=_ascii_prefix(compression_table_payload) if compression_table_payload is not None else None,
    )


def _makernote_tiff_offset(payload: bytes) -> tuple[int | None, str]:
    if payload.startswith(NIKON_MAKER_PREFIX) and len(payload) > NIKON_MAKER_TIFF_OFFSET + 8:
        return NIKON_MAKER_TIFF_OFFSET, "Nikon Type 2 MakerNote"
    if payload[:2] in {b"II", b"MM"}:
        return 0, "Nikon TIFF MakerNote"
    return None, "unknown Nikon MakerNote"


def _first_tag_value(ifds: tuple[TiffIfd, ...], tag_code: int) -> Any:
    for ifd in ifds:
        tag = ifd.tags.get(tag_code)
        if tag is not None:
            return tag.value
    return None


def _tag_bytes(ifd: TiffIfd, tag_code: int) -> bytes | None:
    tag = ifd.tags.get(tag_code)
    if tag is None:
        return None
    return _undefined_bytes(tag.value)


def _undefined_bytes(value: Any) -> bytes | None:
    if isinstance(value, bytes):
        return value
    if isinstance(value, tuple):
        try:
            return bytes(int(item) & 0xFF for item in value)
        except (TypeError, ValueError):
            return None
    return None


def _tag_ascii(ifd: TiffIfd, tag_code: int) -> str | None:
    value = ifd.tags.get(tag_code).value if tag_code in ifd.tags else None
    if isinstance(value, str):
        text = value.strip("\x00 ")
        return text or None
    payload = _undefined_bytes(value)
    if payload is None:
        return None
    return _ascii_prefix(payload, max_length=16)


def _tag_int(ifd: TiffIfd, tag_code: int) -> int | None:
    value = ifd.tags.get(tag_code).value if tag_code in ifd.tags else None
    if isinstance(value, tuple):
        if len(value) != 1:
            return None
        value = value[0]
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _tag_int_tuple(ifd: TiffIfd, tag_code: int) -> tuple[int, ...] | None:
    value = ifd.tags.get(tag_code).value if tag_code in ifd.tags else None
    if value is None:
        return None
    values = value if isinstance(value, tuple) else (value,)
    try:
        return tuple(int(item) for item in values)
    except (TypeError, ValueError):
        return None


def _ascii_prefix(payload: bytes | None, *, max_length: int = 8) -> str | None:
    if not payload:
        return None
    chars = []
    for value in payload[:max_length]:
        if value == 0:
            break
        if 32 <= value <= 126:
            chars.append(chr(value))
        else:
            break
    return "".join(chars) or None
