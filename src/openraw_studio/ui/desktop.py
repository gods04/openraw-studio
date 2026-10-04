"""Small local desktop shell for the OpenRAW Studio V0.1 pipeline."""

from __future__ import annotations

import json
from io import BytesIO
import math
import os
import queue
from pathlib import Path, PurePosixPath, PureWindowsPath
import subprocess
import sys
import threading
from typing import Any, Mapping, Sequence

from openraw_studio.core.artifacts import ArtifactPlan
from openraw_studio.core.files import is_supported_raw_path
from openraw_studio.core.recipe import validate_recipe_shape
from openraw_studio.decision.auto_adjust import (
    AutoAdjustSuggestion,
    suggest_auto_adjustments_for_photo,
)
from openraw_studio.decision.color_noise import suggest_color_noise_for_photo
from openraw_studio.raw.native.interactive import prepare_interactive_photo
from openraw_studio.export.formats import (
    export_display_name,
    normalize_export_format,
    validate_export_bit_depth,
    validate_export_quality,
)
from openraw_studio.pipeline.batch import BatchItemResult, BatchResult, run_batch_export
from openraw_studio.pipeline.errors import BackendUnavailableError, PipelineError, SourceFileError
from openraw_studio.pipeline.interfaces import PipelineRequest
from openraw_studio.pipeline.local import LocalPhotoPipeline
from openraw_studio.qc.histogram import HistogramAnalysis, analyze_rgb_bytes
from openraw_studio.raw.native.dng import DngMetadataReader
from openraw_studio.raw.native.support import NativeSupportReport, inspect_native_support
from openraw_studio.raw.native.synthetic import write_synthetic_dng, write_synthetic_nikon_nef
from openraw_studio.ui.live_preview import LivePreviewWorker
from openraw_studio.ui.viewport import DetailView
from openraw_studio.ui.editing import EditHistory, SessionStore, clean_adjustments


MAX_LIBRARY_FILES = 200
NIKON_RAW_EXTENSIONS = {".nef", ".nrw"}


def _format_exposure_label(value: float) -> str:
    """Return a compact photo-editor style exposure label."""

    rounded = round(value, 1)
    if abs(rounded) < 0.05:
        return "0.0 EV"
    return f"{rounded:+.1f} EV"


def _format_adjustment_label(value: float) -> str:
    amount = int(round(value * 100.0))
    if amount == 0:
        return "0"
    return f"{amount:+d}"


def _histogram_status_text(analysis: HistogramAnalysis | None, *, view: str) -> str:
    if analysis is None:
        return "No histogram yet"
    if analysis.highlight_clipped_pixels == 0 and analysis.shadow_clipped_pixels == 0:
        return f"{view}: no clipped pixels"
    return (
        f"{view}: Highlights {analysis.highlight_clip_fraction:.1%} | "
        f"Shadows {analysis.shadow_clip_fraction:.1%}"
    )


def _histogram_coordinates(
    counts: Sequence[int],
    *,
    width: int,
    height: int,
    peak: int | None = None,
) -> tuple[float, ...]:
    if not counts:
        raise ValueError("histogram counts cannot be empty")
    if width < 2 or height < 2:
        raise ValueError("histogram dimensions must be at least two pixels")
    if any(count < 0 for count in counts):
        raise ValueError("histogram counts cannot be negative")
    resolved_peak = max(counts) if peak is None else peak
    if resolved_peak < max(counts):
        raise ValueError("histogram peak cannot be below the largest count")

    x_step = (width - 1) / max(1, len(counts) - 1)
    baseline = float(height - 1)
    log_peak = math.log1p(resolved_peak)
    points: list[float] = []
    for index, count in enumerate(counts):
        height_ratio = math.log1p(count) / log_peak if log_peak else 0.0
        points.extend((index * x_step, baseline - (height_ratio * baseline)))
    return tuple(points)


def _analyze_pillow_preview(image: Any, *, max_dimension: int = 512) -> HistogramAnalysis:
    sample = image.copy()
    sample.thumbnail((max_dimension, max_dimension))
    if sample.mode != "RGB":
        sample = sample.convert("RGB")
    return analyze_rgb_bytes(sample.tobytes())


def _load_embedded_camera_preview(
    source: Path,
    image_module: Any,
    *,
    max_size: tuple[int, int] = (700, 520),
) -> tuple[Any, HistogramAnalysis]:
    embedded = DngMetadataReader().read_embedded_jpeg_preview(source)
    with image_module.open(BytesIO(embedded.data)) as opened:
        image = opened.convert("RGB")
    histogram = _analyze_pillow_preview(image)
    image.thumbnail(max_size)
    return image, histogram


def _format_bytes(size: int) -> str:
    if size < 1024:
        return f"{size} B"

    amount = float(size)
    for unit in ("KB", "MB", "GB", "TB"):
        amount /= 1024.0
        if amount < 1024.0 or unit == "TB":
            return f"{amount:.1f} {unit}"
    return f"{amount:.1f} TB"


def _short_path(path: Path, *, max_chars: int = 72) -> str:
    text = str(path)
    if len(text) <= max_chars:
        return text
    if max_chars <= 3:
        return "..."[:max_chars]
    return "..." + text[-(max_chars - 3) :]


def _display_path(path: Path, *, base: Path | None = None, max_chars: int = 72) -> str:
    if base is not None:
        try:
            return str(path.relative_to(base))
        except ValueError:
            pass
    return _short_path(path, max_chars=max_chars)


def _format_metadata_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or None
    if isinstance(value, tuple):
        return ", ".join(str(item) for item in value)
    return str(value)


def _format_photo_info(path: Path, metadata: Mapping[str, Any], *, size_bytes: int | None = None) -> str:
    lines = [f"File: {path.name}"]

    width = metadata.get("width")
    height = metadata.get("height")
    if width is not None and height is not None:
        lines.append(f"Dimensions: {int(width)} x {int(height)}")

    camera = _format_metadata_value(metadata.get("unique_camera_model"))
    if camera is None:
        make = _format_metadata_value(metadata.get("make"))
        model = _format_metadata_value(metadata.get("model"))
        camera = " ".join(part for part in (make, model) if part)
    if camera:
        lines.append(f"Camera: {camera}")

    bits_per_sample = metadata.get("bits_per_sample")
    if bits_per_sample is not None:
        lines.append(f"RAW: {int(bits_per_sample)}-bit")
    elif path.suffix:
        lines.append(f"Type: {path.suffix.lstrip('.').upper()}")

    iso = metadata.get("iso")
    if iso is not None:
        lines.append(f"ISO: {int(iso)}")

    exposure_time = _metadata_float(metadata.get("exposure_time"))
    if exposure_time is not None and exposure_time > 0:
        lines.append(f"Shutter: {_format_shutter_speed(exposure_time)}")

    aperture = _metadata_float(metadata.get("aperture"))
    if aperture is not None and aperture > 0:
        lines.append(f"Aperture: f/{aperture:g}")

    focal_length = _metadata_float(metadata.get("focal_length_mm"))
    if focal_length is not None and focal_length > 0:
        lines.append(f"Focal: {focal_length:g} mm")

    lens = _format_metadata_value(metadata.get("lens_model"))
    if lens:
        lines.append(f"Lens: {lens}")

    version = _format_metadata_value(metadata.get("dng_version_text"))
    if version:
        lines.append(f"DNG: {version}")

    if size_bytes is not None:
        lines.append(f"Size: {_format_bytes(size_bytes)}")

    return "\n".join(lines)


def _metadata_float(value: Any) -> float | None:
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
    return number if math.isfinite(number) else None


def _format_shutter_speed(seconds: float) -> str:
    if seconds <= 0:
        return f"{seconds:g}s"
    if seconds < 1:
        denominator = round(1 / seconds)
        return f"1/{denominator}s" if denominator > 1 else f"{seconds:g}s"
    return f"{seconds:g}s"


def _read_photo_info(path: Path) -> str:
    info, _support = _read_photo_info_with_support(path)
    return info


def _read_photo_info_with_support(path: Path) -> tuple[str, NativeSupportReport]:
    size_bytes = path.stat().st_size
    support = inspect_native_support(path)
    info = _format_photo_info(path, support.metadata, size_bytes=size_bytes) + "\n" + _format_native_support_summary(support)
    return info, support


def _format_native_support_summary(report: NativeSupportReport) -> str:
    next_step = f"\nNext: {report.next_steps[0]}" if report.next_steps else ""
    if report.can_render:
        return "Support: Supported by OpenRAW Native V0.1"
    if report.can_preview:
        return f"Support: Preview supported; export not supported yet\nReason: {report.reason}{next_step}"
    if report.can_inspect:
        return f"Support: Import supported; preview/export not supported yet\nReason: {report.reason}{next_step}"
    return f"Support: Not supported yet\nReason: {report.reason}{next_step}"


def _candidate_raw_files(folder: Path, *, limit: int = MAX_LIBRARY_FILES) -> tuple[Path, ...]:
    if not folder.exists():
        raise FileNotFoundError(f"Folder does not exist: {folder}")
    if not folder.is_dir():
        raise NotADirectoryError(f"Path is not a folder: {folder}")
    candidates = sorted(
        (path for path in folder.iterdir() if path.is_file() and is_supported_raw_path(path)),
        key=lambda path: path.name.lower(),
    )
    return tuple(candidates[:limit])


def _library_item_label(path: Path, report: NativeSupportReport) -> str:
    status = "OK" if report.can_render else "PREVIEW" if report.can_preview else "IMPORT" if report.can_inspect else "NO"
    return f"[{status}] {path.name}"


def _scan_library_folder(folder: Path, *, limit: int = MAX_LIBRARY_FILES) -> tuple[tuple[Path, str, bool], ...]:
    return tuple(
        (path, _library_item_label(path, report), report.can_render)
        for path in _candidate_raw_files(folder, limit=limit)
        for report in (inspect_native_support(path),)
    )


def _folder_status_text(folder: Path, item_count: int, *, limit: int = MAX_LIBRARY_FILES) -> str:
    if item_count == 0:
        return f"No RAW files found in {_short_path(folder, max_chars=46)}"
    suffix = f"Showing first {limit}" if item_count >= limit else f"{item_count}"
    return f"{suffix} RAW files in {_short_path(folder, max_chars=46)}"


def _supported_library_sources(items: Sequence[tuple[Path, str, bool]]) -> tuple[Path, ...]:
    return tuple(path for path, _label, can_render in items if can_render)


def _library_sources(items: Sequence[tuple[Path, str, bool]]) -> tuple[Path, ...]:
    return tuple(path for path, _label, _can_render in items)


def _batch_progress_text(done: int, total: int, item: BatchItemResult) -> str:
    return f"Batch {done}/{total}: {item.status} {item.source_path.name}"


def _batch_result_status(result: BatchResult) -> str:
    if result.total == 0:
        return "No RAW files to export"
    if result.cancelled:
        return f"Batch stopped: {result.processed} processed, {result.cancelled} cancelled"
    if result.failed:
        return f"Batch finished with {result.failed} failed, {result.processed} processed, {result.skipped} skipped"
    return f"Batch finished: {result.processed} processed, {result.skipped} skipped"


def _format_batch_result_summary(result: BatchResult, *, limit: int = 6) -> str:
    lines = [
        f"Batch summary: {result.processed} processed, {result.skipped} skipped, {result.failed} failed",
    ]
    for item in result.items[:limit]:
        target = item.export_path or item.preview_path or item.recipe_path
        if target is not None:
            lines.append(f"{item.status.capitalize()}: {item.source_path.name} -> {target}")
        else:
            lines.append(f"{item.status.capitalize()}: {item.source_path.name} -> {item.message}")
    remaining = max(0, result.total - limit)
    if remaining:
        lines.append(f"...and {remaining} more")
    return "\n".join(lines)


def _planned_output_summary(source: Path, output_dir: Path, *, export_format: str = "jpeg") -> str:
    resolved_format = normalize_export_format(export_format)
    plan = ArtifactPlan.for_source(source, output_dir, export_format=resolved_format)
    preview_path = _preview_artifact_path(source, plan)
    return "\n".join(
        [
            f"Folder: {_short_path(plan.output_dir, max_chars=68)}",
            f"Preview: {_display_path(preview_path, base=plan.output_dir)}",
            f"{export_display_name(resolved_format)}: {_display_path(plan.export_path, base=plan.output_dir)}",
            f"Recipe: {_display_path(plan.recipe_path, base=plan.output_dir)}",
        ]
    )


def _preview_artifact_path(source: Path, plan: ArtifactPlan) -> Path:
    if source.suffix.lower() in NIKON_RAW_EXTENSIONS:
        try:
            support = inspect_native_support(source)
        except OSError:
            support = None
        if support is not None and support.can_render:
            return plan.preview_path
        return plan.preview_path.with_name(f"{source.stem}.preview.jpg")
    return plan.preview_path


def _path_name_from_recipe(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if "\\" in text or ":" in text:
        return PureWindowsPath(text).name
    return PurePosixPath(text).name


def _recipe_source_matches(recipe: Mapping[str, Any], source: Path) -> bool:
    recipe_source = recipe.get("source")
    if not isinstance(recipe_source, Mapping):
        return False
    saved_name = _path_name_from_recipe(recipe_source.get("path"))
    return saved_name == source.name


def _clamped_recipe_float(value: Any, *, default: float, minimum: float, maximum: float) -> float:
    if isinstance(value, bool):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return max(minimum, min(maximum, number))


def _recipe_adjustment_overrides(recipe: Mapping[str, Any]) -> dict[str, float]:
    adjustments = recipe.get("adjustments")
    raw = adjustments.get("raw", {}) if isinstance(adjustments, Mapping) else {}
    raw = raw if isinstance(raw, Mapping) else {}
    return {
        "exposure": _clamped_recipe_float(raw.get("exposure"), default=0.0, minimum=-2.0, maximum=2.0),
        "contrast": _clamped_recipe_float(raw.get("contrast"), default=0.0, minimum=-1.0, maximum=1.0),
        "highlights": _clamped_recipe_float(raw.get("highlights"), default=0.0, minimum=-1.0, maximum=1.0),
        "shadows": _clamped_recipe_float(raw.get("shadows"), default=0.0, minimum=-1.0, maximum=1.0),
        "warmth": _clamped_recipe_float(raw.get("warmth"), default=0.0, minimum=-1.0, maximum=1.0),
        "tint": _clamped_recipe_float(raw.get("tint"), default=0.0, minimum=-1.0, maximum=1.0),
        "saturation": _clamped_recipe_float(raw.get("saturation"), default=0.0, minimum=-1.0, maximum=1.0),
        "color_noise": _clamped_recipe_float(raw.get("color_noise"), default=0.0, minimum=0.0, maximum=1.0),
        "luminance_noise": _clamped_recipe_float(raw.get("luminance_noise"), default=0.0, minimum=0.0, maximum=1.0),
    }


def _load_recipe_adjustments(recipe_path: Path, source: Path) -> dict[str, float]:
    recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
    if not isinstance(recipe, Mapping):
        raise ValueError("recipe file must contain a JSON object")
    validate_recipe_shape(recipe)
    if not _recipe_source_matches(recipe, source):
        raise ValueError("recipe does not match the selected photo")
    return _recipe_adjustment_overrides(recipe)


def _recipe_export_options(
    recipe: Mapping[str, Any], *, include_bit_depth: bool = False
) -> tuple[str, int] | tuple[str, int, int]:
    output = recipe.get("output")
    output = output if isinstance(output, Mapping) else {}
    format_value = output.get("format", "jpeg")
    try:
        export_format = normalize_export_format(format_value if isinstance(format_value, str) else "jpeg")
    except ValueError:
        export_format = "jpeg"
    try:
        export_quality = validate_export_quality(int(output.get("quality") or 92))
    except (TypeError, ValueError):
        export_quality = 92
    if include_bit_depth:
        try:
            bit_depth = validate_export_bit_depth(output.get("bit_depth", 8), export_format=export_format)
        except ValueError:
            bit_depth = 8
        return export_format, export_quality, bit_depth
    return export_format, export_quality


def _load_recipe_export_options(
    recipe_path: Path, source: Path, *, include_bit_depth: bool = False
) -> tuple[str, int] | tuple[str, int, int]:
    recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
    if not isinstance(recipe, Mapping):
        raise ValueError("recipe file must contain a JSON object")
    validate_recipe_shape(recipe)
    if not _recipe_source_matches(recipe, source):
        raise ValueError("recipe does not match the selected photo")
    return _recipe_export_options(recipe, include_bit_depth=include_bit_depth)


def _flatten_rgb_pixels(pixels: tuple[tuple[int, int, int], ...]) -> bytes:
    return bytes(channel for pixel in pixels for channel in pixel)


def _format_image_artifact(image: Any) -> str:
    details: list[str] = []
    if image.width > 0 and image.height > 0:
        details.append(f"{image.width} x {image.height}")
    try:
        size_bytes = image.path.stat().st_size
    except OSError:
        pass
    else:
        details.append(_format_bytes(size_bytes))
    suffix = f" ({', '.join(details)})" if details else ""
    return f"{image.path}{suffix}"


def _format_result_summary(result: Any) -> str:
    lines: list[str] = []
    if result.preview is not None:
        preview_label = "Preview JPEG" if result.preview.color_space == "embedded-jpeg" else "Preview"
        lines.append(f"{preview_label}: {_format_image_artifact(result.preview)}")
    if result.exports:
        lines.append(f"{export_display_name(_result_export_format(result))}: {_format_image_artifact(result.exports[0])}")
    if recipe_path := result.diagnostics.get("recipe_path"):
        lines.append(f"Recipe: {recipe_path}")
    if quality_summary := _format_quality_summary(result.recipe):
        lines.append(quality_summary)
    return "\n".join(lines)


def _format_quality_summary(recipe: Mapping[str, Any]) -> str | None:
    analysis = recipe.get("analysis")
    if not isinstance(analysis, Mapping):
        return None
    quality = analysis.get("quality")
    if not isinstance(quality, Mapping) or quality.get("status") not in {"pass", "warning"}:
        return None
    try:
        highlights = float(quality.get("highlight_clip_fraction", 0.0))
        shadows = float(quality.get("shadow_clip_fraction", 0.0))
    except (TypeError, ValueError):
        return None
    warning = " - check clipping" if quality.get("status") == "warning" else " - passed"
    return f"Quality: Highlights {highlights:.1%} | Shadows {shadows:.1%}{warning}"


def _result_export_format(result: Any) -> str:
    exports = result.recipe.get("exports") if isinstance(result.recipe, Mapping) else None
    if isinstance(exports, Sequence) and exports and isinstance(exports[0], Mapping):
        value = exports[0].get("format")
        if isinstance(value, str):
            try:
                return normalize_export_format(value)
            except ValueError:
                pass
    if result.exports:
        suffix = result.exports[0].path.suffix.lower()
        if suffix in {".tif", ".tiff"}:
            return "tiff"
    return "jpeg"


def _open_export_target(result: Any) -> tuple[Path | None, str]:
    if result.exports:
        return result.exports[0].path, f"Open {export_display_name(_result_export_format(result))}"
    if result.preview is not None and result.preview.color_space == "embedded-jpeg":
        return result.preview.path, "Open Preview JPEG"
    return None, "Open Export"


def _open_jpeg_target(result: Any) -> tuple[Path | None, str]:
    """Backward-compatible helper name retained for existing integrations."""

    return _open_export_target(result)


def _can_build_inline_before_preview(result: Any) -> bool:
    if result.preview is None:
        return False
    return result.preview.color_space not in {"embedded-jpeg", "openraw-nikon-34713-rgb"}


def _can_use_embedded_camera_preview(result: Any) -> bool:
    return (
        result.preview is not None
        and result.preview.color_space == "openraw-nikon-34713-rgb"
    )


def _result_status(result: Any) -> str:
    if result.diagnostics.get("preview_only"):
        if result.preview is not None and result.preview.color_space == "embedded-jpeg":
            return "Preview JPEG ready"
        status = "Preview updated"
    elif result.exports:
        status = f"{export_display_name(_result_export_format(result))} exported"
    else:
        status = "Finished"
    qc = result.recipe.get("qc") if isinstance(result.recipe, Mapping) else None
    if isinstance(qc, Mapping) and qc.get("status") == "warning":
        return f"{status} - check clipping"
    return status


def _auto_adjust_status(suggestion: AutoAdjustSuggestion) -> str:
    return (
        "Auto Adjust applied: "
        f"{_format_exposure_label(suggestion.exposure)}, "
        f"Contrast {_format_adjustment_label(suggestion.contrast)}, "
        f"Highlights {_format_adjustment_label(suggestion.highlights)}, "
        f"Shadows {_format_adjustment_label(suggestion.shadows)}, "
        f"Temperature {_format_adjustment_label(suggestion.warmth)}, "
        f"Tint {_format_adjustment_label(suggestion.tint)}, "
        f"Saturation {_format_adjustment_label(suggestion.saturation)}"
    )


def _manual_overrides(
    exposure: float,
    contrast: float,
    highlights: float,
    shadows: float,
    warmth: float,
    tint: float,
    saturation: float,
    color_noise: float = 0.0,
    luminance_noise: float = 0.0,
) -> dict[str, float]:
    return {
        "exposure": float(exposure),
        "contrast": float(contrast),
        "highlights": float(highlights),
        "shadows": float(shadows),
        "warmth": float(warmth),
        "tint": float(tint),
        "saturation": float(saturation),
        "color_noise": float(color_noise),
        "luminance_noise": float(luminance_noise),
    }


def _adjustments_match(
    rendered: Mapping[str, float] | None,
    current: Mapping[str, float],
    *,
    tolerance: float = 0.0001,
) -> bool:
    if rendered is None:
        return False
    for key in ("exposure", "contrast", "highlights", "shadows", "warmth", "tint", "saturation", "color_noise", "luminance_noise"):
        if abs(float(rendered.get(key, 0.0)) - float(current.get(key, 0.0))) > tolerance:
            return False
    return True


def _preview_state_text(rendered: Mapping[str, float] | None, current: Mapping[str, float]) -> str:
    if rendered is None:
        return "No preview yet"
    if _adjustments_match(rendered, current):
        return "Preview current"
    return "Preview needs update"


def _default_sample_path(home: Path | None = None) -> Path:
    root = home or Path.home()
    return root / "Pictures" / "OpenRAW Studio Samples" / "openraw-synthetic.DNG"


def _default_sample_nikon_nef_path(home: Path | None = None) -> Path:
    root = home or Path.home()
    return root / "Pictures" / "OpenRAW Studio Samples" / "openraw-synthetic-nikon.NEF"


def _friendly_error_message(error: BaseException) -> str:
    message = str(error)
    if isinstance(error, SourceFileError):
        if "Unsupported RAW extension" in message:
            return "This file type is not supported yet. OpenRAW Studio V0.1 can render supported DNG files, import Nikon RAW metadata, and preview Nikon RAW files that include embedded JPEGs."
        if "Source file does not exist" in message:
            return "The selected photo could not be found. It may have been moved or deleted."
    if isinstance(error, BackendUnavailableError):
        if (
            "only uncompressed strips are supported" in message
            or "only uncompressed strips or tiles are supported" in message
        ):
            return "This DNG uses a structure that OpenRAW Native does not support yet. Try the built-in sample DNG for the current V0.1 path."
        if "Nikon RAW embedded preview" in message or "Nikon preview failed" in message:
            return "Nikon RAW metadata import is ready, but this file does not include a readable embedded preview yet. Some Nikon 34713 lossless files can already export; this specific file still needs native support."
        if "Nikon RAW metadata" in message or "NEF/NRW" in message:
            return "Nikon RAW preview import is ready. Some Nikon 34713 lossless files can already export; this specific file still needs native support."
        if "currently starts with DNG files" in message:
            return "Nikon RAW preview import is ready. Some Nikon 34713 lossless files can already export; this specific file still needs native support."
        return "OpenRAW Native could not render this photo yet. A recipe may still have been written in the output folder."
    if isinstance(error, OSError):
        return "OpenRAW Studio could not read or write one of the selected files. Check the folder permissions and try again."
    if isinstance(error, ValueError):
        return message
    return message or "OpenRAW Studio could not finish processing this photo."


def _open_in_system(path: Path) -> None:
    if os.name == "nt":
        os.startfile(path)  # type: ignore[attr-defined]
    elif os.name == "posix":
        command = "open" if sys.platform == "darwin" else "xdg-open"
        subprocess.Popen([command, str(path)])


def launch_desktop_app(*, run_mainloop: bool = True, session_dir: Path | None = None) -> Any:
    """Launch the first beginner-facing desktop workflow."""

    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    class DesktopApp:
        def __init__(self, root: Any) -> None:
            self.root = root
            self.root.title("OpenRAW Studio")
            self.root.geometry("1280x820")
            self.root.minsize(800, 560)
            self.source_path: Path | None = None
            self.output_dir: Path | None = None
            self.preview_photo: Any = None
            self.before_photo: Any = None
            self.after_photo: Any = None
            self.before_histogram: HistogramAnalysis | None = None
            self.after_histogram: HistogramAnalysis | None = None
            self.current_histogram: HistogramAnalysis | None = None
            self.before_view_name = "Before"
            self.library_dir: Path | None = None
            self.library_items: list[tuple[Path, str, bool]] = []
            self.current_can_preview: bool | None = None
            self.current_can_render: bool | None = None
            self.library_scan_counter = 0
            self.showing_after = True
            self.last_export_path: Path | None = None
            self.last_preview_overrides: dict[str, float] | None = None
            self.run_counter = 0
            self.is_busy = False
            self.pipeline = LocalPhotoPipeline()
            self.live_worker = LivePreviewWorker(self.pipeline.raw_processor)
            self.live_revision = 0
            self.live_after_id = None
            self.histogram_after_id = None
            self.live_poll_id = None
            self.last_saved_preview_overrides = None
            self.last_live_latency_ms = None
            self.live_image = None
            self.detail_frame = None
            self.detail_full_size = None
            self.detail_anchor = (0.5, 0.5)
            self.source_orientation = 1
            self.preview_only_name = "Camera JPEG"
            self.reference_image = None
            self.resize_after_id = None
            self.info_width = 700
            self.history = EditHistory()
            self.session_store = SessionStore(session_dir)
            self.edit_after_id = None
            self.last_auto_suggestion = None
            self.last_noise_suggestion = None
            self.pan_origin = None
            self.pan_offset = [0.0, 0.0]
            self.callbacks = queue.SimpleQueue()
            self.closing = False
            self.batch_cancel = threading.Event()
            self.batch_running = False
            self.root.protocol("WM_DELETE_WINDOW", self._close)

            self.source_var = tk.StringVar(value="No RAW photo selected")
            self.output_var = tk.StringVar(
                value="Output folder will be chosen automatically"
            )
            self.library_status_var = tk.StringVar(
                value="Import a folder to browse photos"
            )
            self.photo_info_var = tk.StringVar(value="No photo selected")
            self.output_info_var = tk.StringVar(
                value="Output plan appears after import"
            )
            self.photo_info_display_var = tk.StringVar(value="No photo selected")
            self.output_info_display_var = tk.StringVar(
                value="Output plan appears after import"
            )
            self.details_var = tk.BooleanVar(value=False)
            self.edit_status_var = tk.StringVar(value="")
            self.zoom_var = tk.StringVar(value="Fit")
            self.view_var = tk.StringVar(value="Edited")
            self.auto_strength_var = tk.DoubleVar(value=70)
            self.auto_strength_label_var = tk.StringVar(value="70%")
            self.auto_summary_var = tk.StringVar(value="")
            self.support_notice_var = tk.StringVar(value="")
            self.batch_mode_var = tk.StringVar(value="Current adjustments")
            self.status_var = tk.StringVar(value="Choose a RAW photo to begin")
            self.preview_state_var = tk.StringVar(value="No preview yet")
            self.histogram_status_var = tk.StringVar(value="No histogram yet")
            self.exposure_var = tk.DoubleVar(value=0.0)
            self.contrast_var = tk.DoubleVar(value=0.0)
            self.highlights_var = tk.DoubleVar(value=0.0)
            self.shadows_var = tk.DoubleVar(value=0.0)
            self.warmth_var = tk.DoubleVar(value=0.0)
            self.tint_var = tk.DoubleVar(value=0.0)
            self.saturation_var = tk.DoubleVar(value=0.0)
            self.color_noise_var = tk.DoubleVar(value=0.0)
            self.color_noise_label_var = tk.StringVar(value="0")
            self.luminance_noise_var = tk.DoubleVar(value=0.0)
            self.luminance_noise_label_var = tk.StringVar(value="0")
            self.export_format_var = tk.StringVar(value="JPEG")
            self.export_bit_depth_var = tk.IntVar(value=8)
            self.jpeg_quality_var = tk.DoubleVar(value=92.0)
            self.jpeg_quality_label_var = tk.StringVar(value="92")
            self.exposure_label_var = tk.StringVar(value=_format_exposure_label(0.0))
            self.contrast_label_var = tk.StringVar(value=_format_adjustment_label(0.0))
            self.highlights_label_var = tk.StringVar(
                value=_format_adjustment_label(0.0)
            )
            self.shadows_label_var = tk.StringVar(value=_format_adjustment_label(0.0))
            self.warmth_label_var = tk.StringVar(value=_format_adjustment_label(0.0))
            self.tint_label_var = tk.StringVar(value=_format_adjustment_label(0.0))
            self.saturation_label_var = tk.StringVar(
                value=_format_adjustment_label(0.0)
            )
            self._build_style(ttk)
            self._build_layout(tk, ttk, filedialog, messagebox)
            self.live_poll_id = self.root.after(8, self._poll_live_preview)
            self.callback_poll_id = self.root.after(16, self._poll_callbacks)
            self.root.bind("<Control-z>", lambda _e: self._undo())
            self.root.bind("<Control-y>", lambda _e: self._redo())
            self.root.bind("<Control-Shift-Z>", lambda _e: self._redo())
            self.root.bind("<Control-o>", lambda _e: self._choose_source())
            self.root.bind("<Control-s>", lambda _e: self._commit_edit())

        def _build_style(self, ttk_module: Any) -> None:
            from openraw_studio.ui.workspace import configure_style

            configure_style(self.root)

        def _post(self, callback) -> None:
            if not self.closing:
                self.callbacks.put(callback)

        def _poll_callbacks(self) -> None:
            for _ in range(20):
                try:
                    callback = self.callbacks.get_nowait()
                except queue.Empty:
                    break
                try:
                    callback()
                except Exception:
                    import sys

                    self.root.report_callback_exception(*sys.exc_info())
                if self.closing:
                    return
            if not self.closing:
                self.callback_poll_id = self.root.after(16, self._poll_callbacks)

        def _build_layout(
            self, tk_module: Any, ttk_module: Any, filedialog: Any, messagebox: Any
        ) -> None:
            from openraw_studio.ui.workspace import build_workspace

            build_workspace(self, filedialog, messagebox)

        def _scroll_controls(self, event: Any) -> None:
            target = str(event.widget)
            for canvas in (self.controls_canvas, self.export_canvas):
                if target.startswith(str(canvas)) and canvas.yview() != (0.0, 1.0):
                    canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
                    return

        def _resize_histogram(self, event: Any) -> None:
            self._draw_histogram(
                self.current_histogram,
                width=max(2, event.width),
                height=max(2, event.height),
            )

        def _draw_histogram(
            self,
            analysis: HistogramAnalysis | None,
            *,
            width: int | None = None,
            height: int | None = None,
        ) -> None:
            canvas = self.histogram_canvas
            canvas.delete("all")
            width = width or max(2, canvas.winfo_width())
            height = height or max(2, canvas.winfo_height())
            baseline = height - 1
            canvas.create_line(0, baseline, width - 1, baseline, fill="#d2d2d7")
            if analysis is None:
                return

            luminance_points = _histogram_coordinates(
                analysis.luminance, width=width, height=height
            )
            canvas.create_polygon(
                0,
                baseline,
                *luminance_points,
                width - 1,
                baseline,
                fill="#d2d2d7",
                outline="",
            )
            channel_peak = max((*analysis.red, *analysis.green, *analysis.blue))
            for counts, color in (
                (analysis.red, "#d94f55"),
                (analysis.green, "#3a9b65"),
                (analysis.blue, "#4f7fd9"),
            ):
                points = _histogram_coordinates(
                    counts, width=width, height=height, peak=channel_peak
                )
                canvas.create_line(*points, fill=color, width=1.4, smooth=True)

        def _show_histogram(
            self, analysis: HistogramAnalysis | None, *, view: str
        ) -> None:
            self.current_histogram = analysis
            self.histogram_status_var.set(_histogram_status_text(analysis, view=view))
            style = (
                "Warning.TLabel"
                if analysis is not None and analysis.has_significant_clipping()
                else "Muted.TLabel"
            )
            self.histogram_status_label.configure(style=style)
            self._draw_histogram(analysis)

        def _choose_source(self) -> None:
            if self.is_busy:
                return
            selected = self.filedialog.askopenfilename(
                title="Import RAW photo",
                filetypes=[
                    ("RAW photos", "*.dng *.DNG *.nef *.NEF *.nrw *.NRW"),
                    ("Nikon RAW", "*.nef *.NEF *.nrw *.NRW"),
                    ("DNG RAW", "*.dng *.DNG"),
                    ("All files", "*.*"),
                ],
            )
            if not selected:
                return
            self._select_source(Path(selected), ready_status="Ready to process")

        def _choose_library_folder(self) -> None:
            if self.is_busy:
                return
            selected = self.filedialog.askdirectory(title="Import folder")
            if not selected:
                return
            self._start_library_scan(Path(selected))

        def _start_library_scan(self, folder: Path) -> None:
            if self.is_busy:
                return
            self._set_busy(True)
            self.library_scan_counter += 1
            scan_id = self.library_scan_counter
            self.library_dir = folder
            self.library_items = []
            self.library_listbox.delete(0, "end")
            self.library_status_var.set("Scanning folder...")
            threading.Thread(
                target=self._library_scan_worker, args=(scan_id, folder), daemon=True
            ).start()

        def _library_scan_worker(self, scan_id: int, folder: Path) -> None:
            try:
                items = _scan_library_folder(folder)
            except OSError as exc:
                message = _friendly_error_message(exc)
                self._post(lambda: self._show_library_error(scan_id, message))
                return
            self._post(lambda: self._show_library_items(scan_id, folder, items))

        def _show_library_error(self, scan_id: int, message: str) -> None:
            if scan_id != self.library_scan_counter:
                return
            self.library_status_var.set(message)
            self._set_busy(False)

        def _show_library_items(
            self, scan_id: int, folder: Path, items: tuple[tuple[Path, str, bool], ...]
        ) -> None:
            if scan_id != self.library_scan_counter:
                return
            self.library_items = list(items)
            self.library_listbox.delete(0, "end")
            for _path, label, _can_render in self.library_items:
                self.library_listbox.insert("end", label)
            self.library_status_var.set(
                _folder_status_text(folder, len(self.library_items))
            )
            self._set_busy(False)
            if self.library_items:
                first_supported = next(
                    (index for index, item in enumerate(self.library_items) if item[2]),
                    0,
                )
                self.library_listbox.selection_set(first_supported)
                self.library_listbox.activate(first_supported)
                self.library_listbox.see(first_supported)
                self._select_source(
                    self.library_items[first_supported][0],
                    ready_status="Folder imported",
                )

        def _select_library_item(self, _event: Any = None) -> None:
            selection = self.library_listbox.curselection()
            if not selection:
                return
            index = int(selection[0])
            if index < 0 or index >= len(self.library_items):
                return
            self._select_source(
                self.library_items[index][0], ready_status="Photo selected from folder"
            )

        def _create_sample_source(self) -> None:
            try:
                sample_path = write_synthetic_dng(_default_sample_path())
            except OSError as exc:
                self._show_error(_friendly_error_message(exc))
                return
            self._select_source(sample_path, ready_status="Sample DNG ready")

        def _create_sample_nikon_source(self) -> None:
            try:
                sample_path = write_synthetic_nikon_nef(
                    _default_sample_nikon_nef_path()
                )
            except (OSError, ValueError) as exc:
                self._show_error(_friendly_error_message(exc))
                return
            self._select_source(sample_path, ready_status="Synthetic Nikon NEF ready")

        def _select_source(self, source: Path, *, ready_status: str) -> None:
            if self.is_busy:
                return
            if not self._commit_edit():
                return
            self.live_worker.invalidate()
            if self.live_after_id is not None:
                self.root.after_cancel(self.live_after_id)
                self.live_after_id = None
            if self.histogram_after_id is not None:
                self.root.after_cancel(self.histogram_after_id)
                self.histogram_after_id = None
            self.run_counter += 1
            self.source_path = source
            self.source_orientation = 1
            self.preview_only_name = "Camera JPEG"
            self.support_notice_var.set("")
            self.support_notice_label.pack_forget()
            self.zoom_var.set("Fit")
            self.pan_offset = [0.0, 0.0]
            self.last_auto_suggestion = None
            self.auto_summary_var.set("")
            self.last_noise_suggestion = None
            self.current_can_preview = None
            self.current_can_render = None
            self.source_var.set(source.name)
            self.photo_info_var.set("Reading photo info...")
            if self.output_dir is None:
                self.output_dir = source.parent / "openraw-output"
                self.output_var.set(str(self.output_dir))
            self._refresh_output_info()
            self._clear_result()
            self._set_adjustment_values(
                _manual_overrides(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
            )
            recipe_status = self._restore_recipe_if_available()
            saved = self.session_store.load(source)
            if saved is not None:
                self._set_adjustment_values(saved)
                recipe_status = "Edits restored"
            self.history.reset(self._current_overrides())
            self.edit_status_var.set(
                "Edits saved" if saved is not None else "Original RAW"
            )
            self._refresh_history_buttons()
            self._set_busy(False)
            self.status_var.set(recipe_status or ready_status)
            threading.Thread(
                target=self._photo_info_worker,
                args=(source, self.run_counter),
                daemon=True,
            ).start()

        def _choose_output(self) -> None:
            if self.is_busy:
                return
            selected = self.filedialog.askdirectory(title="Choose output folder")
            if selected:
                self.output_dir = Path(selected)
                self.output_var.set(str(self.output_dir))
                self._refresh_output_info()
                if self.source_path is not None:
                    self.status_var.set("Output folder updated")
                self.last_saved_preview_overrides = None
                self._schedule_live_preview()

        def _refresh_output_info(self) -> None:
            if self.source_path is None:
                self.output_info_var.set("Output plan appears after import")
                return
            output_dir = self.output_dir or (self.source_path.parent / "openraw-output")
            plan = ArtifactPlan.for_source(
                self.source_path,
                output_dir,
                export_format=self._selected_export_format(),
            )
            summary = _planned_output_summary(
                self.source_path,
                output_dir,
                export_format=self._selected_export_format(),
            )
            if plan.recipe_path.exists():
                summary += "\nSaved recipe: found"
            self.output_info_var.set(summary)

        def _current_recipe_path(self) -> Path | None:
            if self.source_path is None:
                return None
            output_dir = self.output_dir or (self.source_path.parent / "openraw-output")
            return ArtifactPlan.for_source(self.source_path, output_dir).recipe_path

        def _set_adjustment_values(self, overrides: Mapping[str, float]) -> None:
            self.exposure_var.set(float(overrides.get("exposure", 0.0)))
            self.contrast_var.set(float(overrides.get("contrast", 0.0)))
            self.highlights_var.set(float(overrides.get("highlights", 0.0)))
            self.shadows_var.set(float(overrides.get("shadows", 0.0)))
            self.warmth_var.set(float(overrides.get("warmth", 0.0)))
            self.tint_var.set(float(overrides.get("tint", 0.0)))
            self.saturation_var.set(float(overrides.get("saturation", 0.0)))
            self.color_noise_var.set(float(overrides.get("color_noise", 0.0)))
            self.luminance_noise_var.set(float(overrides.get("luminance_noise", 0.0)))
            self._sync_adjustment_labels(update_status=False)

        def _restore_recipe_if_available(self) -> str | None:
            if self.source_path is None:
                return None
            recipe_path = self._current_recipe_path()
            if recipe_path is None or not recipe_path.exists():
                return None
            try:
                overrides = _load_recipe_adjustments(recipe_path, self.source_path)
                export_format, export_quality, export_bit_depth = _load_recipe_export_options(
                    recipe_path, self.source_path, include_bit_depth=True
                )
            except (OSError, ValueError):
                return "Saved recipe could not be loaded"
            self._set_adjustment_values(overrides)
            self.export_format_var.set(export_display_name(export_format))
            self.export_bit_depth_var.set(export_bit_depth)
            self.jpeg_quality_var.set(float(export_quality))
            self._sync_export_options(update_status=False)
            self._refresh_preview_state()
            return "Saved recipe loaded"

        def _photo_info_worker(self, source: Path, run_id: int) -> None:
            try:
                info, support = _read_photo_info_with_support(source)
            except OSError:
                info = "Photo info unavailable"
                support = None
            self._post(
                lambda: self._show_photo_info(source, info, support, run_id=run_id)
            )

        def _show_photo_info(
            self,
            source: Path,
            info: str,
            support: NativeSupportReport | None,
            *,
            run_id: int,
        ) -> None:
            if self.source_path != source or run_id != self.run_counter:
                return
            if support is not None:
                self.source_orientation = support.metadata.get("orientation", 1)
                self.current_can_preview = support.can_preview or support.can_render
                self.current_can_render = support.can_render
                if support.can_preview and not support.can_render:
                    mode = support.metadata.get("nikon_makernote", {}).get("compression_name")
                    self.preview_only_name = f"{mode} | Camera JPEG" if mode else "Camera JPEG"
                    self.support_notice_var.set(
                        f"{mode or 'Unsupported RAW encoding'}\n"
                        "Camera preview only. RAW editing and export unavailable."
                    )
                    self.support_notice_label.pack(
                        before=self.histogram_canvas, fill="x", pady=(0, 12)
                    )
                    self.edit_status_var.set("Preview only")
                    self.status_var.set("Loading camera JPEG...")
                elif support.can_inspect and not support.can_render:
                    self.status_var.set(
                        "RAW metadata imported; preview/export support is next"
                    )
                self._set_busy(False)
            self.photo_info_var.set(info)
            if self.current_can_render:
                self._schedule_live_preview()
            elif self.current_can_preview:
                self._start_pipeline(preview_only=True)

        def _sync_adjustment_labels(self, *_: Any, update_status: bool = True) -> None:
            self.exposure_label_var.set(
                _format_exposure_label(float(self.exposure_var.get()))
            )
            self.contrast_label_var.set(
                _format_adjustment_label(float(self.contrast_var.get()))
            )
            self.highlights_label_var.set(
                _format_adjustment_label(float(self.highlights_var.get()))
            )
            self.shadows_label_var.set(
                _format_adjustment_label(float(self.shadows_var.get()))
            )
            self.warmth_label_var.set(
                _format_adjustment_label(float(self.warmth_var.get()))
            )
            self.tint_label_var.set(
                _format_adjustment_label(float(self.tint_var.get()))
            )
            self.saturation_label_var.set(
                _format_adjustment_label(float(self.saturation_var.get()))
            )
            self.color_noise_label_var.set(f"{self.color_noise_var.get() * 100:.0f}")
            self.luminance_noise_label_var.set(f"{self.luminance_noise_var.get() * 100:.0f}")
            if update_status and self.source_path is not None and not self.is_busy:
                preview_state = self._refresh_preview_state()
                self.status_var.set(
                    preview_state
                    if preview_state == "Preview needs update"
                    else "Adjustments changed"
                )
            if update_status:
                if not self.showing_after:
                    self.showing_after = True
                    self.view_var.set("Edited")
                if self.source_path is not None:
                    if self.edit_after_id is not None:
                        self.root.after_cancel(self.edit_after_id)
                    self.edit_status_var.set("Unsaved edits")
                    self.edit_after_id = self.root.after(450, self._commit_edit)
                self._schedule_live_preview()

        def _refresh_history_buttons(self) -> None:
            self.undo_button.configure(
                state="normal"
                if self.history.can_undo and not self.is_busy
                else "disabled"
            )
            self.redo_button.configure(
                state="normal"
                if self.history.can_redo and not self.is_busy
                else "disabled"
            )

        def _commit_edit(self) -> bool:
            if self.edit_after_id is not None:
                self.root.after_cancel(self.edit_after_id)
                self.edit_after_id = None
            if self.source_path is None or not self.current_can_render:
                return True
            self.history.commit(self._current_overrides())
            self._refresh_history_buttons()
            try:
                self.session_store.save(self.source_path, self._current_overrides())
                self.edit_status_var.set("Edits saved")
                return True
            except (OSError, ValueError):
                self.edit_status_var.set("Could not save edits")
                return False

        def _undo(self) -> None:
            if self.is_busy:
                return
            self._commit_edit()
            self._set_adjustment_values(self.history.undo())
            self._sync_adjustment_labels()
            self._commit_edit()

        def _redo(self) -> None:
            if self.is_busy:
                return
            self._commit_edit()
            self._set_adjustment_values(self.history.redo())
            self._sync_adjustment_labels()
            self._commit_edit()

        def _reset_one(self, key: str) -> str:
            if not self.is_busy:
                self._commit_edit()
                getattr(self, key + "_var").set(0)
                self._sync_adjustment_labels()
                self._commit_edit()
            return "break"

        def _change_auto_strength(self, *_args) -> None:
            amount = round(self.auto_strength_var.get())
            self.auto_strength_label_var.set(f"{amount}%")
            if self.last_auto_suggestion is not None and not self.is_busy:
                self._set_adjustment_values(
                    {
                        **{key: value * amount / 100 for key, value in self.last_auto_suggestion.as_overrides().items()},
                        "color_noise": self.color_noise_var.get(),
                        "luminance_noise": self.luminance_noise_var.get(),
                    }
                )
                self._sync_adjustment_labels()

        def _zoom_changed(self, _event=None, *, anchor=None) -> None:
            if anchor is None and self.detail_frame is not None:
                x, y, width, height = self.detail_frame.region
                sw, sh = self.detail_frame.native_size
                anchor = ((x + width / 2) / sw, (y + height / 2) / sh)
            self.detail_anchor = anchor or (0.5, 0.5)
            self.pan_offset = [0.0, 0.0]
            self.detail_frame = None
            self.before_histogram = None
            if self.histogram_after_id is not None:
                self.root.after_cancel(self.histogram_after_id)
                self.histogram_after_id = None
            self._fit_live_image()
            self._schedule_live_preview()

        def _detail_view(self):
            scale = {"100%": 1, "200%": 2}.get(self.zoom_var.get())
            if scale is None or not self.current_can_render:
                return None
            return DetailView(
                (max(1, self.preview_label.winfo_width() - 2), max(1, self.preview_label.winfo_height() - 2)),
                scale, tuple(self.pan_offset), self.detail_anchor,
            )

        def _zoom_toggle(self, _event=None) -> str:
            anchor = None
            if self.zoom_var.get() == "Fit" and self.live_image is not None and _event is not None:
                width, height = self.live_image.size
                aw, ah = self.preview_label.winfo_width() - 2, self.preview_label.winfo_height() - 2
                scale = min(aw / width, ah / height)
                anchor = (
                    max(0, min(1, (_event.x - (aw - width * scale) / 2) / (width * scale))),
                    max(0, min(1, (_event.y - (ah - height * scale) / 2) / (height * scale))),
                )
            self.zoom_var.set(("100%" if self.current_can_render else "2x") if self.zoom_var.get() == "Fit" else "Fit")
            self._zoom_changed(anchor=anchor)
            return "break"

        def _pan_start(self, event) -> None:
            self.pan_origin = (event.x, event.y, *self.pan_offset)

        def _pan_move(self, event) -> None:
            if self.pan_origin is not None and self.zoom_var.get() != "Fit":
                x, y, ox, oy = self.pan_origin
                width, height = (
                    self.detail_full_size if self._detail_view() is not None and self.detail_full_size is not None
                    else self.live_image.size if self.live_image is not None else (1, 1)
                )
                area_width, area_height = (
                    self.preview_label.winfo_width(),
                    self.preview_label.winfo_height(),
                )
                scale = min(area_width / width, area_height / height) * {
                    "2x": 2,
                    "4x": 4,
                }.get(self.zoom_var.get(), 1)
                if detail := self._detail_view():
                    scale = detail.scale
                bounds = (
                    max(0, (width * scale - area_width) / 2),
                    max(0, (height * scale - area_height) / 2),
                )
                limits = [(-bound, bound) for bound in bounds]
                if detail is not None and self.detail_full_size is not None:
                    limits = [
                        ((length * anchor - length + min(length, viewport / scale) / 2) * scale,
                         (length * anchor - min(length, viewport / scale) / 2) * scale)
                        for length, anchor, viewport in zip((width, height), self.detail_anchor, detail.viewport)
                    ]
                self.pan_offset = [max(low, min(high, value)) for (low, high), value in zip(limits, (ox + event.x - x, oy + event.y - y))]
                self._fit_live_image()
                if self._detail_view() is not None:
                    self._schedule_live_preview()

        def _selected_export_format(self) -> str:
            return normalize_export_format(self.export_format_var.get())

        def _selected_export_quality(self) -> int:
            return validate_export_quality(round(float(self.jpeg_quality_var.get())))

        def _selected_export_bit_depth(self) -> int:
            return validate_export_bit_depth(
                self.export_bit_depth_var.get(), export_format=self._selected_export_format()
            )

        def _sync_export_options(self, *_: Any, update_status: bool = True) -> None:
            export_format = self._selected_export_format()
            if export_format == "jpeg":
                self.export_bit_depth_var.set(8)
                self.export_bit_depth_combo.configure(state="disabled")
                quality = self._selected_export_quality()
                self.jpeg_quality_var.set(float(quality))
                self.jpeg_quality_label_var.set(str(quality))
                self.jpeg_quality_scale.configure(state="normal")
            else:
                self.export_bit_depth_combo.configure(state="readonly")
                bit_depth = self._selected_export_bit_depth()
                self.jpeg_quality_label_var.set(f"Lossless {bit_depth}-bit")
                self.jpeg_quality_scale.configure(state="disabled")
            self.process_button.configure(
                text=f"Export {export_display_name(export_format)}"
            )
            self._refresh_output_info()
            if update_status and self.source_path is not None and not self.is_busy:
                self.status_var.set(
                    f"{export_display_name(export_format)} export selected"
                )

        def _reset_adjustments(self) -> None:
            if self.is_busy or not self.current_can_render:
                return
            self._commit_edit()
            self.exposure_var.set(0.0)
            self.contrast_var.set(0.0)
            self.highlights_var.set(0.0)
            self.shadows_var.set(0.0)
            self.warmth_var.set(0.0)
            self.tint_var.set(0.0)
            self.saturation_var.set(0.0)
            self.color_noise_var.set(0.0)
            self.luminance_noise_var.set(0.0)
            self._sync_adjustment_labels()
            self._commit_edit()

        def _auto_color_noise(self) -> None:
            if self.is_busy or not self.current_can_render or self.source_path is None:
                return
            self._commit_edit()
            self.run_counter += 1
            self.last_noise_suggestion = None
            self._set_busy(True)
            self.status_var.set("Analyzing color noise...")
            threading.Thread(
                target=self._auto_color_noise_worker,
                args=(self.run_counter, self.source_path, self._current_overrides()),
                daemon=True,
            ).start()

        def _auto_color_noise_worker(self, run_id, source, adjustments) -> None:
            try:
                photo = self.live_worker.get_prepared_photo(source)
                if photo is None:
                    photo = prepare_interactive_photo(self.pipeline.raw_processor, source)
                result = suggest_color_noise_for_photo(photo, adjustments)
            except (PipelineError, OSError, ValueError, RuntimeError, NotImplementedError) as exc:
                message = _friendly_error_message(exc)
                self._post(lambda: self._show_error(message, run_id=run_id))
                return
            self._post(lambda: self._apply_auto_color_noise(result, run_id=run_id))

        def _apply_auto_color_noise(self, result, *, run_id) -> None:
            if run_id != self.run_counter:
                return
            self.last_noise_suggestion = result
            self._set_busy(False)
            if result.strength is None:
                self.status_var.set({
                    "native-samples-unavailable": "Native color-noise analysis unavailable for this file",
                    "insufficient-samples": "Not enough reliable noise samples; settings unchanged",
                    "no-safe-benefit": "No verified noise improvement; settings unchanged",
                }[result.status])
                return
            self.color_noise_var.set(result.strength)
            self._sync_adjustment_labels()
            self._commit_edit()
            self.status_var.set(f"Color noise: {result.strength * 100:.0f}")
            self._schedule_live_preview()

        def _auto_adjust(self) -> None:
            if self.is_busy or not self.current_can_render:
                return
            if self.source_path is None:
                self.messagebox.showinfo("OpenRAW Studio", "Import a RAW photo first.")
                return
            self.run_counter += 1
            self._commit_edit()
            run_id = self.run_counter
            self._set_busy(True)
            self.status_var.set("Auto adjusting...")
            threading.Thread(
                target=self._auto_adjust_worker,
                args=(run_id, self.source_path),
                daemon=True,
            ).start()

        def _auto_adjust_worker(self, run_id: int, source: Path) -> None:
            try:
                photo = self.live_worker.get_prepared_photo(source)
                if photo is None:
                    photo = prepare_interactive_photo(self.pipeline.raw_processor, source)
                suggestion = suggest_auto_adjustments_for_photo(photo)
            except (
                PipelineError,
                OSError,
                ValueError,
                RuntimeError,
                NotImplementedError,
            ) as exc:
                message = _friendly_error_message(exc)
                self._post(lambda: self._show_error(message, run_id=run_id))
                return
            self._post(lambda: self._apply_auto_adjustment(suggestion, run_id=run_id))

        def _apply_auto_adjustment(
            self, suggestion: AutoAdjustSuggestion, *, run_id: int
        ) -> None:
            if run_id != self.run_counter:
                return
            self.last_auto_suggestion = suggestion
            evidence = suggestion.scene_evidence
            if evidence is not None and evidence.status == "ready":
                summary = evidence.scene + " | " + evidence.lighting
            elif evidence is not None and evidence.status == "uncertain":
                summary = suggestion.scene + " | Scene uncertain"
            elif evidence is not None and evidence.status == "unavailable":
                summary = suggestion.scene + " | Model unavailable"
            else:
                summary = suggestion.scene + " | Tonal Auto"
            self.auto_summary_var.set(summary)
            self._set_busy(False)
            self._change_auto_strength()
            self._commit_edit()
            preview_state = self._refresh_preview_state()
            self.status_var.set(
                _auto_adjust_status(suggestion)
                if preview_state != "Preview current"
                else "Auto Adjust applied"
            )
            self._schedule_live_preview()

        def _clear_result(self) -> None:
            self.detail_frame = None
            self.detail_full_size = None
            self.detail_anchor = (0.5, 0.5)
            self.preview_photo = None
            self.before_photo = None
            self.after_photo = None
            self.before_histogram = None
            self.after_histogram = None
            self.before_view_name = "Before"
            self.last_export_path = None
            self.last_preview_overrides = None
            self.last_saved_preview_overrides = None
            self.live_image = None
            self.reference_image = None
            self.showing_after = True
            self.preview_label.configure(image="", text="Loading photo...")
            self.preview_state_var.set("No preview yet")
            self.export_label.configure(text="")
            self.compare_button.configure(state="disabled", text="")
            self.view_var.set("Edited")
            self.open_folder_button.configure(state="disabled")
            self.open_export_button.configure(state="disabled", text="Open Export")
            self._show_histogram(None, view="After")

        def _update_preview(self) -> None:
            if self.current_can_render:
                self._schedule_live_preview()
            else:
                self._start_pipeline(preview_only=True)

        def _schedule_live_preview(self) -> None:
            if self.source_path is None or not self.current_can_render:
                return
            # Throttle, not debounce: continuous dragging must keep producing frames.
            if self.live_after_id is None:
                self.live_after_id = self.root.after(16, self._submit_live_preview)

        def _submit_live_preview(self) -> None:
            self.live_after_id = None
            if self.source_path is None or not self.current_can_render:
                return
            self.live_revision = self.live_worker.submit(
                self.source_path, self._current_overrides(), detail_view=self._detail_view()
            )
            if self.after_photo is None:
                self.preview_state_var.set("Preparing RAW preview...")

        def _poll_live_preview(self) -> None:
            frame = self.live_worker.take()
            if (
                frame is not None
                and frame.source == self.source_path
                and frame.revision <= self.live_revision
                and frame.detail_view == self._detail_view()
            ):
                if frame.error:
                    self.preview_state_var.set("Live preview unavailable")
                    self.status_var.set(frame.error)
                else:
                    image = frame.image.copy()
                    if frame.detail_view is None:
                        self.live_image = image
                        self.detail_frame = None
                    else:
                        self.detail_frame = frame
                        self.detail_full_size = frame.native_size
                        self.before_histogram = None
                    if frame.reference:
                        self.reference_image = image
                        self._fit_live_image()
                        self.before_view_name = "Camera Preview"
                        self.view_var.set("Camera")
                        self.compare_button.configure(state="disabled", text="")
                        self.preview_state_var.set("Camera preview | Preparing RAW...")
                        self.live_poll_id = self.root.after(8, self._poll_live_preview)
                        return
                    self.last_preview_overrides = dict(frame.adjustments)
                    if frame.original_image is not None:
                        if frame.detail_view is None and self.reference_image is not frame.original_image:
                            self.reference_image = frame.original_image
                            self.before_histogram = None
                        self.before_view_name = "Original"
                        self.view_var.set("Edited" if self.showing_after else "Original")
                        self.compare_button.configure(state="normal", text="")
                    self.last_live_latency_ms = frame.elapsed_ms
                    self.preview_state_var.set(
                        f"{'Live' if frame.detail_view is None else self.zoom_var.get()} | {frame.backend} | {frame.elapsed_ms:.0f} ms"
                    )
                    if not self.is_busy:
                        self.status_var.set(
                            "Preview current"
                            if frame.adjustments == self._current_overrides()
                            else "Updating preview..."
                        )
                    if self.reference_image is None:
                        self.reference_image = image
                        self.before_view_name = "Initial Preview"
                        self.compare_button.configure(state="normal", text="")
                    self._fit_live_image()
                    if self.histogram_after_id is not None:
                        self.root.after_cancel(self.histogram_after_id)
                    self.histogram_after_id = self.root.after(
                        180, lambda img=image, before=frame.original_image: self._update_live_histogram(img, before)
                    )
            self.live_poll_id = self.root.after(8, self._poll_live_preview)

        def _queue_preview_resize(self, _event=None) -> None:
            if self.resize_after_id is not None:
                self.root.after_cancel(self.resize_after_id)
            self.resize_after_id = self.root.after_idle(self._fit_live_image)
            if self._detail_view() is not None:
                self._schedule_live_preview()

        def _fit_live_image(self) -> None:
            from PIL import Image, ImageTk

            self.resize_after_id = None
            size = (
                max(1, self.preview_label.winfo_width() - 2),
                max(1, self.preview_label.winfo_height() - 2),
            )
            zoom = {"Fit": 1, "2x": 2, "4x": 4}.get(self.zoom_var.get(), 1)
            detail = self._detail_view()
            if detail is not None and self.detail_frame is not None and self.detail_frame.detail_view == detail:
                frame = self.detail_frame
                draw_size = detail.display_size(frame.region)

                def native_image(image):
                    enlarged = image if detail.scale == 1 else image.resize(
                        (image.width * detail.scale, image.height * detail.scale), Image.Resampling.NEAREST
                    )
                    return ImageTk.PhotoImage(enlarged.crop((0, 0, *draw_size)))

                self.after_photo = native_image(frame.image)
                self.before_photo = native_image(frame.original_image)
                self.preview_photo = self.after_photo if self.showing_after else self.before_photo
                self.preview_label.configure(image=self.preview_photo, text="")
                return
            if detail is not None:
                self.preview_state_var.set("Preparing native detail...")

            def display_image(image):
                width, height = image.size
                if detail is not None and self.detail_full_size is not None:
                    x, y, rw, rh = detail.region(self.detail_full_size)
                    sx, sy = width / self.detail_full_size[0], height / self.detail_full_size[1]
                    return ImageTk.PhotoImage(image.resize(
                        detail.display_size((x, y, rw, rh)), Image.Resampling.BILINEAR,
                        box=(x * sx, y * sy, (x + rw) * sx, (y + rh) * sy),
                    ))
                scale = min(size[0] / width, size[1] / height) * zoom
                draw_width = min(size[0], max(1, round(width * scale)))
                draw_height = min(size[1], max(1, round(height * scale)))
                crop_width, crop_height = (
                    min(width, draw_width / scale),
                    min(height, draw_height / scale),
                )
                left = max(
                    0,
                    min(
                        width - crop_width,
                        (width - crop_width) / 2 - self.pan_offset[0] / scale,
                    ),
                )
                top = max(
                    0,
                    min(
                        height - crop_height,
                        (height - crop_height) / 2 - self.pan_offset[1] / scale,
                    ),
                )
                return ImageTk.PhotoImage(
                    image.resize(
                        (draw_width, draw_height),
                        Image.Resampling.BILINEAR,
                        box=(left, top, left + crop_width, top + crop_height),
                    )
                )

            if self.live_image is not None:
                self.after_photo = display_image(self.live_image)
            if self.reference_image is not None:
                self.before_photo = display_image(self.reference_image)
            if self.after_photo is not None:
                self.preview_photo = (
                    self.after_photo if self.showing_after else self.before_photo
                )
                self.preview_label.configure(image=self.preview_photo, text="")

        def _update_live_histogram(self, image, original=None) -> None:
            self.histogram_after_id = None
            self.after_histogram = _analyze_pillow_preview(image, max_dimension=128)
            if self.before_histogram is None:
                self.before_histogram = _analyze_pillow_preview(
                    original if original is not None else self.reference_image if self.reference_image is not None else image,
                    max_dimension=128,
                )
            if self.showing_after:
                self._show_histogram(self.after_histogram, view="Detail after" if self._detail_view() else "After")

        def _close(self) -> None:
            if self.is_busy:
                self.status_var.set("Please wait for the current export to finish")
                return
            if not self._commit_edit() and not self.messagebox.askyesno(
                "Unsaved edits", "Edits could not be saved. Close anyway?"
            ):
                return
            self.live_worker.close()
            self.closing = True
            self.root.after_cancel(self.callback_poll_id)
            for callback in (
                self.live_after_id,
                self.live_poll_id,
                self.histogram_after_id,
                self.resize_after_id,
            ):
                if callback is not None:
                    self.root.after_cancel(callback)
            self.root.destroy()

        def _export_photo(self) -> None:
            self._start_pipeline(preview_only=False)

        def _export_folder(self) -> None:
            if self.is_busy:
                return
            sources = _library_sources(self.library_items)
            supported_sources = _supported_library_sources(self.library_items)
            if not supported_sources:
                self.messagebox.showinfo(
                    "OpenRAW Studio", "Import a folder with supported RAW files first."
                )
                return
            if self.source_path is None:
                self.messagebox.showinfo("OpenRAW Studio", "Select a photo first.")
                return
            output_dir = self.output_dir or (self.source_path.parent / "openraw-output")
            self.output_dir = output_dir
            self.output_var.set(str(output_dir))
            self._refresh_output_info()
            self.run_counter += 1
            run_id = self.run_counter
            overrides = self._current_overrides()
            export_format = self._selected_export_format()
            export_quality = self._selected_export_quality()
            export_bit_depth = self._selected_export_bit_depth()
            existing = sum(
                ArtifactPlan.for_source(
                    path, output_dir, export_format=export_format
                ).export_path.exists()
                for path in supported_sources
            )
            if existing and not self.messagebox.askyesno(
                "Replace exports?",
                f"Replace {existing} existing export(s) in this folder?",
            ):
                return
            self._commit_edit()
            self.batch_cancel.clear()
            self.batch_running = True
            mode = self.batch_mode_var.get()
            strength = self.auto_strength_var.get() / 100
            self._set_busy(True)
            self._set_batch_progress(0, len(sources))
            self.status_var.set(
                f"Exporting {len(supported_sources)} supported photos as {export_display_name(export_format)}..."
            )
            self.preview_state_var.set("Batch export running...")
            self.export_label.configure(text="")
            threading.Thread(
                target=self._batch_export_worker,
                args=(
                    run_id,
                    sources,
                    output_dir,
                    overrides,
                    export_format,
                    export_quality,
                    mode,
                    strength,
                    export_bit_depth,
                ),
                daemon=True,
            ).start()

        def _batch_export_worker(
            self,
            run_id: int,
            sources: tuple[Path, ...],
            output_dir: Path,
            overrides: dict[str, float],
            export_format: str,
            export_quality: int,
            mode: str,
            strength: float,
            export_bit_depth: int = 8,
        ) -> None:
            def adjustments(source):
                if mode == "Saved edits":
                    saved = self.session_store.load(source)
                    if saved is not None:
                        return saved
                    recipe = ArtifactPlan.for_source(source, output_dir).recipe_path
                    return (
                        _load_recipe_adjustments(recipe, source)
                        if recipe.is_file()
                        else {}
                    )
                if mode == "Auto each photo":
                    photo = prepare_interactive_photo(
                        self.pipeline.raw_processor, source
                    )
                    suggested = suggest_auto_adjustments_for_photo(photo)
                    return {
                        **{key: value * strength for key, value in suggested.as_overrides().items()},
                        "color_noise": overrides.get("color_noise", 0),
                        "luminance_noise": overrides.get("luminance_noise", 0),
                    }
                return overrides

            def on_progress(done: int, total: int, item: BatchItemResult) -> None:
                text = _batch_progress_text(done, total, item)
                self._post(
                    lambda run_id=run_id, text=text, done=done, total=total: (
                        self._show_batch_progress(run_id, text, done, total)
                    ),
                )

            try:
                result = run_batch_export(
                    sources,
                    output_dir,
                    overrides=overrides,
                    export_format=export_format,
                    export_quality=export_quality,
                    export_bit_depth=export_bit_depth,
                    progress_callback=on_progress,
                    pipeline=self.pipeline,
                    should_cancel=self.batch_cancel.is_set,
                    adjustments_for_source=adjustments,
                )
            except Exception as error:
                message = _friendly_error_message(error)
                self._post(lambda: self._show_error(message, run_id=run_id))
                return
            self._post(
                lambda run_id=run_id, result=result: self._show_batch_result(
                    run_id, result
                )
            )

        def _show_batch_progress(
            self, run_id: int, text: str, done: int, total: int
        ) -> None:
            if run_id != self.run_counter:
                return
            self.status_var.set(text)
            self._set_batch_progress(done, total)

        def _show_batch_result(self, run_id: int, result: BatchResult) -> None:
            if run_id != self.run_counter:
                return
            self.batch_running = False
            self._set_busy(False)
            self.status_var.set(_batch_result_status(result))
            self.preview_state_var.set(
                _preview_state_text(
                    self.last_preview_overrides, self._current_overrides()
                )
            )
            self.export_label.configure(text=_format_batch_result_summary(result))
            self._refresh_output_info()
            if result.processed:
                self.open_folder_button.configure(state="normal")

        def _cancel_batch(self) -> None:
            if self.batch_running:
                self.batch_cancel.set()
                self.status_var.set("Stopping after the current photo...")
                self.cancel_batch_button.configure(state="disabled")

        def _start_pipeline(self, *, preview_only: bool) -> None:
            if self.is_busy:
                return
            if self.source_path is None:
                self.messagebox.showinfo("OpenRAW Studio", "Import a RAW photo first.")
                return
            output_dir = self.output_dir or (self.source_path.parent / "openraw-output")
            self.output_dir = output_dir
            self.output_var.set(str(output_dir))
            self._refresh_output_info()
            self.run_counter += 1
            run_id = self.run_counter
            export_format = self._selected_export_format()
            export_quality = self._selected_export_quality()
            export_bit_depth = self._selected_export_bit_depth()
            destination = ArtifactPlan.for_source(
                self.source_path, output_dir, export_format=export_format
            ).export_path
            if (
                not preview_only
                and destination.exists()
                and not self.messagebox.askyesno(
                    "Replace export?", f"{destination.name} already exists. Replace it?"
                )
            ):
                return
            self._set_busy(True)
            export_name = export_display_name(export_format)
            overrides = self._current_overrides()
            reuse_existing_preview = (
                not preview_only
                and self.last_saved_preview_overrides is not None
                and self.last_saved_preview_overrides == overrides
            )
            self.status_var.set(
                "Updating preview..." if preview_only else f"Exporting {export_name}..."
            )
            if preview_only:
                preview_status = "Updating preview..."
            elif reuse_existing_preview:
                preview_status = (
                    f"Exporting {export_name} from current preview settings..."
                )
            else:
                preview_status = f"Exporting preview and {export_name}..."
            self.preview_state_var.set(preview_status)
            self.export_label.configure(text="")
            self.last_export_path = None
            self.open_export_button.configure(state="disabled", text="Open Export")
            threading.Thread(
                target=self._process_worker,
                args=(
                    run_id,
                    self.source_path,
                    output_dir,
                    overrides,
                    preview_only,
                    export_format,
                    export_quality,
                    reuse_existing_preview,
                    export_bit_depth,
                ),
                daemon=True,
            ).start()

        def _process_worker(
            self,
            run_id: int,
            source: Path,
            output_dir: Path,
            overrides: dict[str, float],
            preview_only: bool,
            export_format: str,
            export_quality: int,
            reuse_existing_preview: bool,
            export_bit_depth: int = 8,
        ) -> None:
            try:
                result = self.pipeline.process(
                    PipelineRequest(
                        source,
                        output_dir,
                        overrides=overrides,
                        preview_only=preview_only,
                        export_format=export_format,
                        export_quality=export_quality,
                        reuse_existing_preview=reuse_existing_preview,
                        export_bit_depth=export_bit_depth,
                    )
                )
            except (PipelineError, OSError, ValueError) as exc:
                message = _friendly_error_message(exc)
                self._post(lambda: self._show_error(message, run_id=run_id))
                return
            self._post(
                lambda: self._show_result(
                    result, source, overrides=overrides, run_id=run_id
                )
            )

        def _set_busy(self, busy: bool) -> None:
            self.is_busy = busy
            if busy:
                self.progress_bar.grid()
                self.progress_bar.stop()
                self.progress_bar.configure(mode="indeterminate", maximum=100, value=0)
                self.progress_bar.start(12)
            else:
                self.progress_bar.stop()
                self.progress_bar.configure(mode="determinate", maximum=100, value=0)
                self.progress_bar.grid_remove()
            can_preview = (
                self.current_can_preview is True or self.current_can_render is True
            )
            can_render = self.current_can_render is True
            preview_state = (
                "normal"
                if not busy and self.source_path is not None and can_preview
                else "disabled"
            )
            render_state = (
                "normal"
                if not busy and self.source_path is not None and can_render
                else "disabled"
            )
            self.auto_adjust_button.configure(state=render_state)
            self.auto_color_noise_button.configure(state=render_state)
            self.zoom_combo.configure(values=("Fit", "2x", "4x", "100%", "200%") if can_render else ("Fit", "2x", "4x"))
            self.preview_button.configure(state=preview_state)
            self.process_button.configure(state=render_state)
            batch_state = (
                "disabled"
                if busy or not _supported_library_sources(self.library_items)
                else "normal"
            )
            self.batch_button.configure(state=batch_state)
            for scale in self.edit_scales:
                scale.configure(state=render_state)
            self.auto_strength_scale.configure(state=render_state)
            self.import_button.configure(state="disabled" if busy else "normal")
            self.import_folder_button.configure(state="disabled" if busy else "normal")
            self._refresh_history_buttons()
            self.cancel_batch_button.configure(
                state="normal" if busy and self.batch_running else "disabled"
            )
            self.batch_mode_combo.configure(state="disabled" if busy else "readonly")

        def _set_batch_progress(self, done: int, total: int) -> None:
            self.progress_bar.stop()
            self.progress_bar.configure(
                mode="determinate",
                maximum=max(1, total),
                value=min(max(0, done), max(1, total)),
            )

        def _current_overrides(self) -> dict[str, float]:
            return clean_adjustments(
                _manual_overrides(
                    float(self.exposure_var.get()),
                    float(self.contrast_var.get()),
                    float(self.highlights_var.get()),
                    float(self.shadows_var.get()),
                    float(self.warmth_var.get()),
                    float(self.tint_var.get()),
                    float(self.saturation_var.get()),
                    float(self.color_noise_var.get()),
                    float(self.luminance_noise_var.get()),
                )
            )

        def _refresh_preview_state(self) -> str:
            if self.current_can_preview and self.current_can_render is False:
                preview_state = f"{self.preview_only_name} | Preview only"
                self.preview_state_var.set(preview_state)
                return preview_state
            if self.last_preview_overrides is None and self.before_view_name == "Camera Preview":
                preview_state = "Camera preview | Preparing RAW..."
                self.preview_state_var.set(preview_state)
                return preview_state
            preview_state = _preview_state_text(
                self.last_preview_overrides, self._current_overrides()
            )
            self.preview_state_var.set(preview_state)
            return preview_state

        def _show_error(self, message: str, *, run_id: int | None = None) -> None:
            if run_id is not None and run_id != self.run_counter:
                return
            self.batch_running = False
            self._set_busy(False)
            self.status_var.set("Processing failed")
            self._refresh_preview_state()
            self.messagebox.showerror("OpenRAW Studio", message)

        def _show_result(
            self, result: Any, source: Path, *, overrides: dict[str, float], run_id: int
        ) -> None:
            if run_id != self.run_counter:
                return
            self._set_busy(False)
            self.status_var.set(_result_status(result))
            self.last_saved_preview_overrides = dict(overrides)
            if (
                result.preview is not None
                and result.preview.path.exists()
                and not self.current_can_render
            ):
                self.last_preview_overrides = dict(overrides)
                try:
                    from PIL import Image
                    from openraw_studio.raw.native.nikon import _apply_exif_orientation

                    with Image.open(result.preview.path) as opened:
                        image = opened.convert("RGB")
                    image = _apply_exif_orientation(image, self.source_orientation)
                    self.after_histogram = _analyze_pillow_preview(image)
                    image.thumbnail((1600, 1600))
                    self.live_image = image
                    self.reference_image = None
                    self.showing_after = True
                    self.view_var.set("Camera")
                    self._fit_live_image()
                    self._show_histogram(self.after_histogram, view="Camera")
                except (OSError, RuntimeError, ValueError, NotImplementedError):
                    self.before_photo = None
                    self.after_photo = None
                    self.preview_photo = None
                    self.before_histogram = None
                    self.after_histogram = None
                    self.before_view_name = "Before"
                    self.compare_button.configure(state="disabled", text="")
                    self._show_histogram(None, view="After")
                    self.preview_label.configure(
                        text="Preview created. Open the output folder to view it.",
                        image="",
                    )
                else:
                    self.before_photo = None
                    self.before_histogram = None
                    self.before_view_name = "Camera Preview"
                    self.compare_button.configure(state="disabled", text="")
            self._refresh_preview_state()
            if result.exports:
                exported = result.exports[0]
                receipt = f"{export_display_name(_result_export_format(result))} | {exported.width} x {exported.height}"
                try:
                    receipt += f" | {_format_bytes(exported.path.stat().st_size)}"
                except OSError:
                    pass
                quality = _format_quality_summary(result.recipe)
                self.export_label.configure(
                    text=receipt + (f"\n{quality}" if quality else "")
                )
            else:
                self.export_label.configure(text=_format_result_summary(result))
            if result.preview is not None or result.exports:
                self.open_folder_button.configure(state="normal")
            target_path, button_text = _open_export_target(result)
            self.open_export_button.configure(text=button_text)
            if target_path is not None:
                self.last_export_path = target_path
                self.open_export_button.configure(state="normal")
            if (
                self.current_can_render
                and self.last_preview_overrides != self._current_overrides()
            ):
                self._schedule_live_preview()

        def _toggle_compare(self) -> None:
            if self.after_photo is None or self.before_photo is None:
                return
            self.showing_after = not self.showing_after
            self.preview_photo = (
                self.after_photo if self.showing_after else self.before_photo
            )
            self.preview_label.configure(image=self.preview_photo)
            self.view_var.set("Edited" if self.showing_after else self.before_view_name)
            self.compare_button.state(
                ["!pressed"] if self.showing_after else ["pressed"]
            )
            histogram = (
                self.after_histogram if self.showing_after else self.before_histogram
            )
            self._show_histogram(
                histogram, view=("Detail " if self._detail_view() else "") + ("After" if self.showing_after else self.before_view_name)
            )

        def _open_output_folder(self) -> None:
            if self.output_dir is None or not self.output_dir.exists():
                return
            _open_in_system(self.output_dir)

        def _open_export(self) -> None:
            if self.last_export_path is None or not self.last_export_path.exists():
                return
            _open_in_system(self.last_export_path)

    root = tk.Tk()
    app = DesktopApp(root)
    if run_mainloop:
        root.mainloop()
    return app
