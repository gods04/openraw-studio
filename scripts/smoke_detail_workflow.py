"""Verify actual Tk native-pixel inspection against full-resolution RAW renders."""

import argparse
import json
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace

import numpy as np
from benchmark_live_preview import capture_window
from PIL import ImageTk

from openraw_studio.core.files import sha256_file
from openraw_studio.raw.native.detail import prepare_detail_photo
from openraw_studio.ui.desktop import launch_desktop_app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--geometry", default="1280x820")
    parser.add_argument("--color-noise", type=float, default=0)
    args = parser.parse_args()
    if not 0 <= args.color_noise <= 1:
        parser.error("--color-noise must be within [0, 1]")
    args.output.mkdir(parents=True, exist_ok=True)
    checksum = sha256_file(args.source)
    app = launch_desktop_app(run_mainloop=False, session_dir=args.output / "sessions")
    app.root.geometry(args.geometry)
    app.output_dir = args.output
    app.messagebox.askyesno = lambda *_a, **_k: True
    report = {"source": str(args.source.resolve()), "geometry": args.geometry,
              "checks": [], "errors": [], "regions": []}
    samples = []
    state = {"phase": "load", "started": perf_counter()}

    def require(condition, message):
        if not condition:
            raise AssertionError(message)
        report["checks"].append(message)

    def finish(error=None):
        if error:
            report["errors"].append(str(error))
        app.is_busy = False
        app._close()

    def zoom(value):
        app.zoom_var.set(value)
        app.zoom_combo.event_generate("<<ComboboxSelected>>")
        state["requested"] = perf_counter()

    def current_detail():
        frame = app.detail_frame
        return frame is not None and frame.detail_view == app._detail_view() and frame.adjustments == app._current_overrides()

    def check_pixels(label):
        frame = app.detail_frame
        pixels = np.asarray(frame.image)
        scale = frame.detail_view.scale
        expected = np.repeat(np.repeat(pixels, scale, axis=0), scale, axis=1)
        width, height = frame.detail_view.display_size(frame.region)
        displayed = np.asarray(ImageTk.getimage(app.after_photo).convert("RGB"))
        require(np.array_equal(displayed, expected[:height, :width]), f"{label}: displayed pixels have exact native pitch")
        samples.append((dict(frame.adjustments), frame.region, pixels.copy()))
        report["regions"].append({"phase": label, "region": frame.region, "native_size": frame.native_size})

    def step():
        if perf_counter() - state["started"] > 90:
            raise TimeoutError(state["phase"])
        phase = state["phase"]
        if phase == "load" and app.last_preview_overrides is not None:
            require("100%" in app.zoom_combo.cget("values"), "Supported RAW exposes native zoom")
            app._reset_adjustments()
            app.color_noise_var.set(args.color_noise)
            zoom("100%")
            state["phase"] = "native"
        elif phase == "native" and current_detail():
            report["first_detail_ms"] = (perf_counter() - state["requested"]) * 1000
            require(app.detail_frame.native_size[0] > app.live_image.width, "Detail comes from dimensions larger than the Fit proxy")
            check_pixels("100%")
            state["region"] = app.detail_frame.region
            app.preview_label.event_generate("<ButtonPress-1>", x=180, y=180)
            app.preview_label.event_generate("<B1-Motion>", x=260, y=220)
            state["requested"] = perf_counter()
            state["phase"] = "pan"
        elif phase == "pan" and current_detail():
            report["pan_to_detail_ms"] = (perf_counter() - state["requested"]) * 1000
            require(app.detail_frame.region != state["region"], "Pointer pan requests a different sensor region")
            check_pixels("Panned 100%")
            zoom("200%")
            state["phase"] = "double"
        elif phase == "double" and current_detail() and app.histogram_after_id is None:
            check_pixels("200%")
            app.root.update_idletasks()
            capture_window(app.root, args.output / "detail-workspace.png")
            scale = next(w for w in app.edit_scales if str(w.cget("variable")) == str(app.exposure_var))
            start, end = scale.coords(), scale.coords(.4)
            scale.event_generate("<ButtonPress-1>", x=start[0], y=start[1])
            scale.event_generate("<B1-Motion>", x=end[0], y=end[1])
            scale.event_generate("<ButtonRelease-1>", x=end[0], y=end[1])
            state["requested"] = perf_counter()
            state["phase"] = "edit"
        elif phase == "edit" and current_detail():
            report["edit_to_detail_ms"] = (perf_counter() - state["requested"]) * 1000
            require(app.exposure_var.get() > .3, "Pointer edit changes exposure in detail mode")
            check_pixels("Edited 200%")
            app.auto_adjust_button.invoke()
            state["phase"] = "auto"
        elif phase == "auto" and not app.is_busy and app.last_auto_suggestion is not None and current_detail():
            require(app.color_noise_var.get() == args.color_noise, "Auto preserves manual color-noise strength")
            require(app.last_auto_suggestion.metrics.get("detail_validation_pixels", 0) > 0, "Auto still analyzes the full photo while viewing a crop")
            check_pixels("Auto 200%")
            app.compare_button.invoke()
            require(not app.showing_after, "Detail comparison switches to original RAW")
            expected = np.asarray(app.detail_frame.original_image)
            expected = np.repeat(np.repeat(expected, 2, axis=0), 2, axis=1)
            shown = np.asarray(ImageTk.getimage(app.preview_photo).convert("RGB"))
            require(np.array_equal(shown, expected[:shown.shape[0], :shown.shape[1]]), "Original detail retains exact native pixels")
            app.compare_button.invoke()
            zoom("Fit")
            state["phase"] = "fit"
        elif phase == "fit" and app.detail_frame is None and app.preview_state_var.get().startswith("Live |"):
            require(app.last_preview_overrides == app._current_overrides(), "Returning to Fit refreshes current edits")
            require(app.live_image.width <= 960 and app.live_image.height <= 960, "Fit retains its fast display proxy")
            app._zoom_toggle(SimpleNamespace(x=app.preview_label.winfo_width() * .3, y=app.preview_label.winfo_height() * .65))
            state["phase"] = "focused"
        elif phase == "focused" and current_detail():
            require(app.detail_frame.detail_view.anchor != (.5, .5), "Double-click zoom retains the chosen image position")
            check_pixels("Focused 100%")
            finish()
            return
        app.root.after(12, tick)

    def tick():
        try:
            step()
        except Exception as error:  # noqa: BLE001 - Capture failures from Tk callbacks.
            finish(error)

    app.messagebox.showerror = lambda title, message: finish(RuntimeError(f"{title}: {message}"))
    app.root.report_callback_exception = lambda *_exc: finish(_exc[1])
    app.root.after(0, lambda: app._select_source(args.source, ready_status="Detail validation"))
    app.root.after(20, tick)
    app.root.mainloop()
    try:
        photo = prepare_detail_photo(app.pipeline.raw_processor, args.source)
        last_key = full_image = None
        for edits, region, actual in samples:
            key = tuple(sorted(edits.items()))
            if key != last_key:
                full_image = photo.render_region(edits, (0, 0, *photo.size))
                last_key = key
            x, y, width, height = region
            expected = np.asarray(full_image.crop((x, y, x + width, y + height)))
            require(np.array_equal(actual, expected), "Viewport region equals full-resolution RAW render")
    except Exception as error:  # noqa: BLE001 - Persist reference-render failures.
        report["errors"].append(str(error))
    report["source_unchanged"] = checksum == sha256_file(args.source)
    (args.output / "detail-workflow.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return int(bool(report["errors"]) or not report["source_unchanged"])


if __name__ == "__main__":
    raise SystemExit(main())
