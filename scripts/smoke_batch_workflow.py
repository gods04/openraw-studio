"""Check Tk batch modes, cancellation and compact export-panel scrolling."""

import argparse
import json
from pathlib import Path
from time import perf_counter

from openraw_studio.core.artifacts import ArtifactPlan
from openraw_studio.raw.native.synthetic import write_synthetic_dng
from openraw_studio.ui.desktop import launch_desktop_app, _load_recipe_adjustments
from benchmark_live_preview import capture_window


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    sources = [
        write_synthetic_dng(args.output / f"sample-{i}.DNG", width=40, height=30)
        for i in range(3)
    ]
    app = launch_desktop_app(run_mainloop=False, session_dir=args.output / "sessions")
    app.root.geometry("800x600")
    app.output_dir = args.output
    app.messagebox.askyesno = lambda *_a, **_k: True
    report = {"checks": [], "errors": []}
    state = {"phase": "load", "start": perf_counter(), "result": None}
    original_result = app._show_batch_result

    def received(run_id, result):
        original_result(run_id, result)
        state["result"] = result

    app._show_batch_result = received

    def finish(error=None):
        if error:
            report["errors"].append(str(error))
            capture_window(app.root, args.output / "failure.png")
            report["layout"] = {
                "scroll": app.export_canvas.yview(),
                "bbox": app.export_canvas.bbox("all"),
                "canvas_height": app.export_canvas.winfo_height(),
                "button_bottom": app.open_folder_button.winfo_rooty()
                + app.open_folder_button.winfo_height(),
                "window_bottom": app.root.winfo_rooty() + app.root.winfo_height(),
            }
        (args.output / "batch-workflow.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
        print(json.dumps(report, indent=2), flush=True)
        app.is_busy = False
        app._close()

    def require(condition, message):
        if not condition:
            raise AssertionError(message)
        report["checks"].append(message)

    def start(mode):
        state["result"] = None
        app.batch_mode_var.set(mode)
        app.batch_button.invoke()

    def tick():
        try:
            phase, result = state["phase"], state["result"]
            if perf_counter() - state["start"] > 90:
                raise TimeoutError(phase)
            if phase == "load" and app.last_preview_overrides is not None:
                app.library_items = [(p, p.name, True) for p in sources]
                app.session_store.save(sources[1], {"exposure": 0.6})
                app.session_store.save(sources[2], {"exposure": -0.3})
                app._set_busy(False)
                app.inspector_tabs.select(2)
                start("Saved edits")
                state["phase"] = "saved"
            elif phase == "saved" and result:
                require(
                    result.exported == 3 and result.failed == 0,
                    "Saved-edits batch exports every photo",
                )
                recipe = ArtifactPlan.for_source(sources[1], args.output).recipe_path
                require(
                    _load_recipe_adjustments(recipe, sources[1])["exposure"] == 0.6,
                    "Batch honors each photo's saved exposure",
                )
                start("Auto each photo")
                state["phase"] = "auto"
            elif phase == "auto" and result:
                require(
                    result.exported == 3 and result.failed == 0,
                    "Per-photo Auto batch exports every photo",
                )
                start("Current adjustments")
                app.cancel_batch_button.invoke()
                state["phase"] = "cancel"
            elif phase == "cancel" and result:
                require(
                    result.cancelled > 0 and result.failed == 0,
                    "Stop button cancels remaining photos",
                )
                require(
                    not app.is_busy and not app.batch_running,
                    "Controls recover after cancellation",
                )
                app.root.update_idletasks()
                require(
                    app.export_canvas.yview()[1] < 1,
                    "Compact export panel has scrollable content",
                )
                app.export_canvas.yview_moveto(1)
                state["phase"] = "capture"
            elif phase == "capture":
                app.root.update_idletasks()
                require(
                    app.open_folder_button.winfo_rooty()
                    + app.open_folder_button.winfo_height()
                    <= app.root.winfo_rooty() + app.root.winfo_height(),
                    "Bottom export action remains accessible",
                )
                capture_window(app.root, args.output / "export-small.png")
                finish()
                return
            app.root.after(20, tick)
        except Exception as error:
            finish(error)

    app.messagebox.showerror = lambda title, message: finish(f"{title}: {message}")
    app.root.report_callback_exception = lambda *_exc: finish(_exc[1])
    app.root.after(0, lambda: app._select_source(sources[0], ready_status="Batch test"))
    app.root.after(20, tick)
    app.root.mainloop()
    return int(bool(report["errors"]))


if __name__ == "__main__":
    raise SystemExit(main())
