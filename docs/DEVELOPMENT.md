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
