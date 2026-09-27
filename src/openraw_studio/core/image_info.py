"""Small dependency-free image inspection helpers."""

from __future__ import annotations

from pathlib import Path
import struct


def read_image_size(path: str | Path) -> tuple[int, int]:
    """Read image dimensions for common preview/export formats.

    The helper intentionally avoids a heavy imaging dependency. It currently
    supports PNG, TIFF, and most JPEG files. Unknown formats return ``(0, 0)``.
    """

    image_path = Path(path)
    with image_path.open("rb") as handle:
        header = handle.read(24)
        if header.startswith(b"\x89PNG\r\n\x1a\n") and len(header) >= 24:
            width, height = struct.unpack(">II", header[16:24])
            return int(width), int(height)
        if header.startswith(b"\xff\xd8"):
            handle.seek(2)
            return _read_jpeg_size(handle)
        if header[:4] in {b"II*\x00", b"MM\x00*"}:
            return _read_tiff_size(handle, header)
    return 0, 0


def _read_tiff_size(handle, header: bytes) -> tuple[int, int]:
    byte_order = "<" if header.startswith(b"II") else ">"
    if len(header) < 8:
        return 0, 0
    ifd_offset = struct.unpack(f"{byte_order}I", header[4:8])[0]
    try:
        handle.seek(ifd_offset)
        count_bytes = handle.read(2)
        if len(count_bytes) != 2:
            return 0, 0
        entry_count = struct.unpack(f"{byte_order}H", count_bytes)[0]
        dimensions: dict[int, int] = {}
        for _ in range(entry_count):
            entry = handle.read(12)
            if len(entry) != 12:
                return 0, 0
            tag, value_type, value_count = struct.unpack(f"{byte_order}HHI", entry[:8])
            if tag not in {256, 257} or value_count != 1:
                continue
            if value_type == 3:
                dimensions[tag] = struct.unpack(f"{byte_order}H", entry[8:10])[0]
            elif value_type == 4:
                dimensions[tag] = struct.unpack(f"{byte_order}I", entry[8:12])[0]
        return dimensions.get(256, 0), dimensions.get(257, 0)
    except (OSError, struct.error, ValueError):
        return 0, 0


def _read_jpeg_size(handle) -> tuple[int, int]:
    while True:
        marker_start = handle.read(1)
        if marker_start == b"":
            return 0, 0
        if marker_start != b"\xff":
            continue

        marker = handle.read(1)
        while marker == b"\xff":
            marker = handle.read(1)
        if marker == b"":
            return 0, 0

        marker_value = marker[0]
        if marker_value in {0xD8, 0xD9}:
            continue
        if 0xD0 <= marker_value <= 0xD7:
            continue

        length_bytes = handle.read(2)
        if len(length_bytes) != 2:
            return 0, 0
        segment_length = struct.unpack(">H", length_bytes)[0]
        if segment_length < 2:
            return 0, 0

        if marker_value in {
            0xC0,
            0xC1,
            0xC2,
            0xC3,
            0xC5,
            0xC6,
            0xC7,
            0xC9,
            0xCA,
            0xCB,
            0xCD,
            0xCE,
            0xCF,
        }:
            data = handle.read(5)
            if len(data) != 5:
                return 0, 0
            height, width = struct.unpack(">HH", data[1:5])
            return int(width), int(height)

        handle.seek(segment_length - 2, 1)
