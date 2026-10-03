import struct
from io import BytesIO

from PIL import Image


def synthetic_nikon_nef_metadata_bytes(
    width: int = 6048,
    height: int = 4024,
    embedded_jpeg: bytes | None = None,
    sensor_samples: tuple[int, ...] | None = None,
    compressed_sensor_payload: bytes | None = None,
    black_level: int = 64,
    white_level: int = 4095,
    bits_per_sample: int | None = None,
    maker_note: bytes | None = None,
    make: str = "NIKON CORPORATION",
    model: str = "NIKON Z 6II",
    orientation: int = 1,
) -> bytes:
    if sensor_samples is not None and len(sensor_samples) != width * height:
        raise ValueError("sensor_samples must match width * height")
    if sensor_samples is not None and compressed_sensor_payload is not None:
        raise ValueError("sensor_samples and compressed_sensor_payload cannot be used together")

    effective_bits_per_sample = bits_per_sample if bits_per_sample is not None else (16 if sensor_samples is not None else 14)
    compression_value = 34713 if compressed_sensor_payload is not None else 1 if sensor_samples is not None else 34713
    has_sensor_payload = sensor_samples is not None or compressed_sensor_payload is not None
    make_payload = make.encode("ascii") + b"\x00"
    model_payload = model.encode("ascii") + b"\x00"
    ifd0_defs = [
        (256, 4, 1, struct.pack("<I", width)),
        (257, 4, 1, struct.pack("<I", height)),
        (258, 3, 1, struct.pack("<H", effective_bits_per_sample)),
        (259, 3, 1, struct.pack("<H", compression_value)),
        (271, 2, len(make_payload), make_payload),
        (272, 2, len(model_payload), model_payload),
        (274, 3, 1, struct.pack("<H", orientation)),
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
    elif compressed_sensor_payload is not None:
        pixel_bytes = compressed_sensor_payload
        ifd0_defs.extend(
            [
                (262, 3, 1, struct.pack("<H", 32803)),
                (278, 4, 1, struct.pack("<I", height)),
                (279, 4, 1, struct.pack("<I", len(pixel_bytes))),
                (33421, 3, 2, struct.pack("<HH", 2, 2)),
                (33422, 1, 4, bytes([0, 1, 1, 2])),
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

    ifd0_count = len(ifd0_defs) + 1 + (2 if embedded_jpeg is not None else 0) + (1 if has_sensor_payload else 0)
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
    if has_sensor_payload:
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


def synthetic_nikon_nef_compressed_bytes(
    width: int = 4,
    height: int = 4,
    bits_per_sample: int = 14,
    samples: tuple[int, ...] | None = None,
    active_area: tuple[int, int, int, int] = (16, 8, 5568, 3712),
    white_balance_rb_levels: tuple[float, float, float, float] | None = None,
    alternate_white_balance_rb_levels: tuple[float, float, float, float] | None = None,
    maker_black_levels: tuple[int, int, int, int] | None = None,
    make: str = "NIKON CORPORATION",
    model: str = "NIKON Z 6II",
    orientation: int = 1,
) -> bytes:
    if bits_per_sample not in {12, 14}:
        raise ValueError("compressed synthetic Nikon NEF supports only 12-bit or 14-bit samples")
    if samples is None:
        white = (1 << bits_per_sample) - 1
        samples = tuple(256 + int((white - 256) * index / max(1, width * height - 1)) for index in range(width * height))
    if len(samples) != width * height:
        raise ValueError("samples must match width * height")
    payload = pack_nikon_34713_lossless(samples, width=width, height=height, bits_per_sample=bits_per_sample)
    return synthetic_nikon_nef_metadata_bytes(
        width=width,
        height=height,
        bits_per_sample=bits_per_sample,
        compressed_sensor_payload=payload,
        maker_note=nikon_makernote_bytes(
            bits_per_sample=bits_per_sample,
            active_area=active_area,
            white_balance_rb_levels=white_balance_rb_levels,
            alternate_white_balance_rb_levels=alternate_white_balance_rb_levels,
            black_levels=maker_black_levels,
        ),
        make=make,
        model=model,
        orientation=orientation,
    )


def embedded_jpeg_bytes(width: int = 3, height: int = 2) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (width, height), (42, 84, 126)).save(buffer, format="JPEG")
    return buffer.getvalue()


def nikon_makernote_bytes(
    bits_per_sample: int = 14,
    active_area: tuple[int, int, int, int] = (16, 8, 5568, 3712),
    white_balance_rb_levels: tuple[float, float, float, float] | None = None,
    alternate_white_balance_rb_levels: tuple[float, float, float, float] | None = None,
    white_balance_mode: str = "AUTO1",
    black_levels: tuple[int, int, int, int] | None = None,
) -> bytes:
    predictor = 2048 if bits_per_sample == 14 else 512
    compression_payload = (
        b"F0"
        + struct.pack("<H", predictor)
        + struct.pack("<H", predictor)
        + struct.pack("<H", predictor)
        + struct.pack("<H", predictor)
        + struct.pack("<H", 34)
    )
    entries = [
        (0x0001, 7, 4, b"0211"),
        (0x001B, 3, 7, struct.pack("<7H", 12, 5600, 3728, 5600, 3728, 0, 0)),
        (0x0045, 3, 4, struct.pack("<4H", *active_area)),
        (0x008C, 7, 8, b"I0\x00\xff\x00\xff\x01\x00"),
        (0x0093, 3, 1, struct.pack("<H", 3)),
        (0x0096, 7, len(compression_payload), compression_payload),
    ]
    if white_balance_rb_levels is not None or alternate_white_balance_rb_levels is not None:
        mode_payload = white_balance_mode.encode("ascii") + b"\x00"
        entries.append((0x0005, 2, len(mode_payload), mode_payload))
    if white_balance_rb_levels is not None:
        entries.append((0x000C, 5, 4, _pack_rational_values(white_balance_rb_levels)))
    if alternate_white_balance_rb_levels is not None:
        entries.append((0x003B, 5, 4, _pack_rational_values(alternate_white_balance_rb_levels)))
    if black_levels is not None:
        entries.append((0x003D, 3, 4, struct.pack("<4H", *black_levels)))
    return _tiff_makernote_bytes(entries)


def _pack_rational(numerator: int, denominator: int) -> bytes:
    return struct.pack("<II", numerator, denominator)


def _pack_rational_values(values: tuple[float, ...]) -> bytes:
    denominator = 4096
    return b"".join(_pack_rational(round(value * denominator), denominator) for value in values)


def _tiff_makernote_bytes(
    entries: list[tuple[int, int, int, bytes]], *, byte_order: str = "little",
) -> bytes:
    endian = {"little": "<", "big": ">"}[byte_order]
    entry_count = len(entries)
    header = b"Nikon\x00\x02\x11\x00\x00" + (b"II" if byte_order == "little" else b"MM")
    header += struct.pack(endian + "HI", 42, 8)
    ifd_size = 2 + entry_count * 12 + 4
    external_base = len(header) + ifd_size - 10
    external_data = bytearray()

    def encode(tag: int, field_type: int, count: int, payload: bytes) -> bytes:
        type_size = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 8: 2, 9: 4, 10: 8}[field_type]
        byte_count = type_size * count
        if byte_count <= 4:
            return struct.pack(endian + "HHI", tag, field_type, count) + payload.ljust(4, b"\x00")
        offset = external_base + len(external_data)
        external_data.extend(payload)
        if len(external_data) % 2:
            external_data.extend(b"\x00")
        return struct.pack(endian + "HHII", tag, field_type, count, offset)

    ifd = struct.pack(endian + "H", entry_count) + b"".join(encode(*entry) for entry in entries) + struct.pack(endian + "I", 0)
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


def pack_nikon_34713_lossless(
    values: tuple[int, ...],
    *,
    width: int,
    height: int,
    bits_per_sample: int,
) -> bytes:
    if len(values) != width * height:
        raise ValueError("values must match width * height")
    table = _nikon_huffman_codes(bits_per_sample)
    predictor = 2048 if bits_per_sample == 14 else 512
    row_predictors = [[predictor, predictor], [predictor, predictor]]
    writer = _BitWriter()
    index = 0
    for row in range(height):
        predictors = row_predictors[row & 1].copy()
        for column in range(width):
            channel = column & 1
            value = values[index]
            diff = value - predictors[channel]
            predictors[channel] = value
            if column < 2:
                row_predictors[row & 1][channel] = value
            category, payload = _encode_difference(diff)
            code, code_length = table[category]
            writer.write(code, code_length)
            if category:
                writer.write(payload, category)
            index += 1
    return writer.finish()


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


def _nikon_huffman_codes(bits_per_sample: int) -> dict[int, tuple[int, int]]:
    if bits_per_sample == 14:
        counts = (0, 1, 4, 2, 2, 3, 1, 2, 0, 0, 0, 0, 0, 0, 0, 0)
        values = (7, 6, 8, 5, 9, 4, 10, 3, 11, 12, 2, 0, 1, 13, 14)
    else:
        counts = (0, 1, 4, 2, 3, 1, 2, 0, 0, 0, 0, 0, 0, 0, 0, 0)
        values = (5, 4, 6, 3, 7, 2, 8, 1, 9, 0, 10, 11, 12)
    codes = {}
    code = 0
    value_index = 0
    for code_length, count in enumerate(counts, start=1):
        for _ in range(count):
            codes[values[value_index]] = (code, code_length)
            value_index += 1
            code += 1
        code <<= 1
    return codes


def _encode_difference(diff: int) -> tuple[int, int]:
    if diff == 0:
        return 0, 0
    magnitude = abs(diff)
    category = magnitude.bit_length()
    if diff > 0:
        return category, diff
    return category, diff + ((1 << category) - 1)


class _BitWriter:
    def __init__(self) -> None:
        self._output = bytearray()
        self._buffer = 0
        self._bits = 0

    def write(self, value: int, bit_count: int) -> None:
        for bit_index in range(bit_count - 1, -1, -1):
            self._buffer = (self._buffer << 1) | ((value >> bit_index) & 1)
            self._bits += 1
            if self._bits == 8:
                self._output.append(self._buffer)
                self._buffer = 0
                self._bits = 0

    def finish(self) -> bytes:
        if self._bits:
            self._output.append((self._buffer << (8 - self._bits)) & 0xFF)
            self._buffer = 0
            self._bits = 0
        return bytes(self._output)
