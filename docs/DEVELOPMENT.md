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

## Broader Nikon Sample Validation

The October 2026 local catalog read 6,847 NEF candidates. Metadata identified
3,852 D500 lossless 14-bit, 2,497 1 J5 lossy 12-bit, and 492 Z f HE* 14-bit files;
six containers failed bounds validation. Three additional sampled JPEG previews
were unreadable. These errors are retained in private reports, not silently
repaired or deleted. No Z5 sample was identified in that catalog.

Fourteen representative D500 files were actually decoded and exported, including
backlight, warm/blue scenery, silhouettes, neon, fireworks, high ISO, and an
aspect-cropped portrait. All 289,004,800 sensor samples and active crop rectangles
matched an independent development decoder; all source hashes stayed unchanged.
This validates these samples, not every file in the metadata inventory or exact
camera-JPEG color matching. Private manifests and results stay under `output/`.

Auto now uses rendered checks for newly crushed shadows and unintended midtone
darkening as well as highlight clipping. It reduces positive contrast first when
that harms a dim subject. A strongly dominant color with insufficient neutral
coverage no longer drives a white-balance correction. These are conservative
heuristics, not semantic detection, denoising, or model training. Small bright
subjects now have an additional rendered guard described below; mixed-light skin
and silhouette intent still need quality work.
The optional render callback must return the same pixel count as its baseline.

HE/HE* mode identification reads the newer MakerNote `0x0051` record at byte
offset 10; legacy `0x0093` remains supported. The Huffman path explicitly rejects
HE/HE*, even if a stale legacy linearization table is also present. Verified Z f
HE* files now dispatch to a separate guarded native decoder. For other profiles,
the desktop names the unsupported compression and labels its JPEG as preview-only.
Changing a camera's future recording mode does not convert existing HE files.
References: [ExifTool Nikon tag definitions](https://github.com/exiftool/exiftool/blob/master/lib/Image/ExifTool/Nikon.pm)
and [Nikon Z f recording options](https://onlinemanual.nikonimglib.com/zf/en/psm_raw_recording_122.html).

```powershell
.\.venv\Scripts\python.exe scripts\catalog_raw_samples.py "E:\Photos" --model D500 --per-folder 10 --output output\catalog
.\.venv\Scripts\python.exe scripts\validate_photo_set.py --manifest output\selection.json --export --output output\selected-check
.\.venv\Scripts\python.exe scripts\smoke_preview_only.py "E:\Photos\unsupported-HE-profile.NEF" --output output\preview-only-check
```

The catalog uses read-only memory mapping to avoid loading every sensor payload.
Its contact sheets contain camera JPEGs for selection, not proof of native decode.
The optional manifest is a JSON list of objects with `source`, `case`, and `reason`.
The preview-only smoke test requires an unsupported profile and checks disabled
editing/export and switching back to a supported file using actual Tk widgets.
These scripts do not upload photographs.

### Small Highlight Guard

Auto also counts newly clipped channels with baseline headroom (at most 250/255).
For baseline channels at least 0.60, newly clipped values may consume at most 2%
of that set, with a two-channel allowance for isolated outliers. A separate
whole-frame new-clipping limit prevents previously clipped pixels elsewhere from
offsetting newly lost detail. It tests a smaller positive exposure first, keeping
shadow/color settings when safe, then uses the existing whole-correction backoff.
These are bounded proxy heuristics, not guarantees for sub-pixel highlights or
full-resolution sensor reconstruction. Auto strength below 100% scales settings;
it does not independently re-run the guard at each strength.

All fourteen selected D500 files still decode/export without source changes.
The blue-hour moon's validation-proxy clipped-pixel fraction at full Auto strength
fell from 0.002718 to 0.000046; the visible 70% comparison remains in the private
report. Real Tk Auto took 0.44 s on that sample. A separate D500 landscape run
measured a 52 ms median slider-to-display delay, 49 frames during 50 drag edits,
and a 1.93 s JPEG export on the local RTX 5070. These are sample-specific results.

### Verified Z f HE* Profile

`scripts/inspect_nikon_he.py` checks strip bounds, length-delimited header markers,
precinct payload bounds, slice sequence numbers, and the exact end marker. It
records Bp/Br/depth-hint distributions without decoding any image coefficients.
Eight local 6064 x 4040 Z f HE* files each walked 1010 precincts over 64 slices;
all source hashes were unchanged. This framing-only diagnostic does not itself
establish valid entropy data or sensor support; the native adapter below does
additional validation and reconstruction.

```powershell
.\.venv\Scripts\python.exe scripts\inspect_nikon_he.py "E:\Photos\HE-sample.NEF" --output output\he-framing.json
```

Format research: [published HE stream description](https://github.com/zidage/LibRaw/blob/main_alcedo/doc/nikon_he_public_algorithm.md).
The diagnostic is independently written from framing facts and local byte checks.
Upstream validation claims are not OpenRAW validation, and any future third-party
integration needs its own review.

The `raw/native/he.py`, `compiled_he.py`, and `he_transform.py` modules now
implement bounded packet parsing, significance/unary/GCLI decoding, bit-plane
unpacking, uniform dequantization, and horizontal/vertical 5/3 synthesis. The
Numba packet loop has a checked Python fallback. `decode_component_planes`
returns four color-transform components, **not** linear Bayer pixels. Only the
guarded `nikon_he.py` adapter completes the color inverse/nonlinear mapping and
returns sensor data to the public RAW renderer.

Nine private 6064 x 4040 Z f HE* samples passed full-stream comparisons with an
independently compiled development oracle: 220,705,200 dequantized coefficients
and 220,487,040 reconstructed component values matched exactly, including slice
boundaries and the partial final slice. All source SHA-256 hashes were unchanged.
The slow validation implementation took 57.4 s on one sample; its compiled
counterpart plus coefficient comparison took 1.8-1.9 s on the other eight.
Native component reconstruction took 2.38-2.76 s per sample. These are local
research timings, not HE preview/export performance or sensor-pixel validation.
The oracle was `zidage/LibRaw` at
`3f82ade9b65cfbb0a29020b76819b1a7b6e4ec78`, built only under ignored `output/`.

Verified details differ from some older reference comments: every active band
uses preceding-row prediction in this observed depth-hint profile; state resets
every 16 precincts; bands 12/23 share the two unfiltered rows; and uniform integer
reconstruction, not the older described midpoint formula, matches the oracle.
Truncation levels are calculated from each file's WGT entries, not a copied
camera table. Group padding is excluded before inverse wavelet synthesis.

The experimental reader accepts the observed 14-bit, 5-horizontal/1-vertical
configuration with 25 active depth-hint values of 3; raw-coded packets and other
profiles fail explicitly. A 64-million-sample allocation bound applies. Synthetic
tests cover multiple widths, predictor resets, negative lifting, partial slices,
bitstream bounds/padding, damaged packets, transactional state, and JIT fallback.
Synthetic width coverage does not establish support for additional cameras.
The complete 278-test suite passes with automatic GPU selection and with
`OPENRAW_GPU=off` (one GPU-only skip). New synthetic tests cover the complete
pipeline, color-lifting borders, curve bounds, CPU/JIT equality, crop/orientation,
JPEG/TIFF output, decoder-cache reuse, and rejection of unknown profiles before
editing is enabled.

```powershell
.\.venv\Scripts\python.exe scripts\inspect_nikon_he.py "E:\Photos\HE-sample.NEF" --decode-components --output output\he-components.json
```

The color inverse reconstructs RGGB from luma, red difference, diagonal
difference, and blue difference. Neighbor extension must happen before lifting
the virtual bottom row; clamping the already lifted row produces border errors.
All 220,487,040 nonlinear Bayer codes across the nine samples, plus 150 randomized
synthetic fields, match the development oracle exactly.

The nonlinear mapping is a project-authored, computed two-sided quadratic with
black=1008 and white=16383, not an imported decoder lookup table. It differs by
at most 1 DN against the oracle across its entire index domain and all nine
14-bit linear sensor planes (mean absolute sensor difference 0.264-0.331 DN).
This is an approximation, **not** bit-exact Nikon curve reproduction. The adapter
requires Nikon Z f, HE* mode 14, 14-bit RGGB, the observed black levels, matching
container/stream dimensions, and exact PIH profile bytes. Unknown configurations,
raw-coded packets, and unverified depth hints fail before claiming edit support.
HE mode 13, other cameras/profiles, and Z5 remain unverified.

All nine samples pass native preparation, Auto, and full-resolution JPEG export
without source changes. Landscape exports are 6048 x 4032 after active cropping;
portrait orientation is retained. Local preparation took 3.38-4.28 s and cached
exports 1.72-2.41 s. One actual desktop run passed 16 checks including pointer
dragging, history, Auto, original comparison, JPEG/TIFF export, and persisted edits.
A separate RTX 5070 run measured 58 ms median slider-to-display delay, 49 frames
during 50 drag edits, 1.98 s export, and 4.77 s first native preview. These are
sample-specific timings, not a promise of instant first import. Auto still
backs off entirely on one dark museum scene with a small bright subject; improving
that correction without losing highlight detail is further quality work.

The refreshed local Windows bundle also passes actual frozen-runtime checks:
HE packet JIT stays active even when its disk cache cannot be written. A landscape
GPU run prepared in 6.32 s and exported in 2.54 s at 6048 x 4032. A separate
CPU-only portrait run prepared in 4.98 s and exported in 6.01 s at 4032 x 6048.
These are different photographs, not a controlled GPU speedup ratio. Neither
run changed its source. All fourteen D500 regression exports also still pass.
The bundle contains no private photographs or external reference decoder.

Next: reduce initial import latency, expand verified profiles only with real
samples, and improve difficult-scene Auto. The reference decoder, wrapper, binaries, private
images, and comparison reports stay in ignored `output/`; none is a runtime or
distributed dependency. Primary algorithm references include
[JPEG XS decoder design](https://github.com/OpenVisualCloud/SVT-JPEG-XS/blob/main/documentation/decoder/svt-jpegxs-decoder-design.md)
and [Richter et al., Bayer CFA Pattern Compression With JPEG XS](https://doi.org/10.1109/TIP.2021.3095421).
