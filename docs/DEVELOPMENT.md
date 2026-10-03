# Development Guide

## Current Architecture Status

The native desktop editor now imports supported DNG/Nikon files, keeps local
non-destructive edits, previews adjustments continuously, and exports JPEG/TIFF.
Support is intentionally format-specific; the README lists verified paths.

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

## Editor And Private Photo Checks

```powershell
.\.venv\Scripts\python.exe scripts\smoke_desktop_workflow.py --source "E:\Photos\sample.NEF" --output output\editor-check --geometry 800x600
.\.venv\Scripts\python.exe scripts\smoke_batch_workflow.py --output output\batch-check
.\.venv\Scripts\python.exe scripts\validate_photo_set.py --browse "E:\Photos" --output output\candidates
.\.venv\Scripts\python.exe scripts\validate_photo_set.py --source "E:\Photos\sample.NEF" --output output\photo-check --export
```

The editor smoke test uses actual Tk pointer events, verifies displayed pixels,
undo/redo, Auto strength, original comparison, zoom/pan, JPEG/TIFF, and edit restore.
The batch check covers saved edits, independent Auto, cancellation, and scrollable
small-window export controls. Both isolate local edit records from normal use.
Contact sheets, source paths, exports, and screenshots stay in ignored `output/`.
Never publish these private artifacts as fixtures or README screenshots.

Nine private D500/J5 photos were checked locally across foliage, backlight, blue
lighting, portraits, strong overexposure, and a sparse bright night subject. Seven
J5 visible sensor arrays (145,563,712 samples total) matched an independent
development decoder exactly. This does not certify all Nikon files or color fidelity.
No third-party RAW decoder is imported by the app or included in the Windows build.

Auto uses luminance quantiles and near-neutral midtones, not semantic recognition.
Its optional render callback must use the same unedited proxy and sampling as the
input preview. Clipping checks concern rendered pixels, not recovered sensor detail.
The shadow curve is now black-anchored; Nikon highlights use a neutral white-balance
ceiling before the color matrix to prevent false magenta in clipped regions.
Existing recipes with shadows/highlights may render differently from older builds.

The Nikon D20 implementation currently accepts only 12-bit, non-split streams
with validated monotonic linearization knots. Legacy `*_34713_lossless` function
names remain for compatibility but also handle this explicitly guarded D20 path.
Format references: [NEF compression research](https://photonstophotos.net/NikonInfo/NEF_Compression.htm)
and [published Nikon Huffman tables](https://github.com/LibRaw/LibRaw/blob/master/src/decoders/decoders_dcraw.cpp).
The bitstream loop and linearization implementation are OpenRAW-owned.

Frozen Windows testing also covers a redirected/unwritable Numba disk cache:
cache-write failures retry in-memory JIT before using the Python fallback.
This keeps a cache permission/cross-volume error from turning RAW import into a
slow interpreted decode. The packaged diagnostic distinguishes compilation
from disk-cache availability.
