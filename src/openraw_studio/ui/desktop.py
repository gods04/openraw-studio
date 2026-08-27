"""Small local desktop shell for the OpenRAW Studio V0.1 pipeline."""

from __future__ import annotations

from pathlib import Path
import threading
import os
import subprocess
import sys
from typing import Any

from openraw_studio.pipeline.errors import BackendUnavailableError, PipelineError, SourceFileError
from openraw_studio.pipeline.interfaces import PipelineRequest
from openraw_studio.pipeline.local import LocalPhotoPipeline
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

            self.source_var = tk.StringVar(value="No RAW photo selected")
            self.output_var = tk.StringVar(value="Output folder will be chosen automatically")
            self.status_var = tk.StringVar(value="Choose a RAW photo to begin")
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
            preview.rowconfigure(0, weight=1)

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
            self.process_button = ttk_module.Button(controls, text="AUTO  Process Photo", style="Primary.TButton", command=self._process)
            self.process_button.pack(fill="x", pady=(12, 0))
            ttk_module.Label(controls, textvariable=self.status_var, style="Muted.TLabel", wraplength=260).pack(anchor="w", pady=(16, 0))

            self.preview_label = tk_module.Label(
                preview,
                text="Your preview will appear here",
                background="#f5f5f7",
                foreground="#6e6e73",
                font=("Segoe UI", 14),
            )
            self.preview_label.grid(row=0, column=0, sticky="nsew")
            preview_actions = ttk_module.Frame(preview, style="Panel.TFrame")
            preview_actions.grid(row=2, column=0, sticky="ew", pady=(12, 0))
            preview_actions.columnconfigure(1, weight=1)
            self.compare_button = ttk_module.Button(
                preview_actions,
                text="Show Before",
                style="Secondary.TButton",
                command=self._toggle_compare,
                state="disabled",
            )
            self.compare_button.grid(row=0, column=0, sticky="w")
            self.export_label = ttk_module.Label(preview, text="", style="Muted.TLabel", wraplength=680)
            self.export_label.grid(row=1, column=0, sticky="w", pady=(14, 0))
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
            self.source_path = source
            self.source_var.set(source.name)
            if self.output_dir is None:
                self.output_dir = source.parent / "openraw-output"
                self.output_var.set(str(self.output_dir))
            self._clear_result()

        def _choose_output(self) -> None:
            selected = self.filedialog.askdirectory(title="Choose output folder")
            if selected:
                self.output_dir = Path(selected)
                self.output_var.set(str(self.output_dir))

        def _sync_adjustment_labels(self, *_: Any) -> None:
            self.exposure_label_var.set(_format_exposure_label(float(self.exposure_var.get())))
            self.contrast_label_var.set(_format_adjustment_label(float(self.contrast_var.get())))
            self.warmth_label_var.set(_format_adjustment_label(float(self.warmth_var.get())))

        def _reset_adjustments(self) -> None:
            self.exposure_var.set(0.0)
            self.contrast_var.set(0.0)
            self.warmth_var.set(0.0)
            self._sync_adjustment_labels()

        def _clear_result(self) -> None:
            self.preview_photo = None
            self.before_photo = None
            self.after_photo = None
            self.last_export_path = None
            self.showing_after = True
            self.preview_label.configure(image="", text="Your preview will appear here")
            self.export_label.configure(text="")
            self.compare_button.configure(state="disabled", text="Show Before")
            self.open_folder_button.configure(state="disabled")
            self.open_export_button.configure(state="disabled")

        def _process(self) -> None:
            if self.source_path is None:
                self.messagebox.showinfo("OpenRAW Studio", "Import a RAW photo first.")
                return
            output_dir = self.output_dir or (self.source_path.parent / "openraw-output")
            self.output_dir = output_dir
            self.process_button.configure(state="disabled")
            self.status_var.set("Processing locally...")
            self.export_label.configure(text="")
            self.open_export_button.configure(state="disabled")
            threading.Thread(
                target=self._process_worker,
                args=(
                    self.source_path,
                    output_dir,
                    float(self.exposure_var.get()),
                    float(self.contrast_var.get()),
                    float(self.warmth_var.get()),
                ),
                daemon=True,
            ).start()

        def _process_worker(self, source: Path, output_dir: Path, exposure: float, contrast: float, warmth: float) -> None:
            try:
                result = LocalPhotoPipeline().process(
                    PipelineRequest(
                        source,
                        output_dir,
                        overrides={"exposure": exposure, "contrast": contrast, "warmth": warmth},
                    )
                )
            except (PipelineError, OSError, ValueError) as exc:
                message = _friendly_error_message(exc)
                self.root.after(0, lambda: self._show_error(message))
                return
            self.root.after(0, lambda: self._show_result(result, source))

        def _show_error(self, message: str) -> None:
            self.process_button.configure(state="normal")
            self.status_var.set("Processing failed")
            self.messagebox.showerror("OpenRAW Studio", message)

        def _show_result(self, result: Any, source: Path) -> None:
            self.process_button.configure(state="normal")
            self.status_var.set("Finished")
            if result.preview is not None and result.preview.path.exists():
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
