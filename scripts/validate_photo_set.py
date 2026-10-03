"""Private, local-only contact sheets and native Auto/export validation."""

from __future__ import annotations

import argparse
from html import escape
from io import BytesIO
import json
from pathlib import Path
from time import perf_counter

import numpy as np
from PIL import Image, ImageDraw, ImageOps

from openraw_studio.core.files import sha256_file
from openraw_studio.decision.auto_adjust import suggest_auto_adjustments_from_preview
from openraw_studio.pipeline.interfaces import PipelineRequest
from openraw_studio.pipeline.local import LocalPhotoPipeline
from openraw_studio.raw.native.dng import DngMetadataReader
from openraw_studio.raw.native.interactive import prepare_interactive_photo
from openraw_studio.raw.native.nikon import _apply_exif_orientation
from openraw_studio.raw.native.tone import PreviewRgbImage


def contact_sheet(tiles, output, columns=4):
    width, height = 300, 230
    sheet = Image.new(
        "RGB",
        (columns * width, ((len(tiles) + columns - 1) // columns) * height),
        "#eef0f2",
    )
    draw = ImageDraw.Draw(sheet)
    for index, (label, image) in enumerate(tiles):
        x, y = index % columns * width, index // columns * height
        thumb = ImageOps.contain(image, (width - 12, height - 35))
        sheet.paste(thumb, (x + (width - thumb.width) // 2, y + 5))
        draw.text((x + 8, y + height - 25), label, fill="#18262a")
    sheet.save(output)


def comparison_report_html(records):
    sections = []
    unchanged = sum(record.get("source_unchanged") is True for record in records)
    for index, record in enumerate(records):
        title = escape(record.get("case") or Path(record["source"]).name)
        reason = escape(record.get("reason", ""))
        source = escape(record["source"])
        if record["ok"]:
            frames = "".join(
                f'<figure><a href="photo-{index:02d}/{name}.jpg">'
                f'<img src="photo-{index:02d}/{name}.jpg" alt="{label}" loading="lazy"></a>'
                f'<figcaption>{label}</figcaption></figure>'
                for name, label in (("original", "Original RAW render"), ("auto", "Auto 70%"))
            )
            result = f'<div class="pair">{frames}</div>'
        else:
            result = f'<p class="error">{escape(record.get("error", "Validation failed"))}</p>'
        sections.append(
            f'<section><h2>{index + 1:02d}. {title}</h2><p>{reason}</p>'
            f'{result}<p class="source">{source}</p></section>'
        )
    return (
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>OpenRAW - Private sample comparisons</title><style>'
        'body{margin:0;background:#fff;color:#252b2d;font:15px system-ui,sans-serif;letter-spacing:0}'
        'main{max-width:1160px;margin:auto;padding:24px}h1{font-size:26px}h2{font-size:18px}'
        'header{border-bottom:2px solid #147a68;padding-bottom:16px}p{line-height:1.5}'
        'section{padding:20px 0;border-bottom:1px solid #dce1e4}.pair{display:grid;grid-template-columns:1fr 1fr;gap:16px}'
        'figure{margin:0;min-width:0}img{width:100%;height:350px;object-fit:contain;background:#eef1f2}'
        'figcaption{padding:8px 0;color:#596268}.source{color:#596268;overflow-wrap:anywhere;font-size:12px}'
        '.error{color:#a44225}@media(max-width:620px){.pair{grid-template-columns:1fr}main{padding:16px}img{height:290px}}'
        '</style><main><header><h1>OpenRAW sample comparisons</h1>'
        f'<p>{len(records)} local samples. {unchanged}/{len(records)} source hashes verified unchanged. No photos uploaded.</p>'
        '<p>Native RAW render versus Auto at 70%. These are validation samples, not model training.</p>'
        '</header>' + "".join(sections) + '</main></html>'
    )


def browse(folder, output):
    folders = {}
    for path in sorted(folder.rglob("*")):
        if path.suffix.lower() in {".nef", ".nrw", ".dng"} and path.is_file():
            folders.setdefault(path.parent, []).append(path)
    records, tiles = [], []
    for paths in folders.values():
        indices = np.linspace(0, len(paths) - 1, min(24, len(paths)), dtype=int)
        for index in indices:
            source = paths[index]
            record = {"index": len(records), "source": str(source)}
            try:
                embedded = DngMetadataReader().read_embedded_jpeg_preview(source)
                metadata = DngMetadataReader().read(source)
                with Image.open(BytesIO(embedded.data)) as opened:
                    image = _apply_exif_orientation(
                        opened.convert("RGB"), metadata.summary.get("orientation", 1)
                    )
                image.thumbnail((500, 500))
                tiles.append(
                    (f"{record['index']}: {source.parent.name}/{source.name}", image)
                )
                record["ok"] = True
            except (OSError, ValueError, RuntimeError) as error:
                record.update(ok=False, error=str(error))
            records.append(record)
    for page in range(0, len(tiles), 20):
        contact_sheet(
            tiles[page : page + 20], output / f"candidates-{page // 20 + 1}.jpg"
        )
    return records


def validate(sources, output, export):
    records, tiles = [], []
    pipeline = LocalPhotoPipeline()
    for source in sources:
        record = {"source": str(source)}
        before = sha256_file(source)
        try:
            started = perf_counter()
            photo = prepare_interactive_photo(
                pipeline.raw_processor, source, max_dimension=960
            )
            original, backend = photo.render({})
            record.update(prepare_seconds=perf_counter() - started, backend=backend)
            sample = ImageOps.contain(original, (256, 256))
            preview = PreviewRgbImage(
                sample.width, sample.height,
                tuple(map(tuple, np.asarray(sample).reshape(-1, 3))), "gamma-2.2"
            )
            started = perf_counter()
            suggestion = suggest_auto_adjustments_from_preview(
                preview,
                render=lambda values: ImageOps.contain(
                    photo.render(values)[0], (256, 256)
                ),
            )
            adjustments = {
                key: value * 0.7 for key, value in suggestion.as_overrides().items()
            }
            adjusted, _ = photo.render(adjustments)
            record.update(
                auto_seconds=perf_counter() - started,
                scene=suggestion.scene,
                metrics=suggestion.metrics,
                adjustments=adjustments,
            )
            item_dir = output / f"photo-{len(records):02d}"
            item_dir.mkdir(exist_ok=True)
            original.save(item_dir / "original.jpg", quality=95)
            adjusted.save(item_dir / "auto.jpg", quality=95)
            tiles.extend(
                [
                    (f"{len(records)}: {source.name} Original", original),
                    (f"{len(records)}: Auto 70% / {suggestion.scene}", adjusted),
                ]
            )
            if export:
                started = perf_counter()
                result = pipeline.process(
                    PipelineRequest(source, item_dir, overrides=adjustments)
                )
                record["export_seconds"] = perf_counter() - started
                with Image.open(result.exports[0].path) as full:
                    full.load()
                    record["export_size"] = list(full.size)
                    resized = full.resize(adjusted.size, Image.Resampling.BOX)
                    record["preview_export_mean_error"] = float(
                        np.abs(
                            np.asarray(resized, dtype=float)
                            - np.asarray(adjusted, dtype=float)
                        ).mean()
                    )
            record["ok"] = True
        except (OSError, ValueError, RuntimeError, NotImplementedError) as error:
            record.update(ok=False, error=f"{type(error).__name__}: {error}")
        record["source_unchanged"] = before == sha256_file(source)
        records.append(record)
        print(json.dumps(record), flush=True)
    for page in range(0, len(tiles), 12):
        contact_sheet(
            tiles[page : page + 12],
            output / f"comparison-{page // 12 + 1}.jpg",
            columns=2,
        )
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browse", type=Path)
    parser.add_argument("--source", action="append", type=Path, default=[])
    parser.add_argument("--manifest", type=Path, help="Private JSON list of source/case/reason records")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--export", action="store_true")
    args = parser.parse_args()
    selection = []
    if args.manifest:
        try:
            selection = json.loads(args.manifest.read_text(encoding="utf-8"))
            if not isinstance(selection, list) or not all(
                isinstance(item, dict) and isinstance(item.get("source"), str)
                for item in selection
            ):
                raise ValueError("Expected a list of source/case/reason objects")
            args.source.extend(Path(item["source"]) for item in selection)
        except (OSError, ValueError) as error:
            parser.error(str(error))
    if not args.browse and not args.source:
        parser.error("Provide --browse FOLDER, --source RAW, or --manifest JSON")
    args.output.mkdir(parents=True, exist_ok=True)
    records = (
        browse(args.browse, args.output)
        if args.browse
        else validate(args.source, args.output, args.export)
    )
    annotations = {str(Path(item["source"])): item for item in selection}
    for record in records:
        annotation = annotations.get(record["source"], {})
        record.update({key: annotation[key] for key in ("case", "reason") if key in annotation})
    (args.output / "report.json").write_text(
        json.dumps(records, indent=2), encoding="utf-8"
    )
    if not args.browse:
        (args.output / "index.html").write_text(comparison_report_html(records), encoding="utf-8")
    return int(
        any(
            not record["ok"] or not record.get("source_unchanged", True)
            for record in records
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
