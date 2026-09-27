# Development Guide

## Current Architecture Status

The code currently defines contracts plus a first CLI/dry-run pipeline skeleton.
That is deliberate. The V0.1 goal is to turn this skeleton into a real vertical
slice while keeping the engines replaceable.

## Recommended V0.1 Build Order

1. Implement a real RAW backend adapter behind `RawProcessor`.
2. Generate previews into an output artifact folder.
3. Extract real EXIF/RAW metadata.
4. Replace the placeholder image reference with real preview dimensions.
5. Export JPEG or lossless 8-bit TIFF through `ExportEngine`.
6. Add local smoke-test guidance that does not commit private photos.
7. Add import-folder watching after one-file processing works.

Already present:

- `openraw doctor`
- `openraw process --dry-run`
- artifact path planning
- checksum and source metadata
- heuristic `VisionEngine`
- rule-based `DecisionEngine`
- `recipe.v1` sidecar writing
- OpenRAW Native RAW engine scaffold
- local JPEG/TIFF `ExportEngine`
- Nikon `.NEF` / `.NRW` metadata import
- Nikon `.NEF` / `.NRW` embedded JPEG preview extraction
- guarded Nikon `.NEF` / `.NRW` native sensor decode for TIFF-style uncompressed Bayer payloads
- row-aligned 12/14-bit packed Bayer strip decoding for guarded DNG/Nikon payloads

## Module Ownership

- `raw`: decode, inspect, preview, and base render only.
- `vision`: analyze content only.
- `decision`: produce targets, constraints, confidence, and recipe updates.
- `portrait`: apply mask-aware and landmark-aware portrait changes.
- `color`: apply scene-aware color transforms while protecting skin.
- `film`: apply film profile behavior separate from LUTs and color correction.
- `qc`: detect clipping, halos, oversmoothing, geometry artifacts, and failures.
- `export`: write derivative files and sidecar recipes.
- `pipeline`: coordinate stages without absorbing algorithm code.

## UI Design Rule

Before implementing desktop UI screens, read `docs/UI_DESIGN.md`.

The app should feel simple, calm, and premium. Beginner operation comes first:

```text
import -> AUTO -> compare -> export
```

Advanced controls should be expandable and quiet, not dumped onto the first
screen.

## Testing Strategy

Phase 0 tests are contract smoke tests. As implementations arrive:

- test each engine adapter with fakes or small controlled inputs
- test decisions without requiring model inference
- test recipe migration separately from processing
- keep private RAW files outside Git
- add regression image support only after licensing is clear

## Live Preview Performance

Run an actual Tk slider/export benchmark with a local supported RAW:

```powershell
.\.venv\Scripts\python.exe scripts\benchmark_live_preview.py "E:\Photos\sample.NEF" --output output\live-benchmark --screenshot
```

Use `--cpu` to verify fallback, or `--geometry 800x600` for the small-window case.
The script drives real slider callbacks, checks displayed pixels, sends 50 edits
during a continuous drag, exports at full resolution, and checks the source hash.
It records JSON and optional screenshots under the ignored output directory.
Screenshots contain private photographs and must not be committed.

Local Windows measurements (RTX 5070, Nikon D500, 3712 x 5568 portrait export):

| Measurement | GPU automatic | CPU forced |
| --- | --- | --- |
| Slider-to-display median | 46-51 ms | 110 ms |
| Frames displayed during 50 continuous edits | 48-49 | 12 |
| GUI JPEG export after preview | 2.6-3.1 s | 6.2 s |
| Import-to-editable RAW preview, compiled cache warm | 1.7-1.8 s | 1.4 s |

These are local measurements, not performance guarantees for all hardware or
RAW formats. GPU/Numba first-use compilation can increase initial loading time.
A second D500 NEF tested with an empty Numba cache took 2.5 s to display its first
editable RAW preview and 2.7 s to export after editing.
Status-bar render timings exclude some GUI scheduling/display work; use the
benchmark's end-to-end measurements when comparing responsiveness.

The compiled Nikon decoder is tested against the reference implementation.
OpenCL rendering is compared with CPU output within one 8-bit code value, across
Bayer layouts/crop offsets. Hardware checks skip when no GPU is available; the
CPU fallback and pending-request/source-change behavior are tested independently.
