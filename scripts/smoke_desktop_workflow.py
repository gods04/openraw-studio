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
    parser.add_argument("--luminance-noise", type=float, default=0)
    parser.add_argument("--auto-color-noise", action="store_true")
    parser.add_argument("--tiff-bit-depth", type=int, choices=(8, 16), default=8)
    args = parser.parse_args()
    if not 0 <= args.color_noise <= 1:
        parser.error("--color-noise must be within [0, 1]")
    if not 0 <= args.luminance_noise <= 1:
        parser.error("--luminance-noise must be within [0, 1]")
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
        app.root.update_idletasks()
        return app.live_after_id is None and app.resize_after_id is None and app.last_preview_overrides == app._current_overrides()

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
            state['noise_controls'] = [(key, amount) for key, amount in (
                ('color_noise', args.color_noise), ('luminance_noise', args.luminance_noise)
            ) if amount]
            state['noise_amounts'] = {}
            state['noise_index'] = 0
            if state['noise_controls']:
                header = next(w for w in descendants(app.root) if w.winfo_class() == "TCheckbutton" and str(w.cget("text")) == "Detail")
                header.invoke()
                app.root.update_idletasks()
                app.controls_canvas.yview_moveto(1)
                app.root.update_idletasks()
                state['phase'] = 'start_noise'
            else:
                state['phase']='start_auto'
        elif phase == 'start_noise' and frame_current():
            if state['noise_index'] == len(state['noise_controls']):
                state['phase'] = 'start_auto'
            else:
                key, amount = state['noise_controls'][state['noise_index']]
                variable = getattr(app, key + '_var')
                state['before_noise_pixels'] = ImageTk.getimage(app.after_photo).tobytes()
                scale = next(w for w in app.edit_scales if str(w.cget('variable')) == str(variable))
                require(app.controls_canvas.winfo_rooty() <= scale.winfo_rooty() and scale.winfo_rooty() + scale.winfo_height() <= app.controls_canvas.winfo_rooty() + app.controls_canvas.winfo_height(), f'{key} slider is accessible in compact window')
                drag_scale(scale, amount)
                require(abs(variable.get() - amount) < .02, f'Pointer dragging changes {key}')
                state['noise_amounts'][key] = app._current_overrides()[key]
                state['phase'] = 'noise'
        elif phase == 'noise' and frame_current():
            key, _ = state['noise_controls'][state['noise_index']]
            variable = getattr(app, key + '_var')
            require(ImageTk.getimage(app.after_photo).tobytes() != state['before_noise_pixels'], f'{key} edit changes displayed pixels')
            app.undo_button.invoke()
            require(variable.get() == 0, f'Undo restores {key}')
            app.redo_button.invoke()
            require(variable.get() == state['noise_amounts'][key], f'Redo restores {key}')
            state['noise_index'] += 1
            state['phase'] = 'start_noise'
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
            if app.last_auto_suggestion.scene_evidence is not None:
                from dataclasses import asdict
                report["scene_analysis"] = asdict(app.last_auto_suggestion.scene_evidence)
                report["auto_summary"] = app.auto_summary_var.get()
                app.controls_canvas.yview_moveto(0)
                app.root.update_idletasks()
                label = app.auto_summary_label
                require(
                    app.controls_canvas.winfo_rooty() <= label.winfo_rooty()
                    and label.winfo_rooty() + label.winfo_height()
                    <= app.controls_canvas.winfo_rooty() + app.controls_canvas.winfo_height(),
                    "Scene summary is visible in the compact Adjust tab",
                )
            state["scene_ready"] = perf_counter()
            state["phase"] = "auto_summary"
        elif phase == "auto_summary" and frame_current() and perf_counter() - state["scene_ready"] > .4:
            capture_window(app.root, args.output / "auto-scene.png")
            require(app.history.can_undo, "Auto is a reversible edit")
            app.auto_strength_var.set(0)
            app._change_auto_strength()
            require(
                all(v == 0 for k,v in app._current_overrides().items() if k not in ('color_noise', 'luminance_noise')),
                "Zero Auto strength restores original tone settings",
            )
            for key in ('color_noise', 'luminance_noise'):
                require(getattr(app, key + '_var').get() == state['noise_amounts'].get(key, 0), f'Auto strength preserves manual {key}')
            app.auto_strength_var.set(100)
            app._change_auto_strength()
            app._commit_edit()
            state["phase"] = "auto_noise_start" if args.auto_color_noise else "compare"
        elif phase == "auto_noise_start" and frame_current():
            if not args.color_noise and not args.luminance_noise:
                header = next(w for w in descendants(app.root) if w.winfo_class() == "TCheckbutton" and str(w.cget("text")) == "Detail")
                header.invoke()
            app.root.update_idletasks()
            app.controls_canvas.yview_moveto(1)
            app.root.update_idletasks()
            button = app.auto_color_noise_button
            require(app.controls_canvas.winfo_rooty() <= button.winfo_rooty() and button.winfo_rooty() + button.winfo_height() <= app.controls_canvas.winfo_rooty() + app.controls_canvas.winfo_height(), "Auto color-noise button is visible in compact window")
            state["before_auto_noise"] = app._current_overrides()
            state["before_auto_noise_pixels"] = ImageTk.getimage(app.after_photo).tobytes()
            state["before_auto_noise_native"] = app.live_image.tobytes()
            state["before_auto_noise_size"] = [app.after_photo.width(), app.after_photo.height()]
            state["before_busy_preview_box"] = [app.preview_label.winfo_width(), app.preview_label.winfo_height()]
            state["noise_started"] = perf_counter()
            button.invoke()
            require(app.is_busy and str(button.cget("state")) == "disabled", "Auto color noise starts asynchronously and prevents duplicate work")
            app.root.update_idletasks()
            require([app.preview_label.winfo_width(), app.preview_label.winfo_height()] == state["before_busy_preview_box"], "Busy progress keeps photo viewport dimensions stable")
            state["phase"] = "auto_noise"
        elif phase == "auto_noise" and not app.is_busy and app.last_noise_suggestion is not None and frame_current():
            require([app.preview_label.winfo_width(), app.preview_label.winfo_height()] == state["before_busy_preview_box"], "Completing noise advice keeps photo viewport dimensions stable")
            result = app.last_noise_suggestion
            report["auto_noise_seconds"] = perf_counter() - state["noise_started"]
            report["auto_noise"] = {"strength": result.strength, "status": result.status, "metrics": result.metrics}
            before = state["before_auto_noise"]
            after = app._current_overrides()
            require(all(after[k] == before[k] for k in after if k != "color_noise"), "Auto color noise preserves every tone control")
            require(after["color_noise"] == (result.strength if result.strength is not None else before["color_noise"]), "Auto color noise applies advice or retains settings when evidence is insufficient")
            if before != after:
                require(ImageTk.getimage(app.after_photo).tobytes() != state["before_auto_noise_pixels"], "Noise advice changes displayed preview pixels")
                app.undo_button.invoke()
                require(app._current_overrides() == before, "Undo restores setting before noise advice")
                app.redo_button.invoke()
                require(app._current_overrides() == after, "Redo restores advised noise amount")
            else:
                report["abstained_preview"] = {
                    "native_unchanged": app.live_image.tobytes() == state["before_auto_noise_native"],
                    "before_size": state["before_auto_noise_size"],
                    "after_size": [app.after_photo.width(), app.after_photo.height()],
                }
                require(ImageTk.getimage(app.after_photo).tobytes() == state["before_auto_noise_pixels"], "Abstaining from noise advice preserves displayed pixels")
            from openraw_studio.decision.color_noise import ColorNoiseSuggestion
            app._apply_auto_color_noise(ColorNoiseSuggestion(.99, "suggested"), run_id=app.run_counter-1)
            require(app._current_overrides() == after, "Stale noise advice is ignored")
            app.auto_strength_var.set(50)
            app._change_auto_strength()
            require(app.color_noise_var.get() == after["color_noise"], "Tonal Auto strength preserves accepted noise advice")
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
            require(app._selected_export_bit_depth() == 8 and app.export_bit_depth_combo.instate(["disabled"]),
                    "JPEG locks bit depth to 8")
            app.process_button.invoke()
            state["phase"] = "jpeg"
        elif phase == "jpeg" and not app.is_busy and app.last_export_path is not None:
            with Image.open(app.last_export_path) as exported:
                require(exported.format == "JPEG", "JPEG export opens correctly")
                report["export_size"] = list(exported.size)
            app.export_format_var.set("TIFF")
            app.export_format_combo.event_generate("<<ComboboxSelected>>")
            app.export_bit_depth_var.set(args.tiff_bit_depth)
            app.export_bit_depth_combo.event_generate("<<ComboboxSelected>>")
            require(not app.export_bit_depth_combo.instate(["disabled"]), "TIFF enables bit-depth selection")
            app.process_button.invoke()
            state["phase"] = "tiff"
        elif phase == "tiff" and not app.is_busy and app.last_export_path is not None:
            import tifffile

            with tifffile.TiffFile(app.last_export_path) as opened:
                require(opened.pages[0].bitspersample == args.tiff_bit_depth, "TIFF contains the selected bit depth")
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
            require(app._selected_export_bit_depth() == args.tiff_bit_depth, "Reopening restores TIFF bit depth")
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
            app.inspector_tabs.select(2)
            app.root.after(400, capture_export)
        except Exception as error:
            fail(error)

    def capture_export():
        try:
            combo = app.export_bit_depth_combo
            require(combo.winfo_ismapped() and 0 < combo.winfo_rooty() - app.root.winfo_rooty()
                    < app.root.winfo_height() - combo.winfo_height(),
                    "Bit-depth selector is visible in the compact Export tab")
            capture_window(app.root, args.output / "export-options.png")
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
