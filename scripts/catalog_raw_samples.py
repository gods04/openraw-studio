"""Read-only local RAW inventory and bounded, evenly spaced contact sheets.

Reports contain private paths. Keep --output under the ignored output directory.
Camera JPEGs are for sample selection only, never proof of native RAW decoding.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from io import BytesIO
import json
import mmap
from pathlib import Path

import numpy as np
from PIL import Image

from openraw_studio.raw.native.dng import (
    DngMetadata, DngMetadataReader, _build_summary,
    _select_embedded_jpeg_ifd, _slice_checked, _tag_scalar_int,
)
from openraw_studio.raw.native.nikon import (
    _apply_exif_orientation, summarize_nikon_makernote,
)
from validate_photo_set import contact_sheet


def read_record(source):
    # Memory mapping avoids reading every full sensor payload from an external drive.
    with source.open("rb") as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as data:
        order, _endian, ifds = DngMetadataReader()._read_structure(data)
        metadata = DngMetadata(order, tuple(ifds), _build_summary(ifds))
        maker = summarize_nikon_makernote(metadata)
        record = {
            "source": str(source), "bytes": len(data),
            **{key: metadata.summary.get(key) for key in (
                "model", "width", "height", "bits_per_sample", "compression",
                "orientation", "iso", "exposure_time", "aperture", "focal_length_mm",
            )},
            "nikon_compression": maker.compression_name if maker else None,
            "nikon_compression_mode": maker.compression_mode if maker else None,
            "table_prefix": maker.compression_table_prefix if maker else None,
            "ok": True,
        }
        try:
            preview = _select_embedded_jpeg_ifd(ifds)
            record["jpeg_offset"] = _tag_scalar_int(preview, 513, "JPEGInterchangeFormat")
            record["jpeg_length"] = _tag_scalar_int(preview, 514, "JPEGInterchangeFormatLength")
        except ValueError:
            pass
        return record


def preview_image(record):
    with Path(record["source"]).open("rb") as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as data:
        jpeg = _slice_checked(data, record["jpeg_offset"], record["jpeg_length"])
    with Image.open(BytesIO(jpeg)) as opened:
        opened.draft("RGB", (600, 600))
        image = _apply_exif_orientation(opened.convert("RGB"), record.get("orientation") or 1)
    image.thumbnail((500, 500))
    return image


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--per-folder", type=int, default=12)
    parser.add_argument("--model", default="")
    parser.add_argument("--inventory-only", action="store_true")
    args = parser.parse_args()
    if not args.root.is_dir() or args.per_folder < 1:
        parser.error("Use an existing root folder and a positive --per-folder")
    ignored_output = Path(__file__).resolve().parents[1] / "output"
    if not args.output.resolve().is_relative_to(ignored_output):
        parser.error("Private catalogs must stay inside the project's ignored output folder")
    args.output.mkdir(parents=True, exist_ok=True)
    sources = sorted(p for p in args.root.rglob("*") if p.suffix.lower() in {".nef", ".nrw", ".dng"} and p.is_file())
    records = []
    for index, source in enumerate(sources):
        try:
            record = read_record(source)
        except (ValueError, OSError) as error:
            record = {"source": str(source), "ok": False, "error": str(error)}
        record["index"] = index
        records.append(record)
        if (index + 1) % 250 == 0:
            print(f"Inventoried {index + 1}/{len(sources)}", flush=True)
    counts = Counter((r.get("model"), r.get("nikon_compression"), r.get("bits_per_sample")) for r in records if r["ok"])
    summary = [{"model": model, "mode": mode, "bits": bits, "count": count} for (model, mode, bits), count in counts.most_common()]
    catalog = {"summary": summary, "records": records, "preview_source": "embedded camera JPEG"}
    (args.output / "inventory.json").write_text(json.dumps(catalog, indent=2), encoding="utf-8")
    print(json.dumps(summary), flush=True)
    if args.inventory_only:
        return int(any(not r["ok"] for r in records))
    folders = defaultdict(list)
    for record in records:
        if record["ok"] and args.model.lower() in (record.get("model") or "").lower() and "jpeg_offset" in record:
            folders[str(Path(record["source"]).parent)].append(record)
    tiles, candidates = [], []
    for items in folders.values():
        for index in np.linspace(0, len(items) - 1, min(args.per_folder, len(items)), dtype=int):
            record = items[index].copy()
            try:
                image = preview_image(record)
                path = Path(record["source"])
                tiles.append((f"#{record['index']} {path.name} ISO {record.get('iso')}", image))
            except (ValueError, OSError, KeyError) as error:
                record.update(ok=False, error=str(error))
            candidates.append(record)
    for page in range(0, len(tiles), 20):
        contact_sheet(tiles[page:page + 20], args.output / f"candidates-{page // 20 + 1:02d}.jpg")
    (args.output / "candidates.json").write_text(json.dumps(candidates, indent=2), encoding="utf-8")
    print(f"Contact sheets: {len(tiles)} camera JPEGs in {args.output}", flush=True)
    return int(any(not r["ok"] for r in records + candidates))


if __name__ == "__main__":
    raise SystemExit(main())
