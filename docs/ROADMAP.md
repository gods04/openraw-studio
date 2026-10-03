# Roadmap

## Current Desktop Milestone

Implemented: photo-first editor, continuous GPU/CPU preview, guarded Auto with
strength control, original RAW comparison, undo/redo, local edit retention,
manual and sampled Auto color-noise reduction, native-pixel 100%/200% inspection, JPEG/TIFF export, and cancellable per-photo
batch modes. Supported compressed
Nikon paths include F-series lossless, 12-bit D20 non-split (verified on 1 J5),
verified Z5 12/14-bit lossless FX, and the verified Z f HE/HE* 14-bit profile
with approximate nonlinear mapping.
Local Windows EXE builds are available; this is not a public installer release.

Next quality work: Auto under difficult lighting, broader camera calibration
and gamut handling, edge-aware demosaic, actual sensor highlight reconstruction,
and sensor/luminance denoise. Further first-use initialization and broader verified HE profiles
also remain open.
Semantic portrait/scene AI and mobile remain later phases.

Latest decoder increment: the public Z f HE sample matches all 24,498,560
nonlinear Bayer values against the isolated development reference, with at most
1 DN difference in the approximate linear mapping. HE* and ordinary Z f lossless
public samples also pass. Both Z5 lossless bit depths match all 24,353,280 sensor
samples exactly; 12-bit black normalization is corrected to 252, and an exact-model
DNG 13.2 camera calibration is added. All five files pass native Auto and full-size
JPEG export with unchanged sources. Z5 D40 lossy, other HE profiles, and other
Z5 crop/firmware variants still need verification. See the public sample IDs,
hashes, and reference boundaries in `DEVELOPMENT.md`.
All 431 tests pass with GPU and CPU-only (four GPU-only skips). Two actual desktop
workflows pass 24 checks each. The refreshed EXE verifies exact recipe/native-detail
agreement with source execution and unchanged originals. HE GPU export is 1.38 s;
the Z5 CPU case is 2.86 s after restart with cached compilation. Live edits measure
59 / 72 ms median on those different HE GPU / Z5 CPU samples, while first RAW
display still takes 4.93 / 2.29 s. No public ZIP or installer is released.

Earlier quality increment: a separate `Auto color noise` wand command analyzes
retained native Nikon regions at the current tones. It changes only the noise
amount, requires measured benefit and tone/color guards, and retains existing
settings when evidence is insufficient. It is not semantic texture recognition,
a calibrated sensor-noise model, or automatic denoise in toolbar/batch Auto.
All 427 tests pass with GPU and CPU-only (four GPU-only skips). Across 26 private
D500/Z f/1 J5 captures, advice agrees between CPU/GPU and all sampled pixels match
full export. The 23 existing tonal Auto suggestions remain unchanged. At five
tone settings per photo (130 cases), all 15 nonzero recommendations pass the
full-size clipping/shadow budgets; originals are unchanged. Compact-window
workflows verify accepted, zero, abstained, and unavailable results, undo/redo,
saved edits, JPEG/TIFF, and stale-result rejection. Noise advice takes 0.27 s for
the D500 high-ISO CPU example and 0.20 s for the Z f GPU example. These are local
measurements, not universal latency or full-image quality guarantees. The visible
improvement remains mild; luminance grain and large color blotches are unresolved.
The refreshed local EXE verifies positive and abstained advice, matching recipes
and exact native-detail pixels, with unchanged originals. Cached CPU advice is
0.15 s; two GPU samples take 0.06-0.08 s, excluding import and EXE startup. First
CPU use takes 0.65 s. No public ZIP or installer was generated or released.

### Earlier Verification

Optional `Adjust > Detail > Color noise` filters
rendered color speckles on GPU, compiled CPU, or the NumPy fallback. Zero is an
exact bypass and the default for old recipes. History, local saved edits,
JPEG/TIFF, native inspection, and batch modes retain the setting; Auto preserves
the manually selected amount without estimating noise. All 414 tests pass with
GPU and CPU-only (four GPU-only skips). All 26 D500/Z f/1 J5 real-photo checks
retain original hashes, stay within one 8-bit level across CPU/GPU, and match
native regions to full export. A separate full-frame check at strength 75 stays
within the existing clipping/shadow budgets. This is sample evidence, not a
universal highlight-preservation guarantee. The filter is not sensor-domain,
luminance, AI, or ISO-adaptive denoise; low-contrast color texture can soften,
while large color blotches and luminance grain remain. Fit is approximate.

Actual compact-window checks cover slider input/displayed pixels, undo/redo,
Auto, edit retention, JPEG/TIFF, native detail, and all batch modes. With color
noise at 75, separate source benchmarks measure 49 ms median slider response
and 2.11 s export on Z f GPU; D500 CPU measures 113 ms and 4.36 s. The filter
adds work, especially on CPU; these are local timings for different photos,
not a direct hardware comparison or universal performance promise.
The refreshed local EXE verifies saved amounts, exact native-detail pixels,
unchanged originals, and cached CPU compilation. With color noise at 75, warm
export measures 4.48 s D500 CPU and 1.64 s Z f GPU. First-use compilation still
adds latency; no public ZIP or installer was generated or released.

Supported compressed Nikon full-size export and
100%/200% inspection use MHC gradient-corrected demosaic on GPU, compiled CPU,
and the NumPy fallback. Full-size renders for 26 D500/Z f/1 J5 captures match
the reference within one 8-bit level; native inspection matches export pixels.
Auto adds bounded native-sample checks, addressing small highlights and high-ISO
shadow loss hidden by proxy averaging. All 23 selected D500/Z f files pass the
existing clipping/shadow budgets at four strengths and three resolutions (276
checks). Seven suggestions change, 16 retain their settings, and originals are
unchanged. All 397 tests pass on GPU and CPU (three GPU-only skips), alongside
editing, JPEG/TIFF export, native inspection, and batch workflows. This is linear
detail reconstruction and sampled tonal protection, not denoise, semantic AI,
exhaustive quality validation, or broader RAW support. Fit previews stay fast.
The refreshed local EXE also verifies Auto, export, native inspection, and the
two corrected outliers with unchanged originals. Warm D500 CPU export is 2.37 s;
Z f GPU export is 1.49 s on this machine. First-use compilation still adds delay.

Highlight-limited Auto tries a bounded shadow lift
and requires measurable benefit at both proxy resolutions before accepting it.
Low-key and predominantly dark scenes are excluded. Six of the 23 selected
D500/Z f suggestions gain shadow detail; the other 17 retain their settings.
All 23 Auto/full-size exports and 4,600 strength/proxy checks pass with unchanged
originals. The six changed samples also pass full-resolution clipping/shadow
checks at 70% and 100%. All 376 tests pass on GPU and CPU (two GPU-only skips),
plus actual editing/JPEG/TIFF, native-detail, and batch workflows. Real desktop
Auto measures 0.64 s D500 CPU and 0.80 s Z f GPU. This is global tonal refinement,
not object recognition, ISO-aware denoise, or general image-quality completion.
The refreshed EXE verifies matching Auto settings, full-size export, and native
inspection on D500 CPU and Z f GPU without modifying originals. Further quality
work and broader verified Nikon support remain open.

HE horizontal synthesis reuses bounded work buffers,
reducing first-call compilation from 2.17 to 0.53 s in a controlled empty-cache
profile. CPU RAW preparation falls from 5.41 to 3.82 s there; warm throughput is
essentially unchanged. All 367 tests pass with GPU and CPU-only (two GPU-only
skips), including scratch boundaries, concurrent use, and cache/compiler fallback.
All 23 private D500/Z f files retain identical metadata, sensor bytes, 69 previews,
and 23 full-size renders, with unchanged originals. Actual cold-cache Z f desktop
checks measure 5.24 s first RAW preview on CPU and 4.84 s on GPU, then 62/46 ms
median slider response. Editing, JPEG/TIFF, native inspection, and batch checks
pass. These are local measurements, not universal or instant-startup guarantees;
other initialization costs and image-quality work remain.
The refreshed EXE verifies cold/warm preparation at 4.38/1.98 s on GPU and
4.31/1.82 s on CPU, excluding executable startup, with no compiler/cache fallback.
Z f Auto, full-size export, and native inspection pass; GPU export stays near 1.4 s.

TIFF binary fields stay compact instead of repeatedly
expanding and converting integer tuples; numeric fields use checked bulk parsing.
All 363 tests pass on GPU and CPU-only (two GPU-only skips). The 6,847-file metadata
inventory matches its earlier results, including six rejected containers; this
does not imply all those files were decoded. The 23-photo D500/Z f set retains
identical metadata, sensor data, 69 previews, and 23 full-size renders. Three
additional 1 J5 decode/full-render comparisons pass with unchanged sources.
The same D500 desktop export improves from 2.65 to 2.38 s CPU and 1.64 to 1.36 s
GPU. Desktop editing, JPEG/TIFF, native inspection, and batch checks pass.
First-use compilation remains separate work; no format support or Auto behavior
was expanded. These are local sample timings, not universal performance promises.
The refreshed EXE verifies compiled CPU/GPU operation and restart cache reuse;
D500 CPU export is 2.34-2.53 s warm and Z f GPU export 1.42 s. GPU HE* preparation
still takes 6.34 s on first compilation and 2.24 s after restart.

Vectorized Nikon preview tables and bounded RGB
histogram/QC reduce the same D500 desktop export from 3.11 to 2.65 s on CPU and
1.95 to 1.64 s on GPU. All 69 previews and histograms across 23 private D500/Z f
photos match the retained scalar references exactly, with unchanged originals.
All 353 tests pass with GPU and CPU-only (two GPU-only skips), plus desktop
editing/JPEG/TIFF, native-detail, and batch workflows. Live dragging measures
73 ms median on CPU and 53 ms on GPU; this increment targets export overhead,
not a new Auto algorithm or camera profile. Timings are local sample results.
First HE* import and repeated MakerNote conversion remain optimization targets.
The refreshed EXE passes D500 CPU and Z f CPU/GPU export/detail checks, including
restart cache reuse. D500 CPU export is 2.52 s warm; Z f GPU export is 1.56-1.62 s.

Compiled, bounded-memory CPU Bayer interpolation reuses
the fused tone kernel for full-size exports and native detail. On the same D500
desktop benchmark, CPU export improves from 5.09 to 3.11 seconds without changing
dimensions or export quality. All 69 full-resolution comparisons across 23 private
D500/Z f photos stay within 1 DN of the NumPy reference; source hashes are unchanged.
All 344 tests pass with GPU and CPU-only (two GPU-only skips), plus actual
JPEG/TIFF editing, native-detail equality, and batch workflows. The matching GPU
benchmark remains at 1.95 s export. This retains bilinear quality and the existing
format guards, not new camera support, denoise, or edge-aware interpolation.
The refreshed EXE verifies CPU kernel activation and restart cache reuse:
D500 export is 3.49 s on first compilation and 3.05 s warm; Z f CPU/GPU export
and native inspection also pass. First HE* import remains unfinished work.

Fused CPU live tones reduce repeated full-frame memory
passes without shrinking the preview. The same D500 desktop benchmark improves
from 109 to 62-72 ms median response and from 16 to 48 displayed frames during 50
edits. CPU full-size export remains about 5 seconds and is not changed here.
Across 23 private photos and seven adjustment cases, compiled output stays within
1 DN of the NumPy reference and all Auto suggestions are identical. Original
hashes remain unchanged. All 333 tests pass on GPU selection and CPU-only
(two GPU-only skips). These are sample-specific results, not universal timing
or pixel-equality guarantees. NumPy remains the compiler-failure fallback.
The CPU path also passes 4,600 Auto guard checks across every integer strength
and both proxy sizes, plus the actual 16-step desktop edit/JPEG/TIFF workflow.
All 22 native-detail workflow checks pass on CPU; the GPU regression measures
53 ms median preview response and 2.08 s cached export on the same D500 sample.
The refreshed local EXE verifies compiled CPU tones and cache reuse after restart;
D500 sea Auto measures 0.62-0.69 s. Z f GPU export/detail remains working.

A continuous negative-highlight shoulder retains distinctions
above display white instead of applying a constant offset and clipping again.
CPU, GPU, generic DNG, and Nikon preview/export paths share the curve. Auto now
checks intermediate strengths for corrections relying on highlight compression;
an endpoint-only check missed a real portrait regression at 25-70% strength.
This is display-tone compression, not reconstruction of saturated sensor data.
All 23 selected D500/Z f Auto/full-size exports pass with unchanged source hashes.
A wider audit passes all 4,600 checks at integer strengths 1-100% and both proxy
sizes. This remains sample-specific evidence, not a full-resolution guarantee.
GPU live dragging with Highlights at -1 measures 59 ms median; CPU fallback is
slower at 109 ms after limiting shoulder evaluation to over-white pixels.
All 322 tests pass with GPU selection and CPU fallback (two GPU-only skips).
The refreshed local EXE passes Z f GPU and D500 CPU-only export/detail checks.

True 100%/200% detail inspection uses bounded native
Nikon sensor regions, with an interpolation halo matching full-resolution export.
It retains original comparison, live adjustments, click-centered zoom, and pan;
Auto continues to analyze the whole photo. Real D500 and portrait Z f desktop
checks verify exact displayed pixel pitch and equality with full RAW renders.
That inspection increment passed its 313-test baseline on GPU and CPU.
Native inspection does not add new format support or improve demosaic quality.

Auto validates promising corrections at 960 pixels
as well as the 256-pixel analysis size. All 184 checks across 23 selected photos,
two resolutions, and four strengths pass; the six earlier detail-budget failures
are resolved at those tested settings. Both guards cache candidate measurements.
Single-photo Auto reuses the unedited live proxy with source-change/invalidation
checks; desktop, batch, validation, and packaged smoke paths use the same helper.
The Auto increment passed its 304-test baseline with GPU and CPU fallback.
Real museum Auto improved from 0.62 s to 0.43 s; the difficult sea sample takes
0.89 s. Both desktop workflows and batch cancellation checks pass. These remain
preview-resolution heuristics, not full-resolution or semantic guarantees.
Nine Z f HE* streams match an independent development
oracle through entropy, wavelets, and nonlinear Bayer reconstruction. The computed
two-sided quadratic mapping differs by at most 1 DN in all nine 14-bit linear
sensor planes; this is an approximation, not bit-exact Nikon curve reproduction.
All nine native Auto/export runs pass with unchanged sources. A real desktop
workflow passes 16 checks including JPEG/TIFF export, history, and edit persistence.
The subsequent transform optimization preserves every sensor-buffer hash across
the nine samples and reduces steady decode from about 3.2 s to 1.3 s. One local
RTX 5070 desktop run measured 58 ms median slider latency, 2.02 s export, and
2.90 s first native preview. The Windows bundle now retains compiled kernels
across process restarts: preparation measured 1.90-2.14 s after a 6.35 s initial
compile. First-use loading still needs improvement.
Unknown cameras, HE modes, black levels, and stream profiles remain blocked.

## Phase 0 - Foundation

Status: in progress.

Goals:

- repository structure
- product and architecture documentation
- interface contracts for major engines
- versioned recipe schema
- processing preset and creative look formats
- V0.1 implementation plan
- model/license tracking policy
- developer CLI skeleton
- dry-run recipe/artifact planning
- UI design direction
- OpenRAW render-engine strategy
- OpenRAW Native RAW engine scaffold

Acceptance:

- docs describe product, architecture, pipeline, roadmap, and licensing
- contract tests import the engine interfaces
- schemas and example presets are valid JSON
- no model weights or private photos are committed
- `openraw process --dry-run` writes a recipe sidecar
- default RAW engine identity is `openraw-native`
- `openraw process --preview-only` writes a native preview for supported narrow uncompressed 12/14/16-bit DNG files and guarded Nikon sensor files

## V0.1 - Pipeline Proof

Goals:

- process one RAW file from a CLI or simple desktop shell
- inspect metadata
- generate preview
- run basic portrait vs non-portrait and scene heuristics
- create recipe JSON
- render base image through a replaceable RAW backend
- export JPEG

Suggested implementation order:

1. keep `darktable-cli` optional and developer-facing
2. expand OpenRAW Native DNG-first metadata extraction
3. expand native DNG pixel extraction beyond simple uncompressed strips
4. [done] add first-pass DNG white balance and color-matrix conversion to the preview
5. [done] expand preview-derived native JPEG export into a proper export stage
6. improve preview dimension and color-space metadata
7. [done] add export writer abstraction for final derivatives
8. add command-line smoke flow documentation using a local RAW file
9. add fixture-free tests around recipe, decisions, and artifact paths
10. add source discovery and import-folder watcher contract
11. [done] add locally generated synthetic DNG/Nikon NEF files for safe smoke tests
12. [done] add first Windows ZIP packaging workflow
13. [done] add basic exposure, contrast, warmth, and saturation controls to preview/export recipes
14. [done] split desktop preview refresh from final JPEG export
15. [done] mark desktop previews stale when adjustments change
16. [done] add conservative desktop Auto Adjust starter action
17. [done] show selected-photo info and planned output paths in the desktop app
18. [done] restore basic desktop adjustments from saved recipe sidecars
19. [done] add native extraction for simple uncompressed tiled DNG payloads
20. [done] add Native support reporting through CLI and desktop photo info
21. [done] add desktop folder import with a lightweight photo list
22. [done] add batch folder export through CLI and desktop app
23. [done] add Nikon NEF/NRW metadata import and import-only desktop state
24. [done] add first Nikon NEF/NRW embedded preview extraction
25. [done] add first native Nikon NEF/NRW sensor decode path for guarded TIFF-style uncompressed payloads
26. [done] add row-aligned 12/14-bit packed Bayer strip decoding for guarded DNG/Nikon payloads
27. [done] parse Nikon MakerNote compression metadata for 34713 render blockers
28. [done] add first Nikon 34713 lossless Huffman sensor decode and half-resolution render path
29. [done] improve Nikon 34713 black-level handling and Python render speed
30. [done] add Nikon 34713 camera-aware color and full-resolution bilinear export (D500 color complete)
31. add support for more compressed Nikon NEF sensor payload variants
32. [done] add region-weighted highlights and shadows controls to preview/export recipes and Auto Adjust
33. [done] add normalized temperature and green/magenta tint controls with legacy warmth compatibility
34. [done] add rendered-preview RGB/luminance histogram and clipping feedback that follows Before/After view
35. [done] replace direct DNG ColorMatrix1 multiplication with inverse camera-to-XYZ, Bradford adaptation, and linear-sRGB conversion
36. [done] compare native Nikon 34713 results with their camera-authored embedded JPEG without a second sensor decode
37. [done] add Nikon D500 MakerNote black level, standard as-shot white balance, exact camera profile, and EXIF orientation
38. [partial] add edge-aware demosaic and lossless TIFF export (8-bit TIFF done; edge-aware and 16-bit linear output pending)
39. [done] preserve safe camera/capture metadata in JPEG/TIFF derivatives without GPS passthrough
40. [done] publish previews/exports/recipes atomically and show desktop processing progress
41. [done] record advisory rendered-preview clipping QC and surface desktop warnings
42. [done] vectorize Nikon previews and reuse decoded sensor/current preview state in the desktop workflow
43. [done] compile the native Nikon decoder, auto-select OpenCL GPUs with CPU fallback, and update in-memory previews continuously while dragging

Exit criteria:

- one public or local test RAW can be processed end to end
- generated synthetic DNG/Nikon NEF samples can be created locally for onboarding smoke tests
- a Windows ZIP package can be built by maintainers
- original RAW remains byte-identical
- export and recipe are written
- recipe can be reloaded and traced to engine versions

## V0.2 - Face and Segmentation Foundation

Goals:

- face detection
- facial landmarks
- person segmentation
- coarse face/body/hair/background mask references
- multiple face IDs
- adaptive face exposure
- basic skin color handling

Exit criteria:

- faces are tracked by stable run-local IDs
- small distant faces receive conservative processing
- portrait operations can be disabled independently
- model licenses are documented before bundling

## V0.3 - Portrait Editing

Goals:

- skin smoothing with texture preservation
- skin brightening in perceptual color space
- eye enhancement
- teeth whitening when masks are reliable
- face slimming and eye sizing through controlled warps
- strength controls per face

Exit criteria:

- edits are mask-aware
- geometry warps use landmarks and smooth falloff
- obvious background distortion is flagged or prevented

## V0.4 - Scene-Aware Color and Looks

Goals:

- stronger scene intelligence
- adaptive color targets
- golden hour, night, indoor, landscape, city, aquarium, and astro profiles
- creative look presets
- LUT import contract

Exit criteria:

- skin protection works during grading
- low-confidence scene analysis produces conservative color changes
- processing presets remain separate from creative looks

## V0.5 - Film Engine

Goals:

- film profile format
- tone curve behavior
- color response
- grain
- halation
- bloom
- density

Exit criteria:

- grain is resolution-aware
- LUT processing stays separate from grain, halation, bloom, and tone behavior
- film strength is reproducible from the recipe

## V0.6 - QC, Camera Profiles, Performance

Goals:

- artifact detection
- noise score
- camera profile format
- GPU/CPU runtime selection
- performance profiling

Exit criteria:

- QC reports are saved with recipe/export metadata
- noisy and underexposed images receive different denoise decisions
- backend runtime can fall back cleanly

## V1.0 - Usable Desktop Application

Goals:

- import RAW photos
- AUTO processing
- portrait enhancement controls
- creative look controls
- film simulation controls
- before/after comparison
- JPEG/TIFF export
- complete non-destructive recipe retention

Exit criteria:

- beginner workflow is usable without tuning
- advanced controls are available without breaking AUTO
- reference regression set can be rerun across releases

## Future - Mobile Exploration

Mobile support is intentionally later than V1.0 desktop foundations. When the
desktop workflow is proven, evaluate:

- mobile companion app
- responsive shared design system
- local device processing limits
- optional handoff between desktop and phone
- privacy and storage model for mobile imports/exports
