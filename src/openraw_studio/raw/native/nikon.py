"""Nikon MakerNote helpers for OpenRAW Native."""

from __future__ import annotations

from array import array
from dataclasses import dataclass
from pathlib import Path
import struct
from typing import Any

from openraw_studio.raw.native.dng import DngMetadata, DngMetadataError, DngMetadataReader, TiffIfd


MAKER_NOTE_TAG = 37500
NIKON_MAKER_PREFIX = b"Nikon\x00"
NIKON_MAKER_TIFF_OFFSET = 10
NIKON_COMPRESSED_RAW = 34713

_NIKON_HUFFMAN_TABLES = {
    2: (
        (0, 1, 4, 2, 3, 1, 2, 0, 0, 0, 0, 0, 0, 0, 0, 0),
        (5, 4, 6, 3, 7, 2, 8, 1, 9, 0, 10, 11, 12),
    ),
    5: (
        (0, 1, 4, 2, 2, 3, 1, 2, 0, 0, 0, 0, 0, 0, 0, 0),
        (7, 6, 8, 5, 9, 4, 10, 3, 11, 12, 2, 0, 1, 13, 14),
    ),
}


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


@dataclass(frozen=True)
class NikonCompressionSetup:
    """Decoded Nikon compression metadata required by the 34713 path."""

    version: str
    huffman_select: int
    initial_predictors: tuple[tuple[int, int], tuple[int, int]]
    active_area: tuple[int, ...] | None = None
    compression_mode: int | None = None


@dataclass(frozen=True)
class NikonDecodedPixelData:
    """Decoded Nikon sensor payload ready for the generic native pipeline."""

    width: int
    height: int
    source_bits_per_sample: int
    output_bits_per_sample: int
    samples_per_pixel: int
    byte_order: str
    raw_bytes: bytes
    storage_layout: str
    strip_offsets: tuple[int, ...]
    strip_byte_counts: tuple[int, ...]
    rows_per_strip: int | None
    black_level: int
    white_level: int
    cfa_pattern: tuple[int, ...] | None
    compression: int
    compression_setup: NikonCompressionSetup


class NikonCompressionError(ValueError):
    """Raised when a Nikon compressed RAW payload is not supported yet."""


def summarize_nikon_makernote(metadata: DngMetadata) -> NikonMakerNoteSummary | None:
    """Parse a Nikon Type 2 MakerNote summary when one is present."""

    value = _first_tag_value(metadata.ifds, MAKER_NOTE_TAG)
    if value is None:
        return None
    payload = _undefined_bytes(value)
    if payload is None:
        return None
    return summarize_nikon_makernote_payload(payload)


def can_decode_nikon_34713_lossless(metadata: DngMetadata) -> bool:
    try:
        _nikon_compression_setup(metadata, _required_bits_per_sample(_nikon_pixel_ifd(metadata)))
    except NikonCompressionError:
        return False
    return True


def decode_nikon_34713_lossless(path: str | Path, metadata: DngMetadata | None = None) -> NikonDecodedPixelData:
    """Decode Nikon 34713 lossless Huffman Bayer data to 16-bit little-endian samples."""

    source_path = Path(path)
    data = source_path.read_bytes()
    source_metadata = metadata or DngMetadataReader().read(source_path)
    pixel_ifd = _nikon_pixel_ifd(source_metadata)

    compression = _required_int(pixel_ifd, 259, "Compression")
    if compression != NIKON_COMPRESSED_RAW:
        raise NikonCompressionError(f"unsupported Nikon compression: {compression}")

    bits_per_sample = _required_bits_per_sample(pixel_ifd)
    if bits_per_sample not in {12, 14}:
        raise NikonCompressionError(f"unsupported Nikon compressed bit depth: {bits_per_sample}")

    samples_per_pixel = _optional_int(pixel_ifd, 277, default=1)
    if samples_per_pixel != 1:
        raise NikonCompressionError(f"unsupported Nikon SamplesPerPixel: {samples_per_pixel}")

    width = _required_int(pixel_ifd, 256, "ImageWidth")
    height = _required_int(pixel_ifd, 257, "ImageLength")
    strip_offsets = _required_int_tuple(pixel_ifd, 273, "StripOffsets")
    strip_byte_counts = _required_int_tuple(pixel_ifd, 279, "StripByteCounts")
    if not strip_offsets or not strip_byte_counts:
        raise NikonCompressionError("Nikon compressed payload strip tags are empty")
    if len(strip_offsets) != len(strip_byte_counts):
        raise NikonCompressionError("Nikon compressed StripOffsets and StripByteCounts have different lengths")

    setup = _nikon_compression_setup(source_metadata, bits_per_sample)
    payload = b"".join(_slice_checked(data, offset, count) for offset, count in zip(strip_offsets, strip_byte_counts))
    samples = _decode_nikon_lossless_samples(
        payload,
        width=width,
        height=height,
        setup=setup,
    )

    output = array("H", samples)
    if struct.pack("=H", 1) != b"\x01\x00":
        output.byteswap()
    return NikonDecodedPixelData(
        width=width,
        height=height,
        source_bits_per_sample=bits_per_sample,
        output_bits_per_sample=16,
        samples_per_pixel=samples_per_pixel,
        byte_order="little",
        raw_bytes=output.tobytes(),
        storage_layout="nikon-34713-lossless-strips",
        strip_offsets=strip_offsets,
        strip_byte_counts=strip_byte_counts,
        rows_per_strip=_optional_int(pixel_ifd, 278),
        black_level=0,
        white_level=(1 << bits_per_sample) - 1,
        cfa_pattern=_optional_int_tuple(pixel_ifd, 33422),
        compression=compression,
        compression_setup=setup,
    )


def render_nikon_34713_to_file(
    path: str | Path,
    output_path: str | Path,
    *,
    metadata: DngMetadata | None = None,
    max_dimension: int | None = None,
    exposure: float = 0.0,
    contrast: float = 0.0,
    warmth: float = 0.0,
    jpeg_quality: int = 92,
) -> tuple[int, int]:
    """Render a supported Nikon 34713 RAW file directly to PNG or JPEG."""

    decoded = decode_nikon_34713_lossless(path, metadata)
    return render_decoded_nikon_34713_to_file(
        decoded,
        output_path,
        max_dimension=max_dimension,
        exposure=exposure,
        contrast=contrast,
        warmth=warmth,
        jpeg_quality=jpeg_quality,
    )


def render_decoded_nikon_34713_to_file(
    decoded: NikonDecodedPixelData,
    output_path: str | Path,
    *,
    max_dimension: int | None = None,
    exposure: float = 0.0,
    contrast: float = 0.0,
    warmth: float = 0.0,
    jpeg_quality: int = 92,
) -> tuple[int, int]:
    """Render an already decoded Nikon 34713 sensor payload to PNG or JPEG."""

    destination = Path(output_path)
    samples = array("H")
    samples.frombytes(decoded.raw_bytes)
    if struct.pack("=H", 1) != b"\x01\x00":
        samples.byteswap()

    crop = _render_crop(decoded)
    width, height, rgb = _bayer_blocks_to_rgb8(
        samples,
        source_width=decoded.width,
        source_height=decoded.height,
        crop=crop,
        cfa_pattern=decoded.cfa_pattern,
        black_level=decoded.black_level,
        white_level=decoded.white_level,
        exposure=exposure,
        contrast=contrast,
        warmth=warmth,
    )
    try:
        from PIL import Image
    except ImportError as exc:
        raise NikonCompressionError("Pillow is required for Nikon 34713 rendering") from exc

    image = Image.frombytes("RGB", (width, height), bytes(rgb))
    if max_dimension is not None:
        image = _resize_pillow_image(image, max_dimension=max_dimension)

    destination.parent.mkdir(parents=True, exist_ok=True)
    suffix = destination.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        image.save(destination, format="JPEG", quality=jpeg_quality, optimize=False, progressive=False)
    elif suffix == ".png":
        image.save(destination, format="PNG")
    else:
        raise NikonCompressionError("Nikon 34713 render output must be .png, .jpg, or .jpeg")
    return image.size


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


def _nikon_compression_setup(metadata: DngMetadata, bits_per_sample: int) -> NikonCompressionSetup:
    maker_ifd = _nikon_makernote_ifd(metadata)
    if maker_ifd is None:
        raise NikonCompressionError("missing Nikon MakerNote")
    compression_payload = _tag_bytes(maker_ifd, 0x0096)
    if compression_payload is None:
        raise NikonCompressionError("missing Nikon NEF linearization/compression table tag 0x0096")
    byte_order = _makernote_byte_order(metadata)
    if len(compression_payload) < 12:
        raise NikonCompressionError("Nikon compression table is too short")

    v0 = compression_payload[0]
    v1 = compression_payload[1]
    if v0 != 0x46:
        raise NikonCompressionError(f"unsupported Nikon compression table version: 0x{v0:02x} 0x{v1:02x}")

    huffman_select = 2
    if bits_per_sample == 14:
        huffman_select += 3
    if huffman_select not in _NIKON_HUFFMAN_TABLES:
        raise NikonCompressionError(f"unsupported Nikon Huffman table: {huffman_select}")

    unpack_u16 = _u16_unpacker(byte_order)
    initial_predictors = (
        (unpack_u16(compression_payload[2:4]), unpack_u16(compression_payload[6:8])),
        (unpack_u16(compression_payload[4:6]), unpack_u16(compression_payload[8:10])),
    )
    return NikonCompressionSetup(
        version=bytes(compression_payload[:2]).decode("ascii", errors="replace"),
        huffman_select=huffman_select,
        initial_predictors=initial_predictors,
        active_area=_tag_int_tuple(maker_ifd, 0x0045),
        compression_mode=_tag_int(maker_ifd, 0x0093),
    )


def _decode_nikon_lossless_samples(payload: bytes, *, width: int, height: int, setup: NikonCompressionSetup) -> array:
    table = _build_huffman_lookup(setup.huffman_select)
    bits = _MsbBitReader(payload)
    maximum = 0xFFFF
    try:
        output = array("H", [0]) * (width * height)
    except MemoryError as exc:
        raise NikonCompressionError("not enough memory to hold the decoded Nikon sensor payload") from exc

    row_predictors = [list(setup.initial_predictors[0]), list(setup.initial_predictors[1])]
    index = 0
    for row in range(height):
        predictors = row_predictors[row & 1].copy()
        for column in range(width):
            diff = _decode_huffman_difference(bits, table)
            channel = column & 1
            predictors[channel] += diff
            if column < 2:
                row_predictors[row & 1][channel] = predictors[channel]
            output[index] = _clamp_int(predictors[channel], 0, maximum)
            index += 1
    return output


def _render_crop(decoded: NikonDecodedPixelData) -> tuple[int, int, int, int]:
    active = decoded.compression_setup.active_area
    if active is not None and len(active) >= 4:
        left, top, width, height = active[:4]
        if left >= 0 and top >= 0 and width > 1 and height > 1 and left < decoded.width and top < decoded.height:
            width = min(width, decoded.width - left)
            height = min(height, decoded.height - top)
            if left % 2:
                left += 1
                width -= 1
            if top % 2:
                top += 1
                height -= 1
            width -= width % 2
            height -= height % 2
            if width > 1 and height > 1:
                return left, top, width, height
    return 0, 0, decoded.width - (decoded.width % 2), decoded.height - (decoded.height % 2)


def _bayer_blocks_to_rgb8(
    samples: array,
    *,
    source_width: int,
    source_height: int,
    crop: tuple[int, int, int, int],
    cfa_pattern: tuple[int, ...] | None,
    black_level: int,
    white_level: int,
    exposure: float,
    contrast: float,
    warmth: float,
) -> tuple[int, int, bytearray]:
    del source_height
    left, top, crop_width, crop_height = crop
    out_width = crop_width // 2
    out_height = crop_height // 2
    if out_width <= 0 or out_height <= 0:
        raise NikonCompressionError("Nikon 34713 render crop is empty")

    pattern = _cfa_2x2(cfa_pattern)
    output = bytearray(out_width * out_height * 3)
    exposure_scale = 2.0 ** _clamp_float(exposure, -4.0, 4.0)
    contrast_factor = 1.0 + _clamp_float(contrast, -1.0, 1.0) * 0.75
    warmth_value = _clamp_float(warmth, -1.0, 1.0)
    span = max(1, white_level - black_level)
    out_index = 0

    for row in range(out_height):
        source_row = top + row * 2
        row0 = source_row * source_width + left
        row1 = (source_row + 1) * source_width + left
        for column in range(out_width):
            source_column = column * 2
            block_values = (
                samples[row0 + source_column],
                samples[row0 + source_column + 1],
                samples[row1 + source_column],
                samples[row1 + source_column + 1],
            )
            red, green, blue = _block_rgb_values(block_values, pattern)
            red = _normalize_raw(red, black_level=black_level, span=span)
            green = _normalize_raw(green, black_level=black_level, span=span)
            blue = _normalize_raw(blue, black_level=black_level, span=span)
            red, green, blue = _apply_warmth(red, green, blue, warmth=warmth_value)
            output[out_index] = _encode_channel(_apply_contrast(red * exposure_scale, contrast_factor))
            output[out_index + 1] = _encode_channel(_apply_contrast(green * exposure_scale, contrast_factor))
            output[out_index + 2] = _encode_channel(_apply_contrast(blue * exposure_scale, contrast_factor))
            out_index += 3

    return out_width, out_height, output


def _cfa_2x2(cfa_pattern: tuple[int, ...] | None) -> tuple[str, str, str, str]:
    mapping = {0: "R", 1: "G", 2: "B"}
    if cfa_pattern is None or len(cfa_pattern) < 4:
        return "R", "G", "G", "B"
    channels = tuple(mapping.get(value, "?") for value in cfa_pattern[:4])
    if sorted(channels) != ["B", "G", "G", "R"]:
        return "R", "G", "G", "B"
    return channels  # type: ignore[return-value]


def _block_rgb_values(block_values: tuple[int, int, int, int], pattern: tuple[str, str, str, str]) -> tuple[int, int, int]:
    red_values = []
    green_values = []
    blue_values = []
    for value, channel in zip(block_values, pattern):
        if channel == "R":
            red_values.append(value)
        elif channel == "G":
            green_values.append(value)
        elif channel == "B":
            blue_values.append(value)
    red = red_values[0] if red_values else block_values[0]
    green = sum(green_values) // len(green_values) if green_values else block_values[1]
    blue = blue_values[0] if blue_values else block_values[3]
    return red, green, blue


def _resize_pillow_image(image: Any, *, max_dimension: int) -> Any:
    if max_dimension <= 0:
        raise NikonCompressionError("max_dimension must be greater than zero")
    longest = max(image.size)
    if longest <= max_dimension:
        return image
    scale = max_dimension / float(longest)
    size = (max(1, round(image.size[0] * scale)), max(1, round(image.size[1] * scale)))
    return image.resize(size, resample=1)


def _normalize_raw(value: int, *, black_level: int, span: int) -> float:
    return _clamp_float((value - black_level) / float(span), 0.0, 1.0)


def _apply_warmth(red: float, green: float, blue: float, *, warmth: float) -> tuple[float, float, float]:
    return red * (1.0 + warmth * 0.12), green * (1.0 + warmth * 0.03), blue * (1.0 - warmth * 0.12)


def _apply_contrast(value: float, factor: float) -> float:
    return ((value - 0.18) * factor) + 0.18


def _encode_channel(value: float) -> int:
    return int(round((_clamp_float(value, 0.0, 1.0) ** (1.0 / 2.2)) * 255.0))


def _clamp_float(value: float, minimum: float, maximum: float) -> float:
    if value < minimum:
        return minimum
    if value > maximum:
        return maximum
    return value


def _decode_huffman_difference(bits: "_MsbBitReader", table: tuple[tuple[int, int], ...]) -> int:
    prefix = bits.peek(8)
    category, code_length = table[prefix]
    if code_length <= 0:
        raise NikonCompressionError("invalid Nikon Huffman prefix")
    bits.skip(code_length)
    if category == 0:
        return 0
    if category == 16:
        return -32768
    value = bits.read(category)
    if value < (1 << (category - 1)):
        return value - ((1 << category) - 1)
    return value


def _build_huffman_lookup(huffman_select: int) -> tuple[tuple[int, int], ...]:
    counts, values = _NIKON_HUFFMAN_TABLES[huffman_select]
    lookup: list[tuple[int, int]] = [(-1, 0)] * 256
    code = 0
    value_index = 0
    for code_length, count in enumerate(counts, start=1):
        for _ in range(count):
            value = values[value_index]
            value_index += 1
            if code_length <= 8:
                prefix = code << (8 - code_length)
                fill = 1 << (8 - code_length)
                for table_index in range(prefix, prefix + fill):
                    lookup[table_index] = (value, code_length)
            code += 1
        code <<= 1
    return tuple(lookup)


class _MsbBitReader:
    __slots__ = ("_data", "_byte_pos", "_buffer", "_bits")

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._byte_pos = 0
        self._buffer = 0
        self._bits = 0

    def peek(self, count: int) -> int:
        self._ensure(count)
        return (self._buffer >> (self._bits - count)) & ((1 << count) - 1)

    def read(self, count: int) -> int:
        value = self.peek(count)
        self.skip(count)
        return value

    def skip(self, count: int) -> None:
        if count < 0:
            raise NikonCompressionError("cannot skip a negative number of bits")
        self._ensure(count)
        self._bits -= count
        if self._bits:
            self._buffer &= (1 << self._bits) - 1
        else:
            self._buffer = 0

    def _ensure(self, count: int) -> None:
        while self._bits < count:
            if self._byte_pos >= len(self._data):
                raise NikonCompressionError("Nikon compressed bitstream ended early")
            self._buffer = (self._buffer << 8) | self._data[self._byte_pos]
            self._byte_pos += 1
            self._bits += 8


def _nikon_pixel_ifd(metadata: DngMetadata) -> TiffIfd:
    candidates = [
        ifd
        for ifd in metadata.ifds
        if 273 in ifd.tags and 279 in ifd.tags and _optional_int(ifd, 259, default=1) == NIKON_COMPRESSED_RAW
    ]
    if not candidates:
        raise NikonCompressionError("no Nikon 34713 compressed pixel IFD found")
    return max(candidates, key=lambda ifd: (_optional_int(ifd, 256, default=0) or 0) * (_optional_int(ifd, 257, default=0) or 0))


def _nikon_makernote_ifd(metadata: DngMetadata) -> TiffIfd | None:
    value = _first_tag_value(metadata.ifds, MAKER_NOTE_TAG)
    if value is None:
        return None
    payload = _undefined_bytes(value)
    if payload is None:
        return None
    tiff_offset, _kind = _makernote_tiff_offset(payload)
    if tiff_offset is None:
        return None
    try:
        _byte_order, _endian, ifds = DngMetadataReader()._read_structure(payload[tiff_offset:])
    except DngMetadataError:
        return None
    return ifds[0] if ifds else None


def _makernote_byte_order(metadata: DngMetadata) -> str:
    value = _first_tag_value(metadata.ifds, MAKER_NOTE_TAG)
    payload = _undefined_bytes(value)
    if payload is None:
        return metadata.byte_order
    tiff_offset, _kind = _makernote_tiff_offset(payload)
    if tiff_offset is None:
        return metadata.byte_order
    try:
        byte_order, _endian, _ifds = DngMetadataReader()._read_structure(payload[tiff_offset:])
    except DngMetadataError:
        return metadata.byte_order
    return byte_order


def _required_bits_per_sample(ifd: TiffIfd) -> int:
    value = ifd.tags.get(258).value if 258 in ifd.tags else None
    if isinstance(value, tuple):
        if len(value) != 1:
            raise NikonCompressionError("Nikon compressed BitsPerSample must be scalar")
        value = value[0]
    if value is None:
        raise NikonCompressionError("missing Nikon compressed BitsPerSample")
    return int(value)


def _required_int(ifd: TiffIfd, tag_code: int, label: str) -> int:
    value = _optional_int(ifd, tag_code)
    if value is None:
        raise NikonCompressionError(f"missing Nikon compressed tag: {label}")
    return value


def _optional_int(ifd: TiffIfd, tag_code: int, default: int | None = None) -> int | None:
    tag = ifd.tags.get(tag_code)
    if tag is None:
        return default
    value = tag.value
    if isinstance(value, tuple):
        if len(value) != 1:
            return default
        value = value[0]
    if value is None or isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _required_int_tuple(ifd: TiffIfd, tag_code: int, label: str) -> tuple[int, ...]:
    value = _optional_int_tuple(ifd, tag_code)
    if value is None:
        raise NikonCompressionError(f"missing Nikon compressed tag: {label}")
    return value


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


def _optional_int_tuple(ifd: TiffIfd, tag_code: int) -> tuple[int, ...] | None:
    tag = ifd.tags.get(tag_code)
    if tag is None:
        return None
    return _value_int_tuple(tag.value)


def _value_int_tuple(value: Any) -> tuple[int, ...] | None:
    if value is None:
        return None
    values = value if isinstance(value, tuple) else (value,)
    try:
        return tuple(int(item) for item in values)
    except (TypeError, ValueError):
        return None


def _u16_unpacker(byte_order: str) -> Any:
    endian = "<" if byte_order == "little" else ">" if byte_order == "big" else None
    if endian is None:
        raise NikonCompressionError(f"unsupported Nikon MakerNote byte order: {byte_order}")

    def unpack(payload: bytes) -> int:
        if len(payload) != 2:
            raise NikonCompressionError("Nikon compression metadata needs 16-bit values")
        return struct.unpack(endian + "H", payload)[0]

    return unpack


def _slice_checked(data: bytes, offset: int, length: int) -> bytes:
    if offset < 0 or length < 0 or offset + length > len(data):
        raise NikonCompressionError("Nikon compressed strip points outside the file")
    return data[offset : offset + length]


def _clamp_int(value: int, minimum: int, maximum: int) -> int:
    if value < minimum:
        return minimum
    if value > maximum:
        return maximum
    return value


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
