"""Support reporting for the current OpenRAW Native DNG path."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from pathlib import Path
from typing import Any, Mapping

from openraw_studio.raw.native.bitpacking import SUPPORTED_SENSOR_BIT_DEPTHS
from openraw_studio.raw.native.dng import DngMetadataError, DngMetadataReader, TiffIfd
from openraw_studio.raw.native.nikon import (
    NIKON_COMPRESSED_RAW,
    NikonMakerNoteSummary,
    can_decode_nikon_34713_lossless,
    summarize_nikon_makernote,
)
from openraw_studio.raw.native.profiles import find_camera_color_profile


SUPPORTED_CFA_PATTERNS = {
    (0, 1, 1, 2): "RGGB",
    (1, 0, 2, 1): "GRBG",
    (1, 2, 0, 1): "GBRG",
    (2, 1, 1, 0): "BGGR",
}
NIKON_RAW_EXTENSIONS = {".nef", ".nrw"}


@dataclass(frozen=True)
class NativeSupportReport:
    """A beginner-friendly answer to whether OpenRAW Native can render a file."""

    source_path: Path
    file_exists: bool
    can_inspect: bool
    can_render: bool
    status: str
    reason: str
    can_preview: bool = False
    details: tuple[str, ...] = ()
    next_steps: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_path": str(self.source_path),
            "file_exists": self.file_exists,
            "can_inspect": self.can_inspect,
            "can_preview": self.can_preview,
            "can_render": self.can_render,
            "status": self.status,
            "reason": self.reason,
            "details": list(self.details),
            "next_steps": list(self.next_steps),
            "metadata": dict(self.metadata),
        }


def inspect_native_support(source_path: str | Path, *, dng_reader: DngMetadataReader | None = None) -> NativeSupportReport:
    """Report whether the current OpenRAW Native path can render one source."""

    path = Path(source_path).expanduser()
    try:
        if not path.exists():
            return _report(path, file_exists=False, status="missing", reason="Source file does not exist.")
        if not path.is_file():
            return _report(path, file_exists=True, status="unsupported", reason="Source path is not a file.")
    except OSError as exc:
        return _report(path, file_exists=False, status="unreadable", reason=f"Source path could not be checked: {exc}")

    suffix_lower = path.suffix.lower()
    if suffix_lower in NIKON_RAW_EXTENSIONS:
        return _inspect_nikon_raw(path, dng_reader=dng_reader)
    if suffix_lower != ".dng":
        suffix = path.suffix or "<none>"
        return _report(
            path,
            file_exists=True,
            status="unsupported",
            reason="OpenRAW Native V0.1 can currently render DNG files and import Nikon RAW metadata.",
            details=(f"File type: {suffix}",),
        )

    reader = dng_reader or DngMetadataReader()
    try:
        metadata = reader.read(path)
    except (DngMetadataError, OSError) as exc:
        return _report(
            path,
            file_exists=True,
            status="unsupported",
            reason=f"DNG metadata could not be read: {exc}",
        )

    summary = metadata.as_dict()
    issues, details = _evaluate_dng_summary(metadata.ifds, summary)
    if issues:
        return _report(
            path,
            file_exists=True,
            can_inspect=True,
            status="unsupported",
            reason=issues[0],
            details=tuple(details + issues[1:]),
            metadata=summary,
        )

    return _report(
        path,
        file_exists=True,
        can_inspect=True,
        can_preview=True,
        can_render=True,
        status="supported",
        reason="Supported by OpenRAW Native V0.1.",
        details=tuple(details),
        metadata=summary,
    )


def _report(
    path: Path,
    *,
    file_exists: bool,
    status: str,
    reason: str,
    can_inspect: bool = False,
    can_preview: bool = False,
    can_render: bool = False,
    details: tuple[str, ...] = (),
    next_steps: tuple[str, ...] = (),
    metadata: Mapping[str, Any] | None = None,
) -> NativeSupportReport:
    return NativeSupportReport(
        source_path=path,
        file_exists=file_exists,
        can_inspect=can_inspect,
        can_preview=can_preview,
        can_render=can_render,
        status=status,
        reason=reason,
        details=details,
        next_steps=next_steps,
        metadata=dict(metadata or {}),
    )


def _inspect_nikon_raw(source_path: Path, *, dng_reader: DngMetadataReader | None) -> NativeSupportReport:
    reader = dng_reader or DngMetadataReader()
    try:
        metadata = reader.read(source_path)
    except (DngMetadataError, OSError) as exc:
        return _report(
            source_path,
            file_exists=True,
            status="unsupported",
            reason=f"Nikon RAW metadata could not be read: {exc}",
        )

    summary = metadata.as_dict()
    maker_note = summarize_nikon_makernote(metadata)
    support_metadata = dict(summary)
    if maker_note is not None:
        support_metadata["nikon_makernote"] = maker_note.as_dict()
    render_issues, render_details = _evaluate_nikon_summary(metadata, source_path)
    details = _nikon_import_details(source_path, summary, render_detail=None)
    details.extend(_render_detail_extras(render_details))
    details.extend(_nikon_makernote_details(maker_note))
    camera_profile = find_camera_color_profile(
        _optional_text(summary.get("make")),
        _optional_text(summary.get("model")),
    )
    if camera_profile is not None:
        details.append(f"Color: exact native profile for {camera_profile.model} to linear sRGB")
    elif not render_issues:
        details.append("Color: exact camera profile not available; generic camera RGB rendering")
    try:
        preview = reader.read_embedded_jpeg_preview(source_path)
    except (DngMetadataError, OSError):
        embedded_preview_label = None
    else:
        embedded_preview_label = f"Preview: embedded JPEG ({len(preview.data)} bytes)"

    if not render_issues:
        if embedded_preview_label:
            details.append(embedded_preview_label)
        details.append("Preview: native sensor render")
        details.append(_nikon_render_success_detail(summary))
        return _report(
            source_path,
            file_exists=True,
            can_inspect=True,
            can_preview=True,
            can_render=True,
            status="supported",
            reason=_nikon_render_success_reason(summary),
            details=tuple(details),
            metadata=support_metadata,
        )

    blocker_details = _render_blocker_details(render_issues)
    next_steps = _nikon_next_steps(render_issues, has_embedded_preview=embedded_preview_label is not None)
    high_efficiency = maker_note is not None and maker_note.compression_mode in {13, 14}
    if high_efficiency:
        next_steps = (
            "The camera JPEG opens automatically when available; RAW adjustments and export remain unavailable.",
            "For future photos, select RAW recording > Lossless compression in the camera; "
            "existing HE/HE* files cannot be changed by changing this setting. "
            "Lossless files still need a supported sensor layout.",
        )

    if embedded_preview_label is None:
        details.append("Preview: embedded JPEG not found")
        details.extend(blocker_details)
        return _report(
            source_path,
            file_exists=True,
            can_inspect=True,
            can_preview=False,
            can_render=False,
            status="import_only",
            reason=(
                f"Nikon {maker_note.compression_name} RAW decoding is not implemented, and no embedded JPEG was found."
                if high_efficiency else
                "Nikon RAW metadata import is supported; preview/export are blocked because this file has no "
                "embedded JPEG preview and native sensor rendering is not implemented for its payload yet."
            ),
            details=tuple(details),
            next_steps=next_steps,
            metadata=support_metadata,
        )

    details.append(embedded_preview_label)
    details.extend(blocker_details)
    return _report(
        source_path,
        file_exists=True,
        can_inspect=True,
        can_preview=True,
        can_render=False,
        status="preview_only",
        reason=(
            f"Nikon {maker_note.compression_name}: camera JPEG preview only. "
            "RAW adjustments and final export are not supported for this compression yet."
            if high_efficiency else
            "Nikon RAW embedded preview is supported; final export is blocked by native sensor rendering limits."
        ),
        details=tuple(details),
        next_steps=next_steps,
        metadata=support_metadata,
    )


def _nikon_import_details(
    source_path: Path,
    summary: Mapping[str, Any],
    *,
    render_detail: str | None = "Render: NEF/NRW decoding is future work",
) -> list[str]:
    details = [f"Format: Nikon {source_path.suffix.lstrip('.').upper()}/TIFF"]
    width = _scalar_int(summary.get("width"))
    height = _scalar_int(summary.get("height"))
    if width is not None and height is not None:
        details.append(f"Dimensions: {width} x {height}")
    camera = _camera_label(summary)
    if camera:
        details.append(f"Camera: {camera}")
    iso = _scalar_int(summary.get("iso"))
    if iso is not None:
        details.append(f"ISO: {iso}")
    exposure_time = _scalar_float(summary.get("exposure_time"))
    if exposure_time is not None and exposure_time > 0:
        details.append(f"Exposure: {_format_exposure_time(exposure_time)}")
    aperture = _scalar_float(summary.get("aperture"))
    if aperture is not None and aperture > 0:
        details.append(f"Aperture: f/{aperture:g}")
    focal_length = _scalar_float(summary.get("focal_length_mm"))
    if focal_length is not None and focal_length > 0:
        details.append(f"Focal length: {focal_length:g} mm")
    if render_detail is not None:
        details.append(render_detail)
    return details


def _nikon_render_success_detail(summary: Mapping[str, Any]) -> str:
    if _scalar_int(summary.get("compression"), default=1) == NIKON_COMPRESSED_RAW:
        return "Render: native Nikon 34713 sensor decode"
    return "Render: native TIFF-style sensor decode"


def _nikon_render_success_reason(summary: Mapping[str, Any]) -> str:
    if _scalar_int(summary.get("compression"), default=1) == NIKON_COMPRESSED_RAW:
        return "Supported by OpenRAW Native Nikon 34713 sensor decode."
    return "Supported by OpenRAW Native V0.1 guarded Nikon sensor decode."


def _render_detail_extras(render_details: list[str]) -> list[str]:
    return [
        detail
        for detail in render_details
        if not detail.startswith("Format:") and not detail.startswith("Dimensions:")
    ]


def _nikon_makernote_details(maker_note: NikonMakerNoteSummary | None) -> list[str]:
    if maker_note is None:
        return []

    details = [f"MakerNote: {maker_note.kind}, {maker_note.tag_count} tags"]
    if maker_note.compression_mode is not None:
        name = f" ({maker_note.compression_name})" if maker_note.compression_name else ""
        details.append(f"Nikon compression mode tag {maker_note.compression_source}: {maker_note.compression_mode}{name}")
    if maker_note.compression_table_byte_count is not None:
        prefix = f", prefix {maker_note.compression_table_prefix}" if maker_note.compression_table_prefix else ""
        details.append(f"Nikon compression table tag 0x0096: {maker_note.compression_table_byte_count} bytes{prefix}")
    if maker_note.curve_byte_count is not None:
        prefix = f", prefix {maker_note.curve_prefix}" if maker_note.curve_prefix else ""
        details.append(f"Nikon curve/table tag 0x008c: {maker_note.curve_byte_count} bytes{prefix}")
    if maker_note.active_area is not None:
        details.append(f"Nikon active area tag 0x0045: {_join_ints(maker_note.active_area)}")
    if maker_note.as_shot_white_balance is not None:
        red, green, blue = maker_note.as_shot_white_balance
        source = f" from {maker_note.white_balance_source}" if maker_note.white_balance_source else ""
        mode = f", mode {maker_note.white_balance_mode}" if maker_note.white_balance_mode else ""
        details.append(f"Nikon as-shot white balance{source}: R {red:.3f}, G {green:.3f}, B {blue:.3f}{mode}")
    if maker_note.black_levels is not None:
        details.append(f"Nikon black levels from 0x003d: {_join_ints(maker_note.black_levels)}")
    return details


def _join_ints(values: tuple[int, ...]) -> str:
    return ", ".join(str(value) for value in values)


def _render_blocker_details(render_issues: list[str]) -> list[str]:
    if not render_issues:
        return []
    return [f"Render: {render_issues[0]}"] + [f"Render blocker: {issue}" for issue in render_issues[1:]]


def _nikon_next_steps(render_issues: list[str], *, has_embedded_preview: bool) -> tuple[str, ...]:
    if not render_issues:
        return ()

    steps = []
    if has_embedded_preview:
        steps.append("The embedded JPEG opens automatically; final export needs native sensor decoding for this file.")
    else:
        steps.append("Use Create Sample DNG/NEF or a supported uncompressed file to test rendering today.")

    primary = render_issues[0]
    if "compression" in primary.lower():
        value = _compression_value_from_issue(primary)
        compression = f" value {value}" if value else ""
        steps.append(f"Engine work needed: add Nikon compression{compression} decoding before this file can be rendered.")
    elif "No complete strip or tile pixel payload" in primary:
        steps.append("Engine work needed: locate the sensor payload IFD and offsets for this camera file.")
    elif "CFA" in primary:
        steps.append("Engine work needed: map this file's CFA pattern into the native Bayer pipeline.")
    else:
        steps.append(f"Engine work needed: {primary}")
    return tuple(steps)


def _compression_value_from_issue(issue: str) -> str | None:
    marker = "compression:"
    position = issue.lower().find(marker)
    if position < 0:
        return None
    value = issue[position + len(marker) :].split(";", 1)[0].strip()
    return value or None


def _evaluate_dng_summary(ifds: tuple[TiffIfd, ...], summary: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    return _evaluate_bayer_tiff_summary(
        ifds,
        summary,
        format_label="DNG/TIFF",
        image_label="DNG image",
        compression_label="DNG",
        compression_detail="only uncompressed DNG is supported",
    )


def _evaluate_nikon_summary(
    metadata: Any,
    source_path: Path,
) -> tuple[list[str], list[str]]:
    summary = metadata.as_dict()
    issues, details = _evaluate_bayer_tiff_summary(
        metadata.ifds,
        summary,
        format_label=f"Nikon {source_path.suffix.lstrip('.').upper()}/TIFF",
        image_label="Nikon RAW image",
        compression_label="Nikon RAW",
        compression_detail="only uncompressed TIFF-style sensor data is supported",
    )
    compression = _scalar_int(summary.get("compression"), default=1)
    maker_note = summarize_nikon_makernote(metadata)
    if compression == NIKON_COMPRESSED_RAW and maker_note and maker_note.compression_mode in {13, 14}:
        issues = [issue for issue in issues if "compression" not in issue.lower()]
        issues.insert(0, f"Nikon {maker_note.compression_name} compression requires a separate sensor decoder.")
    if compression == NIKON_COMPRESSED_RAW and can_decode_nikon_34713_lossless(metadata):
        issues = [issue for issue in issues if "compression" not in issue.lower()]
        issues = [issue for issue in issues if issue != "Missing scalar black level."]
        issues = [issue for issue in issues if issue != "Missing scalar white level."]
        details.append(
            "Compression: Nikon 34713 Huffman (F lossless / 12-bit D20 non-split)"
        )
        details.append(
            "Levels: native sample range with MakerNote black level or inactive-border fallback"
        )
        details.append("Render: native Nikon 34713 sensor decode")
    return issues, details


def _evaluate_bayer_tiff_summary(
    ifds: tuple[TiffIfd, ...],
    summary: Mapping[str, Any],
    *,
    format_label: str,
    image_label: str,
    compression_label: str,
    compression_detail: str,
) -> tuple[list[str], list[str]]:
    issues: list[str] = []
    details: list[str] = [f"Format: {format_label}"]

    width = _scalar_int(summary.get("width"))
    height = _scalar_int(summary.get("height"))
    if width is not None and height is not None:
        details.append(f"Dimensions: {width} x {height}")
    else:
        issues.append(f"{image_label} dimensions are missing or not scalar.")

    compression = _scalar_int(summary.get("compression"), default=1)
    if compression == 1:
        details.append("Compression: uncompressed")
    else:
        issues.append(f"Unsupported {compression_label} compression: {compression}; {compression_detail}.")

    bits_per_sample = _scalar_int(summary.get("bits_per_sample"))
    if bits_per_sample in SUPPORTED_SENSOR_BIT_DEPTHS:
        details.append(f"Bit depth: {bits_per_sample}-bit")
    else:
        issues.append(f"Unsupported BitsPerSample: {bits_per_sample}; only 12-bit, 14-bit, or 16-bit data is supported.")

    samples_per_pixel = _scalar_int(summary.get("samples_per_pixel"), default=1)
    if samples_per_pixel == 1:
        details.append("Samples: single-sample Bayer")
    else:
        issues.append(f"Unsupported SamplesPerPixel: {samples_per_pixel}; only single-sample Bayer data is supported.")

    pixel_ifd = _select_pixel_ifd(ifds)
    if pixel_ifd is None:
        issues.append("No complete strip or tile pixel payload was found.")
    elif _has_strip_payload(pixel_ifd):
        issues.extend(_check_strip_payload(pixel_ifd, details))
    else:
        issues.extend(_check_tile_payload(pixel_ifd, details, width=width, height=height, bits_per_sample=bits_per_sample))

    cfa_pattern = _tuple_int(summary.get("cfa_pattern"))
    cfa_name = SUPPORTED_CFA_PATTERNS.get(cfa_pattern)
    if cfa_name:
        details.append(f"CFA: {cfa_name}")
    else:
        issues.append("Unsupported or missing CFA pattern.")

    black_level = _scalar_float(summary.get("black_level"))
    white_level = _scalar_float(summary.get("white_level"))
    if black_level is None:
        issues.append("Missing scalar black level.")
    if white_level is None:
        issues.append("Missing scalar white level.")
    if black_level is not None and white_level is not None:
        if white_level <= black_level:
            issues.append("White level must be greater than black level.")
        else:
            details.append(f"Levels: black {black_level:g}, white {white_level:g}")

    return issues, details


def _select_pixel_ifd(ifds: tuple[TiffIfd, ...]) -> TiffIfd | None:
    candidates = [ifd for ifd in ifds if _has_strip_payload(ifd) or _has_tile_payload(ifd)]
    if not candidates:
        return None
    return max(candidates, key=_ifd_area)


def _ifd_area(ifd: TiffIfd) -> int:
    width = _scalar_int(_tag_value(ifd, 256), default=0) or 0
    height = _scalar_int(_tag_value(ifd, 257), default=0) or 0
    return width * height


def _has_strip_payload(ifd: TiffIfd) -> bool:
    return 273 in ifd.tags and 279 in ifd.tags


def _has_tile_payload(ifd: TiffIfd) -> bool:
    return 322 in ifd.tags and 323 in ifd.tags and 324 in ifd.tags and 325 in ifd.tags


def _check_strip_payload(ifd: TiffIfd, details: list[str]) -> list[str]:
    offsets = _tuple_int(_tag_value(ifd, 273))
    byte_counts = _tuple_int(_tag_value(ifd, 279))
    if not offsets or not byte_counts:
        return ["Strip pixel payload tags are empty or invalid."]
    if len(offsets) != len(byte_counts):
        return ["StripOffsets and StripByteCounts have different lengths."]
    details.append(f"Storage: {len(offsets)} {_plural('strip', len(offsets))}")
    return []


def _check_tile_payload(
    ifd: TiffIfd,
    details: list[str],
    *,
    width: int | None,
    height: int | None,
    bits_per_sample: int | None,
) -> list[str]:
    issues: list[str] = []
    tile_width = _scalar_int(_tag_value(ifd, 322))
    tile_length = _scalar_int(_tag_value(ifd, 323))
    offsets = _tuple_int(_tag_value(ifd, 324))
    byte_counts = _tuple_int(_tag_value(ifd, 325))
    if tile_width is None or tile_length is None or tile_width <= 0 or tile_length <= 0:
        issues.append("TileWidth and TileLength must be scalar values greater than zero.")
    if not offsets or not byte_counts:
        issues.append("Tile pixel payload tags are empty or invalid.")
    elif len(offsets) != len(byte_counts):
        issues.append("TileOffsets and TileByteCounts have different lengths.")
    elif width is not None and height is not None and tile_width is not None and tile_length is not None:
        expected_tiles = _ceil_div(width, tile_width) * _ceil_div(height, tile_length)
        if len(offsets) != expected_tiles:
            issues.append(f"Tile count does not match image dimensions: {len(offsets)} != {expected_tiles}.")
    if bits_per_sample in {12, 14}:
        issues.append("Packed 12/14-bit tiled payloads are not supported yet.")
    if not issues:
        details.append(f"Storage: {len(offsets)} {_plural('tile', len(offsets))} ({tile_width} x {tile_length})")
    return issues


def _tag_value(ifd: TiffIfd, tag_code: int) -> Any:
    tag = ifd.tags.get(tag_code)
    return None if tag is None else tag.value


def _scalar_int(value: Any, *, default: int | None = None) -> int | None:
    if value is None:
        return default
    if isinstance(value, bool):
        return None
    if isinstance(value, tuple):
        if len(value) != 1:
            return None
        value = value[0]
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return int(number)


def _scalar_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, tuple):
        if len(value) != 1:
            return None
        value = value[0]
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _tuple_int(value: Any) -> tuple[int, ...]:
    if value is None or isinstance(value, bool):
        return ()
    values = value if isinstance(value, tuple) else (value,)
    try:
        return tuple(int(item) for item in values if not isinstance(item, bool))
    except (TypeError, ValueError):
        return ()


def _ceil_div(value: int, divisor: int) -> int:
    return (value + divisor - 1) // divisor


def _camera_label(summary: Mapping[str, Any]) -> str | None:
    unique = summary.get("unique_camera_model")
    if isinstance(unique, str) and unique.strip():
        return unique.strip()
    parts = [
        value.strip()
        for value in (summary.get("make"), summary.get("model"))
        if isinstance(value, str) and value.strip()
    ]
    return " ".join(parts) if parts else None


def _optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _format_exposure_time(seconds: float) -> str:
    if seconds <= 0:
        return f"{seconds:g}s"
    if seconds < 1:
        denominator = round(1 / seconds)
        return f"1/{denominator}s" if denominator > 1 else f"{seconds:g}s"
    return f"{seconds:g}s"


def _plural(word: str, count: int) -> str:
    return word if count == 1 else f"{word}s"
