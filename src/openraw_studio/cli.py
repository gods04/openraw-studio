"""Command-line entry point for OpenRAW Studio."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Mapping

from openraw_studio import __version__
from openraw_studio.pipeline.batch import BatchResult, discover_batch_sources, run_batch_export
from openraw_studio.pipeline.errors import BackendUnavailableError, PipelineError
from openraw_studio.pipeline.interfaces import PipelineRequest
from openraw_studio.pipeline.local import LocalPhotoPipeline
from openraw_studio.raw.backends import check_darktable_cli
from openraw_studio.raw.darktable import DarktableCliProcessor
from openraw_studio.raw.native import NativeRawProcessor, NativeSupportReport, inspect_native_support


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="openraw",
        description="OpenRAW Studio command-line tools.",
    )
    parser.add_argument("--version", action="version", version=f"openraw {__version__}")

    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="Check local development/runtime environment.")
    doctor.add_argument(
        "--include-experimental-backends",
        action="store_true",
        help="Also check optional developer backends such as darktable-cli.",
    )
    doctor.add_argument("--darktable-cli", help="Optional explicit darktable-cli executable path.")

    inspect_command = subparsers.add_parser(
        "inspect",
        help="Explain whether one RAW file can be imported or rendered by OpenRAW Native.",
    )
    inspect_command.add_argument("source", help="Path to a RAW file.")
    inspect_command.add_argument("--json", action="store_true", help="Print a machine-readable support report.")

    batch = subparsers.add_parser("batch", help="Batch export currently renderable RAW files from one folder.")
    batch.add_argument("source_dir", help="Folder containing RAW files.")
    batch.add_argument("--output", "-o", required=True, help="Output directory for batch artifacts.")
    batch.add_argument("--processing-profile", default=None, help="Processing profile ID.")
    batch.add_argument("--creative-look", default=None, help="Creative look ID.")
    batch.add_argument("--auto-strength", type=float, default=0.5, help="AUTO strength from 0.0 to 1.0.")
    batch.add_argument("--exposure", type=float, default=0.0, help="Exposure adjustment in stops (-4.0 to 4.0).")
    batch.add_argument("--contrast", type=float, default=0.0, help="Contrast adjustment from -1.0 to 1.0.")
    batch.add_argument("--warmth", type=float, default=0.0, help="Warmth adjustment from -1.0 cooler to 1.0 warmer.")
    batch.add_argument("--limit", type=int, default=200, help="Maximum number of RAW-like files to scan.")
    batch.add_argument("--preview-only", action="store_true", help="Render previews and recipes but skip JPEG export.")
    batch.add_argument("--json", action="store_true", help="Print a machine-readable batch report.")

    process = subparsers.add_parser("process", help="Process or plan processing for one RAW file.")
    process.add_argument("source", help="Path to a RAW file.")
    process.add_argument("--output", "-o", required=True, help="Output directory for artifacts.")
    process.add_argument("--processing-profile", default=None, help="Processing profile ID.")
    process.add_argument("--creative-look", default=None, help="Creative look ID.")
    process.add_argument("--auto-strength", type=float, default=0.5, help="AUTO strength from 0.0 to 1.0.")
    process.add_argument("--exposure", type=float, default=0.0, help="Exposure adjustment in stops (-4.0 to 4.0).")
    process.add_argument("--contrast", type=float, default=0.0, help="Contrast adjustment from -1.0 to 1.0.")
    process.add_argument("--warmth", type=float, default=0.0, help="Warmth adjustment from -1.0 cooler to 1.0 warmer.")
    process.add_argument(
        "--raw-backend",
        choices=("native", "darktable-experimental"),
        default="native",
        help="RAW backend to use. Default is OpenRAW Native.",
    )

    subparsers.add_parser("app", help="Open the beginner-friendly local desktop app.")
    process.add_argument("--darktable-cli", help="Optional explicit path for the experimental darktable backend.")
    process.add_argument("--dry-run", action="store_true", help="Write recipe/artifact plan without rendering pixels.")
    process.add_argument(
        "--preview-only",
        action="store_true",
        help="Render only a preview artifact and skip final export.",
    )

    return parser


def _run_doctor(include_experimental_backends: bool, darktable_cli: str | None) -> int:
    native = NativeRawProcessor().engine_info()
    print(f"OpenRAW Studio {__version__}")
    print("Python package: available")
    print(f"{native.name}: available")
    print(
        "  status: foundation ready; Nikon NEF/NRW metadata import plus simple PNG "
        "preview/native render and local JPEG export for narrow uncompressed DNG files"
    )
    if include_experimental_backends or darktable_cli:
        check = check_darktable_cli(darktable_cli)
        print(f"{check.name} experimental: {'available' if check.available else 'missing'}")
        if check.executable:
            print(f"  executable: {check.executable}")
        if check.version:
            print(f"  version: {check.version}")
        if check.message:
            print(f"  note: {check.message}")
    return 0


def _run_inspect(args: argparse.Namespace) -> int:
    report = inspect_native_support(Path(args.source))
    if args.json:
        print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
    else:
        print(_format_inspect_report(report))
    if not report.file_exists:
        return 2
    return 0 if report.can_render else 1


def _format_inspect_report(report: NativeSupportReport) -> str:
    render_status = "supported" if report.can_render else "not supported yet"
    lines = [
        "OpenRAW inspect",
        f"Source: {report.source_path}",
        f"Native render: {render_status}",
        f"Reason: {report.reason}",
    ]
    if report.can_inspect and not report.can_render:
        lines.insert(3, "Import: metadata supported")

    if camera := _camera_from_metadata(report.metadata):
        lines.append(f"Camera: {camera}")
    if report.details:
        lines.append("Details:")
        lines.extend(f"  - {detail}" for detail in report.details)
    return "\n".join(lines)


def _camera_from_metadata(metadata: Mapping[str, object]) -> str | None:
    unique = metadata.get("unique_camera_model")
    if isinstance(unique, str) and unique.strip():
        return unique.strip()
    make = metadata.get("make")
    model = metadata.get("model")
    parts = [part.strip() for part in (make, model) if isinstance(part, str) and part.strip()]
    if parts:
        return " ".join(parts)
    return None


def _run_process(args: argparse.Namespace) -> int:
    adjustment_error = _validate_adjustment_args(args)
    if adjustment_error is not None:
        return adjustment_error
    if args.dry_run and args.preview_only:
        print("error: --dry-run and --preview-only cannot be used together", file=sys.stderr)
        return 2

    raw_processor = _build_raw_processor(args.raw_backend, args.darktable_cli)
    pipeline = LocalPhotoPipeline(raw_processor=raw_processor)
    try:
        result = pipeline.process(
            PipelineRequest(
                source_path=Path(args.source),
                output_dir=Path(args.output),
                processing_profile=args.processing_profile,
                creative_look=args.creative_look,
                auto_strength=args.auto_strength,
                overrides={"exposure": args.exposure, "contrast": args.contrast, "warmth": args.warmth},
                dry_run=args.dry_run,
                preview_only=args.preview_only,
            )
        )
    except BackendUnavailableError as exc:
        print(f"backend unavailable: {exc}", file=sys.stderr)
        return 3
    except PipelineError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print("OpenRAW process complete.")
    if result.diagnostics.get("dry_run"):
        print("Mode: dry-run")
    if result.diagnostics.get("preview_only"):
        print("Mode: preview-only")
    if recipe_path := result.diagnostics.get("recipe_path"):
        print(f"Recipe: {recipe_path}")
    if result.preview is not None and result.preview.role == "preview":
        print(f"Preview: {result.preview.path}")
    planned = result.diagnostics.get("planned_artifacts")
    if isinstance(planned, dict) and result.diagnostics.get("dry_run"):
        print(f"Planned preview: {planned['preview']}")
        print(f"Planned export: {planned['export']}")
    if result.diagnostics.get("preview_only"):
        print("Final export: skipped")
    if result.exports:
        print(f"Export: {result.exports[0].path}")
    return 0


def _run_batch(args: argparse.Namespace) -> int:
    adjustment_error = _validate_adjustment_args(args)
    if adjustment_error is not None:
        return adjustment_error
    if args.limit <= 0:
        print("error: --limit must be greater than 0", file=sys.stderr)
        return 2

    try:
        sources = discover_batch_sources(Path(args.source_dir), limit=args.limit)
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    result = run_batch_export(
        [source.path for source in sources],
        Path(args.output),
        processing_profile=args.processing_profile,
        creative_look=args.creative_look,
        auto_strength=args.auto_strength,
        overrides={"exposure": args.exposure, "contrast": args.contrast, "warmth": args.warmth},
        preview_only=args.preview_only,
    )

    if args.json:
        print(json.dumps(result.as_dict(), indent=2, sort_keys=True))
    else:
        print(_format_batch_report(Path(args.source_dir), result))
    return 0 if result.failed == 0 and result.processed > 0 else 1


def _format_batch_report(source_dir: Path, result: BatchResult) -> str:
    lines = [
        "OpenRAW batch complete.",
        f"Source folder: {source_dir}",
        f"Output folder: {result.output_dir}",
        f"Found: {result.total} RAW files",
        f"Processed: {result.processed}",
        f"Exported: {result.exported}",
        f"Previewed: {result.previewed}",
        f"Skipped: {result.skipped}",
        f"Failed: {result.failed}",
    ]

    for item in result.items:
        label = item.status.capitalize()
        target = item.export_path or item.preview_path or item.recipe_path
        if target is not None:
            lines.append(f"{label}: {item.source_path.name} -> {target}")
        else:
            lines.append(f"{label}: {item.source_path.name} -> {item.message}")
    return "\n".join(lines)


def _validate_adjustment_args(args: argparse.Namespace) -> int | None:
    if not 0.0 <= args.auto_strength <= 1.0:
        print("error: --auto-strength must be between 0.0 and 1.0", file=sys.stderr)
        return 2
    if not -4.0 <= args.exposure <= 4.0:
        print("error: --exposure must be between -4.0 and 4.0", file=sys.stderr)
        return 2
    if not -1.0 <= args.contrast <= 1.0:
        print("error: --contrast must be between -1.0 and 1.0", file=sys.stderr)
        return 2
    if not -1.0 <= args.warmth <= 1.0:
        print("error: --warmth must be between -1.0 and 1.0", file=sys.stderr)
        return 2
    return None


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "doctor":
        return _run_doctor(args.include_experimental_backends, args.darktable_cli)
    if args.command == "inspect":
        return _run_inspect(args)
    if args.command == "batch":
        return _run_batch(args)
    if args.command == "process":
        return _run_process(args)
    if args.command == "app":
        from openraw_studio.ui.desktop import launch_desktop_app

        launch_desktop_app()
        return 0

    parser.error(f"unknown command: {args.command}")
    return 2


def _build_raw_processor(raw_backend: str, darktable_cli: str | None):
    if raw_backend == "native":
        return NativeRawProcessor()
    if raw_backend == "darktable-experimental":
        return DarktableCliProcessor(executable=darktable_cli)
    raise ValueError(f"unknown RAW backend: {raw_backend}")


if __name__ == "__main__":
    raise SystemExit(main())
