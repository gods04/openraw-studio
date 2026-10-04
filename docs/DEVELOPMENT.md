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
5. Export JPEG or lossless 8/16-bit TIFF through `ExportEngine`.
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
offsetting newly lost detail. It jointly tests reduced exposure, neutral contrast,
and bounded highlight compression, retaining shadow/color settings when safe,
then uses the existing whole-correction backoff.
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
The complete 304-test suite passes with automatic GPU selection and with
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
At that milestone HE mode 13, other cameras/profiles, and Z5 were unverified;
the public-sample validation below subsequently adds Z f HE and Z5 lossless.

All nine samples pass native preparation, Auto, and full-resolution JPEG export
without source changes. Landscape exports are 6048 x 4032 after active cropping;
portrait orientation is retained. The initial implementation prepared in 3.38-4.28 s and cached
exports 1.72-2.41 s. One actual desktop run passed 16 checks including pointer
dragging, history, Auto, original comparison, JPEG/TIFF export, and persisted edits.
A separate RTX 5070 run measured 58 ms median slider-to-display delay, 49 frames
during 50 drag edits, 1.98 s export, and 4.77 s first native preview. These are
sample-specific timings, not a promise of instant first import. The dark museum
scene initially forced Auto to back off entirely; joint tone recovery below
now retains a modest correction on that sample.

The initial local Windows bundle also passed actual frozen-runtime checks:
HE packet JIT stays active even when its disk cache cannot be written. A landscape
GPU run prepared in 6.32 s and exported in 2.54 s at 6048 x 4032. A separate
CPU-only portrait run prepared in 4.98 s and exported in 6.01 s at 4032 x 6048.
These are different photographs, not a controlled GPU speedup ratio. Neither
run changed its source. All fourteen D500 regression exports also still pass.
The bundle contains no private photographs or external reference decoder.

### Public Z f HE and Z5 Lossless Validation

Seven public samples were obtained from the [raw.pixls.us catalogue](https://raw.pixls.us/)
and checked against the SHA-256 values in its
[structured catalogue](https://raw.pixls.us/json/getrepository.php?set=all).
Each entry is labeled CC0. Files, derivatives, and development reports stay in
ignored `output/public-nikon`; no photograph or reference binary is shipped.

| Camera / Mode | Catalogue ID / Original Filename | SHA-256 |
| --- | --- | --- |
| Z5 lossless 14-bit | 4136 / DSC_0517.NEF | `9870248c45532080c2459f3b52cdcc4a07bc8846ad50cbb81c7c1c6dc5e36d91` |
| Z5 lossless 12-bit | 4137 / DSC_0518.NEF | `ac11b88c978958222af79946c01071f270e0d660ae8fb5e1f15e4c965a25aa54` |
| Z5 D40 lossy 12-bit | 4138 / DSC_0519.NEF | `d893b6f7e7d2c0aa7d4b016a559bf0d9af440c9ec7486119085c2b2368c7a2a4` |
| Z5 D40 lossy 14-bit | 4139 / DSC_0520.NEF | `e675138e7185b026000c97b654b80ac230c5796f48a9180633672ddacee01bed` |
| Z f lossless 14-bit | 6885 / DSC_0040.NEF | `83c82be0be8865d796096dfbcc8ef2abf5af1bd37db44dfad6715070b0c99d15` |
| Z f HE* 14-bit | 6886 / DSC_0042.NEF | `c888f109dc420e359853a2ce768d8a6274b8ba0109b2f5cb6e0c982981d5a624` |
| Z f HE 14-bit | 6887 / DSC_0043.NEF | `98d6ca8e6c98048ca7ffed68ccaeda7b2b9f03807f0d320d97d5678db21748c2` |

The HE samples' catalogue labels say 8-bit, but the sensor IFD and HE components
are 14-bit. Support decisions use actual container/stream metadata, not the
catalogue label. The HE and HE* samples have the same verified PIH configuration,
black levels, CFA, and packet coding as the existing guarded profile. The native
adapter now accepts MakerNote modes 13 and 14 and reports their names separately;
it does not relax any nonlinear, geometry, packet, depth-hint, or camera guard.

All 24,498,560 nonlinear Bayer values per HE/HE* file match the isolated oracle
at `3f82ade9b65cfbb0a29020b76819b1a7b6e4ec78` exactly. Linear sensor differences
are bounded by 1 DN, with MAE 0.32388 / 0.32412 respectively. This remains an
approximate nonlinear mapping, not Nikon bit-exact or lossless reproduction.
The ordinary Z f lossless sample matches all 24,498,560 sensor values exactly.

Both Z5 lossless samples match all 24,353,280 sensor values exactly. The 12-bit
MakerNote stores black as 1008 in 14-bit units; native normalization now uses
252, matching the independent metadata reference. 14-bit black remains 1008.
White levels remain 4095 / 16383. This conversion is limited to verified J5/Z5
models; unrelated models are not guessed. Z5 now uses the DNG Converter 13.2
numeric calibration recorded in the
[RawTherapee camera-data table](https://github.com/RawTherapee/RawTherapee/blob/dev/rtengine/camconst.json).
No external decoder, image-processing implementation, or runtime is integrated.
The two Z5 D40 samples were initially blocked; the subsequent D40 implementation
below validates them independently of J5 D20. Z5 II, crop variants, and other HE profiles have
not been established by this small sample set.

All five supported public files pass native preparation, Auto70, full-size JPEG,
and unchanged source hashes. Z5 outputs 6016 x 4016; Z f outputs 6048 x 4032.
On this RTX 5070, cached-decoder export takes 1.21-1.57 s; these are per-sample
measurements, not first-import or universal latency guarantees. Contact sheets
were inspected for color/crop artifacts, not used as proof of sensor accuracy.
Actual compact Tk workflows pass 24 checks each on HE/GPU and Z5 12-bit/CPU,
including pointer edits, history, Auto, original comparison, JPEG/TIFF,
zoom/pan, saved edits, and sampled noise advice.

The 431-test suite passes with automatic GPU selection and CPU-only (four
GPU-only skips). The refreshed local Windows EXE passes HE/GPU and Z5 12-bit/CPU
Auto/export/native-detail checks; recipes and detail pixels match source execution
exactly. HE GPU export takes 1.38 s. Z5 CPU export takes 3.26 s initially and
2.86 s after restarting with cached compilation; corresponding Auto times are
2.37 / 0.96 s. No runtime/compiler/cache fallback occurs. The existing optional
TBB packaging warning remains; no reference decoder, photo, public ZIP, or
installer is included in this local update.

Separate actual Tk latency runs measure 59 ms median slider-to-display on HE/GPU
and 72 ms on Z5/CPU, with 49 / 48 intermediate frames during 50 drag changes.
First RAW display remains 4.93 / 2.29 s respectively. These are different photos
and decode formats, not a controlled GPU speedup ratio; first-use initialization
is still unfinished work.

The final local metadata inventory also covers 50 previously uncatalogued files:
47 J5 NEFs, two D500 NEFs, and one Lightroom RGB smart-preview DNG. There is still
no private Z5 capture in the authorized photo roots. Inventory is not full-image
decode verification, and a Lightroom smart preview is not original sensor RAW.

### Native D40 Non-Split Decoding

The public Z5 samples `DSC_0519.NEF` and `DSC_0520.NEF` have D40 tables (`44 40`),
257 equally spaced knots, and no split row. D40 entropy indexes span one quarter
of the declared sensor range (1024 for 12-bit, 4096 for 14-bit). The project-owned
decoder uses the appropriate numeric canonical Huffman table, predicts indexes,
then interpolates the file's curve with integer floor arithmetic. The 14-bit
lossy Huffman table is format data, not an imported decoder implementation;
see the [numeric format reference](https://github.com/LibRaw/LibRaw/blob/master/src/decoders/decoders_dcraw.cpp).

The sensor white point must not be the last reconstructable curve value: on these
files those values are 4092 / 16380, while white remains 4095 / 16383. The setup
records the D40 white separately from the index lookup. Existing D20 and lossless
normalization stay unchanged. D40 predictor values outside the index domain
raise an error in both compiled and Python paths rather than being clamped into
plausible pixels. Split rows, invalid/descending/out-of-range knots, unknown
tables, and truncated entropy fail explicitly. No claim is made for all Nikon
models or every Z5 crop/firmware combination.

Both real D40 files match all 48,706,560 linear sensor values exactly against the
isolated development oracle. Black/white levels also match the independent
rawpy development check. All seven public samples listed above pass sensor
verification (HE/HE* retain the documented <=1 DN nonlinear approximation).
The 26 selected private D500/J5/Z f captures retain their previous sensor hashes
and black/white levels. All originals have unchanged SHA-256 hashes.

Synthetic D40 coverage includes both bit depths, both MakerNote byte orders,
nonlinear curve endpoints, predictor parity, compiled/Python equality, malformed
metadata/entropy, range rejection, crop/orientation, non-destructive editing,
JPEG/TIFF export, and decoder reuse. Both real D40 samples pass native Auto70 and
full-size 6016 x 4016 GPU JPEG export in 1.20-1.23 s with decoded data cached.
Their rendered contact sheet was inspected; no camera-JPEG substitution is used.

All 436 tests pass with automatic GPU selection and CPU-only (four GPU-only
skips). Two compact desktop workflows pass 24 checks each on D40 12-bit/CPU and
14-bit/GPU, including live pointer edits, history, Auto, comparison, JPEG/TIFF,
saved edits, and safe noise advice. A mixed CLI batch exports all seven public
Z5/Z f lossless, D40, HE, and HE* samples with no skips or failures; every JPEG
opens at the expected full size and all source hashes remain unchanged.

The refreshed local Windows EXE passes both D40 paths, with recipes and native
detail pixels matching source execution exactly. CPU 12-bit preparation/Auto/
JPEG export take 2.08/2.32/3.13 s on this first updated-bundle run; the separate
GPU 14-bit sample takes 1.46/0.55/1.25 s. These are different photographs, not a
controlled acceleration ratio or first-use guarantee. Compilation stays active
without fallback. The existing optional TBB packaging warning remains; no
external RAW decoder, photo, public ZIP, or installer is bundled or published.

### HE Import Acceleration and Frozen Caches

Horizontal synthesis now optionally compiles OpenRAW's integer dequantization
and lifting loops. Without the compiler, NumPy batches 32 precincts while entropy
predictors retain their original slice boundaries. Color reconstruction uses
64-row tiles with two source-neighbor rows on either side, then writes linear
uint16 pixels directly. It no longer requires a full nonlinear int64 Bayer frame
or full-frame intermediate color planes. The checked NumPy fallback remains.

All nine real Z f sensor-buffer hashes are identical before/after this change
(220,487,040 samples); no source hash changed. On a same-session nine-photo run,
decode after first-use initialization fell from 3.08-3.42 s to 1.23-1.40 s.
A real desktop run showed 2.90 s first RAW display (previously 4.77 s), 58 ms
median slider response, 49 frames during 50 drag edits, and 2.02 s JPEG export.
All 16 desktop workflow checks still pass. Synthetic tests compare every
transform path, varying batch boundaries, negative values, narrow/odd component
dimensions, padding widths, and unavailable/unwritable compiler cache behavior.

Frozen kernels load as source modules through the PyInstaller hook, not PYZ
bytecode with virtual source filenames. Merely adding .py files as data did not
make Numba use the requested cache locator. Before importing Numba, the launcher
selects a resolved per-user cache directory under `OpenRAW Studio/numba` unless
`NUMBA_CACHE_DIR` was explicitly supplied. Resolution alone was later found
insufficient for Windows app-virtualized file writes; the launcher now tests
atomic replacement and tries a private home cache before the in-memory JIT
fallback (see "Verified Frozen Cache Selection" below).

Actual independent EXE launches verify persistence: Z f preparation was 6.35 s
with no compiled cache, then 2.14 s and 1.90 s after process restarts; exports were
1.96-2.09 s at 6048 x 4032. All three HE kernels remained compiled with no cache
errors. A separate D500 check prepared in 1.47 s initially and 0.87 s after restart,
exporting in 1.58-1.62 s at 5568 x 3712 with its compiled decoder active. These are
local sample-specific measurements; new app builds can require recompilation.

### Joint Auto Tone Recovery

When a suggestion fails highlight checks, Auto now searches a bounded set of
exposure reductions, neutral contrast, and highlight compression down to -0.30.
It rechecks dark tones after changing exposure: a recovered dim-scene correction
must not darken median luminance. Low-key intent and existing clipping/shadow
budgets remain unchanged. Failed recovery still backs off the entire correction.
Per-call metric caching avoids duplicate renders; `validation_renders` and
`highlights_guarded` expose this work without retaining rendered images.

The initial joint-guard validation script used the same scene-linear 256-pixel
proxy as desktop Auto, not a downsized gamma-encoded display image. This fixed a sampling
discrepancy in older comparison reports. Fourteen D500 and nine Z f samples all
pass native Auto/full-size JPEG export with unchanged source hashes. On the local
RTX 5070, Auto plus the 960-pixel comparison render took 0.033-0.109 s, and cached
JPEG exports took 1.60-2.46 s. These exclude first import/decoder preparation.

The museum sample now keeps +0.15 EV, zero contrast, and -0.30 highlights at
100% Auto. Proxy median luma rises from 0.2527 to 0.2700, with one newly clipped
bright channel out of 249 (below the existing allowance). Actual Tk Auto took
0.62 s including proxy preparation/display, and all 16 workflow checks passed.
A separate D500 moon run measured 51 ms median slider response, 49 frames during
50 drag edits, and 1.76 s export. Unit regressions cover real linear rendering,
zero/negative exposure, the one-render fast path, and bounded duplicate-free search.

The initial implementation's 23-photo audit at 25/50/70/100% strength
and 256/960-pixel resolution passed 178 of 184 clipping/shadow checks. All 256-pixel
checks passed, but six 960-pixel cases exceeded the same budgets: backlight, moon,
and high-ISO highlight detail at 100%; fine sea highlights at 70% and 100%; and
temple shadow detail at 100%. This is evidence that a small analysis proxy misses
fine detail, not proof of full-resolution protection. The two-level validation
below addresses those six observed cases. Reports remain local.

### Detail-Aware Auto Validation

Desktop Auto, per-photo batch Auto, local sample validation, and packaged smoke
checks now share `suggest_auto_adjustments_for_photo`. It derives the 256-pixel
analysis proxy from the unedited 960-pixel scene-linear display proxy. Color
parameters and EXIF orientation remain intact; no edited or gamma-encoded image
is resized to make that analysis input.

Promising corrections must pass both analysis and display-resolution guards.
Candidates rejected by the small proxy skip expensive detail renders, and both
guards cache measurements. The existing clipping/shadow budgets are unchanged.
`detail_*` metrics distinguish the finer check from the original analysis metrics.
Tiny synthetic highlights that disappear during downsampling reproduce the old
failure; the new guard preserves them while retaining a useful exposure lift.

Single-photo Auto reuses the live worker's prepared, unedited proxy rather than
rebuilding it from sensor data. Access is synchronized and keyed by resolved path,
file size, and modification time. Source switches, invalidation, missing/changed
files, and worker closure cannot hand out stale prepared state; tests also cover
an invalidated in-flight preparation. There is no new decoded-photo cache.

All 23 selected D500/Z f native Auto/full-size JPEG exports pass with unchanged
source hashes. The same 184 strength/resolution clipping and shadow checks now
all pass, including the previously failing sea, moon, backlight, high-ISO, and
temple cases. This validates those samples at 256/960 pixels and four strengths,
not full-resolution guarantees or every intermediate strength.

On the local RTX 5070, Auto including the report's display render took 0.09-0.79 s
(mean 0.18 s); full-size cached JPEG export took 1.60-2.41 s. Actual Tk Auto took
0.43 s on the museum image (previously 0.62 s), and 0.89 s on the fine-highlight
sea image. Both passed all 16 desktop workflow checks. The seven batch checks
pass, including cancellation and compact-panel scrolling. A separate moon run
measured 59 ms median slider response and 49 frames during 50 edits, with 1.79 s
export. These are sample-specific measurements, not a universal latency promise.
The 304-test suite passes with GPU auto-selection and CPU-only fallback (one
GPU-only skip).

The refreshed local EXE also passes Z f portrait GPU export (4032 x 6048) and
D500 CPU-only export (5568 x 3712), preserving source hashes and matching source
Auto settings. The difficult sea photo takes 1.70 s for Auto and 5.08 s for export
on CPU; fallback performance remains slower than the GPU path. The build still
does not generate or publish a public ZIP release.

### Native-Pixel Inspection

The desktop zoom menu now separates Fit/2x/4x proxy zoom from true 100%/200%
inspection. Double-clicking Fit centers inspection around the chosen point;
changing native scale preserves the viewed position. Dragging pans immediately
with a temporary proxy while the worker renders the new tile. No derivative or
JPEG intermediate is written to inspect detail.

`raw/native/regions.py` maps oriented viewport rectangles into the active sensor
crop. Nikon's existing full renderer accepts a bounded region with a one-pixel
bilinear interpolation halo, then trims that halo before applying EXIF orientation.
This retains full-export pixels at boundaries without demosaicing the whole image
on every pan. `raw/native/detail.py` reuses the processor's decoded Nikon cache.
The generic DNG fallback still prepares a full linear image before cropping;
large-DNG preparation has not received the same bounded-memory optimization.

The live worker retains one native original tile across adjustment changes.
Source and Fit/detail transitions invalidate old generations; viewport identity
rejects obsolete pan/resize frames. The full-photo unedited proxy remains available
for Auto, which does not analyze the currently viewed crop. Histograms explicitly
identify visible detail. 100% has exact 1:1 pixel pitch; 200% repeats pixels 2x
and crops any odd viewport edge, without fractional resampling. Fit and detail
can differ because Fit uses a downsized, half-resolution preview path.

Synthetic tests verify every EXIF orientation, all four Bayer patterns, active
crops, one-pixel borders, GPU/CPU region equality, invalid regions, small images,
odd viewport sizes, original-tile reuse, stale mode changes, and error recovery.
All 313 tests pass with GPU auto-selection and CPU fallback (one GPU-only skip).

`scripts/smoke_detail_workflow.py` checks actual Tk image pixels, pointer pan and
exposure changes, Auto, original comparison, return to Fit, and click-centered
inspection against full-resolution RAW renders. The real D500 moon, D500 sea
(CPU-only), and portrait Z f each pass 22 checks, preserving source hashes.
At tested 1280x820/900x650 window sizes, first detail after Fit takes 88-181 ms;
pan and edit updates take 46-80 ms. These exclude first RAW import and are not
universal latency guarantees. A separate Fit regression retains 53 ms median
slider latency, 49 frames during 50 edits, and 1.77 s cached D500 JPEG export.
The existing 16-check desktop and seven-check batch workflows also pass.

The refreshed local EXE passes Z f portrait GPU and D500 CPU-only smoke checks,
including full-size export, native 512x384 inspection, and source preservation.
The Z f run exports in 2.10-2.36 s and renders its detail region in 123-126 ms.
After first-build compiler initialization (6.80 s), a separate process prepares
the same file in 2.32 s with cached HE kernels active. The D500 CPU run exports
in 4.83 s and renders detail in 64 ms. These are different samples and region
sizes, not a GPU/CPU speedup comparison. No public ZIP was built or published.

Inspection uses the current bilinear renderer; it does not add edge-aware
demosaicing, denoise, 16-bit export, or support for unverified camera profiles.

### Smooth Over-Range Highlight Tones

Negative Highlights previously subtracted a constant above linear display white:
at -1, samples 1.4, 2, and 4 all encoded to 255. They now join a rational shoulder
to the existing subwhite polynomial. For amount `a = -0.3 * highlights` and
`x > 1`, the output is `1 - a*a / (a + (1 - 2*a)*(x - 1))`. At the join, both
value `1-a` and slope `1-2*a` agree. The curve remains ordered and approaches
white without a new hard cutoff. Shadows, subwhite behavior, and nonnegative
Highlights retain their existing mathematical curves.

`raw/native/tonal.py` shares scalar and in-place NumPy behavior across generic
DNG, Nikon lookup previews, live proxies, and full CPU rendering. OpenCL implements
the same expression; automatic device validation now checks the shoulder and both
saturation modes against CPU output. Nikon lookup previews evaluate overflow
above their old four-unit table directly, retaining distinctions at high exposure
without growing the table or reducing its subwhite precision.
The NumPy shoulder only evaluates over-white samples, avoiding full-frame
temporary float arrays when most of a photograph needs no over-range compression.

Full-strength Auto alone was insufficient with the new curve: one Z f portrait
passed at 100% but failed six 25/50/70% checks across the two proxy resolutions.
The full-photo Auto helper now validates 25%, 50%, and 70% as well as the endpoint
whenever a candidate uses negative Highlights. Small-proxy checks run first;
only surviving candidates pay for the display-resolution checks, and both reuse
their existing per-call measurement caches. Budgets are not relaxed, and manual
strength changes still use the fast live renderer without rerunning analysis.
The low-level callback API can request additional validation strengths explicitly.

This is not sensor highlight reconstruction or perceptual gamut mapping. Nikon's
existing neutral camera-space ceiling remains; detail lost there or through sensor
saturation cannot be restored by the shoulder. Saturation can still clip output
channels, which Auto's rendered guards continue to measure. Existing recipes with
negative Highlights may render differently. Neutral renders of all 23 selected
private photos remain byte-identical to the pre-change GPU baseline.

All 23 selected native Auto/full-resolution JPEG exports pass with unchanged
sources. A separate audit at every integer strength from 1% to 100% and both
256/960-pixel sizes passes 4,600 clipping/shadow checks. Runtime Auto samples
25/50/70/100%, not all 100 positions, to bound its cost; the wider audit proves
only these selected photos at these preview sizes. It is not a guarantee for
every image, arbitrary fractional strength, or full-resolution highlight detail.

The 322-test suite covers curve continuity/slope/order, subwhite compatibility,
scalar/NumPy/GPU parity, legacy LUT overflow through +4 EV, device rejection,
and intermediate-strength guard failures. GPU and CPU-only runs pass, with two
GPU-only skips on CPU. Actual Tk checks cover the 16-step editing/export workflow,
22 native-detail checks, and seven batch checks. The portrait Auto run takes
0.65 s and retains +0.1751 EV versus +0.0875 EV before the curve change.

The photo-set run takes 0.09-0.66 s for Auto plus its display render and 1.61-2.18 s
for cached full-size JPEG export on the local RTX 5070. With Highlights fixed at
-1, actual desktop exposure dragging takes 59 ms median and displays 49 frames
during 50 edits; export takes 2.02 s. CPU-only median improves from 124 to 109 ms
after masked shoulder evaluation, with export from 5.40 to 5.02 s. CPU dragging
still displays only 16 frames during the same 50 edits and needs more work.
Timings are local sample measurements, not universal performance promises.

The refreshed EXE passes native Auto/export/detail checks on Z f GPU and D500
CPU-only, preserving source hashes and the source-run adjustment settings.
The portrait Z f exports in 2.09-2.12 s; preparation is 6.85 s for first-build JIT
initialization and 2.20 s after process restart. The CPU sea sample exports in
5.04 s with 1.17 s Auto. No public ZIP was generated or released.

Next: faster CPU full-size export, reduced import latency, improved color/gamut handling, and
expand verified profiles only with real samples. The reference decoder, wrapper, binaries, private
images, and comparison reports stay in ignored `output/`; none is a runtime or
distributed dependency. Primary algorithm references include
[JPEG XS decoder design](https://github.com/OpenVisualCloud/SVT-JPEG-XS/blob/main/documentation/decoder/svt-jpegxs-decoder-design.md)
and [Richter et al., Bayer CFA Pattern Compression With JPEG XS](https://doi.org/10.1109/TIP.2021.3095421).

### Fused CPU Live Tones

`raw/native/compiled_tone.py` combines gain/ceiling, matrix conversion, contrast,
the existing tonal regions, gamma, and saturation in one compiled pass. Only the
RGB8 output needs a full-frame allocation for normal contiguous float32 proxies.
The preview resolution, tone curves, native decoder, and full-size export path
are unchanged. Both saturation modes retain their existing ordering. Float32
arithmetic can differ by 1 DN from NumPy; this is not bit-exact equivalence.

Compilation is lazy, cached, sequential, and releases the GIL. No fastmath or
parallel thread pool is enabled. Read-only array views normalize the JIT signature
without copying ordinary proxies or changing caller flags. A successful GPU path
does not compile CPU tones. Unwritable disk caches retry in-memory compilation;
compiler failures disable that kernel for the process and retain the original
NumPy renderer. Explicit `NUMBA_DISABLE_JIT=1` also selects NumPy instead of
running the scalar pixel loop in Python. Other programming errors are not silently swallowed. Frozen
bundles include the new module as source for Numba's cache locator, and their
smoke report records whether compiled CPU tones actually ran.

The 333-test suite passes with GPU selection and CPU-only (two GPU-only skips).
New checks cover adjustment extremes, the white join, both saturation modes,
readonly/strided/empty inputs, one stable compiled signature, shared-worker input
safety, cache failure, and compiler fallback. The selected 23 D500/Z f photos
pass 161 compiled/reference image comparisons at seven settings (maximum error
1 DN). Auto suggestions are identical to the NumPy path for every sample, and
all 184 rendered guard checks at four strengths/two sizes pass. Sources remain
unchanged; these are local validation samples, not uploaded training data.
A CPU sweep over every integer strength 1-100% at both sizes also passes all
4,600 checks. The actual 16-step Z f CPU desktop workflow verifies pointer edits,
history, Auto/strength, comparison, JPEG/TIFF export, and edit persistence.

On the same D500 moon photo with Highlights at -1, the actual 1080x720 desktop
benchmark improves CPU median slider-to-display response from 109 to 72 ms;
50 consecutive edits show 48 frames instead of 16, with 92 ms from release to
the final frame. CPU full-size JPEG export is still about 5.0 s. These local
measurements exclude first-use JIT from steady edit latency and are not a
universal responsiveness promise. First HE* import and CPU full-size export
remain separate optimization targets.

A repeated CPU run measures 62 ms median with 48 frames, 92 ms final-frame
latency, and 5.09 s export, explicitly confirming the compiled kernel was active.
The matching GPU regression measures 53 ms/49 frames and 2.08 s export, with
CPU compilation correctly unused. All 22 D500 CPU native-detail checks pass,
including byte-identical regions against the full RAW renderer. The benchmark
reports compiler activation/failure as well as the displayed backend so a slow
fallback cannot be mistaken for a successful compiled run.

The refreshed EXE passes D500 CPU and Z f GPU Auto/export/native-detail smoke
checks and independent-process cache reuse, preserving source hashes and the
source-run Auto settings. The D500 CPU run confirms compiled tone activation,
with 0.62-0.69 s Auto, 5.22-5.35 s export, and 0.69 s cached preparation after
1.99 s first-build initialization. Z f GPU preparation remains 6.41 s initially
and 2.22 s after restart; export takes 2.06-2.10 s. These preparation timings
exclude executable startup. GPU runs do not compile the CPU tone kernel.
All seven CPU batch checks pass as well. No public ZIP was generated or released.

### Compiled CPU Bayer Export

`compiled_bayer.py` evaluates the existing normalized bilinear kernel in one
neighborhood traversal, retaining position-specific black levels, crop/CFA phase,
normalization, pre-interpolation gains, and summation order. `fullres.py` calls it
in bounded chunks (256 rows by default), then reuses `compiled_tone.py` with unit
gains because interpolation already applied them. It does not allocate a whole
floating-point image or change the demosaic algorithm, output size, bit depth,
quality setting, camera support guards, or GPU-first selection.

The original NumPy interpolation is retained in `_demosaic_numpy`. Disabling both
`use_gpu` and `use_compiled` selects that reference path. Compiler failure retains
NumPy; tone-only failure can still use compiled interpolation with NumPy color.
Disabled JIT and unwritable cache paths follow the existing checked fallback
policy. The new kernel is sequential, releases the GIL, and has no fastmath.
Packaging retains its source for Numba cache lookup. Frozen/desktop benchmark
reports record actual Bayer compilation and fallback reasons after export.

All 344 tests pass with GPU selection and CPU-only (two GPU-only skips). New
checks cover reference-exact linear interpolation for every accepted CFA layout,
odd/offset crops, borders, one-row chunks, control extremes, bounded working
buffers, readonly signature reuse, invalid bounds, and compiler/cache failure.
Native detail remains byte-identical to the corresponding full render across
orientations and CFA patterns. Final RGB8 can differ from the NumPy reference
by 1 DN because the compiled tone pass uses different floating-point operations.

The private 23-photo D500/Z f audit passes all 69 full-resolution comparisons:
neutral, Auto 70%, and a +4 EV/highlight/color stress case for each. Maximum error
is 1 DN; at most 2.1 channel values per million differ in these samples. Every
source hash is unchanged. Auto 70% rendering takes 1.33-2.03 s versus 2.94-4.29 s
for NumPy, excluding decoding and file export. These are local sample results,
not universal performance or pixel-equivalence guarantees.

Actual D500 desktop CPU export improves from 5.09 to 3.11 s (about 39%), including
preview/QC and JPEG writing. Live editing remains 68 ms median/48 frames during
50 edits. The matching GPU run measures 59 ms/49 frames and 1.95 s export, without
compiling CPU kernels. The real Z f CPU editing/JPEG/TIFF workflow, 22 D500 CPU
native-detail checks, and seven CPU batch checks pass. Legacy preview LUT creation
and preview QC still contribute overhead; those and first HE* import remain
optimization targets. No new format support or image-quality algorithm is implied.

The refreshed EXE confirms compiled Bayer and tone activation on CPU, with no
cache fallback. The D500 sea sample exports in 3.49 s including first-use Bayer
compilation, then 3.05 s after process restart (previous bundle: 5.22 s warm).
Z f CPU export takes 3.72 s and GPU export 2.08 s; both retain source Auto settings,
native inspection, and 4032x6048 orientation. All source hashes remain unchanged.
HE* first-build CPU preparation still takes 5.79 s; a subsequent GPU process
prepares in 2.50 s. Those are different backends, not an import speedup comparison.
No public ZIP was generated or released.

### Vectorized Preview Tables and RGB QC

`raw/native/lookup.py` builds the existing Nikon fixed-point preview tables with
NumPy, preserving the float32 storage boundary before double-precision matrix
evaluation and ties-to-even rounding. The table shapes, mutable `array('i')`
return types, tone curve, overflow path, and resulting preview dimensions remain
unchanged. `_linear_color_luts` retains the original scalar implementation when
NumPy is unavailable. This adds no JIT startup or new runtime dependency.

`qc/histogram.py` processes RGB byte buffers in bounded 262,144-pixel chunks.
Integer luminance and bin calculations preserve the existing shadow/any-channel
highlight thresholds, counts, and recipe reports. The streaming pixel API stays
strict about channel types and remains the no-NumPy fallback. No image pixels
or clipping policies are changed.

All 353 tests pass with GPU selection and CPU-only (two GPU-only skips). Tests
compare every supported bin count, chunk boundaries, mutable/read-only buffers,
invalid input handling, fallback, full lookup entries under control extremes,
and resized rendered-QC reports. All 69 real previews and histogram analyses
(neutral, Auto 70%, and stress settings across 23 private D500/Z f photos) are
identical to the retained references; all source hashes remain unchanged.

On the same D500 desktop benchmark, CPU export improves from 3.11 to 2.65 s and
GPU export from 1.95 to 1.64 s, including preview/QC and JPEG writing. Live edits
measure 73 ms median/47 frames on CPU and 53 ms/49 frames on GPU during 50 edits.
The CPU portrait Z f desktop workflow passes all 16 checks (Auto: 0.81 s),
native-detail inspection passes 22 checks with exact full-render equality, and
batch export/cancellation passes seven checks. These are local measurements,
not universal performance guarantees. The remaining CPU profile shows repeated
MakerNote byte conversion as avoidable overhead; first HE* import also needs work.

The refreshed Windows EXE passes D500 CPU and Z f CPU/GPU Auto/export/detail
smoke checks and independent-process cache reuse without fallback. D500 sea
export takes 3.16 s including first-use Bayer compilation, then 2.52 s warm
(previous bundle: 3.05 s warm). The Z f portrait exports in 3.04 s on CPU and
1.56-1.62 s on GPU, retaining orientation and the same Auto settings. GPU HE*
preparation still takes 6.47 s on first compilation and 2.26 s after restart;
these timings exclude executable startup. All source hashes remain unchanged.
No public ZIP was generated or released.

### Compact TIFF Metadata

TIFF `UNDEFINED` fields now remain immutable `bytes` internally instead of
expanding into integer tuples and repeatedly converting back for MakerNote
parsing. `TiffTag.value` consumers that inspect opaque fields should use bytes;
the public inspection/recipe summaries keep their original tuple/JSON shape.
Numeric accessors treat binary values as byte numbers, not ASCII numerals, and
still accept legacy tuple metadata. Byte, integer, and floating-point numeric
fields use one `struct.unpack` after the existing payload bounds check. Rational,
ASCII, scalar/array, byte-order, and unsupported-type behavior is preserved.
The parser does not retain mapped-file views or introduce a stale metadata cache.

All 363 tests pass with GPU and CPU-only (two GPU-only skips). New tests cover
both byte orders, every numeric field type, empty/single/array values, nonfinite
floats and signed zero, large opaque fields, mapped-file lifetime, malformed
ranges, JSON summaries, and legacy tuple decode/support parity. Private audits
retain identical tag values, public metadata, support reports, decoded planes,
69 preview renders and 23 full-size renders across the selected D500/Z f set.
Three additional real 1 J5 files retain identical decoded/full-render output.
All 26 decoded sources remain hash-identical. The full 6,847-file read-only
inventory also matches its old results, including the same six rejected
containers. Inventory agreement is not a sensor-decode claim for those files.

Across the 23-photo set, mean metadata read plus MakerNote summary time falls
from 24.3 to 8.9 ms. Retained opaque-tag storage averages 1.43 MB before and
0.18 MB after; this is not total process memory. The same D500 actual desktop
export improves from 2.65 to 2.38 s CPU and 1.64 to 1.36 s GPU. Live editing
remains 62 ms median on CPU and 59 ms on GPU. The 16-step Z f CPU desktop workflow,
22 native-detail checks, and seven batch checks pass. These local timings do not
promise universal latency or reduced JIT startup. First HE* compilation remains
an independent optimization target; no camera profile or image algorithm changed.

The refreshed EXE passes D500 CPU and Z f CPU/GPU Auto/export/detail checks and
restart cache reuse, with no compiler fallback and unchanged source hashes.
D500 CPU sea export takes 2.82 s including first Bayer compilation and
2.34-2.53 s across two warm runs (previous bundle: 2.52 s warm). The Z f portrait
exports in 2.97-3.00 s CPU and 1.42 s GPU. GPU HE* preparation still takes
6.34 s initially and 2.24 s after restart, excluding executable startup.
Warm Auto settings match the preceding bundle. No public ZIP was generated or
released; these measurements do not imply first-use compilation was solved.

### HE Horizontal Compilation Latency

`compiled_he_transform.py` now dequantizes into reusable int64 buffers and
performs the same integer 5/3 synthesis directly. It allocates two low/synthesis
rows, one high band, a five-entry size buffer, and the eight-row int32 output per
precinct. These buffers are reused across levels/components, not across calls or
threads. Total array storage is `52 * (width / 2) + 40` bytes. The independent
NumPy implementation, entropy decoder, color lift, profile mapping, and support
guards are unchanged. There is no fastmath, new dependency, or extra thread pool.

All 367 tests pass with GPU and CPU-only (two GPU-only skips). New tests cover
odd band sizes, signed coefficients and threshold extremes, poisoned group
padding, read-only inputs, fixed allocations, initialized scratch contents,
independent concurrent calls, and horizontal cache/compiler failure. The 23-photo
D500/Z f regression retains all metadata, decoded hashes, 69 previews, and 23
full-size render hashes. All source hashes are unchanged. This validates the
optimization against the preceding native version, not new Nikon profiles or
bit-exact Nikon nonlinear mapping.

Separate empty-cache processes in the build environment reduce the horizontal
kernel's first call from 2.17 to 0.53 s, with remaining horizontal work unchanged
at about 0.16 s. Total CPU RAW preparation falls from 5.41 to 3.82 s; the initial
tone render still takes about 0.60 s. These timings exclude module/executable
startup. A warm process prepares in 2.05 s with an 84 ms first CPU tone render.

Actual Tk cold-cache checks on the Z f portrait measure 5.24 s first RAW display
on CPU and 4.84 s on GPU, then 62/46 ms median slider latency. The camera JPEG
remains an explicitly labeled transition while RAW preparation runs. CPU export
takes 3.10 s including first-use Bayer compilation; GPU export takes 1.61 s.
The CPU 16-step edit/JPEG/TIFF workflow, GPU 22-step native-detail workflow, and
seven CPU batch checks pass. These are local sample measurements, not universal
speed guarantees. Further initialization and image-quality improvements remain.

The refreshed Windows EXE passes independent cold/warm processes with separate,
initially empty CPU/GPU cache directories. GPU preparation is 4.38 s cold and
1.98 s warm (preceding bundle's first run: 6.34 s). CPU preparation is 4.31 s
cold and 1.82 s warm. These exclude executable startup. Auto, full-size export,
native inspection, compiler activation, and disk-cache reuse pass without
fallback; source hashes remain unchanged. Z f GPU export stays at 1.39-1.41 s,
and CPU export measures 3.11 s including first Bayer compilation, 2.84 s warm.
No public ZIP was generated or released. Next emphasis is image quality and
Auto on difficult lighting; remaining startup costs are still open work.

### Highlight-Limited Shadow Refinement

After the existing joint tone guards succeed, Auto can try two bounded shadow
increments when the requested exposure was limited by more than 0.15 EV and the
result remains dim. It measures a fixed mask of original display-luma midtones
from 0.08 to 0.35, requiring at least 10% coverage. Low-key scenes and images with
at least 55% dark pixels are excluded. Shadow settings increase by at most 0.40,
capped at 0.65; the second candidate halves the increment. At both proxy sizes,
the mean gain over the already-guarded result must exceed 1/255 but stay within
0.04, 25%, and a final mean of 0.30. Accepted candidates must still pass the
existing clipping, shadow-crushing, median, and intermediate-strength checks.

This changes newly generated desktop/batch Auto suggestions, not the renderer,
manual controls, saved recipes, decoder, or format support. Added diagnostics
record shadow-midtone means and whether refinement was used. The legacy
preview-only suggestion API without a render callback cannot do this refinement.
It is not semantic subject detection, ISO-aware denoise, a local spatial mask,
or sensor highlight reconstruction. The mask evaluates the existing global
Shadows control; it does not selectively render individual objects.

The nine new tests cover useful/absent gain, small subject area, dark-scene
exclusions, excessive lift and clipping at either proxy size, intermediate
strength clipping, and real-render black/highlight preservation. All 376 tests
pass in the GPU build environment and CPU-only (two GPU-only skips). All 23 selected D500/Z f files
pass Auto and full-size JPEG export with unchanged source hashes. Six suggestions
gain additional shadows; the other 17 and all non-shadow parameters are unchanged.
All 4,600 integer-strength checks at 256/960 pixels pass. The six changed samples
also pass the same clipping/shadow budgets on full-resolution renders at 70% and
100%, compared against both the original and preceding Auto. This is sample
evidence, not a full-resolution guarantee for every file or artistic preference.

Old/new full-size comparisons show more visible dark subjects and structures in
the backlit portrait, aquarium, temple, and courtyard without neutralizing their
scene colors. Actual Tk Auto takes 0.64 s on the D500 portrait with CPU and 0.80 s
on the Z f portrait with GPU. Both 16-step editing/JPEG/TIFF/history workflows pass,
as do 22 native-detail checks and seven batch checks. Continuous preview remains
46 ms median on GPU and 68 ms on CPU in separate local benchmarks; full-size
exports there take 1.43 s (Z f GPU) and 2.16 s (D500 CPU). These are different
sample/backend measurements, not a direct speed comparison or universal promise.

The refreshed local Windows EXE passes D500 CPU and Z f GPU smoke tests with
the same new Auto settings as the source runs, active native compilation,
unchanged originals, full-size export, and native inspection. Packaged Auto
takes 0.55/0.61 s and export 2.61/1.40 s respectively. No public ZIP was generated
or released. Further noise handling, demosaic, camera/gamut calibration, and
support for unverified Nikon profiles remain unfinished.

### Native MHC Detail and Auto Sampling

Supported compressed Nikon full-size rendering now uses the 5x5 linear
gradient-corrected filters from Malvar, He and Cutler (ICASSP 2004), implemented
independently in NumPy, cached Numba CPU chunks, and OpenCL. The published method
is linked in `raw/native/malvar.py`; no external RAW runtime or new dependency
was added. Calibration applies position black levels and white balance/exposure
before interpolation. The active-crop border reflects with Bayer phase intact.
Native viewport rendering uses a two-pixel halo, preserving exact equality with
the full render. CPU allocations remain row-chunk bounded. OpenCL validates the
new kernel against the independent NumPy reference before first use; failures
fall back without silently changing the interpolation method.

Fit's fast linear proxy, generic DNG rendering, original sensor bytes, decoder
support guards, and manual control ranges are unchanged. Existing Nikon recipes
now produce different full-size pixels. MHC is not edge-adaptive interpolation,
denoise, sensor highlight recovery, or a camera/gamut calibration improvement.
Visual crops show less blocky false color in hair/fabric/building detail, while
high-ISO noise and defocus remain. Do not describe these as restored lost detail.

An initial 26-photo D500/Z f/1 J5 audit covered neutral, Auto70, and stress
adjustments on full frames. CPU/GPU differ from the independent reference by at
most one 8-bit level, with unchanged originals. It also exposed two Auto issues:
the sea sample already lost too many small highlights with the preceding bilinear
renderer (406/12,427 eligible channels); MHC lost 432/13,557. The high-ISO portrait
newly crushed 1.282% of pixels compared with 0.313% using bilinear. Passing small
proxy checks was insufficient, so these findings were not waived.

`raw/native/auto_samples.py` packs at most 128 native patches with two-pixel halos
into an independent, small Bayer atlas. It samples an 8x8 grid and each CFA
plane's brightest site in up to 4x4 sectors, deduplicating aligned locations.
Core patches are at most 32x32, retaining at most 331,776 raw bytes. Reflecting at
the original crop and discarding atlas halos makes every sampled pixel match
full-size export, including white-balance changes. The prepared display photo
retains these samples, not a second full sensor buffer. No RAW is decoded again
for candidate validation, and slider renders do not run the extra check.

Auto checks these samples after the small analysis proxy and before the larger
display proxy. Existing clipping/shadow budgets and intermediate strengths still
apply. Bright patches are intentionally overrepresented, so native samples do
not classify the scene, demand a median brightness, or decide whether shadow lift
is useful. They only reject damaging tone candidates. This is sampled validation,
not exhaustive coverage or a guarantee for arbitrary images/strengths.

All 397 tests pass on GPU and CPU-only (three GPU-only skips). Added coverage
includes published filter coefficients, all Bayer phases and tiny edges,
calibration, fixed-buffer chunk seams, CPU/GPU/reference parity, cache/compiler
fallback, native sample/export equality, bounded sample storage, hidden shadow
loss, intermediate-strength clipping, and non-destructive preparation. All 23
selected D500/Z f photos now pass full-size and 256/960-pixel checks at 25/50/70/100%
Auto strength: 276 checks with unchanged originals. Seven suggestions change;
16 retain prior settings. Both identified outliers now pass, without changing
the thresholds. Private audit data/images remain ignored local output.

Actual Tk D500 CPU and Z f GPU checks pass editing/history, JPEG/TIFF, and native
inspection at compact window sizes; batch cancellation also passes. Auto measures
0.76/0.94 s respectively. Separate live-preview runs measure 74/46 ms median
slider latency and 2.44/1.44 s full-size export. These are different sample/backend
measurements, not a direct hardware comparison or universal speed guarantee.

The refreshed local EXE passes D500 CPU and Z f GPU Auto/export/native-detail
checks with the same settings as source runs and unchanged originals. CPU Auto
takes 2.12 s on the first new-kernel run, 0.70 s after restarting with its cache;
warm export takes 2.37 s. Z f GPU Auto/export measure 0.80/1.49 s. Separate frozen
checks reproduce the corrected high-ISO portrait and sea suggestions (Auto
0.46 s CPU and 0.53 s GPU respectively). First-use RAW preparation/compilation
remains visible latency, not an instant-open guarantee. The existing optional
TBB packaging warning remains; these sequential kernels and frozen smokes pass
without that library. No public ZIP or installer was generated or released.

### Manual Rendered Color-Noise Reduction

`Adjust > Detail > Color noise` adds a manual 0-100 control, stored as
`adjustments.raw.color_noise` in `[0, 1]`. Missing values default to zero, an
exact bypass without filtering work. Undo/redo, local sessions, recipes,
JPEG/TIFF, and CLI `process`/`batch --color-noise` retain the value. Single-photo
Auto and its strength control preserve the manual amount. Batch Auto uses the
current manual amount; Saved edits uses each photo's retained amount. No noise
level is automatically estimated, and existing Auto tone suggestions are unchanged.

The native implementation uses a 5x5 bilateral-style filter on rendered RGB8
chroma, after tone mapping and before orientation, crop, or resize. It adapts
the domain/range weighting principle cited in `raw/native/chroma.py`, using
display-luma and R-G/B-G differences rather than CIE-Lab. Reconstructed color
is contracted toward the original luminance at gamut boundaries; this is not
camera gamut mapping or preservation of individual channel highlights. There
is no new dependency, external RAW engine, fastmath, or extra thread pool.

NumPy is the independent bounded-chunk fallback. Cached sequential CPU code
prepares each chunk's guide/chroma planes once; OpenCL validates against NumPy
before first use. Driver/compiler failures retain the same filter on fallback,
and disk-cache failures retry compilation in memory. Native Nikon regions use
a four-pixel combined MHC/filter halo; generic DNG regions add two pixels around
the linear tile. Fit still filters a fast linear proxy and is only approximate.
Native 100%/200% inspection is the authoritative full-export detail view.

All 414 tests pass with GPU and CPU-only (four GPU-only skips). New coverage
includes zero bypass, neutral/constant pixels, synthetic chroma noise, saturated
boundaries, luminance rounding, CPU/GPU/reference parity, chunk seams and bounded
scratch arrays, read-only inputs, cache/compiler/driver/JIT failure, every Bayer
phase and EXIF orientation, ROI equality, old recipes, persistence, CLI bounds,
and full pipeline TIFF pixels. No test requires private photos or an installed GPU.

All 26 selected D500/Z f/1 J5 captures pass full-frame CPU/GPU comparison at
strength 75 with at most one 8-bit level difference and at most 0.4961 DN change
in the filter's weighted luminance. Three native regions per photo exactly match
its full export. A separate comparison against Auto70 without the filter (neutral
for 1 J5) passes the existing clipping/shadow budgets on all 26 full frames.
Newly clipped pixels peak below 0.003%, not zero; this remains sample evidence,
not a universal guarantee. Source hashes are unchanged; reports/photos stay local.

Compact-window Tk checks pass pointer input with changed displayed pixels,
undo/redo, Auto preservation, session restoration, JPEG/TIFF, native detail,
saved-edit/Auto/current batch modes, and cancellation. Separate live benchmarks
with color noise at 75 measure 49 ms median slider response and 2.11 s full-size
export for Z f on GPU; D500 on CPU measures 113 ms and 4.36 s. Reusing CPU guide
planes improves that same CPU export from 4.83 s. These are local measurements
with different samples, not a direct hardware comparison. Filtering adds work,
especially on CPU. First-use compilation is separate from cached edit latency.

Visual native crops show fewer colored speckles, but luminance grain, larger
color blotches, and defocus remain. Fine low-contrast color texture can soften.
This is manual rendered-chroma reduction, not sensor-domain, luminance,
ISO-adaptive, or AI denoise. Broader verified camera support, noise estimation,
camera/gamut calibration, and image-quality improvements remain open work.

The refreshed local Windows EXE passes D500 CPU and Z f GPU Auto/export/detail
smokes with color noise at 75. Its native-detail pixels exactly match source
runs, recipes retain the amount, and original hashes are unchanged. D500 CPU
export measures 5.14 s on the first new-kernel run and 4.48 s after restarting
with the cache; Auto falls from 2.26 to 0.69 s. Z f GPU export measures 1.64-1.67 s;
RAW preparation falls from 4.57 to 2.20 s after restart, excluding EXE startup.
There is no compiler/cache fallback. The known optional TBB packaging warning
remains; the sequential kernels do not require it. No public ZIP or installer
was generated or released.

### Sampled Auto Color-Noise Advice

The Detail slider now has a separate Lucide wand command, `Auto color noise`.
It analyzes the current tonal settings with color filtering off, then changes
only the color-noise amount. Toolbar Auto, its strength slider, and batch Auto
still preserve that amount; no default denoise or new camera support is implied.
Accepted advice uses normal history/session/recipe/export retention. Analysis
runs off the Tk thread, disables duplicate work, and rejects stale run IDs.
Low-noise evidence sets zero. Insufficient samples, unavailable native sampling,
or candidates without verified benefit leave all existing settings unchanged.

`decision/color_noise.py` evaluates at most 256 native 16x16 blocks from the
retained grid tiles. It subtracts per-channel planes from R-G/B-G and rendered
luma, measuring robust residual dispersion. Clipped/dark pixels, coarse structure,
and strong four-pixel periodic correlation exclude many unreliable blocks. At
least 12 blocks from six sampled regions are required; each region gets one vote.
This is a bounded rendered-chroma heuristic, not Gaussian sensor variance,
ISO calibration, semantic recognition, or a probability/confidence estimate.
Random fine color texture can still be mistaken for noise.

Candidate strength is capped at 70, with one half-strength backoff. Acceptance
requires at least 5% less dispersion on the same selected blocks, the existing
native highlight/shadow budgets, bounded coarse-color changes in unselected
blocks, bounded mean-color drift everywhere, and no more than 0.5 DN change in
the filter's weighted luminance. This samples a global control; it is not a
spatially selective denoiser or exhaustive full-frame protection. Generic DNG
currently lacks retained native tiles and receives no automatic advice.

Native sampling now retains four RAW pixels around each core, bounded at 409,600
raw bytes for 128 tiles. Tonal-only sample pixels remain identical. Filtered
sampling first demosaics the atlas, supplies the correct two-pixel RGB halo,
and replicates at actual image edges before filtering, so neighboring atlas
tiles and reflected out-of-image RGB cannot leak into the result. Grid samples
vote on noise; additional bright samples only validate tones. No new RAW decode
is needed when the desktop's prepared photo is still current.

All 427 tests pass with GPU and CPU-only (four GPU-only skips). Added coverage
includes clean gradients, known noisy ground truth with improved pixel error,
gray grain, periodic color textures, dark/clipped regions, insufficient coverage,
biased bright samples, candidate backoff, color/tone damage, readonly inputs,
invalid bounds and callback shapes, preservation of current tones, and missing
native data. Extended native-sample tests match filtered full export across
Bayer layouts, image/crop edges, and tiny dimensions.

All 26 D500/Z f/1 J5 private captures produce matching CPU/GPU advice and exact
native-sample/full-export pixels with unchanged original hashes. All 23 existing
tonal Auto suggestions are unchanged. At Auto70 (neutral for 1 J5), three samples
receive strengths 30-45, thirteen receive zero, and ten retain their previous
setting because evidence/benefit is insufficient. Across neutral and 25/50/70/100%
tone settings, 130 cases yield 15 nonzero recommendations; all 15 pass full-frame
clipping/shadow checks. These are local sample results, not universal guarantees.
Visual native crops show mild color-speckle reduction, not removal of luma grain,
large blotches, defocus, or all fine-texture risk.

Actual compact-window workflows cover positive advice, zero advice, abstention,
unsupported DNG advice, asynchronous state, displayed pixels, stale callbacks,
undo/redo, tonal Auto preservation, local retention, and JPEG/TIFF export. The
D500 high-ISO CPU case takes 0.27 s for advice; Z f GPU takes 0.20 s and preserves
the existing amount without sufficient benefit. These include desktop scheduling,
not RAW import or universal first-use latency. The manual filter/export algorithms
and slider rendering are unchanged in this increment.

The refreshed local EXE reproduces the high-ISO CPU recommendation (35), a
blue-hour GPU recommendation (50), and a Z f GPU abstention preserving the
existing amount (75). All three recipes and native-detail pixels match source
execution, with unchanged originals. CPU advice takes 0.65 s on first use and
0.15 s after restarting with cached compilation; the two GPU cases take
0.06-0.08 s, excluding RAW preparation and EXE startup. Full-size exports measure
3.91 s for the cached high-ISO CPU case and 1.28/1.66 s for those GPU cases.
No compiler/cache fallback occurs. The bundled wand asset matches the source;
the existing optional TBB warning remains. No public ZIP/installer was produced.

### Prebuilt HE CPU Kernels

`raw/native/_he_cpu.cpp` compiles OpenRAW's existing packet, horizontal synthesis,
and linear-color arithmetic ahead of time. `he_cpu.py` supplies owned output
arrays through the Python buffer protocol, without a NumPy C-ABI dependency or
third-party RAW decoder. The integer operations, profile guards, and approximate
Z f linearization curve are unchanged. Wavelet work rows are reused; color
conversion keeps the existing bounded tiles and source-derived bottom halo.

The binary interface validates native-endian types, alignment, dimensions,
contiguity, writable outputs, non-aliasing, band sizes, and coefficient limits.
Packet parsing still rejects truncation, excessive unary codes, and nonzero
padding. CPU loops release the GIL, own their scratch memory, and restore the
GIL before reporting errors or releasing buffer exports. Corrupt-data errors
propagate, never triggering a quieter fallback. Absent/unloadable extensions
retain the existing Numba and NumPy/Python paths. Source builds may omit the
extension; Windows packaging and CI require it and also test the fallback.

All 445 tests pass on GPU and CPU-only (four GPU-only skips). HE tests also pass
with `OPENRAW_HE_AOT=off`, and from a pure-Python wheel built with
`OPENRAW_BUILD_HE_CPU=off` (eight extension-specific skips). Added cases cover
exact reference pixels, padded coefficients, odd bands, int32 extremes, readonly
and unsafe buffers, output aliasing, corrupt metadata, concurrent calls, and
buffer-export cleanup after errors. Nine private Z f sensor hashes match the
previous implementation exactly. Seven public Nikon samples pass the isolated
reference audit, retaining the existing HE nonlinear/linear accuracy bounds.

Three interleaved fresh-process trials on one private 24 MP Z f HE capture,
with new Numba caches and integrity-check-warmed filesystem data, measure median
decode at 1.60 s versus 3.71 s with JIT. In-process warm decode is 1.31 s versus
1.23 s: this primarily removes startup compilation, not every processing cost.
The actual Tk GPU benchmark measures first RAW preview at 2.78 s, median
slider-to-display at 47 ms, 49 displayed frames for 50 drag changes, and full-size
JPEG export at 1.45 s. These are local sample timings, not universal guarantees.

Compact HE/GPU and D500/CPU workflows pass Auto, color-noise advice, history,
comparison, panning, session restoration, JPEG, and TIFF checks. The refreshed
Windows EXE includes the verified binary: HE/GPU preparation takes 2.31 s versus
4.18 s with the extension disabled, both using new Numba caches; export is
1.53 s either way. HE*/CPU preparation/export measure 2.56/2.83 s. These exclude
EXE startup. All three frozen recipes and native-detail images match source
execution exactly. Original hashes are unchanged, and no photos, private reports,
rawpy, or decoder oracle are bundled/committed. The optional TBB warning remains;
sequential kernels do not use it. No public ZIP or installer was released.

### Auto Shadow Midtones And Saturation Headroom

Auto now follows the same original pixels with rendered luminance in [0.08, 0.35)
when they cover at least 10% of an unbiased preview. If the scene is not low-key
and Auto is not intentionally reducing exposure, their mean must not fall by
more than the existing tone tolerance (normally 0.01). This catches contrast
darkening a dim subject while a bright background raises the overall median.
Brightness-biased native samples do not set this target; their clipping/detail
checks are unchanged. The 256-pixel analysis and display proxy both validate it.
Positive contrast on these shadows also checks requested intermediate Auto
strengths, even when highlight compression is off.

When highlights reject a correction, tonal recovery tests one zero-added-saturation
candidate per exposure level, after trying the existing tonal candidates. If
accepted, it rechecks restoring contrast, reducing unnecessary highlight
compression, and retaining half of the requested saturation boost. The usual
clipping, shadow, native-sample, and strength guards still apply. It never adds
negative saturation, changes white balance through this search, or bypasses a
real exposure limit. Safe initial suggestions take the original fast path.

The 33-sample D500/Z f/1 J5/public Z5 audit produces identical CPU/GPU settings:
six suggestions change and 27 retain every previous adjustment. All six changed
cases pass full-size clipping/shadow budgets at 25/50/70/100% strength (24 exports).
Maximum newly clipped fraction is 0.0083%, not zero. On the mixed-light D500
portrait, display-proxy shadow darkening falls from about 5 DN to 0.83 DN;
full-size darkening is 0.87 DN at maximum strength. The sea and blue-hour examples
gain 0.3 EV at full strength by omitting extra saturation; scene white balance
is retained. All original hashes remain unchanged and comparisons stay local.

This is deterministic global adjustment, not face detection, local masking,
semantic lighting inference, or exhaustive protection of every dark region.
Small shadow regions, low-key scenes, deliberate exposure reductions, and
brightness-biased sampling have explicit limits. Live/manual rendering and
existing recipes are unchanged. On this machine, Auto over the 33 samples has
median/max CPU times of 0.48/1.48 s and GPU times of 0.30/0.87 s, excluding import.

All 463 tests pass with automatic GPU selection and CPU-only (four GPU-only
skips). Compact D500 CPU/GPU desktop runs pass 24/26 workflow checks, including
live edits, reversible Auto/noise advice, strength, comparison, native inspection,
session restoration, and JPEG/TIFF export. The nine batch-mode/cancellation
checks pass too. Frozen Auto/noise advice, persisted settings, and native-detail
pixels match source execution exactly on the checked D500/Z f examples.

### Verified Frozen Cache Selection

A normal-launch cache regression was reproduced on this Windows machine:
file creation under LocalAppData was virtualized onto another volume, while
atomic replacement still resolved differently. Resolving the directory alone
did not catch this. Numba stayed compiled in memory, but discarded its disk
cache each launch; the same D500 CPU Auto repeatedly took 2.87-2.90 s.

The launcher now reserves two unique private probe files and verifies atomic
replacement/readback before selecting the cache. If LocalAppData fails it tries
`Path.home() / ".cache" / "OpenRAW Studio" / "numba"`. Only probe-owned files are
cleaned; existing kernels and explicit `NUMBA_CACHE_DIR` overrides are preserved.
When both private locations fail, the existing in-memory fallback remains.
Nine focused tests cover source/explicit environments, missing/unwritable homes,
cross-volume replacement, silent no-op replacement, and cleanup isolation.

Without a cache override, independent EXE launches select the tested home cache.
The D500 CPU example measures preparation at 1.80 s initially, then 0.64/0.65 s;
Auto takes 1.65 s initially, then 0.42/0.41 s, with export at 2.32-2.41 s. On the
high-ISO D500, color-noise advice takes 0.73 s initially and 0.15 s after restart;
export with the suggested 35% amount is about 3.68 s. Separate D500/Z f GPU
exports take 1.03/1.25 s. These are local timings excluding EXE startup, not
universal guarantees or evidence that the cache fix accelerates every export.
First-use compilation after updates remains possible. No original photos are
modified, and no private captures/reports, reference decoder, public ZIP, or
installer are bundled or released.
The final rebuilt EXE passes seven independent CPU/GPU smoke runs, with matching
Auto/noise advice, persisted recipes, and exact native-detail pixels. The report
captures tone/demosaic/noise compiler state after the entire workflow; none of
these runs uses the compiler/cache fallback. The optional TBB packaging warning
remains unrelated to the sequential kernels used here.

### Rendered Luminance-Noise Control

The optional `Detail > Luminance noise` slider reduces rendered brightness grain
independently of the existing color filter. It is a local 5x5 bilateral filter
with a 12-DN luminance range, not sensor-domain, ISO-calibrated, or learned denoise.
The weighting is independently implemented from the domain/range idea in
[Tomasi and Manduchi (ICCV 1998)](https://users.cs.duke.edu/~tomasi/papers/tomasi/tomasiIccv98.pdf).
All channels receive the same bounded integer shift, preserving their color
differences without per-channel truncation. Low-contrast texture can soften,
especially at high amounts, and larger noise structures remain.

Q16 lookup weights and strength, 64-bit accumulation, and explicit integer
ties-to-even rounding make the luminance stage bit-exact on checked CPU/GPU
inputs. Its maximum accumulator is below signed 64-bit limits for RGB8 and
the 25-neighbor footprint. Sequential CPU scratch is chunk-bounded; unavailable
GPU/compiler/disk caches retain the checked CPU/NumPy fallbacks. Zero bypasses
the filter exactly, including in old recipes. The frozen hook keeps the new
compiled module as source so its JIT cache can persist across restarts.

The processing order is luminance then color. Native regions include the sum of
both spatial footprints plus demosaic support. Retained Auto samples have six
RAW pixels of halo; between filters they re-replicate the actual image boundary,
not an already-filtered artificial extension. This fixes a reproduced one-DN
sample/full-frame disagreement at combined-filter edges. Native detail uses the
same full-size path. Fit remains approximate and scales the luminance amount by
its linear size ratio, avoiding full-strength smoothing across oversized proxy
footprints. It does not claim native noise accuracy at Fit resolution.

The recipe field is `adjustments.raw.luminance_noise`, bounded to [0, 1] and
defaulting to zero. CLI process/batch, desktop history, persisted sessions,
saved recipes, JPEG/TIFF, and all batch modes retain it. Tonal Auto preserves
manual noise settings. Color-noise advice measures the current luminance-filtered
samples, but still changes only color-noise strength. There is no automatic
luminance-noise advice in this increment.

The 33-file D500/Z f/1 J5/public Z5 audit retains every previous CPU/GPU Auto
suggestion and source hash. At Auto70 with both noise amounts at 65%, full-frame
filter outputs agree within 1 DN between CPU/GPU; retained samples and inspected
regions match the full render exactly. All 33 clipping/shadow budgets pass at
these settings, with maximum newly clipped fraction 0.00021%, not zero. Five
private native-crop comparisons were inspected for remaining grain and softened
texture; these are not clean-ground-truth or exhaustive image-quality proofs.

Compact D500/GPU and Z f/CPU desktop workflows pass 41/39 checks, including both
sliders, undo/redo, retained Auto settings, accepted/abstained color-noise advice,
comparison, JPEG/TIFF, and edit restoration. A reproduced viewport size change
when the busy progress bar appeared is fixed by reserving its layout height;
the workflow now checks unchanged dimensions during/after analysis and waits
for idle layout before comparing displayed pixels. Eleven batch checks cover
saved/current/Auto modes, noise retention, cancellation, and small-window access.

On the same private D500 capture with color noise 65 and luminance noise 60,
actual Tk slider-to-display median is 46 ms on RTX 5070 and 109 ms on CPU, with
47/16 frames displayed during 50 drag edits. Full-size JPEG export takes
1.31/4.64 s, respectively. These local measurements include both filters but
exclude RAW preparation from export; they are not universal speed guarantees.
CPU-only filtering remains visibly slower. The unedited first preview still
needs preparation (1.52/1.14 s in those already-compiled source runs).

That increment's suite has 481 tests: all pass with GPU enabled; CPU-only runs skip the
five GPU-only checks. Native 100%/200% desktop inspection passes 24 checks in
each CPU/GPU run, including both retained noise amounts and exact full-render
region comparisons. Six independent launches of the rebuilt EXE cover D500,
Z f, synthetic DNG, CPU/GPU, warm restart, and a legacy zero-noise recipe.
Auto/noise advice, persisted edits, and native-detail pixels match source
execution exactly; the legacy detail also matches the preceding EXE output.
Source hashes are unchanged, and every exported JPEG opens at its reported size.

With no explicit cache override, these launches select the verified private home
cache and report no compiler/disk-cache fallback. On the high-ISO D500, CPU
color-noise advice with luminance 60 takes 1.18 s on first use and 0.19 s after
restart; the corresponding JPEG export takes 4.80/4.61 s. GPU exports for the
checked D500/Z f take 1.20/1.68 s. These runs use suggested color noise 35 for
D500 and retain manual color noise 65 when Z f advice abstains, so they are not
direct timing comparisons with the fixed-65 benchmark above. Timings exclude
EXE startup. The bundle contains the new cached kernel source but no private
RAWs, development reference decoder, public ZIP, or installer.

### Measured Auto White-Balance Refinement

After tonal recovery, Auto can refine Temperature/Tint using the current
renderer's measured response. Original usable near-neutral pixels vote in a
4x4 grid; at least six tiles, three agreeing quadrants, and 80% directional
agreement are required. The display proxy must independently support the same
cast. Low-key, very dark, dim red/blue-dominated, uncertain, or already-neutral
scenes retain the preceding correction. These are conservative image heuristics,
not semantic scene or illuminant classification.

Two bounded probe renders estimate a local two-parameter response, including the
camera matrix. The proposal targets half the residual cast, with absolute bounds
of 0.35 Temperature and 0.25 Tint and one smaller backoff. It must reduce the
selected neutral log-ratio error by at least 10% and 0.004 at both preview sizes;
intermediate strengths must not worsen it beyond the 0.001 quantization allowance.
Exposure, contrast, highlights, shadows, and saturation are unchanged by this
stage. Existing clipping and shadow guards are reapplied to preview/native
samples at full and intermediate strengths before acceptance.

Neutral residuals share the existing render-measurement cache, so no adjustment
is rendered twice for the added analysis. Each residual uses at most 4096 fixed
original-pixel locations. An uncertain small proxy skips display-color analysis.
The ten new tests cover two actual color matrices, spatial/ambient abstention,
bounded sampling, singular renderer response, detail disagreement, backoff,
intermediate color regression, native clipping, and low-key preservation.

The final 33-photo CPU/GPU audit produces identical settings: four D500/Z f
examples receive bounded color refinements, while 29 retain the previous Auto
settings. All original hashes are unchanged. The four changed examples pass
full-size clipping/shadow budgets at 25/50/70/100% Auto and color checks at both
proxy sizes. Their selected-neutral endpoint error falls about 38-48%; this is
not a ground-truth color-accuracy score. Comparisons were visually inspected,
and early changes to dim ambient-color scenes were rejected before shipping.

In that already-prepared corpus, median Auto time is 0.44 s on RTX 5070 and
0.74 s on CPU. Refinement adds analysis work; it does not accelerate decoding,
manual live rendering, or export. Two actual compact desktop workflows each
pass 41 checks, including history, strength, both noise controls, comparison,
JPEG/TIFF and session restoration. Auto takes 0.98 s on the checked D500 CPU
workflow and 0.86 s on the Z f GPU workflow. These are local measurements.

Pale colored surfaces remain ambiguous, and mixed lighting is not solved.
Manual Temperature/Tint and original comparison remain available. No renderer
or recipe field changes: existing saved settings render unchanged until the
user runs Auto again. The full 491-test suite passes with GPU enabled and with
five expected GPU-only skips on CPU.

The refreshed EXE passes six independent CPU/GPU smoke launches on D500, Z f,
synthetic DNG, and a dim ambient-color scene. Auto/noise advice and persisted
settings match source execution, native-detail pixels match exactly, all JPEGs
open, and originals are unchanged. Two preceding D500/Z f saved recipes also
reproduce their previous native-detail output exactly. On the checked D500 CPU,
Auto takes 2.27 s on the first launch after rebuilding and 0.84 s after restart;
first-use compilation remains possible. Checked D500/Z f GPU exports take
1.21/1.50 s with luminance noise 60 and retained/advised color-noise settings.
These timings exclude EXE startup and are not speed guarantees. No private
photos, development reference decoder, public ZIP, or installer are bundled.

For hidden PowerShell smoke launches, quote each source/output path inside
`Start-Process -ArgumentList`. Its joined command line does not preserve an
unquoted path containing spaces; this caused a reproduced test-harness startup
failure before the corrected command passed.

### Genuine 16-Bit TIFF Export

`RawRenderRequest.bit_depth`, `ExportRequest.bit_depth`, and
`PipelineRequest.export_bit_depth` default to 8. TIFF accepts 8 or 16, JPEG only
8; invalid combinations are rejected before processing. CLI process/batch use
`--format tiff --bit-depth 16`. The desktop Export tab retains output depth in
the recipe without changing edit history or invalidating the live preview.
Batch workers capture it on the UI thread alongside format and quality.

Native Nikon full-resolution CPU/OpenCL rendering quantizes float color directly
to uint16. DNG's scalar renderer likewise encodes directly at 16 bits. Both
noise filters preserve uint16 values; only their range-weight guides retain
display-scale bins. Orientation, region crops, optional resizing, and TIFF
encoding never pass through Pillow RGB8. This remains rendered sRGB with the
existing transfer curve, not linear/wide-gamut/HDR export or extra sensor bits.
Previews, native inspection, histogram/QC, and Auto remain 8-bit. With filtering,
16-bit exports need not downconvert to identical 8-bit preview pixels.

OpenCL compiles its 16-bit program lazily and validates MHC/noise kernels before
using them. CPU dtype specialization keeps range divisions compile-time constant
and retains the previous RGB8 filter speed/output. GPU/Numba failures retain CPU
reference paths; TIFF8/JPEG defaults and legacy render settings are unchanged.

Tifffile 2026.3.3 is pinned for Python 3.11 compatibility. It writes lossless
Deflate strips through stdlib zlib with at most four compression workers and
an atomic destination. Only safe camera/capture tags are forwarded; no GPS or
MakerNotes. The installed BSD-3 license is bundled. Re-export cannot silently
promote RGB8 into fake RGB16; same-path TIFF checks actual bit depth and size.

Tests cover genuine extra levels, scalar/compiled/reference/GPU paths, all Bayer
layouts and EXIF orientations, chunk edges, both noise filters, geometry,
roundtrip metadata, atomic failure, recipe/CLI/batch forwarding, and real Tk
controls. Real D500/Z f comparisons retain originals and reproduce preceding
JPEG files byte-for-byte. With both filters, their RGB16 CPU/GPU 99.9th-percentile
difference is 1 DN16; worst pixels differ by 133/49 DN16 from range-bin boundary
sensitivity. Do not claim bit-identical CPU/GPU filtered output. Display-scale
16-versus-8 differences are also measured, not treated as encoding corruption.

Two compact desktop checks (900x640 CPU / 1080x720 GPU) exercise JPEG/TIFF16,
Auto, noise advice, history, comparison, and recipe restoration, including a
visible bit-depth control. The earlier blank Export screenshot was a capture
race after switching tabs; waiting for Tk paint and checking mapped controls
produces the verified screenshots. Private audit assets stay under ignored output.

The 539-test suite passes in the source and packaging environments, and with
GPU disabled (six expected GPU-only skips). Eight rebuilt-EXE checks cover
D500, Z f, Z5 D40, and synthetic DNG; source/native-detail pixels match, both
16-bit CPU noise kernels compile without fallback, metadata/precision roundtrip,
and all original hashes remain unchanged. Two frozen GPU TIFF16 files match
source pixels exactly, and the legacy D500 JPEG remains byte-identical when
the same manual noise settings are supplied.

On RTX 5070, checked full-size TIFF16 exports take 2.56 s (D500), 3.10 s (Z5),
and 3.57 s (Z f); the matching D500 JPEG takes 1.36 s. These include pipeline
processing after RAW preparation, not startup. The D500 CPU TIFF16 check takes
10.30 s initially and 7.63 s after restart, so CPU/high-precision export remains
a performance target. Files are roughly 101-140 MB for these examples. A
single Z f compression-only comparison goes from 3.74 s serial to 1.27-1.32 s
with four workers at the same compressed size.

Actual D500 slider-to-display checks with both noise filters measure 46 ms
median on GPU and 108 ms on warmed CPU, with 48/17 visible frames during a
50-event drag. Release-to-final is 64/164 ms. The warmed CPU JPEG benchmark is
4.12 s; the first run after a kernel change is 7.02 s. First-use compilation is
not removed and these local results are not universal speed guarantees. The
Windows EXE is refreshed; no public ZIP/installer or private photos are shipped.

### Bounded Parallel CPU Strips

Large compiled Bayer/tone and color/luminance-noise renders use independent row
strips on at most four GIL-releasing workers. The first strip warms its kernels
synchronously. Frames below two million pixels, compiler/reference fallbacks,
and hosts with fewer than three logical CPUs stay serial. A nonblocking process
gate permits only one parallel frame at a time; other callers continue serially.
Lane count reserves one logical CPU and caps estimated compiled strip scratch
at 128 MiB. That estimate is not a total process-memory limit: decoded input,
full output, compiler state, and encoder allocations are separate.

Workers share read-only inputs and write disjoint output rows. A late compilation
or thread-start failure finishes only missing strips serially, after joining
existing workers. Unexpected pixel-processing errors still propagate. No fastmath,
resampling, extra quantization, or different denoise math is introduced. GPU
rendering returns before the CPU scheduler; preview and Auto kernels are unchanged.
`OPENRAW_CPU_WORKERS=1` selects the serial render/noise baseline for diagnostics;
values 2-4 request fewer workers within the same limits. This variable does not
control the separate bounded TIFF compression pool.

Thirteen added tests cover warmup, worker/scratch limits, exactly-once rows,
concurrent callers, thread-start and compiler failures, error propagation,
immutable/strided inputs, all Bayer layouts, both demosaics, and RGB8/RGB16 parity.
A private paired audit covers seven real D500/Z f/Z5/1 J5 captures plus a tiny
synthetic DNG: all sixteen JPEG/TIFF16 comparisons are byte-identical between
serial and parallel output, with unchanged original hashes and no kernel fallback.
Public Z5 lossless and D40 lossy profiles are both included. Assets remain ignored.

On this i5-12500H, a repeated already-decoded D500 test with both noise filters
reduces warmed JPEG rendering from 3.10-3.23 s to 1.78-1.83 s, and TIFF16 from
4.24-4.27 s to 2.97-3.05 s. A paired actual Tk workflow measures JPEG export at
4.26 s serial and 2.17 s parallel, including the pipeline. CPU slider median
stays at 108 ms with 18 visible frames during 50 drag events, and 138 ms from
release to final frame. GPU remains 46 ms median / 46 frames / 48 ms release,
with 1.39 s export. These are local sample measurements, not speed guarantees;
first-use compilation and RAW import remain separate costs.

The compact Z f CPU workflow passes 44 checks including Auto, both noise controls,
history, JPEG/TIFF16, comparison, and session restoration. Its screenshots verify
mapped controls and the visible 16-bit selector at 900x640.

All 552 tests pass in the source and packaging environments, with six expected
GPU-only skips when GPU is disabled. The refreshed Windows EXE passes eight
independent checks on D500, Z f, Z5 D40, and DNG. Auto/noise advice and saved
parameters are unchanged; native detail and full TIFF16 pixels match their
previous same-backend EXE or serial-source reference exactly. All original
hashes are unchanged and no compiler/cache fallback is reported.

For the same D500 TIFF16 recipe, the rebuilt EXE measures 7.84 s with serial CPU
strips and 4.09 s with four workers (previous EXE: 7.63 s). First use of the
new build still takes 6.48 s for export, plus preparation/Auto compilation;
this change does not remove cold-start costs. Checked GPU TIFF16 export stays
at 2.55 s for D500 and 3.54 s for Z f. These exclude EXE startup. The local EXE
is refreshed without creating a public ZIP, installer, or private-photo bundle.

### Auto Exposure Recovery

The initial upper-tail exposure cap is estimated before highlight compression.
Joint tonal recovery can now try -0.50 highlights before giving up useful
exposure. Auto then revisits the initial exposure cap when usable midtones
remain dim. This additional step tries at most 0.4/0.2 EV extra exposure, never
exceeding the original scene request, and at most -0.50 highlights. A candidate
must improve the same original shadow midtones and median at both proxy sizes,
within bounded brightness gains, and retain existing clipping/shadow budgets
at all validation domains. The additional step excludes low-key, predominantly
dark, already-bright, and unconstrained scenes. No RAW renderer, recipe fields,
live slider math, or manual noise amounts change; previous saved edits render
unchanged until Auto is run again.

Stronger exposure and compression can produce a clipping peak between zero and
25% Auto strength. A private sweep reproduced this on Z f and Z5 even though
25/50/70/100% passed, including a preceding Z f suggestion. Photo Auto now also
checks 10/15/20/35% throughout tonal recovery and white-balance refinement, not
just in the additional exposure step. Rejected candidates back off or try
additional compression; the budget thresholds are unchanged.

Comparisons include backlit subjects, sky/sea, sunset, aquarium, and night scenes.
Stronger compression can reduce highlight contrast; this remains a sampled
global heuristic, not semantic lighting recognition, local HDR, or sensor-level
highlight reconstruction. Private comparison images remain under ignored output.

Additional validation adds work: this increment does not accelerate Auto,
import, preview, or export. Cold-start costs and broader image-quality limits
remain separate work.

Across the same 33 D500/Z f/1 J5/Z5 captures, 15 suggestions change and 18 retain
their preceding settings; four changes use the additional exposure step.
CPU/GPU settings agree exactly and original hashes remain unchanged. An integer
1-100% sweep at three validation scales passes 9,900 clipping/shadow checks.
Full-size checks pass at four strengths on all 15 changed photos, plus four
additional low/intermediate strengths on the two shoulder-peak examples: 68
full-resolution checks in total. Negative-exposure scenes permit intended
midtone darkening while retaining clipping and newly-crushed-shadow guards.
These are sampled budget checks, not proof of zero clipping on arbitrary photos.

With prepared photos and warm kernels, the 33-photo Auto audit measures a
median of 1.08 s on RTX 5070 and 1.82 s on i5-12500H CPU; maxima are 2.52/5.12 s.
These exclude RAW import, startup, and denoise advice. Stronger guards add
analysis latency, which remains an optimization target separate from live edits.

All 564 tests pass in source and packaging environments, including twelve new
exposure/strength regressions; GPU-disabled execution passes with six expected
GPU-only skips. Actual compact CPU D500 and GPU Z f workflows each pass 46
checks covering pointer edits,
noise advice, history, comparison, JPEG/TIFF16, and edit/bit-depth restoration.
Screenshots verify the visible export selector at 900x640 and 1080x720.

With highlights at -0.50 and both noise filters active, the same D500 live test
measures 46/109 ms median GPU/CPU slider response, 46/16 visible frames during
50 drag events, and 61/178 ms release-to-final. JPEG export takes 1.30/2.38 s.
These are local warm-run measurements, not startup or universal latency claims.

Eight rebuilt-EXE runs cover D500, Z f, Z5 D40, and synthetic DNG. Source Auto,
noise advice, and native-detail pixels agree exactly; all five TIFF16 outputs
match source pixels, and two preceding saved recipes retain identical native
detail. All original hashes remain unchanged, with no compiler/cache fallback.
The new D500 CPU Auto takes 6.07 s on first build use and 2.40 s after restart;
its JPEG export takes 2.81 s. Checked GPU TIFF16 exports take 3.01-3.81 s on
these examples. A separate Z5 CPU TIFF16 restart check takes 5.72 s versus
9.30 s initially, producing byte-identical output. First-use and CPU high-bit
depth performance remain open. The local EXE is refreshed without a public ZIP,
installer, or private-photo bundle.

### Faster Auto Measurements

Rendered RGB8 normalization now allocates only the float32 result, without
redundant finite/range scans of inherently bounded bytes. Other numeric inputs
retain finite validation and float32 clamping. Clipping measurements reuse
the existing channel mask and combine its three RGB lanes directly instead of
repeating small-axis maximum/any reductions. The same guard is used by sampled
color-noise advice. No sampling domains, thresholds, candidate order, strength
checks, white-balance logic, or renderer math change.

Nine focused tests cover all byte values, boundary floats, all channel-mask
combinations, strided/read-only/Pillow/legacy inputs, fallback numeric types,
invalid inputs, reference metrics, neutral measurements, and cache reuse.
Across 33 D500/Z f/1 J5/Z5 captures, both CPU and GPU retain the preceding Auto
parameters, rationale, every metric, and validation-render count exactly.
Original hashes are unchanged. Paired prepared-photo measurements reduce Auto
median from 0.96 to 0.46 s on RTX 5070, and 1.87 to 1.36 s on i5-12500H CPU.
The slowest checked example improves from 2.16 to 1.05 s GPU and 4.95 to 3.78 s
CPU. These local timings exclude import, startup, and noise advice; they do not
remove first-use compilation or accelerate full-resolution rendering/export.

All 573 tests pass in source and packaging environments, including GPU-disabled
execution with six expected GPU-only skips. Sixty-six paired color-noise advice
checks at original and Auto tones across all 33 photos retain strengths,
statuses, and metrics exactly.
Two actual 46-check desktop workflows retain pointer preview, history, noise
advice, original comparison, JPEG/TIFF16, and saved edit/bit-depth restoration.
For the same prepared photos, measured desktop Auto changes from 2.48 to 1.79 s
on D500 CPU and 1.80 to 0.99 s on Z f GPU. Compact-window screenshots retain
the preview and visible 16-bit export controls without overlap.

Eight rebuilt-EXE checks reproduce source Auto and all metrics, noise advice,
native-detail pixels, and full TIFF16 pixels. All eight JPEG/TIFF16 exports are
byte-identical to the preceding EXE, with unchanged originals and no compiler
or cache fallback. Two older saved recipes also retain exact native detail.
The checked frozen D500 CPU Auto takes 2.72 s initially and 1.54 s after restart;
Z f GPU Auto takes 0.75 s. These timings are separate from preparation/export
and remain machine/run-dependent; first-use compilation still exists. The
Windows EXE is refreshed locally without a public ZIP, installer, or photos.

### Content-Checked Frozen Kernel Caches

The frozen app now selects a content-based cache locator for its seven reviewed
native JIT modules. The previous standard locator tied source validity to the
EXE, so rebuilding unrelated UI code discarded reusable compiled kernels.
The new identity hashes every bundled native Python source file, Python/NumPy/
Numba/llvmlite versions, platform, and resolved Numba configuration. Cache
location and cache logging are excluded; installation path, EXE content, and
source timestamps do not determine validity. Numba's existing CPU-feature,
signature, bytecode, and version checks remain in place.

The PyInstaller hook collects the whole native package as source, including
helpers. Only known functions located inside that bundle may use the locator.
Missing/unreadable sources or an unwritable cache fall through to the standard
locators and existing checked in-memory compilation. Explicit cache directory
and locator overrides are preserved. Source-mode execution is unchanged. A
dependency test requires review before kernels import new external project
helpers; expanding those dependencies requires expanding the cache identity.

This uses Numba's documented [custom locator configuration](https://numba.readthedocs.io/en/stable/reference/envvars.html#numba-cache-locator-classes).
Its [cache limitations](https://numba.readthedocs.io/en/stable/developer/caching.html)
explain why helper content and compiler configuration must be included, not
only the decorated function's timestamp. First-ever compilation, runtime
startup, RAW decoding, and full-resolution rendering are still real costs.
Cache reuse is conditional on an unchanged processing package and compatible
runtime, not a guarantee that all future app updates avoid compilation.

All 585 tests pass in source and packaging environments (Numba 0.67/0.68),
including GPU-disabled execution with six expected skips. Twelve added tests
cover identity, path ownership, helper changes, writable-cache fallback, and
explicit runtime configuration. A real subprocess regression compiles 8/16-bit
tones, then proves reuse after EXE replacement, timestamp changes, and bundle
relocation. Same-size/same-timestamp source edits and changed compilation flags
invalidate the cache; changed source also changes the rendered pixels.

Two actual Windows builds have different EXE hashes but identical copies of all
42 native Python source files. Nine checks per build cover D500, Z f, Z5 D40,
synthetic DNG, CPU/GPU, and the HE JIT fallback with the prebuilt extension
disabled. After repacking, every previously used JIT signature hits its cache
with zero misses, including tone8/tone16, Bayer, both noise filters, and the
three HE kernels. Default Z f GPU processing uses prebuilt HE and OpenCL rather
than those JIT kernels. All nine paired exports and native-detail images remain
byte-identical, with exact Auto/metrics/noise advice and unchanged originals.
The eight main checks also match the preceding EXE; source verification covers
all five main TIFF16 exports and two older saved recipes.

On the local i5-12500H, the first D500 CPU run prepares in 1.91 s and runs Auto
in 2.83 s; after repacking these take 0.94/2.09 s without recompilation. The HE
JIT example prepares in 4.23 s initially and 2.15 s after repacking. Runtime
loading is included but EXE startup is not. These are individual observations,
not a paired steady-state benchmark: several warm exports were slower during
the second pass, and the Z5 CPU TIFF16 example still takes about 7 s. No render
math or export compression was accelerated in this increment. Private reports
remain local and no photos or public installer are included in the build.

Two actual desktop workflows (D500 CPU at 900x640 and Z f GPU at 1080x720)
each pass 46 checks for pointer preview, Auto/noise advice, history, comparison,
JPEG/TIFF16 export, and saved edits/bit depth. Screenshots retain visible
controls and an unobstructed photo viewport. These source-mode checks exercise
workflow regression, not frozen startup timing; the separate EXE checks above
verify the changed cache/packaging behavior.

### Reusing CPU Luminance Guides

CPU luminance filtering now computes its rounded 0..255 guide once per halo
pixel, retaining it in a bounded uint8 strip instead of repeating integer
division in every overlapping 5x5 neighborhood. The full-precision weighted
luminance, signed differences, Q16 weights/strength, ties-to-even rounding,
and gamut-bounded equal-channel shift are unchanged. The extra scratch uses
one byte per halo pixel, remains inside the existing per-strip memory budget,
and is never retained as a whole-photo cache. GPU math and the NumPy reference
are unchanged, as are Auto decisions, metadata, export encoding, and recipes.

Four additional tests cover all 65,536 uint16 gray values, extreme guide
distances, narrow/strided/read-only arrays, and direct compiled strip bounds
at both bit depths. Existing scratch tests now include the byte guide and
continue to reject full-frame allocations.

A separate four-photo TIFF experiment checked the existing library's lossless
horizontal predictor. Files shrank, but paired compression times did not
improve consistently and three examples slowed down. The export compression
policy therefore remains unchanged; smaller files alone did not meet the
responsiveness target. Private experiment outputs are not repository assets.

Paired prior/current-kernel measurements on fixed D500/Z5 rendered pixels keep
every value exact. Full-size uint16 luminance filtering improves from
0.57/0.83 s to 0.26/0.38 s, respectively; RGB8 gains are small and preview
timings can be effectively unchanged. First compilation is excluded from these
warm medians. Alternating complete pipeline runs with color/luminance noise
at 0.65/0.60 retain byte-identical JPEG/TIFF outputs and original RAW hashes.
Including regenerated previews, QC, and file writes, CPU TIFF16 improves from
4.34 to 3.93 s on the high-ISO D500 and 5.54 to 5.12 s on the Z5 D40 example.
JPEG medians remain approximately 3.05/3.94 s. These are local i5-12500H
observations with unchanged quality, not a claim that whole exports are twice
as fast or that JPEG/GPU encoding has accelerated.

All 589 tests pass in source and packaging environments, including CPU-only
execution with six expected GPU skips. A separate paired check with the
packaging compiler (Numba 0.68) reproduces exact pixels and full-size uint16
filter improvements of 0.58 to 0.26 s (D500) and 0.81 to 0.38 s (Z5).
The refreshed EXE passes eight main checks plus a CPU TIFF16 restart: source
Auto/metrics, noise advice, native-detail pixels, and all five main TIFF16
pixel arrays agree. All eight exports remain byte-identical to the preceding
EXE, and two older recipes retain exact native detail. On the Z5, the first
new-kernel export takes 8.98 s and a restart takes 5.08 s with all used JIT
signatures cached; this cold/warm difference is not the optimization speedup.
Original RAWs are unchanged, with no compiler/cache fallback or public release.

Actual desktop workflows pass 44 checks on the high-ISO D500 CPU case (noise
advice abstains) and 46 on Z f GPU, retaining history, comparison, JPEG/TIFF16,
and saved edits/bit depth. With both filters enabled and highlights at -0.5,
the same D500 live test retains 46/108 ms median GPU/CPU slider response and
48/16 visible frames during 50 drag events. JPEG export takes 1.40/2.38 s.
These are regression observations, not new GPU/JPEG speed gains. Compact
screenshots retain visible controls without overlapping the photo.

### Denser Evidence For Auto Color Noise

Noise advice now retries an insufficient native sample result on a separate
16x16 uniform grid. Existing successful, low-noise, and no-safe-benefit results
are unchanged. The original 8x8 tone atlas and all tonal Auto parameters/metrics
remain identical. The denser grid caps its axis counts to avoid overlapping
32-pixel cores, retains even CFA alignment and real-edge halos, and reuses up
to 64 bright locations without rescanning the RAW. Small images cannot create
extra votes by duplicating the same pixels. Only a bounded atlas is retained,
not the full decoded sensor buffer; resized interactive proxies share it.

The minimum of 12 blocks from six independent regions, periodic/structured
texture rejection, noise-benefit threshold, and color/luminance budgets remain
unchanged. Each proposed retry must also pass the original sparse atlas's
tone, clipping, texture, and mean-color guards independently. Additional
samples therefore cannot dilute its protection of a small light or texture.
There are at most two retry candidates; the smaller candidate remains available
when the stronger one fails either guard. Failed retries preserve manual edits.
This is still explicit rendered-chroma advice, not calibrated sensor denoise,
automatic luminance filtering, or semantic recognition of intentional color.

On 33 real Nikon captures, 132 original/Auto and luminance-0/0.6 combinations
produce matching CPU/GPU suggestion strengths and statuses. All 106 cases that
did not need a retry retain their prior decisions and metrics on each backend.
Of 26 retries, 13 gain positive suggestions, nine find no safe benefit, and four
still lack evidence. All original RAW hashes and tonal Auto results are unchanged.
Thirteen full-resolution RGB8 checks plus independent, offset native patches pass
tone/color budgets and measure reduced color dispersion. In the high-ISO
portrait, red-lit portrait, and blue aquarium Auto/luminance-0.6 examples,
hold-out chroma dispersion drops by approximately 14%, 21%, and 11%, respectively.
Visual comparisons retain the intentional lighting and visible texture; grain
remains. These are sampled quality checks, not clean-reference noise scores.

Extra atlas preparation takes about 9 ms median in this local corpus and retains
at most 1.18 MiB of RAW samples. Retried advice takes about 0.41 s GPU / 0.72 s CPU
median after preparation, with individual CPU retries near one second. It runs
in the existing background analysis worker, never in the per-slider render path.
The change does not accelerate rendering or exports. All reports and photo
comparisons remain private local outputs rather than repository assets.

All 597 source tests pass, including CPU-only execution with six expected GPU
skips. Tests cover bounded/disjoint sample storage, native pixel/edge parity,
shared resized atlases, unchanged existing advice, dense fallback/backoff,
independent sparse-guard vetoes, malformed candidates, and tiny-image abstention.
Actual D500 CPU and aquarium GPU desktop workflows each pass 46 checks for
asynchronous advice, visible controls, undo/redo, comparison, JPEG/TIFF16,
and restoring saved edits/bit depth. Both previously skipped cases now apply
their advised color amounts. Compact screenshots retain an unobstructed photo.
Live GPU/CPU slider medians remain about 46/108 ms with both filters enabled;
the checked 50-event drag displays 48/15 frames. JPEG exports in these separate
regression runs take 1.50/2.73 s. These are local observations, not new speedups.

All 597 packaging-environment tests also pass. Ten rebuilt-EXE checks cover
D500, Z5 D40, Z f, synthetic DNG, CPU/GPU, and the three newly advised difficult
scenes. Source Auto/metrics, advice, native-detail pixels, and all seven TIFF16
arrays agree exactly; two older recipes retain their original native pixels.
Seven of the eight paired exports remain byte-identical to the preceding EXE.
The aquarium export changes because advice now selects color strength 0.28
instead of retaining the manual 0.65, not because render math changed.
An additional Z5 CPU restart hits all used kernel caches with zero misses,
exporting TIFF16 in 5.31 s versus 7.90 s on the first new-kernel run. That is a
cold/warm distinction, not a denoise/export speedup. No original photos, public
ZIP, or installer are distributed; the local Windows EXE is refreshed.

### Faster Container Reads And First Pictures

TIFF metadata and embedded-JPEG extraction now use a short-lived, explicitly
read-only file mapping. This avoids copying the whole sensor payload just to
read a small set of tags or the camera JPEG. Parsed opaque fields and JPEG data
remain owned immutable bytes, with no mapped views, open handles, or stale metadata
cache retained after a call. Empty/malformed input keeps the existing parser
errors. If mapping is unavailable, the reader falls back to the previous
buffered read behavior. Uncompressed sensor extraction and RAW decoding math
are unchanged. Python documents the mapping's [read-only access and context-managed lifetime](https://docs.python.org/3/library/mmap.html).

Embedded previews carry the same container orientation used before, removing
the live worker's second metadata read. The temporary camera reference requests
a reduced JPEG decode before allocating RGB pixels, then retains the existing
960-pixel Fit bound and orientation. This changes only the resampled camera
reference, not original JPEG bytes, RAW editing, Auto, or export pixels. It is
labeled `Camera` until a genuine native frame replaces it. Slider edits during
loading no longer discard an otherwise useful camera reference; source/view
changes still reject it. A known-stale or closed request is skipped before
starting RAW preparation. Malformed/oversized camera JPEGs do not prevent a
valid native RAW path from being attempted.

All 33 selected real Nikon files retain exact metadata, JPEG bytes, support
reports, and decoded sensor data against the preceding committed reader, with
unchanged source hashes. The separate 6,847-file read-only inventory also agrees
exactly, including its six rejected containers. This inventory checks metadata,
not sensor decoding support for every listed file. Alternating warm reads across
the 33-file set reduce median metadata time from 9.5 to 0.9 ms and embedded-JPEG
extraction from 10.7 to 2.4 ms. Median Python allocation peak during metadata
read falls from 24.2 to 0.48 MB; mapped OS pages and total app memory are not
included in that Python-only measurement.

Three alternating actual-desktop runs per version and photo distinguish the
first visible camera picture from the editable native preview. D500 CPU medians
change from 296 to 192 ms for the first picture and 1.37 to 1.24 s for RAW;
Z f GPU changes from 429 to 359 ms and 2.78 to 2.66 s, respectively. Continuous
slider response remains approximately 115/46 ms CPU/GPU in these runs. These
local warm-cache observations exclude Python/EXE startup; they do not eliminate
RAW decoding, GPU initialization, or first-ever compilation. The benchmark now
records both milestones and can capture the temporary camera view separately.

All 610 tests pass in source and packaging environments; GPU-off also passes
with six expected GPU skips. Two actual desktop workflows pass 46 checks each,
covering adjustments, noise advice, history, comparison, native detail, retained
edits, and JPEG/TIFF16 exports. Small-window captures verify the temporary
`Camera` state and subsequent `Edited` handoff without overlaying the photo or
controls. All six paired desktop JPEG exports remain byte-identical.

Eight rebuilt-EXE checks agree exactly with source Auto metrics, noise advice,
and native-detail pixels, including all five TIFF16 arrays. All eight derivative
files are byte-identical to the preceding build; two older saved recipes retain
their native pixels. Original hashes remain unchanged. A further Z5 CPU restart
hits every used kernel cache with zero misses and produces the same TIFF16 in
5.14 s, versus 8.29 s on the first run of the changed native sources. This is
compilation reuse, not a new export algorithm speedup. The local Windows EXE
has been rebuilt; no photos, public ZIP, or installer are distributed.

### Experimental Content-Conditioned Auto

The desktop and per-photo batch Auto paths can now use a separately installed,
local CLIP ViT-B/32 scene encoder. It supplies coarse content and lighting
similarities, not exposure presets, face identities, or a learned aesthetic
score. English prompt ensembles cover 15 content groups and six lighting
groups. Absolute similarity and ambiguity bound influence; lighting without a
clear winner is displayed as mixed. These gates are heuristics, not calibrated
probabilities. Mixed content and small subjects can still be misclassified.

Existing tonal Auto runs first. The new `decision/scene_color.py` stage selects
color objectives from scene weights and actual original pixels: restrained
chroma opportunity in muted green/blue materials, preservation of measured
ambient color, and conservative daylight neutral refinement. Finite-difference
RAW renders measure the camera's response to Temperature/Tint/Saturation;
a regularized least-squares proposal and backoff solve bounded changes per
image. There is no fixed adjustment dictionary per scene. Already-vivid colors,
uncertain evidence, or candidates without measured benefit retain prior values.

Accepted proposals pass the existing highlight/shadow guards on proxy, native
samples, and finer preview, including intermediate Auto strengths. Additional
color checks limit broad color clipping and hue/saturation drift in possible
warm subject materials. Those selections are color heuristics, not face/skin
segmentation. Adjustments remain global, not separate sky/person masks. The
objective's improvement is not proof that a person will prefer the result.
Portrait light, mixed-scene handling, calibrated confidence, and local masks
remain priority work; no skin whitening or demographic inference is used.

The runtime is optional for source installs and included in Windows builds.
Model files stay outside Git and the app bundle. Explicit setup verifies the
official checkpoint, compares exported ONNX/PyTorch outputs (maximum checked
error about 1.5e-7), retains the license, and installs a hash/prompt manifest.
Runtime inference is CPU-only, offline, and has telemetry events disabled.
One lazy session and a bounded 16-image content cache run only inside Auto's
worker; sliders/export never run the scene model. Invalid/missing models fall
back to tonal Auto. Restart after model replacement. See `models/README.md`
and `MODEL_LICENSES.md` for setup, model provenance, and deployment limits.

The initial private 33-photo comparison applies further color refinement to
11 photos; the remaining 22 retain prior adjustment values. CPU/GPU-rendered
inputs agree on scene/lighting choices and refinement decisions, with a maximum
observed parameter difference of 0.0001. This is a local regression set, not a
general scene-recognition accuracy benchmark. All original hashes remain exact.
All 11 changed photos pass 44 full-resolution checks at 25/50/70/100% strength,
both against unedited RAW output and against the prior Auto at the same strength.
Passing means staying within the established clipping/shadow budgets, not zero
pixel change or sensor-domain recovery. Visual comparisons were checked on
coast, aquarium, and grassland examples. Saved recipes still render as before;
only a new Auto action can request the new color parameters.

In that 33-photo run, total Auto medians are 0.92 s with GPU rendering and
2.78 s with CPU rendering. The 11 changed cases improved from 4.74 to 1.35 s
GPU median during this experiment by reusing color objectives and equivalent
RGB extrema operations, with identical proposals. This is not a speedup over
the older tonal-only Auto. CPU scene inference itself takes about 55 ms per
new image after a roughly 1.04 s initial model load/inference; an identical
cached input takes about 5 ms. These are local observations, not guarantees.

Two real desktop workflows pass 47 checks each at 900x640 and 1080x720,
including visible scene summaries, history, strength, noise advice, comparison,
retained edits, JPEG, and TIFF16. Ten rebuilt-EXE cases match source scene
evidence, Auto parameters/metrics, noise advice, and native-detail pixels;
all six TIFF16 arrays match exactly. Two older saved recipes remain unchanged.
Separate disabled/missing-model EXE runs reproduce the prior aquarium Auto
and TIFF file byte for byte. All tested original RAW hashes remain unchanged.

A separate D500 live benchmark, without noise reduction and with highlights
at -0.5, measures slider-to-display medians of 46/61 ms GPU/CPU and JPEG export
of 1.13/1.49 s. These warm-cache checks exclude process startup and must not be
compared directly with denoised TIFF timings. No scene inference occurs on
slider changes or export. The refreshed local Windows build retains runtime
notices and contains neither model weights nor PyTorch; no public release or
private photographic assets are published.

All 632 tests pass in source and Windows packaging environments. GPU-off also
passes with six expected GPU skips; dependency checks and focused static checks
pass. The added coverage exercises uncertain/missing/broken models, manifest
validation, bounded content caching, exact preprocessing for odd aspect ratios,
scene-dependent and pixel-dependent objectives, vivid-color abstention,
ambient-color preservation, possible warm-subject hue guards, finer-preview
vetoes, and optional-model publishing failures.

### Corroborated Person Color In Auto

Auto can now use a second optional, separately installed local PPHumanSeg model
to distinguish subject pixels from scenery targets. It reuses the CPU ONNX
runtime, verifies the pinned 6.16 MB model hash, and never downloads during app
use. `OPENRAW_PERSON=off` disables this layer; disabling scene analysis disables
both. Missing/corrupt runtimes or models retain the preceding behavior.
`models/README.md` and `MODEL_LICENSES.md` document setup and provenance.

This model is not trusted by itself: a substantial high-scoring core must pass
area/mean-score gates and independent CLIP crop corroboration. When a combined
group crop is ambiguous, at most three substantial disconnected regions can be
checked separately; only corroborated regions are retained. Model scores are
not calibrated probabilities of correctness. Small crowds, separated fragments,
occlusion, and difficult illumination can still fail; no identity, demographics,
face recognition, or skin tone target is inferred. The summary's region count
is not a count of people. The sidebar reports `Person detected` for accepted
evidence, not precise boundaries or complete detection of everybody present.

The decision layer resizes the coarse probability map into each oriented
analysis/finer-preview coordinate system and uses its high-score core. Confirmed
person pixels do not supply grass/sea saturation targets. Measured subject-color
ratios constrain the same global Temperature/Tint/Saturation solver. A spatial
4x4 partition balances large clothing against smaller color regions; hue and
saturation limits are rechecked at intermediate Auto strengths as well as the
endpoint. These checks act alongside existing native-sample/tone guards. The
mask changes decision measurements only: no local pixel compositing, edge
feathering, face edits, or new recipe fields are implemented. Masks remain
in memory; persisted recipes still contain the final global parameters and do
not require either model at render/export time. Selective subject exposure and
editable full-resolution masks remain unfinished product work.

The local 33-photo Nikon regression accepts person evidence in nine photos;
CPU/GPU-rendered inputs agree on all acceptance decisions. A group photo only
retains one corroborated region, and small crowds remain missed. Inspecting
the raw segmentation exposed false positives on fireworks, clouds, architecture,
and objects; the combined gates reject those candidates in this set. This is
not a general precision/recall benchmark. Thirty-two recipes retain their prior
Auto parameters. The aquarium example retracts its additional scene-only color
proposal while retaining tonal Auto; no unverified aesthetic improvement is
claimed for every photo. CPU/GPU parameter differences remain at most 0.0001.

The changed photo passes four full-resolution strength checks against original
and prior outputs. Separate native-rendered, every-third-pixel subject checks
show the prior 100% result fails the new small-region color guard; the updated
result passes at 25/50/70/100%. Those are sampled mask-based color checks, not
full-resolution ground-truth segmentation. Visual comparisons preserve ambient
blue light rather than whitening skin. All original hashes remain unchanged.

The person encoder takes roughly 10 ms on this machine in an initial 33-image
diagnostic, before crop corroboration and Auto rendering. The final local total
Auto medians are 0.89 s GPU and 2.63 s CPU; run-to-run/cache variation means this
is not evidence of a speedup over scene-only Auto. A bounded 16-entry mask cache
retains about 2.25 MiB of probability arrays. Neither model runs during sliders,
native detail inspection, or export. Gray reference pixels with undefined hue
no longer cause spurious color-guard failures; saturation protection remains.

Two actual desktop workflows pass 48 checks each, including person-state labels
at 900x640 and 1080x720, Auto/history/strength, manual and advised noise, comparison,
session retention, JPEG, and TIFF16. Eight rebuilt-EXE cases agree with source
scene/person evidence, parameters/metrics, noise advice, and native-detail pixels;
all five TIFF16 arrays agree exactly. Two older saved recipes remain exact.
Three EXE fallbacks (person off, person model missing, and scene off) reproduce
the corresponding prior Auto and TIFF bytes. The final gray-reference fix also
retains all 33 audited parameter sets and person-evidence records exactly.

The benchmark now accepts `--auto-before` to measure editing after both models
have actually run. On the D500 aquarium example, GPU/CPU slider-to-display
medians are 46/62 ms, with 49/47 visible updates during the drag test. JPEG
exports take 1.30/1.70 s in these runs with noise reduction disabled and
highlights at -0.5. These warm-cache timings exclude process startup and are
not comparable to denoised TIFF export times. Both paths retain original hashes.

All 649 tests pass in the Windows build environment and source GPU-off run
(six expected GPU skips in the latter). Focused static checks, dependency checks,
and whitespace validation pass. The local EXE is refreshed; model weights and
private images remain outside Git and the app bundle. No public release is made.

### Person Selection Boundaries And Inspection

Accepted person regions now receive conservative RGB-guided refinement before
Auto measures subject colors. `vision/mask.py` independently implements the RGB
equations from [He, Sun, Tang (2010)](https://people.csail.mit.edu/kaiming/publications/eccv10guidedfilter.pdf)
and the coefficient-subsampling approach of [He and Sun (2015)](https://arxiv.org/abs/1505.00996).
Coefficients use at most 384 pixels on the long edge; reconstruction uses the
unedited, oriented preview at up to 960. Float64 cumulative box sums feed
regularized float32 3x3 solves. The result is clipped below the original
resampled selection, so refinement cannot add newly classified foreground.
These weights are not calibrated model probabilities. Model evidence still
reports its original coarse coverage/score; the refined >=0.9 core supplies
Auto's measurements. No dependency, checkpoint, or recognition threshold changed.

The desktop retains this transient analysis and passes it into Auto to avoid
duplicate inference. `Person mask` inspects that same core, projected through
normalized full-frame coordinates for Fit, zoom, pan, and 100%/200% detail.
The display-only magenta overlay never enters render buffers, histograms,
history, sessions, recipes, or exports. Reimport clears the selection, stale
Auto callbacks cannot replace it, and absent/unconfirmed evidence disables
inspection. Original comparison uses identical selection coordinates.

Both GPU and CPU 33-photo RAW audits retain all preceding Auto parameter sets
and their respective person-evidence records. Nine accepted regions receive
refinement; no new person detections are claimed. The initial full-proxy
implementation took about 0.68-0.87 s per accepted sample. Subsampled final
refinement takes 0.13-0.21 s in the GPU audit (154 ms mean), runs on CPU once
during Auto, and never runs on slider changes or export. Original hashes are
unchanged. Visual inspection still finds incomplete groups, missing hands,
and incorrectly included objects; refinement cannot repair semantic mistakes.
This increment does not claim improved aesthetics, accurate skin/face masks,
editable selections, or shipped local exposure/color compositing.

The actual Tk workflow now checks mask enable/disable, exact image restoration,
unchanged buffers/settings/histogram, stale-result rejection, native-detail
alignment before and after pan, 200% inspection, and reimport clearing. Final D500 GPU and
rotated portrait CPU workflows each pass 64 checks at 1280x820 and 800x560;
the synthetic DNG/no-mask path passes 27 checks. Screenshots were inspected.

`benchmark_live_preview.py --auto-before --person-mask` measures the inspector
while editing, and requires a corroborated mask. On the checked D500 aquarium
at 900x640, GPU slider medians are 46 ms without inspection and 61 ms with it;
CPU with inspection is 77 ms. Drag tests show 49/49/48 updates, respectively.
JPEG exports take 1.44/1.36/1.71 s with noise reduction off; inspected and plain
GPU exports have identical SHA256. These are local warm-render measurements,
not startup or full-Auto latency promises. Initial Auto in those processes is
2.11/2.22/2.96 s including model loading.

All 659 tests pass in source and build environments. The rebuilt local EXE
passes eight Auto/export/native-detail cases; all parameters, metrics, model
evidence, and five TIFF16 arrays match current source. All eight exported files
are byte-identical to the preceding person-aware EXE, and two older saved recipes
remain exact. Three model-off/missing EXE fallbacks retain earlier Auto and TIFF
bytes. Static, dependency, and whitespace checks pass. No model weights or
private images are bundled or committed; no public installer/ZIP is released.

### Portable Subject Exposure

The next increment adds a real, independently enabled subject-exposure layer.
Desktop Auto prepares a zero-effect candidate when person evidence is accepted;
Subject > Select can create one without replacing global tone/color edits.
The selection uses smoothstep weights from the conservative refined .8-1 range,
quantized to uint8 at up to 960 pixels per axis. It remains a coarse person
selection, not a skin/face matte or manual brush. Automatic local exposure
and local color decisions are intentionally not enabled yet.

`adjustments.raw.subject` uses the `subject.v1` contract: original SHA256,
width/height, bounded base64/zlib mask, exposure in [-1, 1], and enabled flag.
Decode bounds reject malformed, oversized, truncated, or trailing data.
Session load/save, the live worker, and file-render/pipeline boundaries reject
another source's layer. A bounded file-identity hash cache avoids rehashing on
every slider update. Undo/redo deep-copies layer settings. Global Auto strength
preserves local edits; Reset removes them reversibly. Current-adjustment batch
copy omits image-bound layers; Saved edits uses each photo's own layer.

Local exposure follows global rendering/noise reduction in oriented RGB, before
output resizing. The peak-channel gamma-2.2 inverse provides a common RGB gain
with a white-anchored shoulder. It cannot recover already-clipped sensor data.
Integer full-frame pixel centers define mask sampling, keeping native ROI and
full-frame output consistent across orientation, pan, and 8/16-bit output.
The persisted selection needs no model at render time. Magenta inspection is
display-only and uses that stored soft mask after reopening.

An optional sequential no-fastmath Numba kernel fuses bilinear mask projection
and RGB gain. Explicit float32 arithmetic matches the bounded NumPy strip
fallback exactly in tested 8/16-bit cases. Cache write failures retry in memory;
compilation failures fall back. Selection workers prime the RGB8 kernel before
the first local slider move. Warm measurements on one 20MP D500 sample with
local +0.6 and noise reduction off: initial NumPy version 75 ms median display
latency / 2.84 s JPEG, fused GPU-backed global pipeline 59 ms / 1.79 s, CPU
pipeline 87 ms / 2.06 s. These are local measurements, not first-use promises;
the first RGB16 signature can still compile on its first export.

Focused tests cover payload/source validation, immutable originals, background
identity, monotonic gain, channel ratios, exact NumPy/compiled agreement, cache
and compiler fallback, strip/ROI equivalence, all eight Nikon orientations,
DNG/NEF TIFF8/16 pipeline output, and reversible UI state. Two real desktop
workflows each pass 52 checks: aquarium GPU at 1280x820 and rotated portrait
CPU at 800x560, including pointer-driven local edits, disable/baseline equality,
JPEG/TIFF16, and reopen. Screenshots were inspected. The existing Auto solver
and recognition thresholds are unchanged; this does not claim improved
segmentation or automatic aesthetic accuracy.

The fused kernel is included in the content-checked frozen cache allowlist.
The existing cross-process cache test now covers subject RGB8/16 signatures,
including repacking, moving, source changes, and compilation-flag changes.
Source/build suites pass 676 tests; the extended cache tests pass in both
environments. A further 800x560 desktop workflow passes 56 checks, including
independent Select and preservation of all global controls. Changed-file static
checks pass; the broader F401 scan still reports the pre-existing compatibility
import in `raw/native/color.py`, which this increment does not alter.

The rebuilt local EXE passes four saved-layer replays (positive/negative,
GPU/CPU, JPEG/TIFF16, and both models disabled). All match source rendering and
native detail; three TIFF16 arrays also retain every zero-mask background pixel.
The frozen cache reports misses only on the first new signatures, then hits;
the warm local-layer JPEG case takes 1.91 s. Eight existing frozen Auto cases
retain all previous parameters, metrics, and exported file bytes, including
five exact source TIFF16 arrays; two older recipes also remain exact. Original
SHA256 values stay unchanged. No weights, private photos, or public ZIP release
are included. Local metering and content-dependent local color remain open.

### Face-Metered Local Exposure

The next increment adds an optional offline face locator and a local exposure
solver, separate from global scene/color Auto. `vision/face.py` verifies the
pinned YuNet 2023mar artifact, prepares aspect-preserved BGR640 input, decodes
boxes, applies score/size/border/NMS guards, and caches up to 16 results. This is
location-only inference, not identification, demographics, or a skin matte.
Models remain separately installed; missing/corrupt/disabled inference is an
explicit abstention, never a requirement for saved-layer replay or editing.

`decision/subject_exposure.py` meters inset face ellipses only when at least 85%
overlaps the corroborated person selection. Background luminance and continuous
night/sunset/colored-light/aquarium evidence limit a relative fill target.
Clothing is not the face brightness target. Twelve bounded trial renders test
the actual local-exposure transform; each face and the rest of the selected
subject have clipping guards. Conflicting face needs, insufficient background,
or no verified benefit produce no change. This remains a bounded heuristic
objective, not a learned aesthetic score or calibrated skin rendering.

First toolbar Auto may add metered exposure when creating a new selection.
Existing layers, including disabled/manual ones, are preserved. The Subject
wand independently recalculates exposure against current global settings,
excluding the prior local effect, and is undoable. The strength label explicitly
says Global Auto. Per-photo batch Auto analyzes each original independently;
Current adjustments still excludes source-bound masks, and Saved edits retains
them. Model work is confined to explicit Auto/Select commands, not slider or
export replay. Noise controls remain unchanged.

On 33 private/public RAW validation files, the initial face threshold accepts
five boxes in four photos. Identical prepared input compared with the external
OpenCV FaceDetectorYN oracle gives matching accepted boxes (IoU above .999,
score difference below .0001). OpenCV is a development-only oracle, not an app
dependency. This small comparison does not establish general face accuracy.
CPU/GPU local-advice decisions agree: two portraits receive +.7675/+.7763 EV,
31 photos receive no new local exposure. Missing/excluded faces are not treated
as proof that a photo has no people.

Both changed portraits pass full-resolution checks at 70/100% global strength,
8/16-bit output, CPU/GPU: 16 cases. Zero-weight background pixels remain exact;
no new channels cross from below 250/255 to at least 254/255 or from above 8/255
to at most 2/255. Native face crops match full RGB8 renders. These are measured
sample results, not an exhaustive guarantee for unseen photographs.

Three real Tk workflows pass 50/60/64 checks: first Auto, independent local Auto,
manual edits after advice, disable/enable, undo/redo, native inspection,
JPEG/TIFF16, reopen, and selection without global changes. The 800x560 check
also verifies that all Subject controls retain their requested widths; the
Select button uses content width to avoid clipping its neighbor. Screenshots
were inspected. Eleven batch checks pass, plus a focused test proves that each
Auto-batch photo receives its own source-bound selection and retains chosen
noise amounts. Build-environment tests pass 703 cases.

The refreshed Windows EXE passes six additional checks: GPU/CPU portrait advice,
JPEG/TIFF16, face-disabled, face-missing, and all-models-off layer replay. Advice
matches source execution exactly; exported pixels and native detail match source
rendering, including five complete TIFF16 arrays. Models-off replay uses the
saved selection without inference. The sampled warm full-size JPEG export is
1.95 s; local metering adds 0.55-0.72 s in those processes, including cached
person analysis and face inference. These are local measurements, not cold-start
or universal performance promises. Original hashes remain unchanged. No public
ZIP, model weights, or private photos are released.

Eight prior frozen Auto cases retain their earlier global parameters, metrics,
noise advice, and exported file bytes; five TIFF16 arrays also match source
rendering. Two older saved recipes retain exact native-detail pixels. The Z f
HE CPU Auto is separately checked against its CPU baseline, not the slightly
different GPU baseline. No RAW decoder, global color solver, or saved-layer
render transform changes in this increment.

## Measured Local Subject Color

Subject layers now support independent rendered Temperature/Tint as
`subject.v2`, with strictly bounded numeric fields and the existing source-bound
compressed mask. v1 is not rewritten when no color is used; both contracts
replay without models. Local exposure is applied first, followed by RGB gains
normalized to preserve display luma. A common gamut backoff retains color
direction without independently clipping channels. This is not sensor white
balance, Kelvin calibration, or reconstruction of clipped sensor samples.
The fused optional CPU kernel and chunked NumPy fallback agree exactly for
RGB8/16, including rotated buffers and full-frame/ROI mask projection. RGB8
signatures are primed in the worker after selection/Auto and when reopening a
saved layer; frozen cache identity includes the new kernel.

`decision/subject_color.py` derives corrections from the current rendered image.
Reliable face overlap and optional neutral-clothing corroboration are required.
It excludes faces from candidate neutral references, collects spatial tile
measurements, and requires common bias, brightness diversity, and differing
background light. The background is corroboration, not a target shirt or skin
color. Two trial renders measure the local Temperature/Tint response; a bounded
fit must reduce the measured common cast without worsening any reference tile,
excessively shifting any metered face, or introducing near-clipped channels.
Night, sunset, colored light, aquarium content, and ambiguous evidence abstain.
The method remains deterministic, evaluated heuristics, not a trained aesthetic
optimizer or a promise of correct intrinsic material color.

Pure near-neutral pixel selection initially proposed removing color from a
pale-pink shirt. The optional material head now corroborates white/gray clothing
before such a proposal is considered. It shares the same local CLIP image
encoder/session; 14 garment descriptions add a separate projection without
changing scene text embeddings. Exported ONNX/PyTorch outputs differ by at most
1.64e-7 on the export probe. All 33 cached regression images retain bit-identical
scene scores and evidence. This coarse crop-level check can still be confused
by surrounding objects, mixed wardrobes, and lighting; its similarities are
not confidence probabilities. The pink-clothing example is rejected as
uncertain, not claimed to be correctly classified. Scene-only model exports
remain supported, and missing/disabled material evidence preserves edits.
See `models/README.md` for explicit setup, hashes, and deployment limitations.

The 33-photo CPU/GPU RAW regression preserves every prior global Auto setting.
Only the white-clothing mixed-light sample receives local color advice, at
both 70% and 100% global strength; 32 abstain. Equal spatial voting prevents
the oppositely lit shirt/trouser areas from hiding each other. The measured
common cast norm falls from .1191 to .0766 at 70% and .1064 to .0699 at 100%.
These are internal reference metrics, not general picture-quality scores.
Eight full-native cases (CPU/GPU, both strengths, RGB8/16) retain exact
zero-mask background pixels, introduce no fully clipped channels, and change
display luma by less than .51 sample codes. Native crops match full renders.
All original RAW checksums remain unchanged; private images/reports stay local.

Two 79-check Tk workflows at 800x560 and 1440x900 cover first Auto, separate
color/exposure Auto, real pointer drags, undo/redo, bypass, global strength,
native inspection, JPEG/TIFF16 export, and reopening. Each wand preserves the
other local controls and all globals. Existing layers are never overwritten by
top-level Auto. Batch Auto derives each photo's own layer; Current adjustments
still strips source-bound edits. Screenshots were inspected for clipped controls.

On the sampled D500 image, cached local-color render medians are 24-25 ms on
the GPU path and 66-71 ms on CPU. End-to-end Tk slider medians are 75/95 ms,
maxima 78/110 ms, with 47/30 displayed changes during 50-step continuous drags.
JPEG export with local exposure/color takes 2.00/2.39 s; initial Auto including
model loading takes 2.64/3.23 s. These are local warm-kernel observations, not
cold-start or universal performance guarantees. The benchmark's
`--slider subject_warmth` / `--slider subject_tint` modes exercise local color directly.

The refreshed local Windows EXE passes eight new source-equivalence checks:
CPU/GPU color advice, JPEG/TIFF16, uncertain colored clothing, material disabled,
the old scene-only model, models-off v2 replay, and v1 replay. Advice, metrics,
exported pixels, and native detail agree with source execution. The v1 replay
and disabled-versus-old-head TIFF files remain byte-identical to their references.
Both new RGB8/16 kernel signatures hit the persistent frozen cache in later
processes, with no compilation fallback. The sampled warm EXE JPEG export is
2.33 s; TIFF16 timings are separate and include compression/first-use costs.
Eight earlier D500/Z5/Z f/DNG frozen regression cases preserve all prior global
Auto parameters, metrics, noise advice, and output file bytes. Five TIFF16 arrays
also match source rendering, and two older recipes retain exact native detail.

Source and packaging environments each pass 729 tests. Fourteen batch UI checks additionally
cover distinct saved v2 layers, per-photo Auto, removal of local layers from
Current adjustments, cancellation, and compact-panel scrolling. Static checks
and dependency checks pass. The optional material head is installed locally;
its predecessor is retained privately for regression. No model weights, private
photos, public ZIP, or installer are published.

### Ambient-Aware White-Balance Reference

A subsequent 30-photo D500 cohort exposed an ordering conflict: optional scene
evidence recognized golden-hour lighting, but neutral-cast refinement had
already removed part of its warm color before semantic color refinement ran.
Camera JPEGs were used only for sample selection and visual reference, not as
RAW-rendering ground truth. After inspection this cohort is development data,
not an untouched quality holdout.

`decision/ambient_color.py` now combines optional scene/lighting evidence with
usable background colors, excluding corroborated person cores. A minimum image
fraction, at least four spatial tiles, two quadrants, and directional agreement
are required. Confidence controls retention continuously. Sunset retains only
the warm/cool projection; night, colored lighting, and aquarium contexts derive
their direction from actual background log-channel ratios. A scene label alone,
a small colored patch, or a person's clothing cannot supply this reference.
Intrinsic material color and illumination remain ambiguous, so this is a
conservative heuristic, not calibrated illuminant estimation.

Initial white balance and `NeutralCast` both use this reference. The latter
fixes the retained component to the original near-neutral pixels and never
re-estimates it from candidate renders. Measured renderer-response fitting then
corrects the residual cast, including orthogonal green/magenta deviations,
subject to the existing preview/native/detail and intermediate-strength guards.
No decoder, renderer, recipe schema, or model artifact changes are required.
Missing/uncertain scene evidence retains the previous path. Saved recipes render
unchanged; rerunning Auto can generate different settings.

The additional 30 D500 photos and previous 33-photo regression were processed
on CPU/GPU at 70%/100%, with original hashes checked. Four new-cohort and three
older-cohort global suggestions change. New-cohort global/local adjustment
values agree exactly between backends. In the combined 63, one pre-existing
global warmth rounding difference and one local tint difference are 0.0001;
soft masks and RGB8 previews differ by at most two sample codes. These are not
claimed as pixel-identical GPU/CPU results.

For the golden-hour portrait, original near-neutral warm-axis projection is
.0673; at Auto 70% the earlier result retained .0333 versus .0478 now, and at
100% .0237 versus .0413. This is a fixed original-pixel log-channel metric,
not a human aesthetic score. Additional/older cohorts refine 4/3 images,
respectively, without making every scene warmer.

Seven changed images receive 56 full-native RGB8 checks across both backends
and strengths 25/50/70/100%. All native regions match full renders. 54 checks
pass the existing clipping/shadow budgets. The same indoor photo at 100% on
both backends exceeds the 0.5% crushed-shadow threshold: .51695% now versus
.51810% previously. The strict full-native audit therefore remains failing;
this is a recorded pre-existing issue, not a waived passing check. Highlight
metrics remain inside budget. All source checksums remain unchanged. Next
quality work should address this sampling gap, dark-scene intent, and the
appearance of already-clipped bright lights; no highlight reconstruction is
claimed here. Private evidence is under `output/ambient-native` and the
`output/*-auto-ambient*` cohorts, never included in Git.

Source and packaging environments each pass 742 tests, including 13 focused
ambient-reference tests. Tk workflows pass 42 checks at 800x560 on GPU and 69
at 1440x900 on CPU, covering global/local Auto, live edits, reversible strength,
native inspection, JPEG/TIFF16, and reopening. Screenshots have no overlapping
controls. Static and dependency checks pass. The Windows build is local only;
there is no public ZIP or model distribution.

The refreshed EXE passes 11 source-equivalence cases: every changed photo,
CPU/GPU golden-hour checks, JPEG/TIFF8/TIFF16, model-disabled fallback, and the
existing local-color workflow. Auto settings match exactly, metrics agree to
1e-6, and exports/native-detail crops are pixel-identical to source execution.
The earlier local-color portrait keeps identical Auto/local advice and identical
TIFF file bytes. The sampled warm EXE golden-hour JPEG export takes 1.57 s;
this is not a universal or cold-start benchmark. All inputs remain unchanged.

### Measured Contrast and Near-Black Headroom

The indoor counterexample crossed the full-native shadow threshold even though
the small/display proxies narrowly passed. At full Auto, the display proxy
reported .487% crushed shadows while the full render had .517%. Neither was
an exhaustive prediction of the other; simply reusing the binary cutoff missed
the pending loss. Tonal Auto now adds a soft risk band from 2 to 4 RGB8 luma
codes on originally usable shadow pixels, alongside the unchanged hard metric.
Existing black pixels do not count. This is conservative quantization/proxy
headroom, not a calibrated perceptual model or statistical confidence bound.
The extra margin is opt-in on `_RenderGuard`; the color-noise estimator retains
its earlier guard behavior.

When positive contrast fails tone checks, `_limit_contrast` verifies zero and
uses five bounded bracket trials. It returns an actually checked candidate,
never an unrendered interpolation. If zero cannot preserve the tones, the
existing whole-correction backoff still applies. Every camera uses its real
render response; there is no per-file or per-scene contrast table. Sparse usable
shadows now also trigger intermediate-strength checks. Existing tests that
required exactly half/zero contrast now check the original rendered tone
budgets and the measured safe range instead. Nine additional tests cover the
soft band, bounded probes, nonmonotonic responses, native/detail validation,
and intermediate-strength loss hidden by an acceptable endpoint.

The indoor example now selects contrast .0938 rather than .1, with the other
global values unchanged. Full-native RGB8 shadow loss falls to .46067%, below
the .5% budget. The two 30/33-photo development cohorts change 10/9 global
suggestions; contrast can increase or decrease relative to the earlier coarse
backoff. Later white-balance/color fits can consequently differ too. These are
development cohorts, not independent aesthetic-quality benchmarks.

Across 19 changed images, 152 full-native RGB8 checks cover CPU/GPU and strengths
25/50/70/100%. All native regions match full renders, and original hashes are
unchanged. 150 checks satisfy the existing budgets. The two failing checks are
the same photograph at 25% on both backends: five out of 231 original usable
highlight channels reach the clipping threshold, just above the 2% relative
budget. The earlier Auto also loses those same five channels; it is not a new
regression, but the strict audit remains failing rather than being relabeled
as passed. The original shadow counterexample passes on both backends. Local
evidence is in `output/shadow-native` and `output/*-auto-shadow*`.

Eight further full-native RGB16 checks cover the indoor example and the local
color portrait at 70/100%, on CPU/GPU. All meet the existing tone budgets,
including the portrait's combined local edits; native crops are exact and input
hashes unchanged. Across the 63-photo CPU/GPU regression, 62 global suggestions
match exactly; the remaining warmth difference is the earlier .0001 rounding
case. The local-color portrait differs by at most .0001 in advice, masks by two
codes, and rendered RGB8 previews by two codes. These are bounded numeric
differences, not pixel-identical backend results.

The source environment passes 751 tests. Actual Tk workflows pass 28 checks at
800x560 (GPU) and 69 at 1440x900 (CPU), plus 14 batch checks. Screenshots show no
overlapping controls. Two initialized-process benchmarks at 1080x720 measure
58 ms median GPU exposure-slider latency on the indoor photo and 90 ms CPU
local-temperature latency on the portrait. The respective JPEG exports take
1.25/2.33 s; Auto itself takes 2.04/3.40 s, including model setup in each
process. Initial RAW previews take 1.59/1.03 s, with camera previews visible
in about .20/.18 s. These are different photos and operations, not a controlled
GPU-versus-CPU speed comparison or cold-machine guarantee. Evidence remains
local in `output/shadow-gui-*`, `output/shadow-batch`, and
`output/shadow-benchmark-*`.

The packaging environment also passes all 751 tests. The refreshed local EXE
passes 14 source-equivalence cases spanning CPU/GPU, JPEG/TIFF8/TIFF16,
combined subject edits, and disabled optional models. Global Auto parameters
are exact, metrics agree to 1e-6, and exports/native crops are pixel-identical
to source execution. Every source hash is unchanged. Dependency and static
checks pass; no public ZIP, private photo, or model was published. The optional
Numba TBB-pool DLL warning remains in packaging; the tested CPU paths work
without that optional pool. Local evidence: `output/shadow-frozen-*` and
`output/shadow-frozen-verification.json`.

That audit identified a next step: distinguish dark-scene intent from missing
image information before choosing a tonal target. One almost-black frame received a
Sky/Night label (scene reliability .319) and +1.2 EV, without useful visible
recovery. A starry sky with foreground detail and an illuminated street are
different cases. Semantic evidence alone is not sufficient to decide how much
to lift these images; both information content and rendered appearance need
to participate. The tiny highlight-loss exception above also remains open.

### Relative Dark-Scene Intent And Abstention

`decision/tonal_intent.py` combines ready Night/Colored-light evidence with
observed darkness. Confidence is an influence weight, not a calibrated
probability. Relative fill headroom is derived from the original median and
interquartile range, with a small RGB8 floor; weak evidence continuously relaxes
the limit. This changes the intended brightness target, not a preset exposure
for a scene name. Five bounded trials can jointly reduce positive exposure and
shadow lift while leaving the other controls intact. Both the small and display
guards enforce their own measured median ceilings, including requested strength
samples and subsequent color refinement. Native samples are brightness-biased
and deliberately do not set this limit.

The earlier exposure metering floor could reverse the correction sign when a
new relative target lay below .025. The smaller floor/deadband apply only when
dark-scene evidence actually limits the target; nonlimiting weak evidence keeps
the previous balanced-exposure deadband. Sparse illuminated subjects remain
eligible and the established low-key safeguards still apply.

Abstention requires a nearly black, low-range preview with negligible usable
bright area, no substantial stronger color-channel signal, and less than one
RGB8 code of coarse spatial variation. Saturated blue is not empty just because
its luminance is low. Isolated
hot pixels are bounded for that spatial check. A finer display preview can veto
the small proxy's abstention. This is not a claim that the RAW lacks recoverable
signal: faint structure, tiny real subjects below sampling resolution, strong
noise, or unusual processing can fool this heuristic. Import, manual editing,
and export stay available. Auto reports insufficient information rather than
trusting a semantic label from a nearly uniform frame. Desktop application of
that result preserves all existing edits, pixels, and history; it does not
install an all-zero Auto-strength recipe.

Fourteen focused tests cover image-dependent headroom, uncertain/daylight
fallbacks, bounded fitting, noise/hot pixels, faint spatial structure, sparse
subjects, the metering-floor sign, intermediate strengths, and detail-domain
vetoes. The actual 800x560 low-information workflow passes 30 checks including
manual edit retention, undo/redo, comparison, JPEG/TIFF16, and reopening.
Private development evidence remains in `output/*-auto-intent*` and
`output/intent-gui-abstain`; these are not independent aesthetic benchmarks.

In the 30/33-photo regression, five global suggestions change: a star field,
a cloudy night sky, two night streets, and the almost-black abstention example.
The other 58 retain their global values. All original hashes remain unchanged.
CPU/GPU global advice is identical on 61/63 photos; the other two differ by at
most .0001 per control. Existing local-color rounding differs by .0001, masks
by at most two codes, and previews by at most two codes. Forty full-native RGB8
checks on the five changed photos cover both backends and 25/50/70/100% Auto.
All meet the existing clipping/shadow budgets and applicable dark-scene median
ceilings, and native crops match full renders exactly.

Six additional D500 samples were selected after implementation, outside the
63-photo set, by high-ISO metadata in other local folders. They were compared
with `f87a677` without fitting new parameter values to their results. Three
change, including an illuminated building, a pagoda, and a moonlit landscape.
All 12 full-native checks at 70/100% preserve the existing clipping/shadow
budgets and exact crops. One unchanged high-ISO moon image at 100% exceeds the
new full-native median ceiling by .00034145 (about .087 RGB8 luma codes), despite
passing preview-domain checks. The strict extra audit remains failing; it is
not relabeled as a pass or evidence of universal native-target protection.

Another nearly black, red-tinted frame still passes the information gate and
receives the same earlier correction. Its embedded camera preview is also
nearly black. Broader signal-versus-noise discrimination, uncertain scene labels,
and small full-native sampling differences remain open, alongside the prior
five-channel highlight exception. These inspected extra samples now form part
of the development evidence, not an independent aesthetic benchmark. Private
artifacts: `output/intent-native` and `output/extra-auto-intent`.

The source environment passes 765 tests. Actual desktop workflows pass 30
checks at 800x560 for abstention, 28 at 1280x820 for a night street, and 69 at
1440x900 for CPU subject exposure/color editing. Screenshots show no overlapping
controls. The batch workflow passes 14 checks, including per-photo Auto, saved
edits, cancellation, and compact export-panel access. Private evidence remains
in `output/intent-gui-*` and `output/intent-batch`.

On the same star-field photograph at 1080x720, initialized GPU/CPU exposure
slider latency is 58/76 ms median, with 49/43 frames during a continuous drag.
Release-to-frame latency is 61/116 ms; JPEG exports take 1.18/1.78 s. Auto itself
takes 2.81/5.63 s, including model setup in each process, so CPU Auto latency is
still an optimization target. Camera previews appear in about .18 s and initial
RAW previews take 1.47/1.05 s. These are local initialized-process measurements,
not cold-machine guarantees or a before/after comparison with the earlier
commit. Reports: `output/intent-benchmark-gpu` and `output/intent-benchmark-cpu`.

The packaging environment also passes all 765 tests. The rebuilt local EXE
passes 10 source-equivalence cases spanning CPU/GPU, JPEG/TIFF8/TIFF16, subject
exposure/color, abstention, and disabled optional models. Auto parameters match
exactly, metrics agree to 1e-6, and exported pixels/native crops equal source
execution. Every source hash is unchanged. These equivalence checks do not
replace the native quality audits or resolve their recorded exceptions.
Fresh-process frozen star-field Auto takes 4.70 s on GPU and 7.36 s on CPU;
startup-sensitive timings remain higher than the initialized desktop benchmark.
Evidence: `output/intent-frozen-*` and `output/intent-frozen-verification.json`.

Dependency, compile, and diff checks pass. Packaging retains the known warning
about the optional Numba TBB-pool DLL; the tested CPU paths work without that
pool. No public ZIP, private photograph, or model was published. Further work
should prioritize information/noise discrimination, native sampling exceptions,
and Auto latency without weakening the rendered quality checks.

### Bounded CPU Auto Rendering

Profiling the star-field example found repeated tone rendering, not model
inference, dominated initialized CPU Auto. A private experiment estimated that
32 MiB of candidate-image caching would save about .89 s in that run; the
implemented change instead parallelizes independent rows without retaining
candidate images. It does not change the tone kernel, precision, search order,
candidate count, metrics, clipping budgets, or strength checks.

`raw/native/cpu_tone_batch.py` provides a thread-local scope around
`suggest_auto_adjustments_for_photo`. CPU tone frames of at least 200,000 pixels
can use one lazily created, reused pool. Up to four row jobs follow a serial
one-row warmup, so JIT/cache setup completes before sharing the signature.
The existing `OPENRAW_CPU_WORKERS`, spare-CPU, and scratch limits still apply.
The pool shares `cpu_chunks`' nonblocking ownership guard: another Auto/export
does not create a competing pool and can proceed serially. A batch releases its
pool and ownership on normal completion or exceptions. Nested scopes reuse the
outer scope; normal slider rendering outside Auto is unchanged.

GPU tone rendering bypasses this path. Compiler failure still reaches the
NumPy fallback. Failed thread creation or partial submission joins outstanding
work before a serial retry; pixel-processing errors are propagated, including
errors in work queued by a failed submission. Partial RGB is never returned.
The focused tests cover lifecycle, concurrency, worker limits, GPU failure,
compiler/thread failures, exact row coverage, and exact real-kernel pixels with
orientation, color, and immutable strided inputs.

Both 63-photo CPU/GPU regressions retain every prior Auto parameter, rationale,
evidence value, quality metric, and combined-edit preview pixel. Original hashes
are unchanged. Private diagnostic and comparison artifacts remain under
`output/intent-auto-profile`, `output/auto-batch-cpu`, `output/auto-batch-gpu`, and
`output/probe_auto_render_reuse.py`. This performance work does not resolve the
previous dark-scene sampling, low-information, or tiny-highlight exceptions.

All 778 source tests pass, including 13 focused batch tests. Four alternating
old/new pairs per photograph, after initializing models/kernels, compare against
`3bc6ad2` in the same process. CPU Auto medians fall from 7.08 to 4.81 s for a star
field, 5.58 to 3.45 s for a coast, and 2.53 to 1.83 s for a portrait (about
27-38% less time). Every paired suggestion and metric is exact. These figures
exclude import/initialization and are not cold-start or universal speed claims.

GPU medians in the initial four-pair check vary from about -2% to +6%; a ten-pair
star-field repeat measures 2.695/2.706 s before/after. No GPU speedup is claimed.
Private timings: `output/auto-batch-benchmark-*`. Actual 1080x720 Tk benchmarks
on the star field measure 58/74 ms median exposure-slider latency for GPU/CPU,
49/47 continuous-drag frames, and 61/86 ms release-to-final latency. JPEG exports
take 1.27/1.84 s; desktop Auto, including per-process model initialization, takes
3.24/4.57 s. Camera previews appear in .19/.18 s and RAW previews in 1.44/1.03 s.
Original hashes remain unchanged. Evidence: `output/auto-batch-live-*`.

Actual desktop workflows pass 69 CPU checks at 1440x900 for local exposure/color,
28 GPU checks at 800x560 for a night scene, and 14 batch checks. Undo/redo,
comparison, JPEG/TIFF16, reopened settings, cancellation, and compact export
controls work; inspected screenshots show no overlapping controls. Evidence:
`output/auto-batch-gui-*` and `output/auto-batch-batch`.

The packaging environment also passes 778 tests. The rebuilt local EXE passes
12 source-equivalence cases covering CPU/GPU, JPEG/TIFF8/TIFF16, local edits,
abstention, disabled models, and explicit single-worker mode. Auto settings are
exact, metrics agree to 1e-6, and export/native-crop pixels match source execution.
Every original hash is unchanged. Evidence: `output/batch-frozen-*` and
`output/batch-frozen-verification.json`.

The first frozen CPU star-field Auto takes 7.34 s and records tone/Bayer cache
misses. Native code changes deliberately invalidate the content-checked frozen
cache, so this first-use compilation cost remains. With cache hits, separate
fresh-process star-field runs take 7.25 s with one worker and 5.15 s with four;
these single-run measurements include model setup and are distinct from the
paired warm-source benchmarks. The slower first result is retained, not
replaced by the repeat. The optional Numba TBB DLL packaging warning remains;
this implementation uses ordinary bounded threads and the tested CPU paths work.
Dependency, compile, and diff checks pass. No public ZIP, model, or private
photograph was published.

### Noise-Dominated Dark Previews

`tonal_intent.dark_noise_evidence` extends the existing information-abstention
path, not denoising or scene classification. It uses actual unedited RGB8
analysis/display domains. Both must have low luminance/channel percentiles,
small 16x16 tile spans, and no connected tiny lights or substantial bright
coverage. Pixel spread must dominate coarse spread in the finest domain.
Absolute neighbor correlations at 1/2/4 pixels in both domains protect fine
repeated texture and coherent faint gradients. Noise attenuation in the smaller
proxy alone does not invalidate evidence from the larger proxy.

These are conservative heuristics in rendered preview space, not calibrated
sensor SNR or a proof that the RAW lacks signal. Weak structure below the noise
floor, isolated tiny subjects, demosaic artifacts, and unusual textures remain
ambiguous. Metadata, camera identity, filenames, and ISO are not inputs. The
extra checks run only on very dark Auto candidates, not during ordinary slider
or pixel-export rendering.
Diagnostic metrics accompany abstention; desktop Auto uses the existing
no-op path, preserving manual edits, history, and preview pixels.

Thirteen focused tests cover random noise, cross-scale attenuation/vetoes,
sub-code faint gradients, small subjects, edge/diagonal light pairs, hot pixels,
periodic texture with/without noise, strong colored light, rotations/reflections,
minimum dimensions, and invalid numeric input. Prior night-intent, clipping,
shadow, color, and intermediate-strength checks are unchanged.

Source and packaging tests pass all 791 cases. A targeted private audit covers 16 D500
captures on CPU/GPU, including 12 hash-distinct additions to the earlier
69-photo development set. Two nearly black red-noise captures now abstain
instead of proposing +1.2 EV; the other 14 retain their prior advice/metrics.
The two new abstention decisions agree across backends. New examples include
high-ISO portraits, a faint textured surface, aquarium light, and small moons.
Neighboring captures are correlated; this is development evidence, not a
held-out aesthetic benchmark. Original checksums remain unchanged. Reports and
comparison images are private in `output/dark-noise-{gpu,cpu}`.

Real Tk workflows each pass 30 checks at 800x560 (GPU) and 1440x900 (CPU), using
the two newly abstaining captures. Manual edits, history, displayed pixels,
comparison/panning, JPEG/TIFF16 export, and reopened settings are preserved.
Screenshots were inspected; controls do not overlap. Evidence lives under
`output/noise-gui-{gpu,cpu}`. The prior full-native tiny-highlight and tight
moon-median sampling exceptions are unchanged and remain open.

Both 63-photo CPU/GPU regressions retain all previous parameters, rationale,
evidence, metrics, and combined-edit preview pixels at 70/100%, comparing each
backend with its own baseline. Reports: `output/noise-regression-cpu` and
`output/noise-regression-gpu-final`.

The refreshed local Windows EXE passes six source-equivalence cases covering
both new abstentions on CPU/GPU, a normally adjusted star field, disabled
optional models, and JPEG/TIFF8/TIFF16. Auto settings match exactly, metrics
within 1e-6, and exported/native-detail pixels exactly; original hashes remain
unchanged. Reports: `output/noise-frozen-*` and
`output/noise-frozen-verification.json`. The existing optional TBB DLL packaging
warning remains; tested CPU paths work. Dependency, compile, and diff checks
pass. No public ZIP, model, or private photo was published.

### Face-Corroborated Person Regions

In additional low-light D500 portraits, a visible aquarium subject had a
high-scoring PPHumanSeg mask and a YuNet face, but its CLIP crop emphasized the
aquarium and rejected person confirmation. `confirm_person` now accepts an
optional lazy face locator for otherwise unconfirmed substantial components.
Existing area/mean-score gates, the three-component bound, scene thresholds,
and the successful scene-only path remain unchanged. A score-qualified face
interior must cover at least 24 pixels on the segmentation grid and overlap
the same component by at least 85%. Other candidates do not inherit that face.

Face inference is invoked at most once per confirmation attempt, only when
eligible unresolved components remain. Disabled/missing/failed face inference
leaves scene-confirmed components intact. `PersonEvidence.face_regions` is an
additive count of face-confirmed components; `scene_agreement` retains only the
scene model's evidence. No lighting confidence is invented. `face_interiors`
shares the previous normalized ellipse geometry with local exposure metering;
its score, bounds, minimum area, and overlap behavior are unchanged there.

No model, prompt, weight, decoder, render kernel, or recipe schema changes.
Guided selection still only reduces the proposed mask, and saved layers replay
without inference. Face confirmation does not establish precise person/skin
boundaries: one group portrait still includes a foreground glass in its mask.
That example's Auto remains balanced, but manual mask refinement and better
occlusion handling remain open. A dark two-person image still lacks detected
faces, and another small subject fails the face/selection overlap check; this
increment does not silently loosen those downstream safeguards.

Source and packaging suites pass 804 tests, including 13 new
confirmation/geometry tests.
They cover strong versus weak/tiny/diffuse masks, independent components,
partial overlaps, invalid faces, missing scene confidence, disabled/failed face
inference, preservation of scene-confirmed components, and analysis wiring.

Both 63-photo CPU/GPU regressions retain their own baseline's global parameters
and every combined-edit preview pixel at 70/100%. Two photos gain confirmed
regions: a group with a second person and an uncertain-light pair. Neither
receives a new local Auto correction. Evidence/metrics are not byte-identical:
`face_regions` is additive everywhere, and the group meters two faces instead
of one. Reports: `output/{existing,unseen}-auto-face[-cpu]` and
`output/face-regression-comparison.json`.

A separate 16-photo mask audit changes only the aquarium subject, and six
low-light portraits were rendered on both backends. The aquarium case keeps its
measured low scene-crop agreement while gaining one face-confirmed region.
Person color protection removes the previously proposed global temperature/tint
shift and limits added saturation; local exposure is about +0.46 EV. Local color
abstains because material evidence is uncertain. Reports and inspected private
comparisons: `output/face-confirmation-{audit,extra}` and
`output/lowlight-people-{baseline,face,face-cpu}`. These are correlated development
captures, not a held-out aesthetic benchmark.

Eight full-native aquarium checks cover CPU/GPU, 70/100% global strength and
RGB8/RGB16. Compared with global-only rendering, subject exposure raises face
median luminance by about 0.04, preserves all 14,822,591 zero-weight background
pixels exactly, and introduces no tested new clipped or black channels. RGB8
native face crops match full rendering exactly; combined RGB8 results pass the
existing whole-image highlight/shadow proxy budgets. Source checksums are
unchanged. Evidence: `output/face-native/report.json`. This does not resolve the
previous tiny-highlight and tight moon-median sampling exceptions.

Real Tk workflows pass 55 checks each at 800x560 (GPU) and 1440x900 (CPU),
including independent selection, local Auto, mask inspection, stale-result
rejection, undo/redo, JPEG/TIFF16, and restored edits. Screenshots were inspected.
The batch workflow passes all 14 checks. Reports: `output/face-gui-{gpu,cpu}`
and `output/face-batch`.

For this 3712x5568 photo on the local RTX 5070 machine, separate initialized
desktop runs measure median slider-to-display latency of 46.3 ms GPU / 75.9 ms
CPU; the continuous 50-edit drag displays 49 / 41 intermediate frames. JPEG
export is 1.66 / 1.88 seconds. Auto before those drags takes 2.75 / 3.27 seconds,
including initial model setup. Camera preview appears at 240 / 215 ms, with
native RAW preview at 1.47 / 1.29 seconds. These are single-run observations,
not cross-photo performance guarantees or a measured speedup from this change.
Evidence: `output/face-live-{gpu,cpu}/benchmark.json`.

The refreshed local Windows EXE passes eight source-equivalence cases: aquarium
CPU/GPU, JPEG/TIFF8/TIFF16, the group and uncertain-light pair, a prior local-color
portrait, face inference off, and all optional models off. Auto parameters
match exactly, metrics within 1e-6, and exported/native-detail pixels exactly.
Face-off advice also matches its pre-change CPU baseline. Original hashes
remain unchanged. Reports: `output/face-frozen-*` and
`output/face-frozen-verification.json`. The existing optional TBB DLL packaging
warning remains; tested CPU paths work. Dependency, compile, and diff checks
pass. No public ZIP, model, or private photo was published.

### RAW Preview Failure Recovery

A mixed-light/night-color audit added 20 readable D500 photos and found one
unreadable copy with a valid embedded JPEG. Other same-named copies decode;
the failing copy has the same length but a substantially different compressed
tail. The native decoder reports an early bitstream end. This is not evidence
for a new unsupported encoding, and no original was repaired or rewritten.
Private comparisons are under `output/mixed-light-baseline`; eight existing
white-balance refinements were separately inspected under
`output/mixed-balance-baseline`. These are diagnostic baselines, not a measured
color improvement. No Auto, decoder, renderer, model, or recipe math changed.

`LiveFrame.preparation_failed` distinguishes source/initial-preparation failure
from invalid edits or later render/detail errors. A fast failure retains the
oriented camera JPEG even when it supersedes the temporary reference frame.
Only a failure for the current source, revision, and viewport disables RAW
editing. Existing generation checks still reject stale worker results.

The desktop clears stale native pixels/histograms, labels the camera-only view,
and shows a retry icon beside zoom. Auto, export, history, resets, and local
editing remain frozen, while parameters and saved sessions are retained. Retry
rereads metadata and prepares the source once; controls stay frozen until a
native frame arrives. Unsupported metadata, repeated failure, switching photos,
and missing files have explicit recovery states. Folder jobs keep their own
run identifier and failure accounting; their completion preserves the selected
photo's failure status. A transient edited/detail render error does not disable
an otherwise working RAW source.

Real Tk recovery checks pass 32 assertions each at 800x560 with automatic GPU
selection and 1440x900 with forced CPU. They cover the actual failed D500 copy,
failed retry, same-path recovery using an intentionally rewritten generated
fixture, retained edits, normal reimport, scene-adaptive Auto, JPEG/TIFF16, and
a mixed-validity folder job that exports one photo and reports one failure.
Original-photo hashes remain unchanged. Settled screenshots were inspected;
the error banner and retry icon fit both windows. Evidence is private under
`output/import-recovery-{gpu,cpu}`.

The existing 55-check real-Tk person-selection/Auto/history/comparison/export
workflow and 14-check batch workflow also pass. Evidence:
`output/recovery-desktop` and `output/recovery-batch`. No 63-photo Auto rerun is
claimed for this UI-only increment; the Auto/RAW code is unchanged.

Source and packaging environments both pass 821 tests, including 17 new
worker/UI tests for failure stages, stale results, retry, retained edits, and
batch ownership. The source pytest run also passes 2,046 subtests. The real
Windows Tk checks above complement GUI unit tests that skip without a display.
Both environments pass dependency checks; compilation and diff checks pass.

The refreshed local Windows EXE passes four source-equivalence cases: mixed
light on GPU/CPU, a face-corroborated aquarium subject, and optional models off.
JPEG/TIFF16 exports and native-detail pixels match the same-backend source
renderer exactly; Auto settings match exactly and metrics within 1e-6. Two
additional frozen checks reject the unreadable RAW with a nonzero exit and no
exports. All original hashes are unchanged. Reports:
`output/recovery-frozen-summary.json`, `output/recovery-frozen-*`,
`output/recovery-error-*`, and `output/recovery-frozen-verification.json`.
The actual packaged GUI was also opened on the failing photo, captured, and
closed cleanly; its camera-only label, disabled controls, and retry icon are
visible in `output/recovery-frozen-ui/unreadable-raw.png`. Functional retry
automation remains the source Tk audit, not the frozen CLI checks. The existing
optional TBB packaging warning remains; tested CPU paths work. No public ZIP,
model weights, generated derivatives, or private photos were published.

### Render-Fitted Exposure

The initial exposure estimate still uses the existing conservative -0.8/+1.2 EV
bounds. When the rendered correction remains dim, the recovery stage now fits
within the measured exposure range, bounded by the desktop's +2 EV maximum,
instead of trying only +0.4/+0.2 EV. The accepted initial correction remains a
fallback, including when no candidate offers a visible gain. Low-key or
predominantly dark scenes retain their exclusions and dark-intent checks.

Two existing highlight settings are considered. Each bounded interval search
uses at most seven checks and returns only a measured candidate, not an
unchecked midpoint. Cheap primary-proxy rejection precedes native/detail work.
Both whole-image domains must improve their median and original shadow
midtones; their scene-derived brightness target limits the lift. Native samples
protect clipping/tones but never supply a whole-image brightness target.

Promising candidates pass through existing measured white-balance and semantic
color refinement before adoption. The complete result must pass highlight,
shadow, and brightness checks at every integer strength from 1% to 100%, plus
any custom caller strengths. Checking after color allows a genuinely safe
combined correction while rejecting later color changes that invalidate it.
Color benefit itself keeps its earlier bounded strength samples. Existing
measurement caching avoids re-rendering identical checks across these stages.

This dense check applies to newly fitted exposure, not every unchanged fallback
or every possible continuous strength. It uses bounded proxies/native samples,
not exhaustive full-resolution QC. No scene model, prompt, decoder, render
kernel, or saved recipe format changes. Existing edits/export pixels change
only when Auto is run again. This remains a deterministic optimizer conditioned
on optional local model evidence, not learned aesthetic ranking.

The source suite passes 835 tests and 2,057 subtests, including fourteen new
interval/strength tests. They cover renderer-dependent fits beyond the old
initial cap, unchanged non-rendered estimates, negative intervals, invalid
anchors, bounded probes, detail-limited gain, no-gain pruning, clipping between
old strength samples, caller-supplied fractional strengths, and color-stage
ordering. Default pytest discovery now targets `tests`, excluding local ignored
third-party source trees rather than requiring their development dependencies.
The real-renderer tiny-highlight fixture explicitly disables optional scene
and person inference, budgets the dense strength grid, and checks the bright
pixels at low/intermediate/full strength. This prevents installed models from
hiding that tonal-only path. The additional all-CPU, all-models-off suite passes
829 tests and 2,055 subtests; six GPU-specific tests skip. This test-only follow-up
does not change the runtime algorithm or invalidate the photo/bundle comparisons.

The 33 existing, 30 additional, and 20 mixed-light readable samples total 83
distinct source hashes. Both backends change the same 25 global suggestions;
58 retain their own baseline's global/local parameters and every 70/100%
combined-preview pixel. Scene/person evidence remains unchanged. Most changed
comparisons are brighter; two have small decreases versus the earlier fixed
recovery, including a group with less highlight compression. These were visually
inspected, not declared universal aesthetic improvements. CPU/GPU global
parameters agree exactly on 80 photos; three differ by at most 0.0001 per
parameter. All scene labels agree, and combined previews differ by at most
2 RGB8 codes per channel. One additional unreadable copy still reports the
expected early-bitstream error; all source hashes remain unchanged.
Private reports: `output/{existing,unseen}-auto-fit[-cpu]`,
`output/mixed-light-fit-final[-cpu]`, and `output/fit-backend-comparison.json`.

In these sequential local runs, global Auto analysis has a median of 1.47 s
GPU / 2.63 s CPU and a maximum of 6.35 / 13.47 s. Those measurements exclude
RAW preparation, the precomputed person analysis, local advice, and output
saving; they are not UI latency or speedup claims. Dense fitted-strength checks
add meaningful CPU cost. Further analysis optimization remains open, separately
from the unchanged live-render/export algorithms.

All 294 full-native RGB8 checks pass on the 25 changed photos across CPU/GPU:
global strength 25/50/70/83/100%, plus combined subject layers at 70/100% where
present. Every checked native crop matches its full render, and original
hashes remain unchanged. Reports: `output/fit-native[-cpu]/report.json`.
These checks use the existing highlight/shadow budgets; they do not imply
zero clipping or resolve previously documented tiny-highlight/moon-median
exceptions in unchanged photos. The local-color desktop smoke option
`--expect-subject-color-abstain` now explicitly checks edit preservation for
uncertain samples instead of requiring a nonzero correction from every photo.

Real Tk workflows pass 70 checks each at 800x560/GPU and 1440x900/CPU on a
changed daylight portrait, including selection, Auto/strength, local color
abstention, manual local color, local exposure Auto, undo/redo, inspection,
JPEG/TIFF16, and restored edits. The batch workflow passes 14 checks. Settled
screenshots were inspected. Evidence: `output/fit-gui-{gpu,cpu}` and
`output/fit-batch`.

Separate initialized desktop runs on this 5568x3712 sample measure median
slider-to-display latency of 59.6 ms GPU / 77.6 ms CPU; a 50-edit drag displays
49 / 40 intermediate frames. JPEG export takes 1.55 / 1.93 s. Auto before the
drag takes 4.11 / 7.04 s, including model initialization and local advice.
Camera previews appear in 170 / 163 ms and native frames in 1.29 / 1.03 s.
These single-run observations do not establish a speedup or a cross-photo
performance guarantee. Evidence: `output/fit-live-{gpu,cpu}/benchmark.json`.

The packaging environment also passes all 835 tests. The rebuilt local Windows
EXE passes eight source-equivalence cases spanning changed D500/Z f portraits,
mixed light, an HE* sample, CPU/GPU, JPEG/TIFF8/TIFF16, local advice, and optional
models off. Auto parameters match exactly, metrics within 1e-6, and exported
pixels/native-detail crops exactly on the same backend. All source hashes are
unchanged. Evidence: `output/fit-frozen-*`, `output/fit-frozen-summary.json`, and
`output/fit-frozen-verification.json`. Both environments pass dependency checks;
compilation and diff checks pass. The existing optional TBB DLL packaging
warning remains; tested CPU paths work. No public ZIP, model weights, private
photos, or generated derivatives were published.

### Fused Auto Measurements

The rendered guard now measures clipping, newly clipped channels, highlight
detail loss, crushed shadows, and the soft near-black margin in one optional
Numba loop. Threshold arithmetic remains float32, soft shadow counts accumulate
in float64, and fastmath is disabled. Normalization, luma matrix multiplication,
masked shadow means, and neutral-color measurements retain NumPy's existing
semantics. Median partitioning reuses a private luma buffer only after all
position-dependent measurements finish. Input pixels and masks are not mutated.

This changes measurement cost, not candidate generation, semantic evidence,
color targets, rejection thresholds, or the fitted 1-100% strength checks.
The same guard also serves sampled color-noise validation. Missing/disabled JIT,
compilation failure, or unavailable disk cache retain checked fallbacks; failed
compilation is not retried for every candidate. Strided/read-only inputs share
one normalized compiled signature without changing the caller's write flags.

The Windows hook retains only the new decision kernel's source alongside the
existing native source bundle. Its content-checked cache is separately scoped
to reviewed decision kernels; it does not invalidate unrelated native kernels.
Tests exercise process restarts, EXE repacking, moving a bundle, source edits,
runtime flags, and unavailable cache storage. Packaged smoke reports now include
the Auto kernel's compiled status, fallback reason, cache locator, and hit/miss
counts.

An interleaved same-process benchmark on one expensive D500 street sample
compares the exact previous guard method with the fused implementation, after
warming both. Three measured runs per implementation give median global Auto
times of 5.205 to 3.739 s on GPU and 10.791 to 9.103 s on CPU. Every suggestion,
rationale, scene/person evidence, and metric is exactly equal. These roughly
28%/16% reductions are single-sample analysis observations, not guarantees for
other photos, total import latency, sliders, or export. Precomputed person
analysis and RAW preparation are excluded. Private reports:
`output/metrics-paired-{gpu,cpu}.json`.

An isolated empty-cache check on 614,400 pixels measures the new kernel's first
call at 0.416 s and warm median at 1.54 ms. This is statistics-only, not a full
Auto measurement. Evidence: `output/metrics-startup.json`.

All 83 distinct readable photos from the existing/additional/mixed-light cohorts
retain exactly the previous same-backend Auto parameters, rationale, metrics,
scene/person evidence, and local exposure/color advice on GPU and forced CPU.
Original and combined 70/100% preview pixels also match exactly. The additional
unreadable copy retains its early-bitstream error; all source hashes are unchanged.
Private evidence: `output/metrics-regression[-cpu].json`. No full-native 294-check
rerun is claimed for this measurement-only change; render math is unchanged.

These new sequential cohort runs measure median global Auto at 1.237 s GPU /
2.360 s CPU and maxima of 5.060 / 11.462 s. They exclude RAW preparation,
precomputed person analysis, local advice, and image saving. The older cohort
timings were not interleaved controls; use the paired benchmark above for its
single-sample speed comparison. Difficult CPU analyses still take seconds.

The source suite passes 848 tests and 2,183 subtests. The all-CPU, all-models-off
run passes 842 tests and 2,181 subtests, with six GPU-specific skips. Thirteen
new tests and expanded process-cache coverage exercise exact reference counts,
float32 threshold neighbors, RGB8/float data, shadow margins, original mask
coordinates, read-only/strided inputs, shared JIT signatures, concurrent calls,
disabled compilation, cache retry/failure, and unchanged guard caching. Source
dependency, compilation, and diff checks pass.

Real Tk workflows pass 70 checks each at 800x560/GPU and 1440x900/CPU, including
person selection, Auto/strength, local-color abstention, manual/local edits,
undo/redo, native inspection, JPEG/TIFF16, and restored sessions. The batch
workflow passes 14 checks. Settled Auto screenshots were inspected at both
sizes. Evidence: `output/metrics-gui-{gpu,cpu}` and `output/metrics-batch`.

The separate 5568x3712 desktop benchmark records median slider-to-display
latency of 59.1 / 112.9 ms, 45 / 26 displayed frames during a 50-edit drag,
and JPEG export at 2.19 / 2.87 s for GPU / CPU. Auto before the drag, including
model setup/local advice, takes 4.61 / 9.05 s. These single-run end-to-end
observations vary from the previous run and do not establish a desktop or
export speedup. Source hashes remain unchanged. Evidence:
`output/metrics-live-{gpu,cpu}/benchmark.json`.

The packaging environment also passes all 848 tests. The rebuilt local EXE
passes eight same-backend source-equivalence cases across D500, Z f, HE*,
CPU/GPU, JPEG/TIFF8/TIFF16, local advice, and optional models off. Auto parameters
match exactly, metrics within 1e-6, and export/native-detail pixels exactly.
All eight use compiled Auto counts without fallback/cache errors; the first
process records a cache miss and the remaining seven record hits through the
content-checked locator. Original hashes remain unchanged. Evidence:
`output/metrics-frozen-*`, `output/metrics-frozen-summary.json`, and
`output/metrics-frozen-verification.json`.

The existing optional TBB DLL packaging warning remains; tested CPU paths work.
Both environments pass dependency checks. No public ZIP, model weights, private
photos, or generated derivatives were published.
