"""Guarded adapter for the locally verified Nikon Z f HE* 14-bit profile.

Unknown profile bytes, black levels, CFA layouts, and camera modes are rejected.
The nonlinear mapping is a bounded approximation, not a lossless codec claim.
"""

from __future__ import annotations

from pathlib import Path

from .he import HeFormatError, read_header, validate_packet_profile
from .he_transform import (
    decode_component_planes,
    reconstruct_zf_he_bayer,
)
from .nikon import (
    NikonCompressionError,
    NikonCompressionSetup,
    NikonDecodedPixelData,
    _coerce_orientation,
    _nikon_pixel_ifd,
    _optional_int,
    _optional_int_tuple,
    _required_bits_per_sample,
    _required_int,
    _required_int_tuple,
    extract_nikon_as_shot_white_balance,
    summarize_nikon_makernote,
)
from .profiles import find_camera_color_profile

# Full observed PIH configuration after length/dimensions. Do not infer support
# from a model name alone or generalize the nonlinear curve to other profiles.
ZF_HE_PICTURE_TAIL = bytes.fromhex("00000010040408124410511450887083f01523d149cd3f7f07")


def read_zf_he_payload(path, metadata):
    source = Path(path)
    summary = metadata.summary
    maker = summarize_nikon_makernote(metadata)
    if ((summary.get("make") or "").strip().upper() != "NIKON CORPORATION"
            or (summary.get("model") or "").strip().upper() != "NIKON Z F"
            or maker is None or maker.compression_mode != 14):
        raise NikonCompressionError("Nikon High Efficiency: only the verified Z f HE* profile is supported")
    if maker.black_levels != (1008, 1008, 1008, 1008):
        raise NikonCompressionError("Nikon High Efficiency: unverified black-level profile")
    pixel = _nikon_pixel_ifd(metadata)
    if (_required_int(pixel, 259, "Compression") != 34713
            or _required_bits_per_sample(pixel) != 14
            or _optional_int(pixel, 277, default=1) != 1
            or _optional_int_tuple(pixel, 33422) != (0, 1, 1, 2)):
        raise NikonCompressionError("Nikon High Efficiency: unsupported sensor layout")
    offsets = _required_int_tuple(pixel, 273, "StripOffsets")
    sizes = _required_int_tuple(pixel, 279, "StripByteCounts")
    if len(offsets) != 1 or len(sizes) != 1:
        raise NikonCompressionError("Nikon High Efficiency: only a single RAW strip is supported")
    if offsets[0] <= 0 or sizes[0] <= 0 or offsets[0] + sizes[0] > source.stat().st_size:
        raise NikonCompressionError("Nikon High Efficiency: RAW strip is outside the file bounds")
    with source.open("rb") as handle:
        handle.seek(offsets[0])
        payload = handle.read(sizes[0])
    try:
        header = read_header(payload)
    except HeFormatError as error:
        raise NikonCompressionError(f"Nikon High Efficiency: {error}") from error
    if (header.picture_payload[4:8] != bytes(4)
            or header.picture_payload[12:] != ZF_HE_PICTURE_TAIL):
        raise NikonCompressionError("Nikon High Efficiency: unverified nonlinear/color profile")
    if (header.width, header.height) != (
        _required_int(pixel, 256, "ImageWidth"), _required_int(pixel, 257, "ImageLength")
    ):
        raise NikonCompressionError("Nikon High Efficiency: codestream/container dimensions disagree")
    try:
        validate_packet_profile(payload, header)
    except HeFormatError as error:
        raise NikonCompressionError(f"Nikon High Efficiency: {error}") from error
    return payload, header, maker


def decode_zf_he(path, metadata):
    payload, header, maker = read_zf_he_payload(path, metadata)
    try:
        components = decode_component_planes(payload)
        samples = reconstruct_zf_he_bayer(components)
    except HeFormatError as error:
        raise NikonCompressionError(f"Nikon High Efficiency: {error}") from error
    summary = metadata.summary
    make, model = summary.get("make"), summary.get("model")
    pixel = _nikon_pixel_ifd(metadata)
    return NikonDecodedPixelData(
        width=header.width, height=header.height, source_bits_per_sample=14,
        output_bits_per_sample=16, samples_per_pixel=1, byte_order="little",
        raw_bytes=samples.astype("<u2", copy=False).tobytes(),
        storage_layout="nikon-he-star-strips",
        strip_offsets=_required_int_tuple(pixel, 273, "StripOffsets"),
        strip_byte_counts=_required_int_tuple(pixel, 279, "StripByteCounts"),
        rows_per_strip=_optional_int(pixel, 278), black_level=1008,
        black_levels=(1008, 1008, 1008, 1008), white_level=16383,
        cfa_pattern=(0, 1, 1, 2), compression=34713,
        compression_setup=NikonCompressionSetup(
            version="HE* Zf profile 1", huffman_select=-1,
            initial_predictors=((0, 0), (0, 0)), active_area=maker.active_area,
            compression_mode=14,
        ),
        white_balance=extract_nikon_as_shot_white_balance(metadata),
        camera_profile=find_camera_color_profile(make, model),
        camera_make=make, camera_model=model,
        orientation=_coerce_orientation(summary.get("orientation")),
    )
