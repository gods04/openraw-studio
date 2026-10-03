"""Check the actual Tk HE/HE* preview-only state and switching to editable RAW."""

import argparse
import json
from pathlib import Path
from time import perf_counter

from PIL import ImageTk

from benchmark_live_preview import capture_window
from openraw_studio.core.files import sha256_file
from openraw_studio.raw.native.synthetic import write_synthetic_dng
from openraw_studio.ui.desktop import launch_desktop_app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--geometry", default="800x600")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    checksum = sha256_file(args.source)
    sample = write_synthetic_dng(args.output / "switch.DNG", width=80, height=60)
    app = launch_desktop_app(run_mainloop=False, session_dir=args.output / "sessions")
    app.output_dir = args.output
    app.root.geometry(args.geometry)
    report = {"checks": [], "errors": []}
    state = {"phase": "preview", "started": perf_counter()}

    def finish():
        report["source_unchanged"] = sha256_file(args.source) == checksum
        (args.output / "workflow.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2), flush=True)
        app.is_busy = False
        app._close()

    def require(condition, label):
        if not condition:
            raise AssertionError(label)
        report["checks"].append(label)

    def error(message):
        report["errors"].append(message)
        finish()

    app.messagebox.showerror = lambda title, message: error(f"{title}: {message}")
    app.root.report_callback_exception = lambda _kind, exc, _tb: error(str(exc))

    def tick():
        try:
            if perf_counter() - state["started"] > 60:
                raise TimeoutError(f"Stalled in {state['phase']}")
            if state["phase"] == "preview" and app.after_photo is not None and not app.is_busy:
                state["phase"] = "preview_capture"
                state["ready_at"] = perf_counter()
            elif state["phase"] == "preview_capture" and perf_counter() - state["ready_at"] > 0.5:
                require(app.current_can_render is False, "HE RAW is not claimed as renderable")
                require(app.current_can_preview, "Camera JPEG opens automatically")
                require(app.process_button.instate(["disabled"]), "RAW export disabled")
                require(app.auto_adjust_button.instate(["disabled"]), "RAW Auto disabled")
                require(app.support_notice_label.winfo_ismapped(), "Encoding notice visible in Adjust tab")
                require("High Efficiency" in app.support_notice_var.get(), "Actual compression named")
                require("Preview only" in app.preview_state_var.get(), "Footer never labels JPEG as live RAW")
                pixels = ImageTk.getimage(app.after_photo).convert("RGB")
                require(any(high > low for low, high in pixels.getextrema()), "Displayed preview is nonblank")
                capture_window(app.root, args.output / "preview-only.png")
                state["phase"] = "editable"
                app._select_source(sample, ready_status="Switch check")
            elif state["phase"] == "editable" and app.last_preview_overrides is not None:
                require(app.current_can_render, "Supported RAW becomes editable")
                require(not app.support_notice_label.winfo_ismapped(), "Preview-only notice cleared on next photo")
                require(app.process_button.instate(["!disabled"]), "Export enabled for supported RAW")
                require(app.auto_adjust_button.instate(["!disabled"]), "Auto enabled for supported RAW")
                finish()
                return
        except Exception as exc:
            error(str(exc))
            return
        app.root.after(40, tick)

    app._select_source(args.source, ready_status="Smoke check")
    app.root.after(40, tick)
    app.root.mainloop()
    return int(bool(report["errors"]) or not report.get("source_unchanged"))


if __name__ == "__main__":
    raise SystemExit(main())
