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
    bits_per_sample: int | None = None,
    maker_note: bytes | None = None,
) -> bytes:
    if sensor_samples is not None and len(sensor_samples) != width * height:
        raise ValueError("sensor_samples must match width * height")

    effective_bits_per_sample = bits_per_sample if bits_per_sample is not None else (16 if sensor_samples is not None else 14)
    ifd0_defs = [
        (256, 4, 1, struct.pack("<I", width)),
        (257, 4, 1, struct.pack("<I", height)),
        (258, 3, 1, struct.pack("<H", effective_bits_per_sample)),
        (259, 3, 1, struct.pack("<H", 1 if sensor_samples is not None else 34713)),
        (271, 2, len(b"NIKON CORPORATION\x00"), b"NIKON CORPORATION\x00"),
        (272, 2, len(b"NIKON Z 6II\x00"), b"NIKON Z 6II\x00"),
        (274, 3, 1, struct.pack("<H", 1)),
        (277, 3, 1, struct.pack("<H", 1)),
    ]
    if sensor_samples is not None:
        pixel_bytes = pack_sensor_rows(
            sensor_samples,
            width=width,
            height=height,
            bits_per_sample=effective_bits_per_sample,
        )
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
    if maker_note is not None:
        exif_defs.append((37500, 7, len(maker_note), maker_note))

    ifd0_count = len(ifd0_defs) + 1 + (2 if embedded_jpeg is not None else 0) + (1 if sensor_samples is not None else 0)
    ifd0_size = 2 + ifd0_count * 12 + 4
    exif_offset = 8 + ifd0_size
    exif_size = 2 + len(exif_defs) * 12 + 4
    external_base = exif_offset + exif_size
    external_data = bytearray()

    def encode(tag: int, field_type: int, count: int, payload: bytes) -> bytes:
        type_size = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1}[field_type]
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


def synthetic_nikon_nef_sensor_bytes(width: int = 4, height: int = 4, bits_per_sample: int = 16) -> bytes:
    if bits_per_sample not in {12, 14, 16}:
        raise ValueError("bits_per_sample must be 12, 14, or 16")

    black_level = 64
    white_level = 16383 if bits_per_sample == 14 else 4095
    span = white_level - black_level
    samples = tuple(64 + int(round(span * index / max(1, width * height - 1))) for index in range(width * height))
    return synthetic_nikon_nef_metadata_bytes(
        width=width,
        height=height,
        sensor_samples=samples,
        black_level=black_level,
        white_level=white_level,
        bits_per_sample=bits_per_sample,
    )


def embedded_jpeg_bytes(width: int = 3, height: int = 2) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (width, height), (42, 84, 126)).save(buffer, format="JPEG")
    return buffer.getvalue()


def nikon_makernote_bytes() -> bytes:
    entries = [
        (0x0001, 7, 4, b"0211"),
        (0x001B, 3, 7, struct.pack("<7H", 12, 5600, 3728, 5600, 3728, 0, 0)),
        (0x0045, 3, 4, struct.pack("<4H", 16, 8, 5568, 3712)),
        (0x008C, 7, 8, b"I0\x00\xff\x00\xff\x01\x00"),
        (0x0093, 3, 1, struct.pack("<H", 3)),
        (0x0096, 7, 6, b"F0\x00\x08\x00\x08"),
    ]
    return _tiff_makernote_bytes(entries)


def _pack_rational(numerator: int, denominator: int) -> bytes:
    return struct.pack("<II", numerator, denominator)


def _tiff_makernote_bytes(entries: list[tuple[int, int, int, bytes]]) -> bytes:
    entry_count = len(entries)
    header = b"Nikon\x00\x02\x11\x00\x00II" + struct.pack("<H", 42) + struct.pack("<I", 8)
    ifd_size = 2 + entry_count * 12 + 4
    external_base = len(header) + ifd_size - 10
    external_data = bytearray()

    def encode(tag: int, field_type: int, count: int, payload: bytes) -> bytes:
        type_size = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 8: 2, 9: 4, 10: 8}[field_type]
        byte_count = type_size * count
        if byte_count <= 4:
            return struct.pack("<HHI", tag, field_type, count) + payload.ljust(4, b"\x00")
        offset = external_base + len(external_data)
        external_data.extend(payload)
        if len(external_data) % 2:
            external_data.extend(b"\x00")
        return struct.pack("<HHII", tag, field_type, count, offset)

    ifd = struct.pack("<H", entry_count) + b"".join(encode(*entry) for entry in entries) + struct.pack("<I", 0)
    return header + ifd + bytes(external_data)


def pack_sensor_rows(
    values: tuple[int, ...],
    *,
    width: int,
    height: int,
    bits_per_sample: int,
) -> bytes:
    if len(values) != width * height:
        raise ValueError("values must match width * height")
    if bits_per_sample == 16:
        return _pack_shorts(values)
    if bits_per_sample not in {12, 14}:
        raise ValueError("bits_per_sample must be 12, 14, or 16")

    rows = []
    for row in range(height):
        start = row * width
        rows.append(_pack_packed_msb(values[start : start + width], bits_per_sample))
    return b"".join(rows)


def _pack_shorts(values: tuple[int, ...]) -> bytes:
    return b"".join(struct.pack("<H", value) for value in values)


def _pack_packed_msb(values: tuple[int, ...], bits_per_sample: int) -> bytes:
    maximum = (1 << bits_per_sample) - 1
    output = bytearray()
    accumulator = 0
    available_bits = 0
    for value in values:
        if value < 0 or value > maximum:
            raise ValueError(f"sample value {value} is outside {bits_per_sample}-bit range")
        accumulator = (accumulator << bits_per_sample) | value
        available_bits += bits_per_sample
        while available_bits >= 8:
            shift = available_bits - 8
            output.append((accumulator >> shift) & 0xFF)
            accumulator &= (1 << shift) - 1
            available_bits = shift
    if available_bits:
        output.append((accumulator << (8 - available_bits)) & 0xFF)
    return bytes(output)
