"""Small local desktop shell for the OpenRAW Studio V0.1 pipeline."""

from __future__ import annotations

from pathlib import Path
import threading
import os
import subprocess
import sys
from typing import Any, Mapping

from openraw_studio.core.artifacts import ArtifactPlan
from openraw_studio.decision.auto_adjust import AutoAdjustSuggestion, suggest_auto_adjustments
from openraw_studio.pipeline.errors import BackendUnavailableError, PipelineError, SourceFileError
from openraw_studio.pipeline.interfaces import PipelineRequest
from openraw_studio.pipeline.local import LocalPhotoPipeline
from openraw_studio.raw.native.dng import DngMetadataError, DngMetadataReader
from openraw_studio.raw.native.preview import render_preview_image
from openraw_studio.raw.native.synthetic import write_synthetic_dng


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

    version = _format_metadata_value(metadata.get("dng_version_text"))
    if version:
        lines.append(f"DNG: {version}")

    if size_bytes is not None:
        lines.append(f"Size: {_format_bytes(size_bytes)}")

    return "\n".join(lines)


def _read_photo_info(path: Path) -> str:
    size_bytes = path.stat().st_size
    if path.suffix.lower() != ".dng":
        return _format_photo_info(path, {}, size_bytes=size_bytes)

    try:
        metadata = DngMetadataReader().read(path).as_dict()
    except DngMetadataError:
        return _format_photo_info(path, {}, size_bytes=size_bytes) + "\nDNG metadata: unavailable"
    return _format_photo_info(path, metadata, size_bytes=size_bytes)


def _planned_output_summary(source: Path, output_dir: Path) -> str:
    plan = ArtifactPlan.for_source(source, output_dir)
    return "\n".join(
        [
            f"Folder: {_short_path(plan.output_dir, max_chars=68)}",
            f"Preview: {_display_path(plan.preview_path, base=plan.output_dir)}",
            f"JPEG: {_display_path(plan.export_path, base=plan.output_dir)}",
            f"Recipe: {_display_path(plan.recipe_path, base=plan.output_dir)}",
        ]
    )


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
            return "This file type is not supported yet. OpenRAW Studio V0.1 is DNG-first."
        if "Source file does not exist" in message:
            return "The selected photo could not be found. It may have been moved or deleted."
    if isinstance(error, BackendUnavailableError):
        if "only uncompressed strips are supported" in message:
            return "This DNG uses a structure that OpenRAW Native does not support yet. Try the built-in sample DNG for the current V0.1 path."
        if "currently starts with DNG files" in message:
            return "OpenRAW Native currently processes DNG files first. Broader camera RAW formats are still on the roadmap."
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
            self.showing_after = True
            self.last_export_path: Path | None = None
            self.last_preview_overrides: dict[str, float] | None = None
            self.run_counter = 0
            self.is_busy = False

            self.source_var = tk.StringVar(value="No RAW photo selected")
            self.output_var = tk.StringVar(value="Output folder will be chosen automatically")
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
            ttk_module.Button(controls, text="Create Sample DNG", style="Secondary.TButton", command=self._create_sample_source).pack(
                fill="x", pady=(8, 0)
            )

            ttk_module.Label(controls, text="OUTPUT", style="Muted.TLabel").pack(anchor="w", pady=(28, 0))
            ttk_module.Label(controls, textvariable=self.output_var, style="Panel.TLabel", wraplength=260).pack(anchor="w", pady=(8, 14))
            ttk_module.Button(controls, text="Choose Folder", style="Secondary.TButton", command=self._choose_output).pack(fill="x")

            ttk_module.Separator(controls).pack(fill="x", pady=28)
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
                filetypes=[("DNG RAW", "*.dng *.DNG"), ("All files", "*.*")],
            )
            if not selected:
                return
            self._select_source(Path(selected))
            self.status_var.set("Ready to process")

        def _create_sample_source(self) -> None:
            try:
                sample_path = write_synthetic_dng(_default_sample_path())
            except OSError as exc:
                self._show_error(_friendly_error_message(exc))
                return
            self._select_source(sample_path)
            self.status_var.set("Sample DNG ready")

        def _select_source(self, source: Path) -> None:
            self.run_counter += 1
            self.source_path = source
            self.source_var.set(source.name)
            self.photo_info_var.set("Reading photo info...")
            if self.output_dir is None:
                self.output_dir = source.parent / "openraw-output"
                self.output_var.set(str(self.output_dir))
            self._refresh_output_info()
            self._clear_result()
            self._set_busy(False)
            threading.Thread(target=self._photo_info_worker, args=(source,), daemon=True).start()

        def _choose_output(self) -> None:
            selected = self.filedialog.askdirectory(title="Choose output folder")
            if selected:
                self.output_dir = Path(selected)
                self.output_var.set(str(self.output_dir))
                self._refresh_output_info()

        def _refresh_output_info(self) -> None:
            if self.source_path is None:
                self.output_info_var.set("Output plan appears after import")
                return
            output_dir = self.output_dir or (self.source_path.parent / "openraw-output")
            self.output_info_var.set(_planned_output_summary(self.source_path, output_dir))

        def _photo_info_worker(self, source: Path) -> None:
            try:
                info = _read_photo_info(source)
            except OSError:
                info = "Photo info unavailable"
            self.root.after(0, lambda: self._show_photo_info(source, info))

        def _show_photo_info(self, source: Path, info: str) -> None:
            if self.source_path != source:
                return
            self.photo_info_var.set(info)

        def _sync_adjustment_labels(self, *_: Any) -> None:
            self.exposure_label_var.set(_format_exposure_label(float(self.exposure_var.get())))
            self.contrast_label_var.set(_format_adjustment_label(float(self.contrast_var.get())))
            self.warmth_label_var.set(_format_adjustment_label(float(self.warmth_var.get())))
            if self.source_path is not None and not self.is_busy:
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
            state = "disabled" if busy or self.source_path is None else "normal"
            self.auto_adjust_button.configure(state=state)
            self.preview_button.configure(state=state)
            self.process_button.configure(state=state)

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
