"""Measure actual Tk slider-to-display latency and full-resolution export.

Run with the project Python and a local supported RAW. All exports and optional
screenshots go to the requested output directory, never beside the source RAW.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from statistics import median
from time import perf_counter


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cpu", action="store_true")
    parser.add_argument("--geometry", default="1080x720")
    parser.add_argument("--screenshot", action="store_true")
    parser.add_argument("--highlights", type=float, help="Exercise a fixed highlight correction while dragging exposure")
    args = parser.parse_args()
    if args.highlights is not None and not -1 <= args.highlights <= 1:
        parser.error("--highlights must be within [-1, 1]")
    if args.cpu:
        os.environ["OPENRAW_GPU"] = "off"
    from PIL import Image, ImageTk

    from openraw_studio.core.files import sha256_file
    from openraw_studio.ui.desktop import launch_desktop_app

    args.output.mkdir(parents=True, exist_ok=True)
    checksum = sha256_file(args.source)
    app = launch_desktop_app(run_mainloop=False, session_dir=args.output / "sessions")
    app.root.geometry(args.geometry)
    app.output_dir = args.output
    app.messagebox.askyesno = lambda *_args, **_kwargs: True
    report = {
        "source": str(args.source.resolve()),
        "highlights": args.highlights,
        "mode": "CPU" if args.cpu else "auto",
        "slider_to_display_ms": [],
        "errors": [],
    }

    def callback_error(*exc):
        import traceback

        traceback.print_exception(*exc)
        report["errors"].append(str(exc[1]))
        finish()

    app.root.report_callback_exception = callback_error
    started = perf_counter()
    values = iter([-0.6, -0.3, 0, 0.3, 0.6, 0.9, 0.5, 0.1, -0.2, 0.2])
    previous_pixels = None
    target = None
    phase = "loading"
    changed_at = None
    slider = None
    drag_count = 0
    drag_frames = 0
    last_drag_frame = None
    drag_finished_at = None

    def drag():
        nonlocal drag_count, drag_finished_at, target
        target = round(-1.0 + drag_count * 0.04, 2)
        slider.set(target)
        drag_count += 1
        if drag_count < 50:
            app.root.after(16, drag)
        else:
            drag_finished_at = perf_counter()

    def descendants(widget):
        for child in widget.winfo_children():
            yield child
            yield from descendants(child)

    def finish():
        from openraw_studio.raw.native import compiled_bayer, compiled_tone

        report["compiled_cpu_bayer"] = bool(getattr(compiled_bayer.demosaic, "signatures", []))
        report["cpu_bayer_fallback_reason"] = compiled_bayer.last_error
        report["compiled_cpu_tone"] = bool(getattr(compiled_tone.tone, "signatures", []))
        report["cpu_tone_fallback_reason"] = compiled_tone.last_error
        report["source_unchanged"] = sha256_file(args.source) == checksum
        (args.output / "benchmark.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
        print(json.dumps(report, indent=2), flush=True)
        app.is_busy = False
        app._close()

    def step():
        nonlocal \
            phase, \
            target, \
            changed_at, \
            slider, \
            previous_pixels, \
            drag_frames, \
            last_drag_frame
        if perf_counter() - started > 60:
            report["errors"].append("Timed out waiting for UI")
            finish()
            return
        ready = app.last_preview_overrides is not None
        if phase == "loading" and ready:
            report["first_raw_preview_ms"] = (perf_counter() - started) * 1000
            report["backend"] = app.preview_state_var.get()
            if args.highlights is not None:
                app.highlights_var.set(args.highlights)
            slider = next(
                w
                for w in descendants(app.root)
                if w.winfo_class() == "TScale"
                and str(w.cget("variable")) == str(app.exposure_var)
            )
            phase = "adjust"
        if phase == "adjust":
            if (
                target is not None
                and app.last_preview_overrides.get("exposure") == target
            ):
                report["slider_to_display_ms"].append(
                    (perf_counter() - changed_at) * 1000
                )
                pixels = ImageTk.getimage(app.after_photo).tobytes()
                if previous_pixels is not None and previous_pixels == pixels:
                    report["errors"].append("Displayed pixels did not change")
                previous_pixels = pixels
                target = None
            if target is None:
                target = next(values, None)
                if target is None:
                    report["median_slider_ms"] = median(report["slider_to_display_ms"])
                    report["max_slider_ms"] = max(report["slider_to_display_ms"])
                    phase = "drag"
                    drag()
                else:
                    changed_at = perf_counter()
                    slider.set(target)
        elif phase == "drag":
            displayed = app.last_preview_overrides.get("exposure")
            if displayed != last_drag_frame and drag_finished_at is None:
                drag_frames += 1
                last_drag_frame = displayed
            if drag_finished_at is not None and displayed == target:
                report["continuous_drag_frames"] = drag_frames
                report["drag_release_to_final_ms"] = (
                    perf_counter() - drag_finished_at
                ) * 1000
                if drag_frames < 3:
                    report["errors"].append(
                        "Continuous drag did not update while moving"
                    )
                changed_at = perf_counter()
                app._export_photo()
                phase = "export"
        elif phase == "export" and not app.is_busy and app.last_export_path is not None:
            report["export_seconds"] = perf_counter() - changed_at
            with Image.open(app.last_export_path) as exported:
                report["export_size"] = list(exported.size)
            report["preview_area"] = [
                app.preview_label.winfo_width(),
                app.preview_label.winfo_height(),
            ]
            if args.screenshot and os.name == "nt":
                phase = "capture"

                def capture():
                    try:
                        capture_window(app.root, args.output / "desktop.png")
                    except Exception as exc:
                        report["errors"].append(str(exc))
                    finish()

                app.root.after(500, capture)
            else:
                finish()
            return
        app.root.after(10, step)

    app.root.after(0, lambda: app._select_source(args.source, ready_status="Benchmark"))
    app.root.after(10, step)
    app.root.mainloop()
    return 1 if report["errors"] or not report["source_unchanged"] else 0


def capture_window(root, path):
    import ctypes
    from ctypes import wintypes

    from PIL import ImageGrab

    root.update_idletasks()
    user = ctypes.windll.user32
    user.GetParent.argtypes = [wintypes.HWND]
    user.GetParent.restype = wintypes.HWND
    hwnd = user.GetParent(root.winfo_id())
    ImageGrab.grab(window=hwnd).save(path)


if __name__ == "__main__":
    raise SystemExit(main())
