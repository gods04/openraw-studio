import struct
from io import BytesIO

from PIL import Image


def synthetic_nikon_nef_metadata_bytes(
    width: int = 6048,
    height: int = 4024,
    embedded_jpeg: bytes | None = None,
) -> bytes:
    ifd0_defs = [
        (256, 4, 1, struct.pack("<I", width)),
        (257, 4, 1, struct.pack("<I", height)),
        (258, 3, 1, struct.pack("<H", 14)),
        (259, 3, 1, struct.pack("<H", 34713)),
        (271, 2, len(b"NIKON CORPORATION\x00"), b"NIKON CORPORATION\x00"),
        (272, 2, len(b"NIKON Z 6II\x00"), b"NIKON Z 6II\x00"),
        (274, 3, 1, struct.pack("<H", 1)),
        (277, 3, 1, struct.pack("<H", 1)),
    ]
    exif_defs = [
        (33434, 5, 1, _pack_rational(1, 125)),
        (33437, 5, 1, _pack_rational(28, 10)),
        (34855, 3, 1, struct.pack("<H", 400)),
        (36867, 2, len(b"2026:08:28 12:34:56\x00"), b"2026:08:28 12:34:56\x00"),
        (37386, 5, 1, _pack_rational(50, 1)),
        (42036, 2, len(b"NIKKOR Z 50mm f/1.8 S\x00"), b"NIKKOR Z 50mm f/1.8 S\x00"),
    ]

    ifd0_count = len(ifd0_defs) + 1 + (2 if embedded_jpeg is not None else 0)
    ifd0_size = 2 + ifd0_count * 12 + 4
    exif_offset = 8 + ifd0_size
    exif_size = 2 + len(exif_defs) * 12 + 4
    external_base = exif_offset + exif_size
    external_data = bytearray()

    def encode(tag: int, field_type: int, count: int, payload: bytes) -> bytes:
        type_size = {2: 1, 3: 2, 4: 4, 5: 8}[field_type]
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
    ifd0_entries.append(encode(34665, 4, 1, struct.pack("<I", exif_offset)))
    exif_entries = [encode(*entry) for entry in exif_defs]
    header = b"II" + struct.pack("<H", 42) + struct.pack("<I", 8)
    ifd0 = struct.pack("<H", ifd0_count) + b"".join(ifd0_entries) + struct.pack("<I", 0)
    exif = struct.pack("<H", len(exif_entries)) + b"".join(exif_entries) + struct.pack("<I", 0)
    return header + ifd0 + exif + bytes(external_data)


def embedded_jpeg_bytes(width: int = 3, height: int = 2) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (width, height), (42, 84, 126)).save(buffer, format="JPEG")
    return buffer.getvalue()


def _pack_rational(numerator: int, denominator: int) -> bytes:
    return struct.pack("<II", numerator, denominator)
