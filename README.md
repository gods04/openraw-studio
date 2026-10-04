# OpenRAW Studio

[![CI](https://github.com/gods04/openraw-studio/actions/workflows/ci.yml/badge.svg)](https://github.com/gods04/openraw-studio/actions/workflows/ci.yml)

OpenRAW Studio is an open-source, local-first computational photography app in
early development.

The goal is simple to say and hard to build well:

```text
Import RAW photo -> AUTO analyze -> non-destructive recipe -> processed export
```

The long-term product should feel like a desktop RAW editor with computational
photography, portrait-aware enhancement, scene-aware color, film looks, and
before/after comparison. The important rule is that original RAW files are never
modified.

## Can I Download The App Yet?

There is not a polished installer yet, but you can open the early local desktop
app from this repository. The repo also contains the first Windows ZIP packaging
workflow for maintainers.

On Windows, clone the repo and run:

```powershell
.\scripts\run_app.ps1
```

Or double-click:

```text
scripts\run_app.cmd
```

The script creates `.venv`, installs OpenRAW Studio locally, and opens the app.
When release builds are published, this section will link to GitHub Releases
with a normal Windows download.

Maintainers can build a local EXE with `.\scripts\build_windows.ps1 -SkipZip`,
then open `dist\OpenRAW Studio\OpenRAW Studio.exe`. Keep the adjacent `_internal`
folder with it. This local build does not publish a GitHub release.

To try the app without using a private photo, open `Library` > `Sample photos`
and choose a sample DNG or NEF. You can also generate the same tiny
synthetic samples from the command line:

```powershell
python scripts\create_sample_dng.py
python scripts\create_sample_nikon_nef.py
.\scripts\run_app.ps1
```

Then import `sample-data\openraw-synthetic.DNG` or
`sample-data\openraw-synthetic-nikon.NEF` in the app.

## What Works Today?

- Project documentation for the product, architecture, roadmap, and pipeline
- Open-source MIT license for the app code
- Versioned processing recipe schema
- Processing preset and creative look formats
- Modular Python interfaces for RAW, vision, decision, portrait, color, film,
  QC, export, model runtime, and pipeline layers
- Example presets for `general`, `portrait`, `clean`, and `warm_film`
- `openraw doctor` environment check
- `openraw inspect` support report for one RAW file
- `openraw process --dry-run` recipe/artifact planner for one RAW-like file
- `openraw batch` folder export for currently renderable DNG/Nikon files
- OpenRAW Native RAW engine scaffold as the default backend
- Native DNG/TIFF metadata reader with compact binary fields and batch numeric
  parsing; inspection and recipe metadata remain JSON-compatible
- Nikon `.NEF` / `.NRW` metadata import and embedded JPEG preview extraction
- Nikon MakerNote summary for compressed NEF render blockers, including
  0x0096 compression-table and 0x008c curve/table detection
- Native Nikon 34713 lossless Huffman sensor decoding with optional Numba
  compilation of OpenRAW's own decoder, MakerNote black levels with inactive-border
  fallback, and full-resolution RGB export for supported files
- Native Nikon 1 J5 12-bit D20 non-split compressed NEF decoding, linearization,
  corrected black levels, exact camera profile, and full-size JPEG/TIFF export
- Verified Nikon Z5 12/14-bit lossless and D40 non-split lossy sensor decoding, camera color calibration,
  corrected 12-bit black levels, and 6016 x 4016 export on the tested FX samples
- Native Nikon Z f HE/HE* 14-bit decoding for the verified profile, with real RAW
  adjustments and full-size JPEG/TIFF export. Its computed nonlinear mapping is
  approximate: at most 1 DN difference against the development reference in
  eleven tested sensor planes. Other HE profiles are not implied to be supported.
- Prebuilt, OpenRAW-owned HE CPU kernels avoid first-use HE compilation in the
  Windows app; Numba and NumPy/Python remain available as source-install fallbacks
- Automatic OpenCL GPU selection for live adjustments and full-resolution Nikon
  rendering, with CPU fallback when a GPU/driver is unavailable
- Fused, cached CPU compilation for live preview and Auto when no GPU is used;
  the NumPy renderer remains available if compilation fails
- Chunked CPU compilation of full-resolution Bayer interpolation and color for
  supported compressed Nikon exports and native-pixel inspection
- Bounded parallel CPU strips for large compiled renders and both noise filters;
  slider previews and compiler fallback remain serial, with unchanged pixel math
- Reused, bounded CPU workers for Auto's larger preview candidates when no GPU
  is used, retaining the same adjustment search and rendered quality checks
- MHC gradient-corrected interpolation for full-size Nikon export and native
  detail, with matching GPU, compiled CPU, and NumPy paths; the Fit proxy stays fast
- Vectorized Nikon preview lookup construction and bounded-memory RGB histogram/QC,
  retaining the scalar fallback and existing preview/clipping behavior
- Camera-aware Nikon D500, 1 J5, Z5, and Z f rendering with standard as-shot white balance, an
  exact camera-to-linear-sRGB profile, and EXIF orientation handling
- Native extraction for simple uncompressed DNG 12/14-bit packed strip payloads
  and 16-bit strip/tile pixel payloads
- Guarded native Nikon `.NEF` / `.NRW` sensor decode for TIFF-style
  uncompressed Bayer payloads
- Native black/white level normalization for 12/14/16-bit Bayer sensor data
- Native simple Bayer demosaic baseline
- Native PNG preview encoding for narrow uncompressed DNG/Nikon test files
- Local JPEG quality control and lossless 8/16-bit RGB TIFF export with recipe traceability
- Atomic preview, derivative, and recipe publication so interrupted writes do not
  replace an existing complete result with a partial file
- Safe derivative metadata for camera, lens, capture settings, date, orientation,
  sRGB, and OpenRAW software identity; GPS is not copied
- Advisory rendered-preview QC records sampled highlight/shadow clipping in the
  recipe and surfaces non-blocking desktop warnings
- Beginner desktop shell launched with `openraw app` or `scripts/run_app.ps1`
- App single-photo import, folder import, output-folder selection, conservative Auto Adjust,
  exposure/contrast/highlights/shadows/temperature/tint/saturation adjustments,
  rendered-preview RGB/luminance histogram with clipping feedback, before/after comparison, built-in
  sample DNG creation, photo/output information display, batch folder export,
  visible single-photo/batch processing progress, final dimensions/file size,
  and direct output opening
- Desktop single-source decode caching plus current-preview reuse, so adjustment
  refreshes and export do not repeatedly decode or regenerate unchanged work
- Automatic, in-memory live preview during slider dragging, with one replaceable
  pending edit instead of a growing render queue; no refresh button is required
- Failed RAW preparation retains a labeled camera JPEG when available, freezes
  unavailable RAW edits/export without discarding saved adjustments, and exposes
  a retry icon beside zoom. A successful retry restores native editing; folder
  export still reports individual failures and continues with readable files.
- Photo-first workspace with Adjust/Library/Export/Info tabs, original-RAW
  comparison, native 100%/200% detail inspection, zoom/pan, undo/redo, and automatic
  local edit retention
- Auto strength control, luminance-aware exposure, conservative color correction,
  black-preserving shadows, and rendered-highlight checks that also guard small
  bright subjects and individual color channels at two preview resolutions;
  joint exposure/contrast/highlight recovery retains useful corrections on difficult dim scenes
- Auto checks substantial shadow midtones so a brighter background cannot hide
  contrast-darkened subjects, and can reduce added saturation to retain useful exposure
- Auto can refine a spatially consistent near-neutral color cast using measured
  renderer response. Color benefit, highlights, and shadows are rechecked before
  accepting it; uncertain or dim color-dominated scenes retain the prior correction.
- Highlight-limited Auto can add a bounded shadow lift, checking visible benefit
  and clipping at both preview sizes; low-key and predominantly dark scenes are excluded
- Still-dim Auto results fit exposure to the photo's measured response instead
  of choosing a fixed lift. Optional highlight compression and color are checked
  together at every integer Auto strength before accepting a fitted correction;
  analysis/display proxies and available native samples retain clipping guards
- Optional `Detail > Color noise` and `Luminance noise` controls, with GPU/CPU
  fallback, live preview, undo/redo, saved edits, and JPEG/TIFF export. Both default
  to off; Auto preserves manual amounts instead of guessing a noise level.
- Separate `Auto color noise` wand button beside the Detail slider. It checks
  retained native Nikon samples at the current tones, applies a bounded amount
  only after validating benefit, and leaves settings unchanged without evidence.
- Supported compressed Nikon Auto also checks bounded original-size samples with
  the export renderer, catching some small highlights/noisy shadows hidden by
  preview averaging. Sampling is not an exhaustive full-image quality guarantee.
- Smooth negative-highlight roll-off preserves distinctions above display white;
  Auto also checks intermediate strengths for highlight compression and contrast
  applied to substantial shadow midtones
- Folder export using current adjustments, per-photo saved edits, or Auto per
  photo; stopping finishes the current photo and cancels the remainder
- Saved recipe detection that restores basic desktop adjustments for the same
  photo
- Synthetic DNG/Nikon NEF generators for safe local smoke tests
- Windows ZIP build script and GitHub Actions packaging workflow
- Contract tests for the initial foundation

## What Does Not Work Yet?

- No packaged installer yet
- OpenRAW Native has a single-illuminant DNG white-balance and `ColorMatrix1`
  conversion to linear sRGB; dual-illuminant interpolation, `ForwardMatrix`, and
  gamut mapping are not implemented yet
- OpenRAW Native has a first optimized Nikon 34713 lossless decode/render path,
  with a half-resolution preview and full-resolution MHC final export.
  Camera profiles are provided for the D500, 1 J5, Z5, and Z f, with generic color for other models;
  first-use compiler/GPU initialization still adds latency, while edge-aware
  demosaic quality, further speed work, and more profiles remain
- Other compressed/proprietary Nikon `.NEF` / `.NRW` sensor payload variants
  are not decoded yet. J5 D20 support is limited to 12-bit, non-split streams;
  HE/HE* support is restricted to the verified Z f 14-bit profile with matching
  stream configuration and black levels. Other HE/HE* profiles and D20/D40
  split-row streams remain unsupported for RAW editing/export. Z5 verification
  covers 12/14-bit lossless and D40 non-split lossy FX samples, not every
  crop mode, firmware, or the Z5 II. Check each file with
  `openraw inspect`.
- Broad proprietary RAW rendering support is not implemented yet
- TIFF supports 8/16-bit rendered sRGB; linear/working-space TIFF is not implemented yet
- QC currently checks rendered 8-bit preview clipping only; sensor-domain
  headroom, sharpness, calibrated noise, and color-accuracy checks are not implemented yet
- No AI model weights included
- Noise reduction uses local filters on rendered RGB8, not RAW sensor-domain,
  ISO-adaptive, or AI denoise. Color noise preserves brightness; optional
  luminance smoothing reduces grain but can soften low-contrast texture.
  Larger color blotches and some grain remain. Inspect at 100%/200%.
- Auto color noise is sampled rendered-chroma advice, not a calibrated noise
  model or semantic texture recognition. It currently requires native compressed
  Nikon samples; generic DNG and preview-only files do not gain automatic advice.
- Auto retains its deterministic tonal/clipping guards. An optional local CLIP
  scene model now conditions a measured color refinement on scene and lighting
  evidence. This is an experimental hybrid, not a trained aesthetic editor or
  sensor highlight recovery. It cannot reconstruct clipped detail.
  Render-fitted exposure adds analysis work, especially on CPU; Auto analysis
  runs separately from cached slider rendering and export.
- Positive contrast is fitted against each image's rendered shadow limits,
  including near-black detail. These remain sampled guards, not a guarantee
  that every original-resolution pixel is protected at every Auto strength.
- Corroborated dark lighting can set a relative brightness-lift budget from
  the original tones, checked during actual rendering. Almost-black previews
  without enough visible structure abstain; the desktop retains manual edits.
  Very dark, noise-dominated previews also abstain after cross-scale structure,
  small-light, and spatial-correlation checks. This is preview evidence, not
  calibrated sensor-noise measurement or automatic denoising.
  This is not a learned aesthetic judgment or proof of missing RAW detail.
- Scene analysis can mistake content or miss small people in mixed scenes.
  Low-confidence/model-unavailable cases retain tonal Auto. Optional person
  segmentation adds corroborated color protection, not face detection or
  precise skin masks. It can miss people and mask boundaries can be wrong.
  No skin whitening, identity inference, or automatic model training is performed.
- Auto white balance can retain measured environmental color when optional
  lighting evidence agrees with several background regions. Sunset warmth is
  separated from green/magenta correction; night and colored-light directions
  come from the image. This is not gray-card calibration or reliable illuminant
  recovery: pale materials and mixed light remain ambiguous. Manual Temperature
  and Tint remain available. Existing saved settings do not change until Auto is run.
- MHC is linear gradient-corrected interpolation, not edge-adaptive demosaic or
  denoise. Existing Nikon recipes now render differently at full/native resolution;
  fast Fit previews and original RAW files are unchanged.
- Negative Highlights now compress over-range display values smoothly. Existing
  edits using that control may render differently; sensor saturation and the
  current Nikon neutral highlight ceiling cannot be undone by this tone curve.
- No portrait retouching algorithms yet
- No film engine implementation yet
- No public test photo dataset yet

That is intentional. The first milestone is a clean foundation that can grow
without turning into one giant image-processing script.

## Quick Start For Developers

Requirements:

- Windows 11, macOS, or Linux for development
- Python 3.11+
- Git

Clone the repository:

```powershell
git clone https://github.com/gods04/openraw-studio.git
cd openraw-studio
```

Fastest Windows app launch:

```powershell
.\scripts\run_app.ps1
```

That script creates the virtual environment and starts the desktop app.

Create safe sample RAW files for testing from the command line:

```powershell
python scripts\create_sample_dng.py
python scripts\create_sample_nikon_nef.py
```

The generated samples are written to `sample-data\openraw-synthetic.DNG` and
`sample-data\openraw-synthetic-nikon.NEF`. They are ignored by Git like other
RAW files.

Manual developer setup:

Create a virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

Check the local environment:

```powershell
openraw doctor
```

`openraw doctor` should report the OpenRAW Native engine foundation. It does not
require darktable for the normal project path.

Check whether one file is supported by the current OpenRAW Native path:

```powershell
openraw inspect "E:\Photos\input\IMG_0001.DNG"
```

For real Nikon `.NEF` / `.NRW` files that are not renderable yet,
`openraw inspect` lists the current render blockers and the next engine
capability needed instead of failing silently.

Open the beginner-friendly desktop app:

```powershell
openraw app
```

Import a supported DNG for preview/export, import a Nikon `.NEF` / `.NRW` to
read metadata, show an embedded JPEG preview when the file provides one, or
render through the guarded native Nikon sensor path when the file exposes a
supported uncompressed Bayer payload. Import a folder to browse RAW-like files,
or choose `Library` > `Sample photos`, then click `Auto`
for a conservative starter look.
The preview appears automatically after import and follows adjustment sliders
while you drag. The preview status shows the active GPU or CPU. Choose
JPEG or TIFF in the `Export` tab, then click the top-right Export button. TIFF
also offers `Bit depth` 8 or 16; JPEG stays 8-bit. After
importing a folder, choose a folder-processing mode and click `Export folder`.
Unsupported files are skipped and reported. `Stop batch` stops between photos.

Edits save automatically on this computer without changing the RAW. Undo/redo
works within the current photo session. The comparison icon shows the original
RAW render, not the camera JPEG. `Fit`, `2x`, and `4x` control fast preview zoom.
For supported RAWs, `100%` shows one native image pixel per display pixel and
`200%` doubles those pixels without smoothing. Double-click a point in Fit view
to inspect it at 100%; double-click again to return to Fit. Drag to pan.
Native Nikon detail renders only the visible sensor region, using the same
full-resolution renderer as export. The histogram describes that visible detail;
Auto still analyzes the whole photo. Preview-only files do not offer native zoom.
The status area shows animated progress for a single photo and exact item
progress for a folder export. Completed previews and exports report their pixel
dimensions and file size.
The app shows basic photo information, current Native support status, and the
planned preview, JPEG, and recipe paths before rendering. Nikon RAW files with
only embedded JPEG previews display that reference automatically, while Auto Adjust and
final export stays disabled and the app shows the next missing engine step.
Nikon RAW files that match the guarded native sensor path can use preview, Auto
Adjust, JPEG/TIFF export, and JPEG quality selection. Supported DNG and
native-renderable Nikon files create a preview PNG, the selected final
derivative, and a recipe JSON in the selected output
folder. Nikon preview-only runs write a `.preview.jpg` file and recipe JSON.
After `Update Preview`, the desktop app enables `Open Preview JPEG` for those
preview-only Nikon files so you can open the camera-authored embedded preview
directly without treating it as a finished RAW export.
The comparison icon uses a cached unedited RAW render after native loading
finishes, including the matching native-pixel region at 100%/200%. A camera JPEG
may appear while the RAW loads or when sensor rendering is unsupported; it is
a reference only and is never used as the source for OpenRAW export.
During native loading, this temporary image is labeled `Camera`, then replaced
automatically by the editable RAW frame. It does not contain your saved edits.

With the optional local scene model installed, **Auto** also considers content
such as coast, grassland, sky, and portrait, plus separate lighting evidence.
It solves bounded Temperature/Tint/Saturation corrections through trial RAW
renders, rather than loading one preset per scene. Already-vivid colors and
uncertain cases can remain unchanged. The sidebar shows the inferred scene and
lighting; these labels are fallible, not calibrated confidence guarantees.
This analysis runs only for Auto, never during slider rendering. Existing saved
edits and export recipes do not change until Auto is run again.
See [local model setup](models/README.md); the app never downloads weights or
uploads photos. `OPENRAW_SCENE=off` retains tonal Auto without scene refinement.

An additional optional person model can separate confirmed subject pixels from
blue/green scenery targets. Measured subject color is included in the solver,
with checks across small subject regions so a large shirt cannot hide a smaller
color shift. The sidebar reports `Person detected` when strong segmentation is
corroborated by crop classification or, with the optional face locator, an
overlapping reliable face. Face confirmation is restricted to its matching
component and does not make uncertain lighting reliable. This is person-aware
**global** Auto, not a local brush, face/skin editing, or identification.
`OPENRAW_PERSON=off` keeps scene
Auto without person analysis. Missing/uncertain masks leave scene Auto unchanged.

**Adjust > Subject > Select** creates a person selection without changing the
global adjustments. Auto also prepares a selection when person evidence is
corroborated. **Subject > Exposure** applies a separate, bounded local correction;
**Enabled** bypasses it. Select starts at zero. With the optional local face
meter installed, a first Auto can also suggest subject exposure based on visible
face interiors, background brightness, and scene/lighting evidence. It tests
rendered candidates with clipping and per-face guards, rather than setting all
faces to a fixed brightness. Conflicting faces or insufficient evidence leave
local exposure unchanged. This is experimental metering, not recognition,
skin-color correction, or calibrated portrait retouching.

The wand beside **Subject > Exposure** recalculates local exposure independently
against current global settings. Top-level Auto preserves an existing local
layer; **Global Auto strength** changes only global settings. Local Auto runs on
request, never while dragging sliders or replaying a saved recipe.
**Subject > Temperature / Tint** provide independent local color balance while
preserving rendered brightness. The wand beside **Temperature** can suggest a
small correction when optional clothing-color evidence, visible faces, and
spatial neutral references agree. It measures the current image, tests actual
renders, and rejects corrections that worsen another reference region. Night,
sunset, ambiguous clothing, and insufficient evidence retain the current edit.
This requires the optional material head described in [model setup](models/README.md);
it is not calibrated skin correction or a universal mixed-light solution.
First Auto and **Auto each photo** can propose this color layer; an existing
manual layer remains unchanged by top-level Auto. Exposure and color wands
preserve each other's settings. Manual sliders and saved layers need no model.
**Person mask** inspects the saved soft selection at Fit, zoom, and
native detail. Check it before editing: refinement cannot recover missed people
or correct wrongly included objects. There is no manual mask brush yet.

The layer follows undo/redo, local edit retention, and JPEG/8/16-bit TIFF export.
Its compressed mask is saved in the recipe and bound to the original RAW's
SHA256, so reopening/export needs no model inference. Original files remain
untouched. Batch **Current adjustments** copies only global controls; **Saved
edits** retains each photo's own subject layer; **Auto each photo** meters each
photo independently. The magenta inspection overlay
never enters exports. This is rendered-image exposure with a white-preserving
shoulder and local RGB balance with gamut backoff, not RAW highlight recovery,
sensor white balance, or calibrated face/skin retouching. New colored layers
use `subject.v2`; earlier `subject.v1` recipes remain supported unchanged.

The compact histogram follows the displayed Before, Camera Preview, or After
image and reports near-black and near-white clipping percentages. It analyzes
the displayed rendered 8-bit image, not untouched sensor values.
The same bounded rendered-preview clipping check is saved under `analysis.quality`
and `qc` in the recipe. A warning is advisory and does not block export. Camera-authored
embedded previews are excluded because they are not OpenRAW renders.
The Exposure, Contrast, Highlights, Shadows, Temperature, Tint, Saturation, Color noise,
and Luminance noise controls are
recorded in the recipe and applied to DNG and native-renderable Nikon preview/final export.
Highlights and Shadows use a smooth tone-region adjustment; lowering Highlights
cannot reconstruct sensor detail that was already fully clipped. Temperature and
Tint are normalized manual balance controls rather than camera-calibrated Kelvin values.
Color noise is a 0-100 control in `Adjust > Detail`; its recipe field is
`adjustments.raw.color_noise` in `[0, 1]`, defaulting to zero for older recipes.
It preserves rendered luminance while filtering local color differences. Fit
remains a fast proxy; native inspection uses the same filter as full-size export.
The CLI accepts `--color-noise 0.75` on `process` and `batch`.
Luminance noise is a separate 0-100 control in the same panel. Its recipe field
is `adjustments.raw.luminance_noise` in `[0, 1]`, also defaulting to zero. It
smooths local brightness grain before the color filter while retaining channel
differences. At strong settings, fine texture can soften. Fit reduces its amount
with proxy scale to avoid overstating the native-pixel effect; it remains an
approximation. Native 100%/200% regions match the 8-bit full-size render; 16-bit
TIFF retains additional precision and is not pixel-identical after noise filtering.
The CLI accepts
`--luminance-noise 0.5`, independently or together with `--color-noise`.
The wand button beside the Color noise value runs `Auto color noise` independently
of tonal Auto. It evaluates the current tones and luminance-noise amount with
color filtering temporarily off,
then changes only the color-noise amount. A low-noise result sets zero; missing
samples or unverified benefit leave the current amount unchanged. Accepted values
participate in undo/redo, saved edits, and export just like a manual slider edit.
When the initial native grid provides too little evidence, Nikon analysis checks
a denser non-overlapping grid, retaining the original tone/texture protections.
This still measures sampled rendered color variation, not calibrated sensor noise;
it can leave difficult photos unchanged rather than force a stronger filter.
The toolbar Auto and batch Auto preserve both chosen noise amounts. Luminance
noise is manual; automatic luminance-noise estimation is not implemented yet.
Nikon embedded
previews are currently extracted as camera-authored JPEGs without applying
those adjustments yet.
`Open Output Folder` opens the generated files directly. When adjustments
change after a preview render, the desktop app marks the preview as needing an
update.
If the selected output folder already contains a matching recipe for the photo,
the desktop app restores the saved Exposure, Contrast, Highlights, Shadows,
Temperature, Tint, Saturation, Color noise, and Luminance noise values.
The on-screen preview is capped at 2048 pixels on its longest side; export keeps
the source dimensions supported by the current Native path.
Generated previews, exports, and recipes are published atomically: an interrupted
encode cleans up its temporary file and leaves any existing complete output in place.

Plan a processing run without rendering pixels:

```powershell
openraw process "E:\Photos\input\IMG_0001.NEF" --output "E:\Photos\openraw-output" --dry-run
```

The dry run writes a recipe JSON and planned artifact paths. It does not decode
or edit the image yet.

To apply a manual exposure adjustment from the command line, add a value in
stops:

```powershell
openraw process "E:\Photos\input\IMG_0001.DNG" --output "E:\Photos\openraw-output" --exposure 0.7
```

You can also pass contrast, highlights, shadows, temperature, tint, and saturation controls:

```powershell
openraw process "E:\Photos\input\IMG_0001.DNG" --output "E:\Photos\openraw-output" --contrast 0.25 --highlights -0.3 --shadows 0.2 --temperature 0.2 --tint -0.1 --saturation 0.15
```

`--warmth` remains available as a compatibility alias for `--temperature`.

Render the current native preview-only path for a narrow supported
uncompressed DNG, a native-renderable Nikon RAW file, or a Nikon RAW file with
an embedded JPEG preview:

```powershell
openraw process "E:\Photos\input\IMG_0001.DNG" --output "E:\Photos\openraw-output" --preview-only
openraw process "E:\Photos\input\IMG_0001.NEF" --output "E:\Photos\openraw-output" --preview-only
```

This writes a `.preview.png` file for simple uncompressed 12/14-bit packed
strip-based or 16-bit strip/tile-based DNG/Nikon files and supported Nikon
34713 lossless files, or a `.preview.jpg` file for Nikon embedded previews,
and skips final export.

Render the current narrow end-to-end native path:

```powershell
openraw process "E:\Photos\input\IMG_0001.DNG" --output "E:\Photos\openraw-output"
openraw process "E:\Photos\input\IMG_0001.NEF" --output "E:\Photos\openraw-output"
openraw process "E:\Photos\input\IMG_0001.NEF" --output "E:\Photos\openraw-output" --format tiff
openraw process "E:\Photos\input\IMG_0001.NEF" --output "E:\Photos\openraw-output" --format tiff --bit-depth 16
```

For supported simple uncompressed DNG files, guarded TIFF-style Nikon sensor
files, and supported Nikon 34713 lossless files, this writes `.preview.png` plus
`.auto.jpg` by default, or `.auto.tif` with `--format tiff`. TIFF output is
lossless Deflate-compressed rendered sRGB, 8-bit by default or genuine 16-bit
with `--bit-depth 16` (also available for `batch`). The 16-bit path quantizes
the processed floating-point image directly and retains precision through noise
reduction, orientation, and encoding. It is not an upscaled 8-bit image, a linear
RAW export, or a wider-gamut/HDR format; files are larger and can take longer.
Live previews remain 8-bit. The image data
is still an early V0.1 render. It applies available DNG `AsShotNeutral`, inverts
`ColorMatrix1` from camera space through XYZ, performs Bradford white-point
adaptation, and converts to linear sRGB before tone mapping. Dual-illuminant
DNG profiles are still future work. Supported Nikon 34713 files use a fast
half-resolution preview and full-resolution MHC final JPEG/TIFF. The D500,
1 J5, Z5, and Z f have native camera profiles; other models use generic camera RGB.
Final JPEG/TIFF files retain safe photographic metadata such as camera, ISO,
shutter, aperture, focal length, and capture date. Exported pixels are already
oriented, so Orientation is written as `1`; location/GPS metadata is not copied.

Batch export currently renderable DNG/Nikon files from a folder:

```powershell
openraw batch "E:\Photos\input" --output "E:\Photos\openraw-output"
```

The batch command scans RAW-like files, processes the files that match the
current OpenRAW Native render path, and reports skipped/preview-only/import-only/failed
files. With `--preview-only`, Nikon files with embedded JPEG previews can be
previewed in batch without final export.

Run tests:

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests
```

More setup notes are in [docs/INSTALL.md](docs/INSTALL.md).
Release build notes are in [docs/RELEASE.md](docs/RELEASE.md).
RAW backend notes are in [docs/BACKENDS.md](docs/BACKENDS.md).
Native render-engine planning is in [docs/OPENRAW_RENDER_ENGINE.md](docs/OPENRAW_RENDER_ENGINE.md).

## Project Map

```text
docs/                    Product and architecture documentation
schemas/                 JSON schemas for recipes, presets, and looks
configs/                 Application configuration schema and examples
presets/                 Versioned processing presets and creative looks
models/                  Model notes only; no weights committed
packaging/               Desktop packaging entry points
scripts/                 Local run, sample generation, and build scripts
src/openraw_studio/      Python package with engine interfaces
tests/                   Contract and schema smoke tests
```

## Development Roadmap

V0.1 should prove the first vertical slice:

```text
RAW input
  -> metadata
  -> preview
  -> basic scene/portrait detection
  -> processing recipe
  -> base RAW render
  -> JPEG or lossless 8/16-bit TIFF export
  -> recipe sidecar
```

See [docs/ROADMAP.md](docs/ROADMAP.md) for the staged plan.
For the practical build order, start with [docs/START_HERE.md](docs/START_HERE.md).
For the app experience direction, see [docs/UI_DESIGN.md](docs/UI_DESIGN.md).
For the render-engine direction, see
[docs/OPENRAW_RENDER_ENGINE.md](docs/OPENRAW_RENDER_ENGINE.md).

## Open Source And Models

The application code is MIT licensed. Third-party libraries, AI models, model
weights, LUTs, datasets, and sample images may have separate licenses and must be
reviewed before they are bundled or redistributed.

See [docs/MODEL_LICENSES.md](docs/MODEL_LICENSES.md) for the license register.
See [docs/COMMERCIALIZATION_STRATEGY.md](docs/COMMERCIALIZATION_STRATEGY.md) for
commercialization planning.
See [NOTICE](NOTICE) for third-party notices, including the required Adobe DNG
technology notice.

## Contributing

Contributions should keep the architecture modular and non-destructive. Start
with [CONTRIBUTING.md](CONTRIBUTING.md).
