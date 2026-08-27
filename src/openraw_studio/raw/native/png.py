"""Small dependency-free PNG writer for OpenRAW Native previews."""

from __future__ import annotations

from pathlib import Path
import struct
import zlib

from openraw_studio.raw.native.tone import PreviewRgbImage


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def encode_png_rgb8(image: PreviewRgbImage) -> bytes:
    """Encode an 8-bit RGB preview image as a PNG byte stream."""

    if image.width <= 0 or image.height <= 0:
        raise ValueError("PNG image dimensions must be positive")
    if len(image.pixels) != image.width * image.height:
        raise ValueError("PNG pixel count does not match image dimensions")

    ihdr = struct.pack(
        ">IIBBBBB",
        image.width,
        image.height,
        8,  # bit depth
        2,  # truecolor RGB
        0,  # compression method
        0,  # filter method
        0,  # interlace method
    )

    scanlines = bytearray()
    for row in range(image.height):
        scanlines.append(0)
        row_start = row * image.width
        for red, green, blue in image.pixels[row_start : row_start + image.width]:
            scanlines.extend((_channel(red), _channel(green), _channel(blue)))

    return b"".join(
        (
            PNG_SIGNATURE,
            _chunk(b"IHDR", ihdr),
            _chunk(b"IDAT", zlib.compress(bytes(scanlines))),
            _chunk(b"IEND", b""),
        )
    )


def write_png(image: PreviewRgbImage, output_path: Path) -> Path:
    """Write an 8-bit RGB PNG preview image."""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(encode_png_rgb8(image))
    return output_path


def _chunk(chunk_type: bytes, data: bytes) -> bytes:
    if len(chunk_type) != 4:
        raise ValueError("PNG chunk type must be 4 bytes")
    return (
        struct.pack(">I", len(data))
        + chunk_type
        + data
        + struct.pack(">I", zlib.crc32(chunk_type + data) & 0xFFFFFFFF)
    )


def _channel(value: int) -> int:
    if value < 0 or value > 255:
        raise ValueError("PNG channel values must be between 0 and 255")
    return int(value)
