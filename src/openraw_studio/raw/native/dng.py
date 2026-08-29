"""Minimal TIFF/DNG metadata reader for OpenRAW Native."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import struct
from typing import Any


class DngMetadataError(ValueError):
    """Raised when a TIFF/DNG metadata structure cannot be parsed."""


@dataclass(frozen=True)
class TiffTag:
    """One parsed TIFF/DNG tag."""

    code: int
    name: str
    field_type: int
    count: int
    value: Any


@dataclass(frozen=True)
class TiffIfd:
    """One TIFF image file directory."""

    offset: int
    tags: dict[int, TiffTag]
    next_ifd_offset: int


@dataclass(frozen=True)
class DngMetadata:
    """Native metadata extracted from a DNG/TIFF-style file."""

    byte_order: str
    ifds: tuple[TiffIfd, ...]
    summary: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "byte_order": self.byte_order,
            "ifd_count": len(self.ifds),
            **self.summary,
        }


@dataclass(frozen=True)
class DngPixelData:
    """Uncompressed Bayer-like pixel payload extracted from a DNG/TIFF IFD."""

    width: int
    height: int
    bits_per_sample: int
    samples_per_pixel: int
    byte_order: str
    raw_bytes: bytes
    storage_layout: str = "strips"
    strip_offsets: tuple[int, ...] = ()
    strip_byte_counts: tuple[int, ...] = ()
    tile_offsets: tuple[int, ...] = ()
    tile_byte_counts: tuple[int, ...] = ()
    tile_width: int | None = None
    tile_length: int | None = None
    rows_per_strip: int | None = None
    black_level: int | float | tuple[int | float, ...] | None = None
    white_level: int | float | tuple[int | float, ...] | None = None
    cfa_pattern: tuple[int, ...] | None = None

    @property
    def expected_byte_count(self) -> int:
        return self.width * self.height * self.samples_per_pixel * (self.bits_per_sample // 8)

    def samples_u16(self) -> tuple[int, ...]:
        """Decode 16-bit unsigned samples for tests and early experiments."""

        if self.bits_per_sample != 16 or self.samples_per_pixel != 1:
            raise DngMetadataError("samples_u16 requires 16-bit single-sample pixel data")
        endian = "<" if self.byte_order == "little" else ">"
        sample_count = len(self.raw_bytes) // 2
        return struct.unpack(endian + f"{sample_count}H", self.raw_bytes[: sample_count * 2])


TIFF_TYPES: dict[int, tuple[str, int, str]] = {
    1: ("BYTE", 1, "B"),
    2: ("ASCII", 1, "c"),
    3: ("SHORT", 2, "H"),
    4: ("LONG", 4, "I"),
    5: ("RATIONAL", 8, "II"),
    6: ("SBYTE", 1, "b"),
    7: ("UNDEFINED", 1, "B"),
    8: ("SSHORT", 2, "h"),
    9: ("SLONG", 4, "i"),
    10: ("SRATIONAL", 8, "ii"),
    11: ("FLOAT", 4, "f"),
    12: ("DOUBLE", 8, "d"),
}


TAG_NAMES = {
    256: "ImageWidth",
    257: "ImageLength",
    258: "BitsPerSample",
    259: "Compression",
    262: "PhotometricInterpretation",
    271: "Make",
    272: "Model",
    273: "StripOffsets",
    274: "Orientation",
    277: "SamplesPerPixel",
    278: "RowsPerStrip",
    279: "StripByteCounts",
    284: "PlanarConfiguration",
    305: "Software",
    306: "DateTime",
    322: "TileWidth",
    323: "TileLength",
    324: "TileOffsets",
    325: "TileByteCounts",
    330: "SubIFDs",
    33434: "ExposureTime",
    33437: "FNumber",
    34665: "ExifIFDPointer",
    34855: "ISOSpeedRatings",
    36867: "DateTimeOriginal",
    37386: "FocalLength",
    42036: "LensModel",
    33421: "CFARepeatPatternDim",
    33422: "CFAPattern",
    50706: "DNGVersion",
    50707: "DNGBackwardVersion",
    50708: "UniqueCameraModel",
    50710: "CFAPlaneColor",
    50711: "CFALayout",
    50713: "BlackLevelRepeatDim",
    50714: "BlackLevel",
    50717: "WhiteLevel",
    50718: "DefaultScale",
    50719: "DefaultCropOrigin",
    50720: "DefaultCropSize",
    50721: "ColorMatrix1",
    50722: "ColorMatrix2",
    50723: "CameraCalibration1",
    50724: "CameraCalibration2",
    50727: "AnalogBalance",
    50728: "AsShotNeutral",
    50729: "AsShotWhiteXY",
    50730: "BaselineExposure",
    50731: "BaselineNoise",
    50732: "BaselineSharpness",
    50733: "BayerGreenSplit",
    50734: "LinearResponseLimit",
    50735: "CameraSerialNumber",
    50736: "LensInfo",
    50778: "CalibrationIlluminant1",
    50779: "CalibrationIlluminant2",
    50780: "BestQualityScale",
    50827: "OriginalRawFileName",
    51009: "OpcodeList1",
    51022: "OpcodeList2",
    51023: "OpcodeList3",
    51125: "DefaultUserCrop",
}


SUMMARY_TAGS = {
    "ImageWidth": "width",
    "ImageLength": "height",
    "BitsPerSample": "bits_per_sample",
    "Compression": "compression",
    "PhotometricInterpretation": "photometric_interpretation",
    "Make": "make",
    "Model": "model",
    "Orientation": "orientation",
    "SamplesPerPixel": "samples_per_pixel",
    "RowsPerStrip": "rows_per_strip",
    "TileWidth": "tile_width",
    "TileLength": "tile_length",
    "TileOffsets": "tile_offsets",
    "TileByteCounts": "tile_byte_counts",
    "ExposureTime": "exposure_time",
    "FNumber": "aperture",
    "ISOSpeedRatings": "iso",
    "DateTimeOriginal": "captured_at",
    "FocalLength": "focal_length_mm",
    "LensModel": "lens_model",
    "CFARepeatPatternDim": "cfa_repeat_pattern_dim",
    "CFAPattern": "cfa_pattern",
    "DNGVersion": "dng_version",
    "DNGBackwardVersion": "dng_backward_version",
    "UniqueCameraModel": "unique_camera_model",
    "CFAPlaneColor": "cfa_plane_color",
    "CFALayout": "cfa_layout",
    "BlackLevelRepeatDim": "black_level_repeat_dim",
    "BlackLevel": "black_level",
    "WhiteLevel": "white_level",
    "DefaultScale": "default_scale",
    "DefaultCropOrigin": "default_crop_origin",
    "DefaultCropSize": "default_crop_size",
    "ColorMatrix1": "color_matrix_1",
    "ColorMatrix2": "color_matrix_2",
    "AsShotNeutral": "as_shot_neutral",
    "CalibrationIlluminant1": "calibration_illuminant_1",
    "CalibrationIlluminant2": "calibration_illuminant_2",
    "CameraSerialNumber": "camera_serial_number",
    "LensInfo": "lens_info",
}


class DngMetadataReader:
    """Read a useful subset of TIFF/DNG metadata without external dependencies."""

    def read(self, path: str | Path) -> DngMetadata:
        data = Path(path).read_bytes()
        byte_order, endian, ifds = self._read_structure(data)
        return DngMetadata(
            byte_order=byte_order,
            ifds=tuple(ifds),
            summary=_build_summary(ifds),
        )

    def read_pixel_data(self, path: str | Path) -> DngPixelData:
        """Extract simple uncompressed strip- or tile-based pixel data.

        This is deliberately narrow: V0.1 native extraction supports
        Compression=1, BitsPerSample=16, SamplesPerPixel=1, and either
        StripOffsets / StripByteCounts or TileOffsets / TileByteCounts.
        Compressed DNG files are future work.
        """

        data = Path(path).read_bytes()
        byte_order, _endian, ifds = self._read_structure(data)
        ifd = _select_pixel_ifd(ifds)
        summary = _build_summary([ifd])

        compression = _expect_int(summary, "compression", default=1)
        bits_per_sample = _expect_int(summary, "bits_per_sample")
        samples_per_pixel = _expect_int(summary, "samples_per_pixel", default=1)
        width = _expect_int(summary, "width")
        height = _expect_int(summary, "height")

        if compression != 1:
            raise DngMetadataError(f"unsupported DNG compression: {compression}; only uncompressed strips or tiles are supported")
        if bits_per_sample != 16:
            raise DngMetadataError(f"unsupported BitsPerSample: {bits_per_sample}; only 16-bit data is supported")
        if samples_per_pixel != 1:
            raise DngMetadataError(
                f"unsupported SamplesPerPixel: {samples_per_pixel}; only single-sample Bayer data is supported"
            )

        bytes_per_pixel = (bits_per_sample // 8) * samples_per_pixel
        strip_offsets: tuple[int, ...] = ()
        strip_byte_counts: tuple[int, ...] = ()
        tile_offsets: tuple[int, ...] = ()
        tile_byte_counts: tuple[int, ...] = ()
        tile_width: int | None = None
        tile_length: int | None = None
        storage_layout = "strips"

        if _ifd_has_strip_payload(ifd):
            strip_offsets = _tuple_of_ints(_require_tag_value(ifd, 273, "StripOffsets"))
            strip_byte_counts = _tuple_of_ints(_require_tag_value(ifd, 279, "StripByteCounts"))
            if len(strip_offsets) != len(strip_byte_counts):
                raise DngMetadataError("StripOffsets and StripByteCounts have different lengths")
            raw_bytes = b"".join(_slice_checked(data, offset, count) for offset, count in zip(strip_offsets, strip_byte_counts))
        elif _ifd_has_tile_payload(ifd):
            storage_layout = "tiles"
            tile_width = _expect_int(summary, "tile_width")
            tile_length = _expect_int(summary, "tile_length")
            tile_offsets = _tuple_of_ints(_require_tag_value(ifd, 324, "TileOffsets"))
            tile_byte_counts = _tuple_of_ints(_require_tag_value(ifd, 325, "TileByteCounts"))
            raw_bytes = _assemble_tiled_payload(
                data,
                tile_offsets,
                tile_byte_counts,
                width=width,
                height=height,
                tile_width=tile_width,
                tile_length=tile_length,
                bytes_per_pixel=bytes_per_pixel,
            )
        else:
            raise DngMetadataError("missing strip or tile pixel payload tags")

        pixel_data = DngPixelData(
            width=width,
            height=height,
            bits_per_sample=bits_per_sample,
            samples_per_pixel=samples_per_pixel,
            byte_order=byte_order,
            raw_bytes=raw_bytes,
            storage_layout=storage_layout,
            strip_offsets=strip_offsets,
            strip_byte_counts=strip_byte_counts,
            tile_offsets=tile_offsets,
            tile_byte_counts=tile_byte_counts,
            tile_width=tile_width,
            tile_length=tile_length,
            rows_per_strip=_optional_int(summary.get("rows_per_strip")),
            black_level=summary.get("black_level"),
            white_level=summary.get("white_level"),
            cfa_pattern=_optional_tuple_int(summary.get("cfa_pattern")),
        )
        if len(pixel_data.raw_bytes) < pixel_data.expected_byte_count:
            raise DngMetadataError(
                f"pixel payload is shorter than expected: {len(pixel_data.raw_bytes)} < {pixel_data.expected_byte_count}"
            )
        return pixel_data

    def _read_structure(self, data: bytes) -> tuple[str, str, list[TiffIfd]]:
        if len(data) < 8:
            raise DngMetadataError("file is too small to be TIFF/DNG")

        byte_order, endian = _read_byte_order(data[0:2])
        magic = struct.unpack(endian + "H", data[2:4])[0]
        if magic != 42:
            raise DngMetadataError("unsupported TIFF magic; BigTIFF is not supported yet")
        first_ifd_offset = struct.unpack(endian + "I", data[4:8])[0]
        if first_ifd_offset <= 0:
            raise DngMetadataError("missing first IFD offset")

        ifds = self._read_ifd_tree(data, endian, first_ifd_offset)
        return byte_order, endian, ifds

    def _read_ifd_tree(self, data: bytes, endian: str, first_ifd_offset: int) -> list[TiffIfd]:
        pending = [first_ifd_offset]
        visited: set[int] = set()
        ifds: list[TiffIfd] = []

        while pending:
            offset = pending.pop(0)
            if offset == 0 or offset in visited:
                continue
            visited.add(offset)
            ifd = _read_ifd(data, endian, offset)
            ifds.append(ifd)
            if ifd.next_ifd_offset:
                pending.append(ifd.next_ifd_offset)
            for pointer_tag in (330, 34665):
                pointer = ifd.tags.get(pointer_tag)
                if pointer is not None:
                    pending.extend(_positive_offsets(pointer.value))

        return ifds


def _read_byte_order(value: bytes) -> tuple[str, str]:
    if value == b"II":
        return "little", "<"
    if value == b"MM":
        return "big", ">"
    raise DngMetadataError("missing TIFF byte-order marker")


def _read_ifd(data: bytes, endian: str, offset: int) -> TiffIfd:
    _require_range(data, offset, 2)
    entry_count = struct.unpack(endian + "H", data[offset : offset + 2])[0]
    entries_offset = offset + 2
    tags: dict[int, TiffTag] = {}

    for index in range(entry_count):
        entry_offset = entries_offset + index * 12
        _require_range(data, entry_offset, 12)
        tag_code, field_type, count = struct.unpack(endian + "HHI", data[entry_offset : entry_offset + 8])
        value_bytes = data[entry_offset + 8 : entry_offset + 12]
        value = _read_tag_value(data, endian, field_type, count, value_bytes)
        name = TAG_NAMES.get(tag_code, f"Tag{tag_code}")
        tags[tag_code] = TiffTag(
            code=tag_code,
            name=name,
            field_type=field_type,
            count=count,
            value=value,
        )

    next_offset_position = entries_offset + entry_count * 12
    _require_range(data, next_offset_position, 4)
    next_ifd_offset = struct.unpack(endian + "I", data[next_offset_position : next_offset_position + 4])[0]
    return TiffIfd(offset=offset, tags=tags, next_ifd_offset=next_ifd_offset)


def _read_tag_value(data: bytes, endian: str, field_type: int, count: int, value_bytes: bytes) -> Any:
    if field_type not in TIFF_TYPES:
        return {"unsupported_type": field_type, "count": count}

    type_name, type_size, fmt = TIFF_TYPES[field_type]
    byte_count = type_size * count
    if byte_count <= 4:
        payload = value_bytes[:byte_count]
    else:
        value_offset = struct.unpack(endian + "I", value_bytes)[0]
        _require_range(data, value_offset, byte_count)
        payload = data[value_offset : value_offset + byte_count]

    if type_name == "ASCII":
        return payload.split(b"\x00", 1)[0].decode("utf-8", errors="replace")
    if type_name == "UNDEFINED":
        return tuple(payload)
    if type_name in {"RATIONAL", "SRATIONAL"}:
        values = []
        pair_fmt = endian + fmt
        for index in range(count):
            start = index * type_size
            numerator, denominator = struct.unpack(pair_fmt, payload[start : start + type_size])
            values.append(float(numerator) / float(denominator) if denominator else None)
        return _collapse(values)

    values = [
        struct.unpack(endian + fmt, payload[index * type_size : (index + 1) * type_size])[0]
        for index in range(count)
    ]
    return _collapse(values)


def _collapse(values: list[Any]) -> Any:
    if len(values) == 1:
        return values[0]
    return tuple(values)


def _positive_offsets(value: Any) -> tuple[int, ...]:
    if value is None or isinstance(value, bool):
        return ()
    values = value if isinstance(value, tuple) else (value,)
    offsets: list[int] = []
    for item in values:
        try:
            offset = int(item)
        except (TypeError, ValueError):
            continue
        if offset > 0:
            offsets.append(offset)
    return tuple(offsets)


def _build_summary(ifds: list[TiffIfd]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for ifd in _largest_ifd_first(ifds):
        for tag in ifd.tags.values():
            key = SUMMARY_TAGS.get(tag.name)
            if key and key not in summary:
                summary[key] = tag.value

    if "dng_version" in summary and isinstance(summary["dng_version"], tuple):
        summary["dng_version_text"] = ".".join(str(part) for part in summary["dng_version"])
    return summary


def _largest_ifd_first(ifds: list[TiffIfd]) -> list[TiffIfd]:
    return sorted(ifds, key=_ifd_area, reverse=True)


def _ifd_area(ifd: TiffIfd) -> int:
    width = ifd.tags.get(256)
    height = ifd.tags.get(257)
    if width is None or height is None:
        return 0
    if not isinstance(width.value, int) or not isinstance(height.value, int):
        return 0
    return width.value * height.value


def _require_range(data: bytes, offset: int, length: int) -> None:
    if offset < 0 or length < 0 or offset + length > len(data):
        raise DngMetadataError("TIFF/DNG structure points outside the file")


def _select_pixel_ifd(ifds: list[TiffIfd]) -> TiffIfd:
    candidates = [ifd for ifd in ifds if _ifd_has_strip_payload(ifd) or _ifd_has_tile_payload(ifd)]
    if not candidates:
        if any(_ifd_has_incomplete_pixel_payload(ifd) for ifd in ifds):
            raise DngMetadataError("incomplete strip or tile pixel payload tags")
        raise DngMetadataError("no strip or tile pixel payload tags found")
    return _largest_ifd_first(candidates)[0]


def _ifd_has_strip_payload(ifd: TiffIfd) -> bool:
    return 273 in ifd.tags and 279 in ifd.tags


def _ifd_has_tile_payload(ifd: TiffIfd) -> bool:
    return 322 in ifd.tags and 323 in ifd.tags and 324 in ifd.tags and 325 in ifd.tags


def _ifd_has_incomplete_pixel_payload(ifd: TiffIfd) -> bool:
    return any(tag in ifd.tags for tag in (273, 279, 322, 323, 324, 325))


def _assemble_tiled_payload(
    data: bytes,
    tile_offsets: tuple[int, ...],
    tile_byte_counts: tuple[int, ...],
    *,
    width: int,
    height: int,
    tile_width: int,
    tile_length: int,
    bytes_per_pixel: int,
) -> bytes:
    if tile_width <= 0 or tile_length <= 0:
        raise DngMetadataError("TileWidth and TileLength must be greater than zero")
    if len(tile_offsets) != len(tile_byte_counts):
        raise DngMetadataError("TileOffsets and TileByteCounts have different lengths")

    tiles_across = _ceil_div(width, tile_width)
    tiles_down = _ceil_div(height, tile_length)
    expected_tiles = tiles_across * tiles_down
    if len(tile_offsets) != expected_tiles:
        raise DngMetadataError(f"tile count does not match image dimensions: {len(tile_offsets)} != {expected_tiles}")

    output = bytearray(width * height * bytes_per_pixel)
    for tile_index, (offset, byte_count) in enumerate(zip(tile_offsets, tile_byte_counts)):
        tile = _slice_checked(data, offset, byte_count)
        tile_row = tile_index // tiles_across
        tile_column = tile_index % tiles_across
        origin_row = tile_row * tile_length
        origin_column = tile_column * tile_width
        copy_rows = min(tile_length, height - origin_row)
        copy_columns = min(tile_width, width - origin_column)
        _copy_tile_into_rows(
            output,
            tile,
            image_width=width,
            origin_row=origin_row,
            origin_column=origin_column,
            copy_rows=copy_rows,
            copy_columns=copy_columns,
            tile_width=tile_width,
            bytes_per_pixel=bytes_per_pixel,
        )
    return bytes(output)


def _copy_tile_into_rows(
    output: bytearray,
    tile: bytes,
    *,
    image_width: int,
    origin_row: int,
    origin_column: int,
    copy_rows: int,
    copy_columns: int,
    tile_width: int,
    bytes_per_pixel: int,
) -> None:
    if copy_rows <= 0 or copy_columns <= 0:
        return
    last_needed_byte = ((copy_rows - 1) * tile_width + copy_columns) * bytes_per_pixel
    if len(tile) < last_needed_byte:
        raise DngMetadataError(f"tile payload is shorter than expected: {len(tile)} < {last_needed_byte}")

    row_bytes = copy_columns * bytes_per_pixel
    for row in range(copy_rows):
        source_start = row * tile_width * bytes_per_pixel
        target_start = ((origin_row + row) * image_width + origin_column) * bytes_per_pixel
        output[target_start : target_start + row_bytes] = tile[source_start : source_start + row_bytes]


def _ceil_div(value: int, divisor: int) -> int:
    return (value + divisor - 1) // divisor


def _require_tag_value(ifd: TiffIfd, tag_code: int, label: str) -> Any:
    tag = ifd.tags.get(tag_code)
    if tag is None:
        raise DngMetadataError(f"missing required tag: {label}")
    return tag.value


def _tuple_of_ints(value: Any) -> tuple[int, ...]:
    values = value if isinstance(value, tuple) else (value,)
    return tuple(int(item) for item in values)


def _optional_tuple_int(value: Any) -> tuple[int, ...] | None:
    if value is None:
        return None
    return _tuple_of_ints(value)


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _expect_int(summary: dict[str, Any], key: str, default: int | None = None) -> int:
    value = summary.get(key, default)
    if value is None:
        raise DngMetadataError(f"missing required metadata: {key}")
    if isinstance(value, tuple):
        if len(value) != 1:
            raise DngMetadataError(f"metadata must be scalar: {key}")
        value = value[0]
    return int(value)


def _slice_checked(data: bytes, offset: int, length: int) -> bytes:
    _require_range(data, offset, length)
    return data[offset : offset + length]
