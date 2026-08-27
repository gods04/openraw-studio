"""Synthetic DNG generation for demos, smoke tests, and onboarding."""

from __future__ import annotations

from pathlib import Path
import struct


BLACK_LEVEL = 64
WHITE_LEVEL = 4095


def synthetic_dng_bytes(width: int = 16, height: int = 16) -> bytes:
    """Create a small uncompressed RGGB DNG supported by OpenRAW Native V0.1."""

    if width < 2 or height < 2:
        raise ValueError("synthetic DNG dimensions must be at least 2x2")
    if width % 2 or height % 2:
        raise ValueError("synthetic DNG dimensions must be even so the RGGB pattern repeats cleanly")

    pixel_bytes = _pack_shorts(_synthetic_bayer_samples(width, height))
    entries = [
        _entry_inline(256, 4, 1, _pack_long(width)),
        _entry_inline(257, 4, 1, _pack_long(height)),
        _entry_inline(258, 3, 1, _pack_short(16)),
        _entry_inline(259, 3, 1, _pack_short(1)),
        _entry_inline(262, 3, 1, _pack_short(32803)),
        _entry_external(271, 2, b"OpenRAW\x00"),
        _entry_external(272, 2, b"Synthetic DNG\x00"),
        _entry_inline(277, 3, 1, _pack_short(1)),
        _entry_inline(278, 4, 1, _pack_long(height)),
        _entry_inline(279, 4, 1, _pack_long(len(pixel_bytes))),
        _entry_inline(33421, 3, 2, _pack_short(2) + _pack_short(2)),
        _entry_inline(33422, 1, 4, bytes([0, 1, 1, 2])),
        _entry_inline(50706, 1, 4, bytes([1, 4, 0, 0])),
        _entry_external(50708, 2, b"OpenRAW Synthetic NativeCam\x00"),
        _entry_inline(50714, 3, 1, _pack_short(BLACK_LEVEL)),
        _entry_inline(50717, 3, 1, _pack_short(WHITE_LEVEL)),
        _entry_external(50721, 10, _pack_srationals([(1, 1), (0, 1), (0, 1), (0, 1), (1, 1), (0, 1), (0, 1), (0, 1), (1, 1)])),
        _entry_external(50728, 5, _pack_rationals([(1, 1), (1, 1), (1, 1)])),
        _entry_inline(50778, 3, 1, _pack_short(21)),
        _entry_pixel_offset(273),
    ]
    return _build_tiff(entries, trailing_payload=pixel_bytes)


def write_synthetic_dng(path: str | Path, *, width: int = 16, height: int = 16) -> Path:
    """Write a synthetic DNG file and return its resolved path."""

    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(synthetic_dng_bytes(width=width, height=height))
    return output_path.resolve()


def _synthetic_bayer_samples(width: int, height: int) -> list[int]:
    span = WHITE_LEVEL - BLACK_LEVEL
    samples: list[int] = []
    for row in range(height):
        vertical = row / max(1, height - 1)
        for column in range(width):
            horizontal = column / max(1, width - 1)
            base = 0.12 + horizontal * 0.62 + vertical * 0.18
            if row % 2 == 0 and column % 2 == 0:
                channel_scale = 1.08
            elif row % 2 == 1 and column % 2 == 1:
                channel_scale = 0.92
            else:
                channel_scale = 1.0
            normalized = min(0.95, max(0.02, base * channel_scale))
            samples.append(BLACK_LEVEL + int(round(span * normalized)))
    return samples


def _build_tiff(entries: list[dict], trailing_payload: bytes = b"") -> bytes:
    header = b"II" + struct.pack("<H", 42) + struct.pack("<I", 8)
    ifd_size = 2 + len(entries) * 12 + 4
    data_offset = 8 + ifd_size
    external_data = bytearray()
    encoded_entries = []

    for entry in entries:
        if entry["inline"]:
            encoded_entries.append(
                struct.pack("<HHI", entry["tag"], entry["field_type"], entry["count"]) + entry["payload"].ljust(4, b"\x00")
            )
        elif entry.get("pixel_offset"):
            offset = data_offset + len(external_data)
            encoded_entries.append(struct.pack("<HHII", entry["tag"], entry["field_type"], entry["count"], offset))
        else:
            payload = entry["payload"]
            offset = data_offset + len(external_data)
            encoded_entries.append(struct.pack("<HHII", entry["tag"], entry["field_type"], entry["count"], offset))
            external_data.extend(payload)
            if len(external_data) % 2:
                external_data.extend(b"\x00")

    ifd = struct.pack("<H", len(entries)) + b"".join(encoded_entries) + struct.pack("<I", 0)
    return header + ifd + bytes(external_data) + trailing_payload


def _entry_inline(tag: int, field_type: int, count: int, payload: bytes) -> dict:
    return {"tag": tag, "field_type": field_type, "count": count, "payload": payload, "inline": True}


def _entry_external(tag: int, field_type: int, payload: bytes) -> dict:
    type_size = {2: 1, 5: 8, 10: 8}[field_type]
    return {"tag": tag, "field_type": field_type, "count": len(payload) // type_size, "payload": payload, "inline": False}


def _entry_pixel_offset(tag: int) -> dict:
    return {"tag": tag, "field_type": 4, "count": 1, "payload": b"", "inline": False, "pixel_offset": True}


def _pack_short(value: int) -> bytes:
    return struct.pack("<H", value)


def _pack_long(value: int) -> bytes:
    return struct.pack("<I", value)


def _pack_shorts(values: list[int]) -> bytes:
    return b"".join(_pack_short(value) for value in values)


def _pack_rationals(values: list[tuple[int, int]]) -> bytes:
    return b"".join(struct.pack("<II", numerator, denominator) for numerator, denominator in values)


def _pack_srationals(values: list[tuple[int, int]]) -> bytes:
    return b"".join(struct.pack("<ii", numerator, denominator) for numerator, denominator in values)
