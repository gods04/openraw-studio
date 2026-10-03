"""Nikon MakerNote helpers for OpenRAW Native."""

from __future__ import annotations

from array import array
from dataclasses import dataclass
import math
from pathlib import Path
import struct
from typing import Any

from openraw_studio.core.files import atomic_output_path
from openraw_studio.raw.native.dng import DngMetadata, DngMetadataError, DngMetadataReader, TiffIfd
from openraw_studio.raw.native.fullres import render_bayer_full_resolution_rgb8
from openraw_studio.raw.native.profiles import CameraColorProfile, Matrix3, find_camera_color_profile


MAKER_NOTE_TAG = 37500
NIKON_MAKER_PREFIX = b"Nikon\x00"
NIKON_MAKER_TIFF_OFFSET = 10
NIKON_COMPRESSED_RAW = 34713
NIKON_COMPRESSION_NAMES = {
    1: "Lossy (type 1)",
    2: "Uncompressed",
    3: "Lossless",
    4: "Lossy (type 2)",
    5: "Striped packed 12-bit",
    6: "Uncompressed reduced 12-bit",
    7: "Unpacked 12-bit",
    8: "Small RAW",
    9: "Packed 12-bit",
    10: "Packed 14-bit",
    13: "High Efficiency",
    14: "High Efficiency*",
}

_NIKON_HUFFMAN_TABLES = {
    0: (
        (0, 1, 5, 1, 1, 1, 1, 1, 1, 2, 0, 0, 0, 0, 0, 0),
        (5, 4, 3, 6, 2, 7, 1, 0, 8, 9, 11, 10, 12, 0),
    ),
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
    compression_source: str | None = None
    compression_name: str | None = None
    curve_byte_count: int | None = None
    curve_prefix: str | None = None
    compression_table_byte_count: int | None = None
    compression_table_prefix: str | None = None
    white_balance_mode: str | None = None
    as_shot_white_balance: tuple[float, float, float] | None = None
    white_balance_source: str | None = None
    black_levels: tuple[int, ...] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "byte_order": self.byte_order,
            "tag_count": self.tag_count,
            "version": self.version,
            "active_area": self.active_area,
            "crop_info": self.crop_info,
            "compression_mode": self.compression_mode,
            "compression_source": self.compression_source,
            "compression_name": self.compression_name,
            "curve_byte_count": self.curve_byte_count,
            "curve_prefix": self.curve_prefix,
            "compression_table_byte_count": self.compression_table_byte_count,
            "compression_table_prefix": self.compression_table_prefix,
            "white_balance_mode": self.white_balance_mode,
            "as_shot_white_balance": self.as_shot_white_balance,
            "white_balance_source": self.white_balance_source,
            "black_levels": self.black_levels,
        }


@dataclass(frozen=True)
class NikonCompressionSetup:
    """Decoded Nikon compression metadata required by the 34713 path."""

    version: str
    huffman_select: int
    initial_predictors: tuple[tuple[int, int], tuple[int, int]]
    active_area: tuple[int, ...] | None = None
    compression_mode: int | None = None
    linearization: tuple[int, ...] | None = None


@dataclass(frozen=True)
class NikonWhiteBalance:
    """Validated as-shot channel gains extracted from a Nikon MakerNote."""

    red_gain: float
    green_gain: float
    blue_gain: float
    source_tag: str
    mode: str | None = None

    @property
    def gains(self) -> tuple[float, float, float]:
        return self.red_gain, self.green_gain, self.blue_gain


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
    black_levels: tuple[int, int, int, int]
    white_level: int
    cfa_pattern: tuple[int, ...] | None
    compression: int
    compression_setup: NikonCompressionSetup
    white_balance: NikonWhiteBalance | None = None
    camera_profile: CameraColorProfile | None = None
    camera_make: str | None = None
    camera_model: str | None = None
    orientation: int = 1


@dataclass(frozen=True)
class NikonRenderedRgbImage:
    """Packed 8-bit RGB render produced by the Nikon 34713 path."""

    width: int
    height: int
    rgb_bytes: bytes
    transfer: str = "gamma-2.2"


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


def can_decode_nikon_34713_lossless(metadata: DngMetadata, source_path: str | Path | None = None) -> bool:
    maker = summarize_nikon_makernote(metadata)
    if maker is not None and maker.compression_mode in {13, 14}:
        if source_path is None:
            return False
        from .nikon_he import read_zf_he_payload

        try:
            read_zf_he_payload(source_path, metadata)
        except (NikonCompressionError, OSError, ValueError):
            return False
        return True
    try:
        _nikon_compression_setup(metadata, _required_bits_per_sample(_nikon_pixel_ifd(metadata)))
    except NikonCompressionError:
        return False
    return True


def extract_nikon_as_shot_white_balance(metadata: DngMetadata) -> NikonWhiteBalance | None:
    """Return conservative Nikon as-shot gains from MakerNote tag 0x000c.

    Tag 0x003b contains a separate multi-exposure white-balance record and is
    deliberately not used as a normal camera multiplier. Unity, malformed,
    non-finite, and implausible values are treated as unavailable.
    """

    maker_ifd = _nikon_makernote_ifd(metadata)
    if maker_ifd is None:
        return None
    return _white_balance_from_makernote_ifd(maker_ifd)


def decode_nikon_34713_lossless(path: str | Path, metadata: DngMetadata | None = None) -> NikonDecodedPixelData:
    """Decode supported Nikon data; the legacy API name is retained.

    F-series lossless and 12-bit D20 non-split lossy streams are supported.
    D20 samples are linearized before any black-level or color processing.
    """

    source_path = Path(path)
    source_metadata = metadata or DngMetadataReader().read(source_path)
    maker = summarize_nikon_makernote(source_metadata)
    if maker is not None and maker.compression_mode in {13, 14}:
        from .nikon_he import decode_zf_he

        return decode_zf_he(source_path, source_metadata)
    data = source_path.read_bytes()
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
        maximum=(1 << bits_per_sample) - 1,
    )
    white_level = (1 << bits_per_sample) - 1
    if setup.linearization is not None:
        import numpy as np

        curve = np.asarray(setup.linearization, dtype=np.uint16)
        indices = np.frombuffer(samples, dtype=np.uint16)
        samples = array("H")
        samples.frombytes(curve[indices].tobytes())
        white_level = int(curve[-1])
    black_levels = _nikon_black_levels(
        source_metadata,
        samples,
        width=width,
        height=height,
        active_area=setup.active_area,
        white_level=white_level,
    )
    black_level = min(black_levels)
    white_balance = extract_nikon_as_shot_white_balance(source_metadata)
    make = _optional_text(source_metadata.summary.get("make"))
    model = _optional_text(source_metadata.summary.get("model"))
    camera_profile = find_camera_color_profile(make, model)

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
        storage_layout="nikon-34713-lossy-strips"
        if setup.linearization
        else "nikon-34713-lossless-strips",
        strip_offsets=strip_offsets,
        strip_byte_counts=strip_byte_counts,
        rows_per_strip=_optional_int(pixel_ifd, 278),
        black_level=black_level,
        black_levels=black_levels,
        white_level=white_level,
        cfa_pattern=_optional_int_tuple(pixel_ifd, 33422),
        compression=compression,
        compression_setup=setup,
        white_balance=white_balance,
        camera_profile=camera_profile,
        camera_make=make,
        camera_model=model,
        orientation=_coerce_orientation(source_metadata.summary.get("orientation")),
    )


def render_nikon_34713_to_file(
    path: str | Path,
    output_path: str | Path,
    *,
    metadata: DngMetadata | None = None,
    max_dimension: int | None = None,
    exposure: float = 0.0,
    contrast: float = 0.0,
    highlights: float = 0.0,
    shadows: float = 0.0,
    warmth: float = 0.0,
    tint: float = 0.0,
    saturation: float = 0.0,
    jpeg_quality: int = 92,
    jpeg_exif: Any | None = None,
    tiffinfo: Any | None = None,
    quality: str = "fast",
) -> tuple[int, int]:
    """Render a supported Nikon 34713 RAW file directly to PNG, JPEG, or TIFF."""

    decoded = decode_nikon_34713_lossless(path, metadata)
    return render_decoded_nikon_34713_to_file(
        decoded,
        output_path,
        max_dimension=max_dimension,
        exposure=exposure,
        contrast=contrast,
        highlights=highlights,
        shadows=shadows,
        warmth=warmth,
        tint=tint,
        saturation=saturation,
        jpeg_quality=jpeg_quality,
        jpeg_exif=jpeg_exif,
        tiffinfo=tiffinfo,
        quality=quality,
    )


def render_decoded_nikon_34713_to_file(
    decoded: NikonDecodedPixelData,
    output_path: str | Path,
    *,
    max_dimension: int | None = None,
    exposure: float = 0.0,
    contrast: float = 0.0,
    highlights: float = 0.0,
    shadows: float = 0.0,
    warmth: float = 0.0,
    tint: float = 0.0,
    saturation: float = 0.0,
    jpeg_quality: int = 92,
    jpeg_exif: Any | None = None,
    tiffinfo: Any | None = None,
    quality: str = "fast",
) -> tuple[int, int]:
    """Render an already decoded Nikon 34713 sensor payload to PNG, JPEG, or TIFF."""

    destination = Path(output_path)
    rendered = render_decoded_nikon_34713_image(
        decoded,
        max_dimension=max_dimension,
        exposure=exposure,
        contrast=contrast,
        highlights=highlights,
        shadows=shadows,
        warmth=warmth,
        tint=tint,
        saturation=saturation,
        quality=quality,
    )
    try:
        from PIL import Image
    except ImportError as exc:
        raise NikonCompressionError("Pillow is required for Nikon 34713 rendering") from exc

    image = Image.frombytes("RGB", (rendered.width, rendered.height), rendered.rgb_bytes)

    suffix = destination.suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png", ".tif", ".tiff"}:
        raise NikonCompressionError("Nikon 34713 render output must be .png, .jpg, .jpeg, .tif, or .tiff")
    with atomic_output_path(destination) as temporary_path:
        if suffix in {".jpg", ".jpeg"}:
            options: dict[str, Any] = {
                "quality": jpeg_quality,
                "optimize": False,
                "progressive": False,
            }
            if jpeg_exif is not None:
                options["exif"] = jpeg_exif
            image.save(temporary_path, format="JPEG", **options)
        elif suffix == ".png":
            image.save(temporary_path, format="PNG")
        else:
            options = {"compression": "tiff_deflate"}
            if tiffinfo is not None:
                options["tiffinfo"] = tiffinfo
            image.save(temporary_path, format="TIFF", **options)
    return image.size


def render_decoded_nikon_34713_image(
    decoded: NikonDecodedPixelData,
    *,
    max_dimension: int | None = None,
    exposure: float = 0.0,
    contrast: float = 0.0,
    highlights: float = 0.0,
    shadows: float = 0.0,
    warmth: float = 0.0,
    tint: float = 0.0,
    saturation: float = 0.0,
    quality: str = "fast",
) -> NikonRenderedRgbImage:
    """Render an already decoded Nikon 34713 sensor payload into packed RGB bytes."""

    crop = _render_crop(decoded)
    camera_matrix = (
        decoded.camera_profile.camera_to_linear_srgb
        if decoded.camera_profile is not None
        else None
    )
    if quality == "full":
        base_gains = _camera_channel_scales(
            decoded.white_balance,
            warmth=warmth,
            tint=tint,
        )
        exposure_scale = 2.0 ** _clamp_float(exposure, -4.0, 4.0)
        full = render_bayer_full_resolution_rgb8(
            decoded.raw_bytes,
            source_width=decoded.width,
            source_height=decoded.height,
            crop=crop,
            cfa_pattern=decoded.cfa_pattern,
            black_levels=decoded.black_levels,
            white_level=decoded.white_level,
            channel_gains=tuple(gain * exposure_scale for gain in base_gains),  # type: ignore[arg-type]
            camera_to_linear_srgb=camera_matrix,
            highlight_ceiling=min(base_gains) * exposure_scale,
            contrast=contrast,
            highlights=highlights,
            shadows=shadows,
            saturation=saturation,
        )
        width, height, rgb = full.width, full.height, full.rgb_bytes
    elif quality == "fast":
        samples = array("H")
        samples.frombytes(decoded.raw_bytes)
        if struct.pack("=H", 1) != b"\x01\x00":
            samples.byteswap()
        width, height, rgb = _bayer_blocks_to_rgb8(
            samples,
            source_width=decoded.width,
            source_height=decoded.height,
            crop=crop,
            cfa_pattern=decoded.cfa_pattern,
            black_level=decoded.black_level,
            black_levels=decoded.black_levels,
            white_level=decoded.white_level,
            exposure=exposure,
            contrast=contrast,
            highlights=highlights,
            shadows=shadows,
            warmth=warmth,
            tint=tint,
            saturation=saturation,
            camera_white_balance=decoded.white_balance,
            camera_to_linear_srgb=camera_matrix,
        )
    else:
        raise NikonCompressionError("Nikon render quality must be 'fast' or 'full'")
    try:
        from PIL import Image
    except ImportError as exc:
        raise NikonCompressionError("Pillow is required for Nikon 34713 rendering") from exc

    image = Image.frombytes("RGB", (width, height), bytes(rgb))
    image = _apply_exif_orientation(image, decoded.orientation)
    if max_dimension is not None:
        image = _resize_pillow_image(image, max_dimension=max_dimension)
    return NikonRenderedRgbImage(width=image.size[0], height=image.size[1], rgb_bytes=image.tobytes())


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
    white_balance = _white_balance_from_makernote_ifd(ifd)
    compression_mode, compression_source = _compression_from_makernote(ifd, byte_order)
    return NikonMakerNoteSummary(
        kind=kind,
        byte_order=byte_order,
        tag_count=len(ifd.tags),
        version=_tag_ascii(ifd, 0x0001),
        crop_info=_tag_int_tuple(ifd, 0x001B),
        active_area=_tag_int_tuple(ifd, 0x0045),
        compression_mode=compression_mode,
        compression_source=compression_source,
        compression_name=NIKON_COMPRESSION_NAMES.get(compression_mode),
        curve_byte_count=len(curve_payload) if curve_payload is not None else None,
        curve_prefix=_ascii_prefix(curve_payload) if curve_payload is not None else None,
        compression_table_byte_count=len(compression_table_payload) if compression_table_payload is not None else None,
        compression_table_prefix=_ascii_prefix(compression_table_payload) if compression_table_payload is not None else None,
        white_balance_mode=white_balance.mode if white_balance is not None else _tag_ascii(ifd, 0x0005),
        as_shot_white_balance=white_balance.gains if white_balance is not None else None,
        white_balance_source=white_balance.source_tag if white_balance is not None else None,
        black_levels=_tag_int_tuple(ifd, 0x003D),
    )


def _compression_from_makernote(ifd: TiffIfd, byte_order: str) -> tuple[int | None, str | None]:
    # Newer cameras moved NEFCompression to a binary record at byte offset 10.
    payload = _tag_bytes(ifd, 0x0051)
    if payload is not None and len(payload) >= 12 and payload[:8].isdigit():
        return int.from_bytes(payload[10:12], byte_order), "0x0051+10"
    mode = _tag_int(ifd, 0x0093)
    return mode, "0x0093" if mode is not None else None


def _nikon_compression_setup(metadata: DngMetadata, bits_per_sample: int) -> NikonCompressionSetup:
    maker_ifd = _nikon_makernote_ifd(metadata)
    if maker_ifd is None:
        raise NikonCompressionError("missing Nikon MakerNote")
    byte_order = _makernote_byte_order(metadata)
    compression_mode, _source = _compression_from_makernote(maker_ifd, byte_order)
    if compression_mode in {13, 14}:
        raise NikonCompressionError(
            f"Nikon {NIKON_COMPRESSION_NAMES[compression_mode]} compression is not supported yet; "
            "the embedded JPEG is not decoded RAW sensor data"
        )
    compression_payload = _tag_bytes(maker_ifd, 0x0096)
    if compression_payload is None:
        raise NikonCompressionError("missing Nikon NEF linearization/compression table tag 0x0096")
    if len(compression_payload) < 12:
        raise NikonCompressionError("Nikon compression table is too short")

    v0 = compression_payload[0]
    v1 = compression_payload[1]
    is_d20 = (v0, v1) == (0x44, 0x20) and bits_per_sample == 12
    if v0 != 0x46 and not is_d20:
        raise NikonCompressionError(f"unsupported Nikon compression table version: 0x{v0:02x} 0x{v1:02x}")

    huffman_select = 0 if is_d20 else 2
    if bits_per_sample == 14:
        huffman_select += 3
    if huffman_select not in _NIKON_HUFFMAN_TABLES:
        raise NikonCompressionError(f"unsupported Nikon Huffman table: {huffman_select}")

    unpack_u16 = _u16_unpacker(byte_order)
    linearization = None
    if is_d20:
        # D20 stores evenly spaced linearization knots and an optional split row.
        if len(compression_payload) < 564:
            raise NikonCompressionError("Nikon D20 compression table is too short")
        if unpack_u16(compression_payload[562:564]) != 0:
            raise NikonCompressionError(
                "Nikon D20 split-row compression is not supported yet"
            )
        count = unpack_u16(compression_payload[10:12])
        if count < 2 or count > 257 or 4096 % (count - 1) or 12 + count * 2 > 562:
            raise NikonCompressionError("invalid Nikon D20 linearization knot count")
        knots = [
            unpack_u16(compression_payload[12 + i * 2 : 14 + i * 2])
            for i in range(count)
        ]
        if knots[-1] <= knots[0] or any(a > b for a, b in zip(knots, knots[1:])):
            raise NikonCompressionError("invalid Nikon D20 linearization curve")
        step = 4096 // (count - 1)
        linearization = tuple(
            (knots[i // step] * (step - i % step) + knots[i // step + 1] * (i % step))
            // step
            for i in range(4096)
        )
    initial_predictors = (
        (unpack_u16(compression_payload[2:4]), unpack_u16(compression_payload[6:8])),
        (unpack_u16(compression_payload[4:6]), unpack_u16(compression_payload[8:10])),
    )
    return NikonCompressionSetup(
        version=bytes(compression_payload[:2]).decode("ascii", errors="replace"),
        huffman_select=huffman_select,
        initial_predictors=initial_predictors,
        active_area=_tag_int_tuple(maker_ifd, 0x0045),
        compression_mode=compression_mode,
        linearization=linearization,
    )


def _decode_nikon_lossless_samples(
    payload: bytes,
    *,
    width: int,
    height: int,
    setup: NikonCompressionSetup,
    maximum: int,
) -> array:
    from openraw_studio.raw.native.compiled_decode import decode_samples

    if width <= 0 or height <= 0:
        raise NikonCompressionError("Nikon sensor dimensions must be positive")
    if width * height > len(payload) * 8:
        raise NikonCompressionError("Nikon compressed bitstream ended early")
    try:
        accelerated = decode_samples(
            payload, width, height, _build_huffman_lookup(setup.huffman_select),
            setup.initial_predictors, maximum,
        )
    except ValueError as exc:
        raise NikonCompressionError(str(exc)) from exc
    if accelerated is not None:
        output = array("H")
        output.frombytes(accelerated.tobytes())
        return output
    return _decode_nikon_lossless_samples_python(
        payload, width=width, height=height, setup=setup, maximum=maximum,
    )


def _decode_nikon_lossless_samples_python(
    payload: bytes, *, width: int, height: int,
    setup: NikonCompressionSetup, maximum: int,
) -> array:
    table = _build_huffman_lookup(setup.huffman_select)
    prefix_bits = (len(table) - 1).bit_length()
    prefix_mask = len(table) - 1
    try:
        output = array("H", [0]) * (width * height)
    except MemoryError as exc:
        raise NikonCompressionError("not enough memory to hold the decoded Nikon sensor payload") from exc

    data = payload
    data_length = len(data)
    byte_pos = 0
    bit_buffer = 0
    bit_count = 0
    masks = tuple((1 << value) - 1 for value in range(17))
    negative_thresholds = tuple(0 if value == 0 else 1 << (value - 1) for value in range(17))
    negative_offsets = masks
    row0_even, row0_odd = setup.initial_predictors[0]
    row1_even, row1_odd = setup.initial_predictors[1]
    index = 0
    for row in range(height):
        if row & 1:
            even_predictor = row1_even
            odd_predictor = row1_odd
        else:
            even_predictor = row0_even
            odd_predictor = row0_odd
        for column in range(width):
            while bit_count < prefix_bits and byte_pos < data_length:
                bit_buffer = (bit_buffer << 8) | data[byte_pos]
                byte_pos += 1
                bit_count += 8
            if bit_count <= 0:
                raise NikonCompressionError("Nikon compressed bitstream ended early")
            if bit_count < prefix_bits:
                prefix = (bit_buffer << (prefix_bits - bit_count)) & prefix_mask
            else:
                prefix = (bit_buffer >> (bit_count - prefix_bits)) & prefix_mask

            packed_code = table[prefix]
            code_length = packed_code & 0x0F
            if code_length <= 0:
                raise NikonCompressionError("invalid Nikon Huffman prefix")
            if code_length > bit_count:
                raise NikonCompressionError("Nikon compressed bitstream ended early")
            bit_count -= code_length
            bit_buffer = bit_buffer & masks[bit_count] if bit_count else 0

            category = packed_code >> 4
            if category == 0:
                diff = 0
            elif category == 16:
                diff = -32768
            else:
                while bit_count < category:
                    if byte_pos >= data_length:
                        raise NikonCompressionError("Nikon compressed bitstream ended early")
                    bit_buffer = (bit_buffer << 8) | data[byte_pos]
                    byte_pos += 1
                    bit_count += 8
                value = (bit_buffer >> (bit_count - category)) & masks[category]
                bit_count -= category
                bit_buffer = bit_buffer & masks[bit_count] if bit_count else 0
                if value < negative_thresholds[category]:
                    diff = value - negative_offsets[category]
                else:
                    diff = value

            if column & 1:
                odd_predictor += diff
                sample = odd_predictor
                if column == 1:
                    if row & 1:
                        row1_odd = odd_predictor
                    else:
                        row0_odd = odd_predictor
            else:
                even_predictor += diff
                sample = even_predictor
                if column == 0:
                    if row & 1:
                        row1_even = even_predictor
                    else:
                        row0_even = even_predictor
            if sample < 0:
                output[index] = 0
            elif sample > maximum:
                output[index] = maximum
            else:
                output[index] = sample
            index += 1
    return output


def _estimate_nikon_black_level(
    samples: array,
    *,
    width: int,
    height: int,
    active_area: tuple[int, ...] | None,
    white_level: int,
) -> int:
    if active_area is None or len(active_area) < 4:
        return 0
    left, top, active_width, active_height = active_area[:4]
    right = left + active_width
    bottom = top + active_height
    if left <= 0 and top <= 0 and right >= width and bottom >= height:
        return 0
    if left < 0 or top < 0 or active_width <= 0 or active_height <= 0 or right > width or bottom > height:
        return 0

    values: list[int] = []
    sample_cap = 20000
    step = max(1, (width * height) // sample_cap)
    for index in range(0, width * height, step):
        row, column = divmod(index, width)
        if row < top or row >= bottom or column < left or column >= right:
            values.append(int(samples[index]))
    if len(values) < 16:
        return 0

    values.sort()
    low_percentile = values[max(0, min(len(values) - 1, len(values) * 5 // 100))]
    return _clamp_int(low_percentile, 0, max(0, white_level // 8))


def _nikon_black_levels(
    metadata: DngMetadata,
    samples: array,
    *,
    width: int,
    height: int,
    active_area: tuple[int, ...] | None,
    white_level: int,
) -> tuple[int, int, int, int]:
    maker_ifd = _nikon_makernote_ifd(metadata)
    levels = _tag_int_tuple(maker_ifd, 0x003D) if maker_ifd is not None else None
    if levels is not None and len(levels) == 4:
        validated = tuple(int(value) for value in levels)
        # J5 MakerNotes express black in 14-bit units even in a 12-bit NEF.
        if (
            metadata.summary.get("model") == "NIKON 1 J5"
            and metadata.summary.get("bits_per_sample") == 12
        ):
            validated = tuple(value >> 2 for value in validated)
        if all(0 <= value < white_level for value in validated):
            return validated  # type: ignore[return-value]

    estimated = _estimate_nikon_black_level(
        samples,
        width=width,
        height=height,
        active_area=active_area,
        white_level=white_level,
    )
    return estimated, estimated, estimated, estimated


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
    height = decoded.height
    if decoded.camera_model == "NIKON 1 J5" and (decoded.width, decoded.height) == (
        5584,
        3726,
    ):
        height = 3724
    return 0, 0, decoded.width - (decoded.width % 2), height - (height % 2)


def _bayer_blocks_to_rgb8(
    samples: array,
    **options: Any,
) -> tuple[int, int, bytes | bytearray]:
    try:
        import numpy as np
    except ImportError:
        return _bayer_blocks_to_rgb8_python(samples, **options)
    return _bayer_blocks_to_rgb8_numpy(np, samples, **options)


def _bayer_blocks_to_rgb8_numpy(
    np: Any,
    samples: array,
    *,
    source_width: int,
    source_height: int,
    crop: tuple[int, int, int, int],
    cfa_pattern: tuple[int, ...] | None,
    black_level: int,
    black_levels: tuple[int, int, int, int],
    white_level: int,
    exposure: float,
    contrast: float,
    highlights: float,
    shadows: float,
    warmth: float,
    tint: float,
    saturation: float,
    camera_white_balance: NikonWhiteBalance | None,
    camera_to_linear_srgb: Matrix3 | None,
    chunk_rows: int = 256,
) -> tuple[int, int, bytes]:
    left, top, crop_width, crop_height = crop
    out_width = crop_width // 2
    out_height = crop_height // 2
    if out_width <= 0 or out_height <= 0:
        raise NikonCompressionError("Nikon 34713 render crop is empty")

    source = np.frombuffer(samples, dtype=np.uint16).reshape(source_height, source_width)
    cropped = source[top : top + crop_height, left : left + crop_width]
    planes = tuple(
        cropped[row::2, column::2][:out_height, :out_width]
        for row in range(2)
        for column in range(2)
    )
    red_index, green0_index, green1_index, blue_index = _cfa_block_indexes(cfa_pattern)
    channel_black_levels = (
        float(black_levels[red_index]),
        (black_levels[green0_index] + black_levels[green1_index]) / 2.0,
        float(black_levels[blue_index]),
    )
    red_matrix_luts, green_matrix_luts, blue_matrix_luts, output_lut = _linear_color_luts(
        black_levels=channel_black_levels,
        fallback_black_level=black_level,
        white_level=white_level,
        exposure=exposure,
        contrast=contrast,
        highlights=highlights,
        shadows=shadows,
        warmth=warmth,
        tint=tint,
        camera_white_balance=camera_white_balance,
        camera_to_linear_srgb=camera_to_linear_srgb,
    )
    matrix_luts = tuple(
        tuple(np.frombuffer(lut, dtype=np.int32) for lut in channel_luts)
        for channel_luts in (red_matrix_luts, green_matrix_luts, blue_matrix_luts)
    )
    encoded_lut = np.frombuffer(output_lut, dtype=np.uint8)
    output_limit = len(output_lut) - 1
    saturation_factor = 1.0 + _clamp_float(saturation, -1.0, 1.0) * 0.75
    output = np.empty((out_height, out_width, 3), dtype=np.uint8)

    for row_start in range(0, out_height, chunk_rows):
        row_end = min(out_height, row_start + chunk_rows)
        camera_red = planes[red_index][row_start:row_end]
        green0 = planes[green0_index][row_start:row_end].astype(np.uint32)
        green1 = planes[green1_index][row_start:row_end].astype(np.uint32)
        camera_green = ((green0 + green1) >> 1).astype(np.uint16)
        camera_blue = planes[blue_index][row_start:row_end]
        channels = []
        for output_channel in range(3):
            values = (
                matrix_luts[0][output_channel][camera_red]
                + matrix_luts[1][output_channel][camera_green]
                + matrix_luts[2][output_channel][camera_blue]
            )
            np.clip(values, 0, output_limit, out=values)
            channels.append(encoded_lut[values])

        if saturation_factor != 1.0:
            red, green, blue = (channel.astype(np.float32) for channel in channels)
            luma = ((54.0 * red) + (183.0 * green) + (19.0 * blue)) / 256.0
            channels = [
                np.clip(np.rint(luma + ((channel - luma) * saturation_factor)), 0, 255).astype(np.uint8)
                for channel in (red, green, blue)
            ]
        output[row_start:row_end] = np.stack(channels, axis=2)
    return out_width, out_height, output.tobytes()


def _bayer_blocks_to_rgb8_python(
    samples: array,
    *,
    source_width: int,
    source_height: int,
    crop: tuple[int, int, int, int],
    cfa_pattern: tuple[int, ...] | None,
    black_level: int,
    black_levels: tuple[int, int, int, int],
    white_level: int,
    exposure: float,
    contrast: float,
    highlights: float,
    shadows: float,
    warmth: float,
    tint: float,
    saturation: float,
    camera_white_balance: NikonWhiteBalance | None,
    camera_to_linear_srgb: Matrix3 | None,
) -> tuple[int, int, bytearray]:
    del source_height
    left, top, crop_width, crop_height = crop
    out_width = crop_width // 2
    out_height = crop_height // 2
    if out_width <= 0 or out_height <= 0:
        raise NikonCompressionError("Nikon 34713 render crop is empty")

    red_index, green0_index, green1_index, blue_index = _cfa_block_indexes(cfa_pattern)
    channel_black_levels = (
        float(black_levels[red_index]),
        (black_levels[green0_index] + black_levels[green1_index]) / 2.0,
        float(black_levels[blue_index]),
    )
    red_matrix_luts, green_matrix_luts, blue_matrix_luts, output_lut = _linear_color_luts(
        black_levels=channel_black_levels,
        fallback_black_level=black_level,
        white_level=white_level,
        exposure=exposure,
        contrast=contrast,
        highlights=highlights,
        shadows=shadows,
        warmth=warmth,
        tint=tint,
        camera_white_balance=camera_white_balance,
        camera_to_linear_srgb=camera_to_linear_srgb,
    )
    rr_lut, rg_lut, rb_lut = red_matrix_luts
    gr_lut, gg_lut, gb_lut = green_matrix_luts
    br_lut, bg_lut, bb_lut = blue_matrix_luts
    output_limit = len(output_lut) - 1
    saturation_factor = 1.0 + _clamp_float(saturation, -1.0, 1.0) * 0.75
    output = bytearray(out_width * out_height * 3)
    out_index = 0

    if (red_index, green0_index, green1_index, blue_index) == (0, 1, 2, 3):
        for row in range(out_height):
            source_row = top + row * 2
            row0 = source_row * source_width + left
            row1 = (source_row + 1) * source_width + left
            for column in range(out_width):
                source_column = column * 2
                camera_red = samples[row0 + source_column]
                camera_green = (samples[row0 + source_column + 1] + samples[row1 + source_column]) >> 1
                camera_blue = samples[row1 + source_column + 1]
                red = output_lut[_clamp_int(rr_lut[camera_red] + gr_lut[camera_green] + br_lut[camera_blue], 0, output_limit)]
                green = output_lut[_clamp_int(rg_lut[camera_red] + gg_lut[camera_green] + bg_lut[camera_blue], 0, output_limit)]
                blue = output_lut[_clamp_int(rb_lut[camera_red] + gb_lut[camera_green] + bb_lut[camera_blue], 0, output_limit)]
                if saturation_factor != 1.0:
                    red, green, blue = _apply_saturation8(red, green, blue, factor=saturation_factor)
                output[out_index] = red
                output[out_index + 1] = green
                output[out_index + 2] = blue
                out_index += 3
        return out_width, out_height, output

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
            camera_red = block_values[red_index]
            camera_green = (block_values[green0_index] + block_values[green1_index]) >> 1
            camera_blue = block_values[blue_index]
            red = output_lut[_clamp_int(rr_lut[camera_red] + gr_lut[camera_green] + br_lut[camera_blue], 0, output_limit)]
            green = output_lut[_clamp_int(rg_lut[camera_red] + gg_lut[camera_green] + bg_lut[camera_blue], 0, output_limit)]
            blue = output_lut[_clamp_int(rb_lut[camera_red] + gb_lut[camera_green] + bb_lut[camera_blue], 0, output_limit)]
            if saturation_factor != 1.0:
                red, green, blue = _apply_saturation8(red, green, blue, factor=saturation_factor)
            output[out_index] = red
            output[out_index + 1] = green
            output[out_index + 2] = blue
            out_index += 3

    return out_width, out_height, output


def _apply_saturation8(red: int, green: int, blue: int, *, factor: float) -> tuple[int, int, int]:
    luma = ((54 * red) + (183 * green) + (19 * blue)) / 256.0
    return (
        _clamp_byte(round(luma + ((red - luma) * factor))),
        _clamp_byte(round(luma + ((green - luma) * factor))),
        _clamp_byte(round(luma + ((blue - luma) * factor))),
    )


def _clamp_byte(value: int) -> int:
    if value < 0:
        return 0
    if value > 255:
        return 255
    return value


def _cfa_block_indexes(cfa_pattern: tuple[int, ...] | None) -> tuple[int, int, int, int]:
    pattern = _cfa_2x2(cfa_pattern)
    red_index = pattern.index("R")
    blue_index = pattern.index("B")
    green_indexes = tuple(index for index, channel in enumerate(pattern) if channel == "G")
    if len(green_indexes) != 2:
        return 0, 1, 2, 3
    return red_index, green_indexes[0], green_indexes[1], blue_index


def _cfa_2x2(cfa_pattern: tuple[int, ...] | None) -> tuple[str, str, str, str]:
    mapping = {0: "R", 1: "G", 2: "B"}
    if cfa_pattern is None or len(cfa_pattern) < 4:
        return "R", "G", "G", "B"
    channels = tuple(mapping.get(value, "?") for value in cfa_pattern[:4])
    if sorted(channels) != ["B", "G", "G", "R"]:
        return "R", "G", "G", "B"
    return channels  # type: ignore[return-value]


def _resize_pillow_image(image: Any, *, max_dimension: int) -> Any:
    if max_dimension <= 0:
        raise NikonCompressionError("max_dimension must be greater than zero")
    longest = max(image.size)
    if longest <= max_dimension:
        return image
    scale = max_dimension / float(longest)
    size = (max(1, round(image.size[0] * scale)), max(1, round(image.size[1] * scale)))
    return image.resize(size, resample=1)


def _apply_exif_orientation(image: Any, orientation: int) -> Any:
    try:
        from PIL import Image
    except ImportError as exc:
        raise NikonCompressionError("Pillow is required for Nikon 34713 orientation") from exc

    operations = {
        2: Image.Transpose.FLIP_LEFT_RIGHT,
        3: Image.Transpose.ROTATE_180,
        4: Image.Transpose.FLIP_TOP_BOTTOM,
        5: Image.Transpose.TRANSPOSE,
        6: Image.Transpose.ROTATE_270,
        7: Image.Transpose.TRANSVERSE,
        8: Image.Transpose.ROTATE_90,
    }
    operation = operations.get(orientation)
    return image.transpose(operation) if operation is not None else image


def _linear_color_luts(
    *,
    black_levels: tuple[float, float, float],
    fallback_black_level: int,
    white_level: int,
    exposure: float,
    contrast: float,
    highlights: float,
    shadows: float,
    warmth: float,
    tint: float,
    camera_white_balance: NikonWhiteBalance | None,
    camera_to_linear_srgb: Matrix3 | None,
) -> tuple[
    tuple[array, array, array],
    tuple[array, array, array],
    tuple[array, array, array],
    bytes,
]:
    exposure_scale = 2.0 ** _clamp_float(exposure, -4.0, 4.0)
    contrast_factor = 1.0 + _clamp_float(contrast, -1.0, 1.0) * 0.75
    highlights_value = _clamp_float(highlights, -1.0, 1.0)
    shadows_value = _clamp_float(shadows, -1.0, 1.0)
    red_scale, green_scale, blue_scale = _camera_channel_scales(
        camera_white_balance,
        warmth=warmth,
        tint=tint,
    )
    matrix = camera_to_linear_srgb or (
        (1.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
        (0.0, 0.0, 1.0),
    )
    fixed_scale = 65535.0

    camera_values: list[array] = []
    for channel_black, channel_scale in zip(
        black_levels,
        (red_scale, green_scale, blue_scale),
    ):
        if not math.isfinite(channel_black) or channel_black < 0.0 or channel_black >= white_level:
            channel_black = float(fallback_black_level)
        span = max(1.0, white_level - channel_black)
        values = array("f")
        values.extend(
            min(
                _clamp_float((value - channel_black) / span, 0.0, 1.0) * channel_scale,
                min(red_scale, green_scale, blue_scale),
            )
            * exposure_scale
            for value in range(65536)
        )
        camera_values.append(values)

    source_luts: list[tuple[array, array, array]] = []
    for source_channel, values in enumerate(camera_values):
        source_luts.append(
            tuple(
                array(
                    "i",
                    (round(value * matrix[output_channel][source_channel] * fixed_scale) for value in values),
                )
                for output_channel in range(3)
            )  # type: ignore[arg-type]
        )

    output_limit = 4 * 65535
    output = bytearray(output_limit + 1)
    for index in range(output_limit + 1):
        linear = index / fixed_scale
        output[index] = _encode_channel(
            _apply_tonal_regions(
                _apply_contrast(linear, contrast_factor),
                highlights=highlights_value,
                shadows=shadows_value,
            )
        )
    return source_luts[0], source_luts[1], source_luts[2], bytes(output)


def _camera_channel_scales(
    white_balance: NikonWhiteBalance | None,
    *,
    warmth: float,
    tint: float,
) -> tuple[float, float, float]:
    warmth_value = _clamp_float(warmth, -1.0, 1.0)
    tint_value = _clamp_float(tint, -1.0, 1.0)
    camera_red, camera_green, camera_blue = (
        white_balance.gains if white_balance is not None else (1.0, 1.0, 1.0)
    )
    return (
        camera_red * (1.0 + warmth_value * 0.12) * (1.0 + tint_value * 0.08),
        camera_green * (1.0 + warmth_value * 0.03) * (1.0 - tint_value * 0.12),
        camera_blue * (1.0 - warmth_value * 0.12) * (1.0 + tint_value * 0.08),
    )


def _apply_contrast(value: float, factor: float) -> float:
    return ((value - 0.18) * factor) + 0.18


def _apply_tonal_regions(value: float, *, highlights: float, shadows: float) -> float:
    if highlights == 0.0 and shadows == 0.0:
        return value
    position = _clamp_float(value, 0.0, 1.0)
    shadow_weight = 4.0 * position * (1.0 - position) ** 2
    highlight_weight = position**2
    return value + (shadows * 0.3 * shadow_weight) + (highlights * 0.3 * highlight_weight)


def _encode_channel(value: float) -> int:
    return int(round((_clamp_float(value, 0.0, 1.0) ** (1.0 / 2.2)) * 255.0))


def _clamp_float(value: float, minimum: float, maximum: float) -> float:
    if value < minimum:
        return minimum
    if value > maximum:
        return maximum
    return value


def _build_huffman_lookup(huffman_select: int) -> tuple[int, ...]:
    counts, values = _NIKON_HUFFMAN_TABLES[huffman_select]
    prefix_bits = max(8, max(index + 1 for index, count in enumerate(counts) if count))
    lookup = [0] * (1 << prefix_bits)
    code = 0
    value_index = 0
    for code_length, count in enumerate(counts, start=1):
        for _ in range(count):
            value = values[value_index]
            value_index += 1
            if code_length <= prefix_bits:
                prefix = code << (prefix_bits - code_length)
                fill = 1 << (prefix_bits - code_length)
                for table_index in range(prefix, prefix + fill):
                    lookup[table_index] = (value << 4) | code_length
            code += 1
        code <<= 1
    return tuple(lookup)


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


def _white_balance_from_makernote_ifd(ifd: TiffIfd) -> NikonWhiteBalance | None:
    mode = _tag_ascii(ifd, 0x0005)
    levels = _tag_float_tuple(ifd, 0x000C)
    if levels is None or len(levels) != 4 or any(value <= 0.0 for value in levels):
        return None
    green_reference = (levels[2] + levels[3]) / 2.0
    if not math.isfinite(green_reference) or green_reference <= 0.0:
        return None
    gains = (levels[0] / green_reference, 1.0, levels[1] / green_reference)
    if all(abs(gain - 1.0) <= 1e-6 for gain in gains):
        return None
    if not all(math.isfinite(gain) and 0.125 <= gain <= 8.0 for gain in gains):
        return None
    return NikonWhiteBalance(
        red_gain=gains[0],
        green_gain=gains[1],
        blue_gain=gains[2],
        source_tag="0x000c",
        mode=mode,
    )


def _tag_float_tuple(ifd: TiffIfd, tag_code: int) -> tuple[float, ...] | None:
    tag = ifd.tags.get(tag_code)
    if tag is None:
        return None
    values = tag.value if isinstance(tag.value, tuple) else (tag.value,)
    try:
        result = tuple(float(item) for item in values)
    except (TypeError, ValueError):
        return None
    return result if all(math.isfinite(item) for item in result) else None


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _coerce_orientation(value: Any) -> int:
    try:
        orientation = int(value)
    except (TypeError, ValueError):
        return 1
    return orientation if 1 <= orientation <= 8 else 1


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
