"""Private, local-only contact sheets and native Auto/export validation."""

from __future__ import annotations

import argparse
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
                with Image.open(BytesIO(embedded.data)) as opened:
                    image = ImageOps.exif_transpose(opened).convert("RGB")
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
                sample.width, sample.height, tuple(sample.getdata()), "gamma-2.2"
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
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--export", action="store_true")
    args = parser.parse_args()
    if not args.browse and not args.source:
        parser.error("Provide --browse FOLDER or one or more --source RAW")
    args.output.mkdir(parents=True, exist_ok=True)
    records = (
        browse(args.browse, args.output)
        if args.browse
        else validate(args.source, args.output, args.export)
    )
    (args.output / "report.json").write_text(
        json.dumps(records, indent=2), encoding="utf-8"
    )
    return int(
        any(
            not record["ok"] or not record.get("source_unchanged", True)
            for record in records
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
