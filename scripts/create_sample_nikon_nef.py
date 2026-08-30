"""Create a small synthetic Nikon NEF test file for OpenRAW Studio."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if SRC_ROOT.exists():
    sys.path.insert(0, str(SRC_ROOT))

from openraw_studio.raw.native.synthetic import write_synthetic_nikon_nef


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create an OpenRAW synthetic TIFF-style Nikon NEF sample.")
    parser.add_argument(
        "--output",
        "-o",
        default=str(REPO_ROOT / "sample-data" / "openraw-synthetic-nikon.NEF"),
        help="Output path for the generated NEF.",
    )
    parser.add_argument("--width", type=int, default=16, help="Even image width. Default: 16.")
    parser.add_argument("--height", type=int, default=16, help="Even image height. Default: 16.")
    parser.add_argument(
        "--bits",
        type=int,
        choices=(12, 14, 16),
        default=14,
        help="Synthetic sensor bit depth. Default: 14.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        path = write_synthetic_nikon_nef(args.output, width=args.width, height=args.height, bits_per_sample=args.bits)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"error: could not write sample: {exc}", file=sys.stderr)
        return 1

    print(f"Synthetic Nikon NEF: {path}")
    print("This is a generated TIFF-style test file for the current OpenRAW Native path.")
    print("Open the desktop app with: .\\scripts\\run_app.ps1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
