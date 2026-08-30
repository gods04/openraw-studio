import struct
from io import BytesIO

from PIL import Image


def synthetic_nikon_nef_metadata_bytes(
    width: int = 6048,
    height: int = 4024,
    embedded_jpeg: bytes | None = None,
    sensor_samples: tuple[int, ...] | None = None,
    black_level: int = 64,
    white_level: int = 4095,
) -> bytes:
    if sensor_samples is not None and len(sensor_samples) != width * height:
        raise ValueError("sensor_samples must match width * height")

    ifd0_defs = [
        (256, 4, 1, struct.pack("<I", width)),
        (257, 4, 1, struct.pack("<I", height)),
        (258, 3, 1, struct.pack("<H", 16 if sensor_samples is not None else 14)),
        (259, 3, 1, struct.pack("<H", 1 if sensor_samples is not None else 34713)),
        (271, 2, len(b"NIKON CORPORATION\x00"), b"NIKON CORPORATION\x00"),
        (272, 2, len(b"NIKON Z 6II\x00"), b"NIKON Z 6II\x00"),
        (274, 3, 1, struct.pack("<H", 1)),
        (277, 3, 1, struct.pack("<H", 1)),
    ]
    if sensor_samples is not None:
        pixel_bytes = _pack_shorts(sensor_samples)
        ifd0_defs.extend(
            [
                (262, 3, 1, struct.pack("<H", 32803)),
                (278, 4, 1, struct.pack("<I", height)),
                (279, 4, 1, struct.pack("<I", len(pixel_bytes))),
                (33421, 3, 2, struct.pack("<HH", 2, 2)),
                (33422, 1, 4, bytes([0, 1, 1, 2])),
                (50714, 3, 1, struct.pack("<H", black_level)),
                (50717, 3 if white_level <= 65535 else 4, 1, struct.pack("<H" if white_level <= 65535 else "<I", white_level)),
            ]
        )
    else:
        pixel_bytes = b""
    exif_defs = [
        (33434, 5, 1, _pack_rational(1, 125)),
        (33437, 5, 1, _pack_rational(28, 10)),
        (34855, 3, 1, struct.pack("<H", 400)),
        (36867, 2, len(b"2026:08:28 12:34:56\x00"), b"2026:08:28 12:34:56\x00"),
        (37386, 5, 1, _pack_rational(50, 1)),
        (42036, 2, len(b"NIKKOR Z 50mm f/1.8 S\x00"), b"NIKKOR Z 50mm f/1.8 S\x00"),
    ]

    ifd0_count = len(ifd0_defs) + 1 + (2 if embedded_jpeg is not None else 0) + (1 if sensor_samples is not None else 0)
    ifd0_size = 2 + ifd0_count * 12 + 4
    exif_offset = 8 + ifd0_size
    exif_size = 2 + len(exif_defs) * 12 + 4
    external_base = exif_offset + exif_size
    external_data = bytearray()

    def encode(tag: int, field_type: int, count: int, payload: bytes) -> bytes:
        type_size = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8}[field_type]
        byte_count = type_size * count
        if byte_count <= 4:
            return struct.pack("<HHI", tag, field_type, count) + payload.ljust(4, b"\x00")
        offset = external_base + len(external_data)
        external_data.extend(payload)
        if len(external_data) % 2:
            external_data.extend(b"\x00")
        return struct.pack("<HHII", tag, field_type, count, offset)

    ifd0_entries = [encode(*entry) for entry in ifd0_defs]
    if embedded_jpeg is not None:
        jpeg_offset = external_base + len(external_data)
        external_data.extend(embedded_jpeg)
        if len(external_data) % 2:
            external_data.extend(b"\x00")
        ifd0_entries.append(encode(513, 4, 1, struct.pack("<I", jpeg_offset)))
        ifd0_entries.append(encode(514, 4, 1, struct.pack("<I", len(embedded_jpeg))))
    if sensor_samples is not None:
        pixel_offset = external_base + len(external_data)
        external_data.extend(pixel_bytes)
        if len(external_data) % 2:
            external_data.extend(b"\x00")
        ifd0_entries.append(encode(273, 4, 1, struct.pack("<I", pixel_offset)))
    ifd0_entries.append(encode(34665, 4, 1, struct.pack("<I", exif_offset)))
    exif_entries = [encode(*entry) for entry in exif_defs]
    header = b"II" + struct.pack("<H", 42) + struct.pack("<I", 8)
    ifd0 = struct.pack("<H", ifd0_count) + b"".join(ifd0_entries) + struct.pack("<I", 0)
    exif = struct.pack("<H", len(exif_entries)) + b"".join(exif_entries) + struct.pack("<I", 0)
    return header + ifd0 + exif + bytes(external_data)


def synthetic_nikon_nef_sensor_bytes(width: int = 4, height: int = 4) -> bytes:
    span = 4095 - 64
    samples = tuple(64 + int(round(span * index / max(1, width * height - 1))) for index in range(width * height))
    return synthetic_nikon_nef_metadata_bytes(width=width, height=height, sensor_samples=samples)


def embedded_jpeg_bytes(width: int = 3, height: int = 2) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (width, height), (42, 84, 126)).save(buffer, format="JPEG")
    return buffer.getvalue()


def _pack_rational(numerator: int, denominator: int) -> bytes:
    return struct.pack("<II", numerator, denominator)


def _pack_shorts(values: tuple[int, ...]) -> bytes:
    return b"".join(struct.pack("<H", value) for value in values)
