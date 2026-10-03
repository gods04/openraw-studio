"""PyInstaller entry point for the OpenRAW Studio desktop app."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from time import perf_counter


def smoke_test(source: Path, output: Path, *, color_noise=0.0, auto_color_noise=False) -> int:
    """Exercise frozen runtime imports, GPU/JIT, adjustment and full export."""
    from openraw_studio.core.files import sha256_file
    from openraw_studio.decision.auto_adjust import (
        suggest_auto_adjustments_for_photo,
    )
    from openraw_studio.decision.color_noise import suggest_color_noise_for_photo
    from openraw_studio.pipeline.interfaces import PipelineRequest
    from openraw_studio.pipeline.local import LocalPhotoPipeline
    from openraw_studio.raw.native import (
        compiled_bayer,
        compiled_chroma,
        compiled_decode,
        compiled_he,
        compiled_he_transform,
        compiled_tone,
        he_cpu,
    )
    from openraw_studio.raw.native.detail import prepare_detail_photo
    from openraw_studio.raw.native.interactive import prepare_interactive_photo
    from openraw_studio.ui.viewport import DetailView

    output.mkdir(parents=True, exist_ok=True)
    report = {}
    try:
        before = sha256_file(source)
        pipeline = LocalPhotoPipeline()
        started = perf_counter()
        photo = prepare_interactive_photo(
            pipeline.raw_processor, source
        )
        _image, backend = photo.render({})
        report["prepare_seconds"] = perf_counter() - started
        report["preview_backend"] = backend
        report["compiled_cpu_tone"] = bool(getattr(compiled_tone.tone, "signatures", []))
        report["cpu_tone_fallback_reason"] = compiled_tone.last_error
        report["cpu_tone_cache_disabled_reason"] = compiled_tone.cache_disabled_reason
        report["compiled_decoder"] = bool(
            getattr(compiled_decode.decode, "signatures", [])
        )
        report["decoder_fallback_reason"] = compiled_decode.last_error
        report["decoder_disk_cache_disabled"] = compiled_decode.cache_disabled_reason is not None
        report["compiled_he_decoder"] = bool(
            getattr(compiled_he.decode, "signatures", [])
        )
        report["he_aot_available"] = he_cpu.extension is not None
        report["he_aot_import_error"] = he_cpu.import_error
        report["he_decoder_fallback_reason"] = compiled_he.last_error
        report["he_decoder_disk_cache_disabled"] = compiled_he.cache_disabled_reason is not None
        report["compiled_he_transforms"] = {
            name: bool(getattr(kernel, "signatures", []))
            for name, kernel in compiled_he_transform.kernels.items()
        }
        report["he_transform_fallback_reasons"] = compiled_he_transform.last_errors.copy()
        report["he_transform_cache_disabled_reasons"] = compiled_he_transform.cache_disabled_reasons.copy()
        report["he_decoder_cache_disabled_reason"] = compiled_he.cache_disabled_reason
        started = perf_counter()
        suggestion = suggest_auto_adjustments_for_photo(photo)
        report["auto_seconds"] = perf_counter() - started
        report["auto"] = suggestion.as_overrides()
        report["auto_metrics"] = suggestion.metrics
        edits = {**suggestion.as_overrides(), "color_noise": color_noise}
        if auto_color_noise:
            started = perf_counter()
            noise = suggest_color_noise_for_photo(photo, edits)
            report["auto_color_noise_seconds"] = perf_counter() - started
            report["auto_color_noise"] = {"strength": noise.strength, "status": noise.status, "metrics": noise.metrics}
            if noise.strength is not None:
                edits["color_noise"] = noise.strength
        report["color_noise"] = edits["color_noise"]
        started = perf_counter()
        result = pipeline.process(
            PipelineRequest(source, output, overrides=edits)
        )
        report["export_seconds"] = perf_counter() - started
        report["compiled_cpu_bayer"] = bool(getattr(compiled_bayer.malvar_demosaic, "signatures", []))
        report["cpu_bayer_fallback_reason"] = compiled_bayer.last_error
        report["cpu_bayer_cache_disabled_reason"] = compiled_bayer.cache_disabled_reason
        report["compiled_cpu_chroma"] = bool(getattr(compiled_chroma.chroma, "signatures", []))
        report["cpu_chroma_fallback_reason"] = compiled_chroma.last_error
        report["cpu_chroma_cache_disabled_reason"] = compiled_chroma.cache_disabled_reason
        report["export_size"] = [result.exports[0].width, result.exports[0].height]
        started = perf_counter()
        detail = prepare_detail_photo(pipeline.raw_processor, source)
        region = DetailView((512, 384)).region(detail.size)
        detail_image = detail.render_region(edits, region)
        report["detail_seconds"] = perf_counter() - started
        report["detail_native_size"] = detail.size
        report["detail_region"] = region
        report["detail_size"] = detail_image.size
        detail_image.save(output / "native-detail.png")
        report["source_unchanged"] = sha256_file(source) == before
        report["ok"] = report["source_unchanged"] and result.exports[0].path.is_file() and detail_image.size == region[2:]
    except Exception as error:  # noqa: BLE001 - Persist unexpected frozen-runtime failures.
        report.update(ok=False, error=f"{type(error).__name__}: {error}")
    (output / "packaged-smoke.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    return 0 if report.get("ok") else 1


def main() -> int:
    from openraw_studio.core.runtime import configure_numba_cache

    configure_numba_cache()
    from openraw_studio.ui.desktop import launch_desktop_app

    parser = argparse.ArgumentParser(description="OpenRAW Studio desktop")
    parser.add_argument("source", nargs="?", type=Path)
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--color-noise", type=float, default=0.0)
    parser.add_argument("--auto-color-noise", action="store_true", help="Select color-noise strength during --smoke-test.")
    args = parser.parse_args()
    if args.smoke_test:
        if args.source is None or args.output is None:
            parser.error("--smoke-test requires a RAW source and --output directory")
        if not 0 <= args.color_noise <= 1:
            parser.error("--color-noise must be within [0, 1]")
        return smoke_test(args.source, args.output, color_noise=args.color_noise, auto_color_noise=args.auto_color_noise)
    app = launch_desktop_app(run_mainloop=False)
    if args.source is not None:
        app.root.after(
            0, lambda: app._select_source(args.source, ready_status="Photo opened")
        )
    app.root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
