"""Exercise real Tk pointer actions, history, Auto, comparison and export."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from time import perf_counter

from PIL import Image, ImageTk

from openraw_studio.core.files import sha256_file
from openraw_studio.raw.native.synthetic import write_synthetic_dng
from openraw_studio.ui.desktop import launch_desktop_app

from benchmark_live_preview import capture_window


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--geometry", default="1280x820")
    parser.add_argument("--color-noise", type=float, default=0)
    args = parser.parse_args()
    if not 0 <= args.color_noise <= 1:
        parser.error("--color-noise must be within [0, 1]")
    args.output.mkdir(parents=True, exist_ok=True)
    source = args.source or write_synthetic_dng(
        args.output / "sample.DNG", width=80, height=60
    )
    checksum = sha256_file(source)
    app = launch_desktop_app(run_mainloop=False, session_dir=args.output / "sessions")
    app.root.geometry(args.geometry)
    app.output_dir = args.output
    app.messagebox.askyesno = lambda *_args, **_kwargs: True
    report = {"checks": [], "errors": []}
    state = {"phase": "load", "started": perf_counter()}
    app.messagebox.showerror = lambda title, message: fail(
        RuntimeError(f"{title}: {message}")
    )

    def fail(error):
        report["errors"].append(str(error))
        finish()

    def finish():
        report["source_unchanged"] = checksum == sha256_file(source)
        (args.output / "workflow.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
        print(json.dumps(report, indent=2), flush=True)
        app.is_busy = False
        app._close()

    def require(condition, message):
        if not condition:
            raise AssertionError(message)
        report["checks"].append(message)

    def frame_current():
        return app.last_preview_overrides == app._current_overrides()

    def descendants(widget):
        for child in widget.winfo_children():
            yield child
            yield from descendants(child)

    def drag_scale(scale, value):
        start = scale.coords()
        end = scale.coords(value)
        require(
            abs(end[0] - start[0]) > 5,
            "Scale thumb has real value-dependent coordinates",
        )
        scale.event_generate("<ButtonPress-1>", x=start[0], y=start[1])
        scale.event_generate("<B1-Motion>", x=end[0], y=end[1])
        scale.event_generate("<ButtonRelease-1>", x=end[0], y=end[1])

    def tick():
        try:
            step()
        except Exception as error:
            fail(error)

    def step():
        phase = state["phase"]
        if perf_counter() - state["started"] > 100:
            raise TimeoutError(f"Workflow stalled in {phase}")
        if phase == "load" and app.last_preview_overrides is not None:
            app._reset_adjustments()
            state["phase"] = "reset"
        elif phase == "reset" and frame_current():
            require(app.reference_image is not None, "Unedited RAW reference available")
            state["original_pixels"] = ImageTk.getimage(app.after_photo).tobytes()
            scale = next(
                w
                for w in app.edit_scales
                if str(w.cget("variable")) == str(app.exposure_var)
            )
            drag_scale(scale, 0.8)
            require(app.exposure_var.get() > 0.5, "Pointer dragging changes exposure")
            state["manual"] = app._current_overrides()
            state["phase"] = "manual"
        elif phase == "manual" and frame_current():
            require(
                ImageTk.getimage(app.after_photo).tobytes() != state["original_pixels"],
                "Pointer edit changes displayed pixels",
            )
            app.undo_button.invoke()
            require(app.exposure_var.get() == 0, "Undo restores original adjustment")
            state["phase"] = "undo"
        elif phase == "undo" and frame_current():
            app.redo_button.invoke()
            require(
                app._current_overrides() == state["manual"],
                "Redo restores edited adjustment",
            )
            state["phase"] = "redo"
        elif phase == "redo" and frame_current():
            if args.color_noise:
                state['before_noise_pixels'] = ImageTk.getimage(app.after_photo).tobytes()
                header = next(w for w in descendants(app.root) if w.winfo_class() == "TCheckbutton" and str(w.cget("text")) == "Detail")
                header.invoke()
                app.root.update_idletasks()
                app.controls_canvas.yview_moveto(1)
                app.root.update_idletasks()
                scale = next(w for w in app.edit_scales if str(w.cget("variable")) == str(app.color_noise_var))
                require(app.controls_canvas.winfo_rooty() <= scale.winfo_rooty() < app.controls_canvas.winfo_rooty() + app.controls_canvas.winfo_height(), "Color-noise slider is accessible in compact window")
                drag_scale(scale,args.color_noise)
                require(abs(app.color_noise_var.get()-args.color_noise)<.02,"Pointer dragging changes color-noise strength")
                state['noise']=app._current_overrides()['color_noise']
                state['phase']='noise'
            else:
                state['phase']='start_auto'
        elif phase == 'noise' and frame_current():
            require(ImageTk.getimage(app.after_photo).tobytes() != state['before_noise_pixels'], 'Color-noise edit changes displayed pixels')
            app.undo_button.invoke()
            require(app.color_noise_var.get()==0,'Undo restores color-noise strength')
            app.redo_button.invoke()
            require(app.color_noise_var.get()==state['noise'],'Redo restores color-noise strength')
            state['phase']='start_auto'
        elif phase == 'start_auto' and frame_current():
            app.auto_adjust_button.invoke()
            state["auto_started"] = perf_counter()
            state["phase"] = "auto"
        elif (
            phase == "auto"
            and not app.is_busy
            and app.last_auto_suggestion is not None
            and frame_current()
        ):
            report["auto_seconds"] = perf_counter() - state["auto_started"]
            report["auto"] = app.last_auto_suggestion.as_overrides()
            report["auto_metrics"] = app.last_auto_suggestion.metrics
            require(app.history.can_undo, "Auto is a reversible edit")
            app.auto_strength_var.set(0)
            app._change_auto_strength()
            require(
                all(v == 0 for k,v in app._current_overrides().items() if k != 'color_noise'),
                "Zero Auto strength restores original tone settings",
            )
            require(app.color_noise_var.get()==state.get('noise',0),'Auto strength preserves manual color-noise setting')
            app.auto_strength_var.set(100)
            app._change_auto_strength()
            app._commit_edit()
            state["phase"] = "compare"
        elif phase == "compare" and frame_current():
            app.compare_button.invoke()
            require(
                not app.showing_after and app.view_var.get() == "Original",
                "Compare shows original RAW",
            )
            app.compare_button.invoke()
            require(app.showing_after, "Compare returns to edited image")
            app.zoom_var.set("2x")
            app.zoom_combo.event_generate("<<ComboboxSelected>>")
            state["zoom_pixels"] = ImageTk.getimage(app.after_photo).tobytes()
            app.preview_label.event_generate("<ButtonPress-1>", x=100, y=100)
            app.preview_label.event_generate("<B1-Motion>", x=160, y=140)
            require(
                ImageTk.getimage(app.after_photo).tobytes() != state["zoom_pixels"],
                "Zoomed image responds to pointer panning",
            )
            app.zoom_var.set("Fit")
            app._zoom_changed()
            app.export_format_var.set("JPEG")
            app._sync_export_options()
            app.process_button.invoke()
            state["phase"] = "jpeg"
        elif phase == "jpeg" and not app.is_busy and app.last_export_path is not None:
            with Image.open(app.last_export_path) as exported:
                require(exported.format == "JPEG", "JPEG export opens correctly")
                report["export_size"] = list(exported.size)
            app.export_format_var.set("TIFF")
            app.export_format_combo.event_generate("<<ComboboxSelected>>")
            app.process_button.invoke()
            state["phase"] = "tiff"
        elif phase == "tiff" and not app.is_busy and app.last_export_path is not None:
            with Image.open(app.last_export_path) as exported:
                require(exported.format == "TIFF", "TIFF export opens correctly")
                require(
                    list(exported.size) == report["export_size"],
                    "JPEG and TIFF retain identical dimensions",
                )
            state["saved"] = app._current_overrides()
            app._commit_edit()
            require(
                app.session_store.load(source) == state["saved"],
                "Session persisted without exporting a new recipe",
            )
            app._select_source(source, ready_status="Reopened")
            require(
                app._current_overrides() == state["saved"], "Reopening restores edits"
            )
            state["phase"] = "capture"
        elif phase == "capture" and frame_current():
            app.inspector_tabs.select(0)
            app.root.after(400, capture)
            return
        app.root.after(15, tick)

    def capture():
        try:
            capture_window(app.root, args.output / "workspace.png")
            finish()
        except Exception as error:
            fail(error)

    app.root.report_callback_exception = lambda *_exc: fail(_exc[1])
    app.root.after(0, lambda: app._select_source(source, ready_status="Smoke test"))
    app.root.after(20, tick)
    app.root.mainloop()
    return int(bool(report["errors"]) or not report["source_unchanged"])


if __name__ == "__main__":
    raise SystemExit(main())
