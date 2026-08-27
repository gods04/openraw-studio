"""Create a small synthetic DNG that can be used to try OpenRAW Studio."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if SRC_ROOT.exists():
    sys.path.insert(0, str(SRC_ROOT))

from openraw_studio.raw.native.synthetic import write_synthetic_dng


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create an OpenRAW synthetic DNG sample.")
    parser.add_argument(
        "--output",
        "-o",
        default=str(REPO_ROOT / "sample-data" / "openraw-synthetic.DNG"),
        help="Output path for the generated DNG.",
    )
    parser.add_argument("--width", type=int, default=16, help="Even image width. Default: 16.")
    parser.add_argument("--height", type=int, default=16, help="Even image height. Default: 16.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        path = write_synthetic_dng(args.output, width=args.width, height=args.height)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"Synthetic DNG: {path}")
    print("Open the desktop app with: .\\scripts\\run_app.ps1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
