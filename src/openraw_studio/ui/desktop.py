"""Small local desktop shell for the OpenRAW Studio V0.1 pipeline."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import subprocess
import sys
import threading
from typing import Any, Mapping, Sequence

from openraw_studio.core.artifacts import ArtifactPlan
from openraw_studio.core.files import is_supported_raw_path
from openraw_studio.core.recipe import validate_recipe_shape
from openraw_studio.decision.auto_adjust import AutoAdjustSuggestion, suggest_auto_adjustments
from openraw_studio.pipeline.batch import BatchItemResult, BatchResult, run_batch_export
from openraw_studio.pipeline.errors import BackendUnavailableError, PipelineError, SourceFileError
from openraw_studio.pipeline.interfaces import PipelineRequest
from openraw_studio.pipeline.local import LocalPhotoPipeline
from openraw_studio.raw.native.preview import render_preview_image
from openraw_studio.raw.native.support import NativeSupportReport, inspect_native_support
from openraw_studio.raw.native.synthetic import write_synthetic_dng


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
    if report.can_render:
        return "Support: Supported by OpenRAW Native V0.1"
    if report.can_preview:
        return f"Support: Preview supported; export not supported yet\nReason: {report.reason}"
    if report.can_inspect:
        return f"Support: Import supported; preview/export not supported yet\nReason: {report.reason}"
    return f"Support: Not supported yet\nReason: {report.reason}"


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


def _planned_output_summary(source: Path, output_dir: Path) -> str:
    plan = ArtifactPlan.for_source(source, output_dir)
    preview_path = _preview_artifact_path(source, plan)
    return "\n".join(
        [
            f"Folder: {_short_path(plan.output_dir, max_chars=68)}",
            f"Preview: {_display_path(preview_path, base=plan.output_dir)}",
            f"JPEG: {_display_path(plan.export_path, base=plan.output_dir)}",
            f"Recipe: {_display_path(plan.recipe_path, base=plan.output_dir)}",
        ]
    )


def _preview_artifact_path(source: Path, plan: ArtifactPlan) -> Path:
    if source.suffix.lower() in NIKON_RAW_EXTENSIONS:
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
        "warmth": _clamped_recipe_float(raw.get("warmth"), default=0.0, minimum=-1.0, maximum=1.0),
    }


def _load_recipe_adjustments(recipe_path: Path, source: Path) -> dict[str, float]:
    recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
    if not isinstance(recipe, Mapping):
        raise ValueError("recipe file must contain a JSON object")
    validate_recipe_shape(recipe)
    if not _recipe_source_matches(recipe, source):
        raise ValueError("recipe does not match the selected photo")
    return _recipe_adjustment_overrides(recipe)


def _flatten_rgb_pixels(pixels: tuple[tuple[int, int, int], ...]) -> bytes:
    return bytes(channel for pixel in pixels for channel in pixel)


def _format_result_summary(result: Any) -> str:
    lines: list[str] = []
    if result.preview is not None:
        lines.append(f"Preview: {result.preview.path}")
    if result.exports:
        lines.append(f"JPEG: {result.exports[0].path}")
    if recipe_path := result.diagnostics.get("recipe_path"):
        lines.append(f"Recipe: {recipe_path}")
    return "\n".join(lines)


def _result_status(result: Any) -> str:
    if result.diagnostics.get("preview_only"):
        return "Preview updated"
    if result.exports:
        return "JPEG exported"
    return "Finished"


def _auto_adjust_status(suggestion: AutoAdjustSuggestion) -> str:
    return (
        "Auto Adjust applied: "
        f"{_format_exposure_label(suggestion.exposure)}, "
        f"Contrast {_format_adjustment_label(suggestion.contrast)}, "
        f"Warmth {_format_adjustment_label(suggestion.warmth)}"
    )


def _manual_overrides(exposure: float, contrast: float, warmth: float) -> dict[str, float]:
    return {
        "exposure": float(exposure),
        "contrast": float(contrast),
        "warmth": float(warmth),
    }


def _adjustments_match(
    rendered: Mapping[str, float] | None,
    current: Mapping[str, float],
    *,
    tolerance: float = 0.0001,
) -> bool:
    if rendered is None:
        return False
    for key in ("exposure", "contrast", "warmth"):
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
            return "Nikon RAW metadata import is ready, but this file does not include a readable embedded preview yet. Full NEF/NRW export decoding is still in progress."
        if "Nikon RAW metadata" in message or "NEF/NRW" in message:
            return "Nikon RAW preview import is ready, but full NEF/NRW export decoding is still in progress."
        if "currently starts with DNG files" in message:
            return "Nikon RAW preview import is ready, but full NEF/NRW export decoding is still in progress."
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


def launch_desktop_app() -> None:
    """Launch the first beginner-facing desktop workflow."""

    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    class DesktopApp:
        def __init__(self, root: Any) -> None:
            self.root = root
            self.root.title("OpenRAW Studio")
            self.root.geometry("1080x720")
            self.root.minsize(760, 540)
            self.source_path: Path | None = None
            self.output_dir: Path | None = None
            self.preview_photo: Any = None
            self.before_photo: Any = None
            self.after_photo: Any = None
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

            self.source_var = tk.StringVar(value="No RAW photo selected")
            self.output_var = tk.StringVar(value="Output folder will be chosen automatically")
            self.library_status_var = tk.StringVar(value="Import a folder to browse photos")
            self.photo_info_var = tk.StringVar(value="No photo selected")
            self.output_info_var = tk.StringVar(value="Output plan appears after import")
            self.status_var = tk.StringVar(value="Choose a RAW photo to begin")
            self.preview_state_var = tk.StringVar(value="No preview yet")
            self.exposure_var = tk.DoubleVar(value=0.0)
            self.contrast_var = tk.DoubleVar(value=0.0)
            self.warmth_var = tk.DoubleVar(value=0.0)
            self.exposure_label_var = tk.StringVar(value=_format_exposure_label(0.0))
            self.contrast_label_var = tk.StringVar(value=_format_adjustment_label(0.0))
            self.warmth_label_var = tk.StringVar(value=_format_adjustment_label(0.0))
            self._build_style(ttk)
            self._build_layout(tk, ttk, filedialog, messagebox)

        def _build_style(self, ttk_module: Any) -> None:
            style = ttk_module.Style(self.root)
            try:
                style.theme_use("vista")
            except tk.TclError:
                pass
            style.configure("App.TFrame", background="#f5f5f7")
            style.configure("Panel.TFrame", background="#ffffff")
            style.configure("Title.TLabel", background="#f5f5f7", foreground="#1d1d1f", font=("Segoe UI", 24, "bold"))
            style.configure("Subtitle.TLabel", background="#f5f5f7", foreground="#6e6e73", font=("Segoe UI", 10))
            style.configure("Panel.TLabel", background="#ffffff", foreground="#1d1d1f", font=("Segoe UI", 10))
            style.configure("Muted.TLabel", background="#ffffff", foreground="#6e6e73", font=("Segoe UI", 9))
            style.configure("Primary.TButton", font=("Segoe UI", 10, "bold"), padding=(18, 10))
            style.configure("Secondary.TButton", padding=(12, 8))

        def _build_layout(self, tk_module: Any, ttk_module: Any, filedialog: Any, messagebox: Any) -> None:
            self.root.configure(background="#f5f5f7")
            shell = ttk_module.Frame(self.root, style="App.TFrame", padding=28)
            shell.pack(fill="both", expand=True)
            shell.columnconfigure(1, weight=1)
            shell.rowconfigure(1, weight=1)

            ttk_module.Label(shell, text="OpenRAW Studio", style="Title.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
            ttk_module.Label(shell, text="A calm, local-first workspace for your RAW photos.", style="Subtitle.TLabel").grid(
                row=0, column=1, sticky="e", padx=(20, 0), pady=(10, 0)
            )

            controls = ttk_module.Frame(shell, style="Panel.TFrame", padding=22)
            controls.grid(row=1, column=0, sticky="ns", pady=(24, 0), padx=(0, 18))
            preview = ttk_module.Frame(shell, style="Panel.TFrame", padding=18)
            preview.grid(row=1, column=1, sticky="nsew", pady=(24, 0))
            preview.columnconfigure(0, weight=1)
            preview.rowconfigure(1, weight=1)

            ttk_module.Label(controls, text="PHOTO", style="Muted.TLabel").pack(anchor="w")
            ttk_module.Label(controls, textvariable=self.source_var, style="Panel.TLabel", wraplength=260).pack(anchor="w", pady=(8, 14))
            ttk_module.Button(controls, text="Import RAW", style="Secondary.TButton", command=self._choose_source).pack(fill="x")
            ttk_module.Button(controls, text="Import Folder", style="Secondary.TButton", command=self._choose_library_folder).pack(
                fill="x", pady=(8, 0)
            )
            ttk_module.Button(controls, text="Create Sample DNG", style="Secondary.TButton", command=self._create_sample_source).pack(
                fill="x", pady=(8, 0)
            )

            ttk_module.Label(controls, text="FOLDER", style="Muted.TLabel").pack(anchor="w", pady=(20, 0))
            library_frame = ttk_module.Frame(controls, style="Panel.TFrame")
            library_frame.pack(fill="x", pady=(8, 6))
            self.library_listbox = tk_module.Listbox(
                library_frame,
                height=5,
                activestyle="none",
                exportselection=False,
                borderwidth=0,
                highlightthickness=1,
                highlightbackground="#d2d2d7",
                selectbackground="#1d1d1f",
                selectforeground="#ffffff",
                font=("Segoe UI", 9),
            )
            library_scroll = ttk_module.Scrollbar(library_frame, orient="vertical", command=self.library_listbox.yview)
            self.library_listbox.configure(yscrollcommand=library_scroll.set)
            self.library_listbox.pack(side="left", fill="both", expand=True)
            library_scroll.pack(side="right", fill="y")
            self.library_listbox.bind("<<ListboxSelect>>", self._select_library_item)
            ttk_module.Label(controls, textvariable=self.library_status_var, style="Muted.TLabel", wraplength=260).pack(anchor="w")

            ttk_module.Label(controls, text="OUTPUT", style="Muted.TLabel").pack(anchor="w", pady=(20, 0))
            ttk_module.Label(controls, textvariable=self.output_var, style="Panel.TLabel", wraplength=260).pack(anchor="w", pady=(8, 14))
            ttk_module.Button(controls, text="Choose Folder", style="Secondary.TButton", command=self._choose_output).pack(fill="x")

            ttk_module.Separator(controls).pack(fill="x", pady=20)
            ttk_module.Label(controls, text="ADJUSTMENTS", style="Muted.TLabel").pack(anchor="w")
            exposure_header = ttk_module.Frame(controls, style="Panel.TFrame")
            exposure_header.pack(fill="x", pady=(8, 0))
            ttk_module.Label(exposure_header, text="Exposure", style="Panel.TLabel").pack(side="left")
            ttk_module.Label(exposure_header, textvariable=self.exposure_label_var, style="Muted.TLabel").pack(side="right")
            ttk_module.Scale(
                controls,
                from_=-2.0,
                to=2.0,
                variable=self.exposure_var,
                orient="horizontal",
                command=self._sync_adjustment_labels,
            ).pack(fill="x", pady=(6, 8))

            contrast_header = ttk_module.Frame(controls, style="Panel.TFrame")
            contrast_header.pack(fill="x")
            ttk_module.Label(contrast_header, text="Contrast", style="Panel.TLabel").pack(side="left")
            ttk_module.Label(contrast_header, textvariable=self.contrast_label_var, style="Muted.TLabel").pack(side="right")
            ttk_module.Scale(
                controls,
                from_=-1.0,
                to=1.0,
                variable=self.contrast_var,
                orient="horizontal",
                command=self._sync_adjustment_labels,
            ).pack(fill="x", pady=(6, 8))

            warmth_header = ttk_module.Frame(controls, style="Panel.TFrame")
            warmth_header.pack(fill="x")
            ttk_module.Label(warmth_header, text="Warmth", style="Panel.TLabel").pack(side="left")
            ttk_module.Label(warmth_header, textvariable=self.warmth_label_var, style="Muted.TLabel").pack(side="right")
            ttk_module.Scale(
                controls,
                from_=-1.0,
                to=1.0,
                variable=self.warmth_var,
                orient="horizontal",
                command=self._sync_adjustment_labels,
            ).pack(fill="x", pady=(6, 8))

            ttk_module.Button(controls, text="Reset Adjustments", style="Secondary.TButton", command=self._reset_adjustments).pack(fill="x")
            self.auto_adjust_button = ttk_module.Button(
                controls,
                text="Auto Adjust",
                style="Secondary.TButton",
                command=self._auto_adjust,
                state="disabled",
            )
            self.auto_adjust_button.pack(fill="x", pady=(12, 0))
            self.preview_button = ttk_module.Button(
                controls,
                text="Update Preview",
                style="Secondary.TButton",
                command=self._update_preview,
                state="disabled",
            )
            self.preview_button.pack(fill="x", pady=(12, 0))
            self.process_button = ttk_module.Button(
                controls,
                text="Export JPEG",
                style="Primary.TButton",
                command=self._export_jpeg,
                state="disabled",
            )
            self.process_button.pack(fill="x", pady=(12, 0))
            self.batch_button = ttk_module.Button(
                controls,
                text="Export Folder",
                style="Secondary.TButton",
                command=self._export_folder,
                state="disabled",
            )
            self.batch_button.pack(fill="x", pady=(8, 0))
            ttk_module.Label(controls, textvariable=self.status_var, style="Muted.TLabel", wraplength=260).pack(anchor="w", pady=(16, 0))

            info_bar = ttk_module.Frame(preview, style="Panel.TFrame")
            info_bar.grid(row=0, column=0, sticky="ew", pady=(0, 14))
            info_bar.columnconfigure(0, weight=1)
            info_bar.columnconfigure(1, weight=1)
            ttk_module.Label(info_bar, text="PHOTO INFO", style="Muted.TLabel").grid(row=0, column=0, sticky="w")
            ttk_module.Label(info_bar, text="OUTPUT PLAN", style="Muted.TLabel").grid(row=0, column=1, sticky="w", padx=(24, 0))
            ttk_module.Label(
                info_bar,
                textvariable=self.photo_info_var,
                style="Muted.TLabel",
                wraplength=320,
                justify="left",
            ).grid(row=1, column=0, sticky="nw", pady=(6, 0))
            ttk_module.Label(
                info_bar,
                textvariable=self.output_info_var,
                style="Muted.TLabel",
                wraplength=360,
                justify="left",
            ).grid(row=1, column=1, sticky="nw", padx=(24, 0), pady=(6, 0))

            self.preview_label = tk_module.Label(
                preview,
                text="Your preview will appear here",
                background="#f5f5f7",
                foreground="#6e6e73",
                font=("Segoe UI", 14),
            )
            self.preview_label.grid(row=1, column=0, sticky="nsew")
            ttk_module.Label(preview, textvariable=self.preview_state_var, style="Muted.TLabel").grid(row=2, column=0, sticky="w", pady=(14, 0))
            self.export_label = ttk_module.Label(preview, text="", style="Muted.TLabel", wraplength=680)
            self.export_label.grid(row=3, column=0, sticky="w", pady=(8, 0))
            preview_actions = ttk_module.Frame(preview, style="Panel.TFrame")
            preview_actions.grid(row=4, column=0, sticky="ew", pady=(12, 0))
            preview_actions.columnconfigure(1, weight=1)
            self.compare_button = ttk_module.Button(
                preview_actions,
                text="Show Before",
                style="Secondary.TButton",
                command=self._toggle_compare,
                state="disabled",
            )
            self.compare_button.grid(row=0, column=0, sticky="w")
            self.open_folder_button = ttk_module.Button(
                preview_actions,
                text="Open Output Folder",
                style="Secondary.TButton",
                command=self._open_output_folder,
                state="disabled",
            )
            self.open_folder_button.grid(row=0, column=2, sticky="e")
            self.open_export_button = ttk_module.Button(
                preview_actions,
                text="Open JPEG",
                style="Secondary.TButton",
                command=self._open_export,
                state="disabled",
            )
            self.open_export_button.grid(row=0, column=1)

            self.filedialog = filedialog
            self.messagebox = messagebox

        def _choose_source(self) -> None:
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
            selected = self.filedialog.askdirectory(title="Import folder")
            if not selected:
                return
            self._start_library_scan(Path(selected))

        def _start_library_scan(self, folder: Path) -> None:
            self.library_scan_counter += 1
            scan_id = self.library_scan_counter
            self.library_dir = folder
            self.library_items = []
            self.library_listbox.delete(0, "end")
            self.library_status_var.set("Scanning folder...")
            threading.Thread(target=self._library_scan_worker, args=(scan_id, folder), daemon=True).start()

        def _library_scan_worker(self, scan_id: int, folder: Path) -> None:
            try:
                items = _scan_library_folder(folder)
            except OSError as exc:
                self.root.after(0, lambda: self._show_library_error(scan_id, _friendly_error_message(exc)))
                return
            self.root.after(0, lambda: self._show_library_items(scan_id, folder, items))

        def _show_library_error(self, scan_id: int, message: str) -> None:
            if scan_id != self.library_scan_counter:
                return
            self.library_status_var.set(message)
            self._set_busy(False)

        def _show_library_items(self, scan_id: int, folder: Path, items: tuple[tuple[Path, str, bool], ...]) -> None:
            if scan_id != self.library_scan_counter:
                return
            self.library_items = list(items)
            self.library_listbox.delete(0, "end")
            for _path, label, _can_render in self.library_items:
                self.library_listbox.insert("end", label)
            self.library_status_var.set(_folder_status_text(folder, len(self.library_items)))
            self._set_busy(False)
            if self.library_items:
                first_supported = next((index for index, item in enumerate(self.library_items) if item[2]), 0)
                self.library_listbox.selection_set(first_supported)
                self.library_listbox.activate(first_supported)
                self.library_listbox.see(first_supported)
                self._select_source(self.library_items[first_supported][0], ready_status="Folder imported")

        def _select_library_item(self, _event: Any = None) -> None:
            selection = self.library_listbox.curselection()
            if not selection:
                return
            index = int(selection[0])
            if index < 0 or index >= len(self.library_items):
                return
            self._select_source(self.library_items[index][0], ready_status="Photo selected from folder")

        def _create_sample_source(self) -> None:
            try:
                sample_path = write_synthetic_dng(_default_sample_path())
            except OSError as exc:
                self._show_error(_friendly_error_message(exc))
                return
            self._select_source(sample_path, ready_status="Sample DNG ready")

        def _select_source(self, source: Path, *, ready_status: str) -> None:
            self.run_counter += 1
            self.source_path = source
            self.current_can_preview = None
            self.current_can_render = None
            self.source_var.set(source.name)
            self.photo_info_var.set("Reading photo info...")
            if self.output_dir is None:
                self.output_dir = source.parent / "openraw-output"
                self.output_var.set(str(self.output_dir))
            self._refresh_output_info()
            self._clear_result()
            self._set_adjustment_values(_manual_overrides(0.0, 0.0, 0.0))
            recipe_status = self._restore_recipe_if_available()
            self._set_busy(False)
            self.status_var.set(recipe_status or ready_status)
            threading.Thread(target=self._photo_info_worker, args=(source,), daemon=True).start()

        def _choose_output(self) -> None:
            selected = self.filedialog.askdirectory(title="Choose output folder")
            if selected:
                self.output_dir = Path(selected)
                self.output_var.set(str(self.output_dir))
                self._refresh_output_info()
                if recipe_status := self._restore_recipe_if_available():
                    self.status_var.set(recipe_status)
                elif self.source_path is not None:
                    self.status_var.set("Output folder updated")

        def _refresh_output_info(self) -> None:
            if self.source_path is None:
                self.output_info_var.set("Output plan appears after import")
                return
            output_dir = self.output_dir or (self.source_path.parent / "openraw-output")
            plan = ArtifactPlan.for_source(self.source_path, output_dir)
            summary = _planned_output_summary(self.source_path, output_dir)
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
            self.warmth_var.set(float(overrides.get("warmth", 0.0)))
            self._sync_adjustment_labels(update_status=False)

        def _restore_recipe_if_available(self) -> str | None:
            if self.source_path is None:
                return None
            recipe_path = self._current_recipe_path()
            if recipe_path is None or not recipe_path.exists():
                return None
            try:
                overrides = _load_recipe_adjustments(recipe_path, self.source_path)
            except (OSError, ValueError):
                return "Saved recipe could not be loaded"
            self._set_adjustment_values(overrides)
            self._refresh_preview_state()
            return "Saved recipe loaded"

        def _photo_info_worker(self, source: Path) -> None:
            try:
                info, support = _read_photo_info_with_support(source)
            except OSError:
                info = "Photo info unavailable"
                support = None
            self.root.after(0, lambda: self._show_photo_info(source, info, support))

        def _show_photo_info(self, source: Path, info: str, support: NativeSupportReport | None) -> None:
            if self.source_path != source:
                return
            if support is not None:
                self.current_can_preview = support.can_preview or support.can_render
                self.current_can_render = support.can_render
                if support.can_preview and not support.can_render:
                    self.status_var.set("RAW preview ready; export support is next")
                elif support.can_inspect and not support.can_render:
                    self.status_var.set("RAW metadata imported; preview/export support is next")
                self._set_busy(False)
            self.photo_info_var.set(info)

        def _sync_adjustment_labels(self, *_: Any, update_status: bool = True) -> None:
            self.exposure_label_var.set(_format_exposure_label(float(self.exposure_var.get())))
            self.contrast_label_var.set(_format_adjustment_label(float(self.contrast_var.get())))
            self.warmth_label_var.set(_format_adjustment_label(float(self.warmth_var.get())))
            if update_status and self.source_path is not None and not self.is_busy:
                preview_state = self._refresh_preview_state()
                self.status_var.set(preview_state if preview_state == "Preview needs update" else "Adjustments changed")

        def _reset_adjustments(self) -> None:
            self.exposure_var.set(0.0)
            self.contrast_var.set(0.0)
            self.warmth_var.set(0.0)
            self._sync_adjustment_labels()

        def _auto_adjust(self) -> None:
            if self.source_path is None:
                self.messagebox.showinfo("OpenRAW Studio", "Import a RAW photo first.")
                return
            self.run_counter += 1
            run_id = self.run_counter
            self._set_busy(True)
            self.status_var.set("Auto adjusting...")
            threading.Thread(target=self._auto_adjust_worker, args=(run_id, self.source_path), daemon=True).start()

        def _auto_adjust_worker(self, run_id: int, source: Path) -> None:
            try:
                suggestion = suggest_auto_adjustments(source)
            except (PipelineError, OSError, ValueError, RuntimeError, NotImplementedError) as exc:
                message = _friendly_error_message(exc)
                self.root.after(0, lambda: self._show_error(message, run_id=run_id))
                return
            self.root.after(0, lambda: self._apply_auto_adjustment(suggestion, run_id=run_id))

        def _apply_auto_adjustment(self, suggestion: AutoAdjustSuggestion, *, run_id: int) -> None:
            if run_id != self.run_counter:
                return
            self.exposure_var.set(suggestion.exposure)
            self.contrast_var.set(suggestion.contrast)
            self.warmth_var.set(suggestion.warmth)
            self._sync_adjustment_labels()
            self._set_busy(False)
            preview_state = self._refresh_preview_state()
            self.status_var.set(_auto_adjust_status(suggestion) if preview_state != "Preview current" else "Auto Adjust applied")

        def _clear_result(self) -> None:
            self.preview_photo = None
            self.before_photo = None
            self.after_photo = None
            self.last_export_path = None
            self.last_preview_overrides = None
            self.showing_after = True
            self.preview_label.configure(image="", text="Your preview will appear here")
            self.preview_state_var.set("No preview yet")
            self.export_label.configure(text="")
            self.compare_button.configure(state="disabled", text="Show Before")
            self.open_folder_button.configure(state="disabled")
            self.open_export_button.configure(state="disabled")

        def _update_preview(self) -> None:
            self._start_pipeline(preview_only=True)

        def _export_jpeg(self) -> None:
            self._start_pipeline(preview_only=False)

        def _export_folder(self) -> None:
            sources = _library_sources(self.library_items)
            supported_sources = _supported_library_sources(self.library_items)
            if not supported_sources:
                self.messagebox.showinfo("OpenRAW Studio", "Import a folder with supported DNG files first.")
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
            self._set_busy(True)
            self.status_var.set(f"Exporting {len(supported_sources)} supported photos...")
            self.preview_state_var.set("Batch export running...")
            self.export_label.configure(text="")
            threading.Thread(
                target=self._batch_export_worker,
                args=(run_id, sources, output_dir, overrides),
                daemon=True,
            ).start()

        def _batch_export_worker(
            self,
            run_id: int,
            sources: tuple[Path, ...],
            output_dir: Path,
            overrides: dict[str, float],
        ) -> None:
            def on_progress(done: int, total: int, item: BatchItemResult) -> None:
                text = _batch_progress_text(done, total, item)
                self.root.after(0, lambda run_id=run_id, text=text: self._show_batch_progress(run_id, text))

            result = run_batch_export(sources, output_dir, overrides=overrides, progress_callback=on_progress)
            self.root.after(0, lambda run_id=run_id, result=result: self._show_batch_result(run_id, result))

        def _show_batch_progress(self, run_id: int, text: str) -> None:
            if run_id != self.run_counter:
                return
            self.status_var.set(text)

        def _show_batch_result(self, run_id: int, result: BatchResult) -> None:
            if run_id != self.run_counter:
                return
            self._set_busy(False)
            self.status_var.set(_batch_result_status(result))
            self.preview_state_var.set(_preview_state_text(self.last_preview_overrides, self._current_overrides()))
            self.export_label.configure(text=_format_batch_result_summary(result))
            self._refresh_output_info()
            if result.processed:
                self.open_folder_button.configure(state="normal")

        def _start_pipeline(self, *, preview_only: bool) -> None:
            if self.source_path is None:
                self.messagebox.showinfo("OpenRAW Studio", "Import a RAW photo first.")
                return
            output_dir = self.output_dir or (self.source_path.parent / "openraw-output")
            self.output_dir = output_dir
            self.output_var.set(str(output_dir))
            self._refresh_output_info()
            self.run_counter += 1
            run_id = self.run_counter
            self._set_busy(True)
            self.status_var.set("Updating preview..." if preview_only else "Exporting JPEG...")
            self.preview_state_var.set("Updating preview..." if preview_only else "Exporting preview and JPEG...")
            self.export_label.configure(text="")
            self.last_export_path = None
            self.open_export_button.configure(state="disabled")
            overrides = self._current_overrides()
            threading.Thread(
                target=self._process_worker,
                args=(
                    run_id,
                    self.source_path,
                    output_dir,
                    overrides,
                    preview_only,
                ),
                daemon=True,
            ).start()

        def _process_worker(self, run_id: int, source: Path, output_dir: Path, overrides: dict[str, float], preview_only: bool) -> None:
            try:
                result = LocalPhotoPipeline().process(
                    PipelineRequest(
                        source,
                        output_dir,
                        overrides=overrides,
                        preview_only=preview_only,
                    )
                )
            except (PipelineError, OSError, ValueError) as exc:
                message = _friendly_error_message(exc)
                self.root.after(0, lambda: self._show_error(message, run_id=run_id))
                return
            self.root.after(0, lambda: self._show_result(result, source, overrides=overrides, run_id=run_id))

        def _set_busy(self, busy: bool) -> None:
            self.is_busy = busy
            can_preview = self.current_can_preview is True or self.current_can_render is True
            can_render = self.current_can_render is True
            preview_state = "normal" if not busy and self.source_path is not None and can_preview else "disabled"
            render_state = "normal" if not busy and self.source_path is not None and can_render else "disabled"
            self.auto_adjust_button.configure(state=render_state)
            self.preview_button.configure(state=preview_state)
            self.process_button.configure(state=render_state)
            batch_state = "disabled" if busy or not _supported_library_sources(self.library_items) else "normal"
            self.batch_button.configure(state=batch_state)

        def _current_overrides(self) -> dict[str, float]:
            return _manual_overrides(
                float(self.exposure_var.get()),
                float(self.contrast_var.get()),
                float(self.warmth_var.get()),
            )

        def _refresh_preview_state(self) -> str:
            preview_state = _preview_state_text(self.last_preview_overrides, self._current_overrides())
            self.preview_state_var.set(preview_state)
            return preview_state

        def _show_error(self, message: str, *, run_id: int | None = None) -> None:
            if run_id is not None and run_id != self.run_counter:
                return
            self._set_busy(False)
            self.status_var.set("Processing failed")
            self._refresh_preview_state()
            self.messagebox.showerror("OpenRAW Studio", message)

        def _show_result(self, result: Any, source: Path, *, overrides: dict[str, float], run_id: int) -> None:
            if run_id != self.run_counter:
                return
            self._set_busy(False)
            self.status_var.set(_result_status(result))
            if result.preview is not None and result.preview.path.exists():
                self.last_preview_overrides = dict(overrides)
                try:
                    from PIL import Image, ImageTk

                    with Image.open(result.preview.path) as opened:
                        image = opened.convert("RGB")
                    image.thumbnail((700, 520))
                    self.after_photo = ImageTk.PhotoImage(image)
                    before = render_preview_image(source, apply_color=False)
                    before_image = Image.frombytes("RGB", (before.width, before.height), _flatten_rgb_pixels(before.pixels))
                    before_image.thumbnail((700, 520))
                    self.before_photo = ImageTk.PhotoImage(before_image)
                    self.preview_photo = self.after_photo
                    self.preview_label.configure(image=self.preview_photo, text="")
                    self.compare_button.configure(state="normal", text="Show Before")
                    self.showing_after = True
                except (OSError, RuntimeError):
                    self.preview_label.configure(text="Preview created. Open the output folder to view it.", image="")
            self._refresh_preview_state()
            self.export_label.configure(text=_format_result_summary(result))
            if result.preview is not None or result.exports:
                self.open_folder_button.configure(state="normal")
            if result.exports:
                self.last_export_path = result.exports[0].path
                self.open_export_button.configure(state="normal")

        def _toggle_compare(self) -> None:
            if self.after_photo is None or self.before_photo is None:
                return
            self.showing_after = not self.showing_after
            self.preview_photo = self.after_photo if self.showing_after else self.before_photo
            self.preview_label.configure(image=self.preview_photo)
            self.compare_button.configure(text="Show Before" if self.showing_after else "Show After")

        def _open_output_folder(self) -> None:
            if self.output_dir is None or not self.output_dir.exists():
                return
            _open_in_system(self.output_dir)

        def _open_export(self) -> None:
            if self.last_export_path is None or not self.last_export_path.exists():
                return
            _open_in_system(self.last_export_path)

    root = tk.Tk()
    DesktopApp(root)
    root.mainloop()
