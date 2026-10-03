"""Build the bundled Lucide PNG assets. Development-only: pip install resvg-py."""

import argparse
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.request import urlopen

import resvg_py


def main():
    target = Path(__file__).resolve().parents[1] / "src/openraw_studio/ui/icons"
    target.mkdir(parents=True, exist_ok=True)
    base = "https://raw.githubusercontent.com/lucide-icons/lucide/main"
    names = (
        "folder-open",
        "folder",
        "undo-2",
        "redo-2",
        "rotate-ccw",
        "download",
        "sliders-horizontal",
        "image",
        "columns-2",
        "maximize",
        "chevron-left",
        "chevron-right",
        "wand-sparkles",
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", choices=names)
    args = parser.parse_args()
    for name in (args.only,) if args.only else names:
        with urlopen(f"{base}/icons/{name}.svg", timeout=30) as response:
            root = ET.fromstring(response.read())
        root.set("stroke", "#30363b")
        svg = ET.tostring(root, encoding="unicode")
        (target / f"{name}.png").write_bytes(
            resvg_py.svg_to_bytes(svg_string=svg, width=20, height=20)
        )
    if not args.only or not (target / "LICENSE.txt").exists():
        with urlopen(f"{base}/LICENSE", timeout=30) as response:
            (target / "LICENSE.txt").write_bytes(response.read())


if __name__ == "__main__":
    main()
