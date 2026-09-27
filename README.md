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

To try the app without using a private photo, click `Create Sample DNG` or
`Create Sample NEF` inside the app. You can also generate the same tiny
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
- Native DNG/TIFF metadata reader for the first RAW-engine milestone
- Nikon `.NEF` / `.NRW` metadata import and embedded JPEG preview extraction
- Nikon MakerNote summary for compressed NEF render blockers, including
  0x0096 compression-table and 0x008c curve/table detection
- Native Nikon 34713 lossless Huffman sensor decoding with optimized Python
  bitstream/render loops, MakerNote black levels with inactive-border fallback,
  a fast half-resolution preview, and chunked full-resolution bilinear RGB
  export for supported files
- Camera-aware Nikon D500 rendering with standard as-shot white balance, an
  exact camera-to-linear-sRGB profile, and EXIF orientation handling
- Native extraction for simple uncompressed DNG 12/14-bit packed strip payloads
  and 16-bit strip/tile pixel payloads
- Guarded native Nikon `.NEF` / `.NRW` sensor decode for TIFF-style
  uncompressed Bayer payloads
- Native black/white level normalization for 12/14/16-bit Bayer sensor data
- Native simple Bayer demosaic baseline
- Native PNG preview encoding for narrow uncompressed DNG/Nikon test files
- Local JPEG quality control and lossless 8-bit RGB TIFF export with recipe traceability
- Atomic preview, derivative, and recipe publication so interrupted writes do not
  replace an existing complete result with a partial file
- Safe derivative metadata for camera, lens, capture settings, date, orientation,
  sRGB, and OpenRAW software identity; GPS is not copied
- Beginner desktop shell launched with `openraw app` or `scripts/run_app.ps1`
- App single-photo import, folder import, output-folder selection, conservative Auto Adjust,
  exposure/contrast/highlights/shadows/temperature/tint/saturation adjustments,
  rendered-preview RGB/luminance histogram with clipping feedback, before/after comparison, built-in
  sample DNG creation, photo/output information display, batch folder export,
  visible single-photo/batch processing progress, final dimensions/file size,
  and direct output opening
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
  with a half-resolution preview and full-resolution bilinear final export.
  Camera-aware color is exact for the D500 and generic for other models;
  edge-aware demosaic quality, further speed work, and more profiles remain
- Other compressed/proprietary Nikon `.NEF` / `.NRW` sensor payload variants
  are not decoded yet
- Broad proprietary RAW rendering support is not implemented yet
- TIFF export is currently 8-bit sRGB; 16-bit linear/working-space TIFF is not implemented yet
- No AI model weights included
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
or click `Create Sample DNG` / `Create Sample NEF`, then click `Auto Adjust`
for a conservative starter look.
Use `Update Preview` to refresh the preview with the current adjustments. Choose
JPEG or TIFF in the Output section, then click the matching Export button. After importing a folder, click
`Export Folder` to export every currently supported file using the current basic
adjustments; unsupported files are skipped and reported.
The status area shows animated progress for a single photo and exact item
progress for a folder export. Completed previews and exports report their pixel
dimensions and file size.
The app shows basic photo information, current Native support status, and the
planned preview, JPEG, and recipe paths before rendering. Nikon RAW files with
only embedded JPEG previews can use `Update Preview`, while Auto Adjust and
final export stays disabled and the app shows the next missing engine step.
Nikon RAW files that match the guarded native sensor path can use preview, Auto
Adjust, JPEG/TIFF export, and JPEG quality selection. Supported DNG and
native-renderable Nikon files create a preview PNG, the selected final
derivative, and a recipe JSON in the selected output
folder. Nikon preview-only runs write a `.preview.jpg` file and recipe JSON.
After `Update Preview`, the desktop app enables `Open Preview JPEG` for those
preview-only Nikon files so you can open the camera-authored embedded preview
directly without treating it as a finished RAW export.
After preview or export, `Show Before` lets supported lightweight RAW paths
compare the basic demosaiced image with the OpenRAW color-treated result. Native
Nikon 34713 renders instead offer `Show Camera Preview`, which compares the
OpenRAW result with the camera-authored embedded JPEG without decoding the large
sensor payload twice. The camera preview is a reference only; OpenRAW still
exports from native sensor data.
The compact histogram follows the displayed Before, Camera Preview, or After
image and reports near-black and near-white clipping percentages. It analyzes
the displayed rendered 8-bit image, not untouched sensor values.
The Exposure, Contrast, Highlights, Shadows, Temperature, Tint, and Saturation controls are
recorded in the recipe and applied to DNG and native-renderable Nikon preview/final export.
Highlights and Shadows use a smooth tone-region adjustment; lowering Highlights
cannot reconstruct sensor detail that was already fully clipped. Temperature and
Tint are normalized manual balance controls rather than camera-calibrated Kelvin values.
Nikon embedded
previews are currently extracted as camera-authored JPEGs without applying
those adjustments yet.
`Open Output Folder` opens the generated files directly. When adjustments
change after a preview render, the desktop app marks the preview as needing an
update.
If the selected output folder already contains a matching recipe for the photo,
the desktop app restores the saved Exposure, Contrast, Highlights, Shadows,
Temperature, Tint, and Saturation values.
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
```

For supported simple uncompressed DNG files, guarded TIFF-style Nikon sensor
files, and supported Nikon 34713 lossless files, this writes `.preview.png` plus
`.auto.jpg` by default, or `.auto.tif` with `--format tiff`. TIFF output is
lossless Deflate-compressed 8-bit sRGB. The image data
is still an early V0.1 render. It applies available DNG `AsShotNeutral`, inverts
`ColorMatrix1` from camera space through XYZ, performs Bradford white-point
adaptation, and converts to linear sRGB before tone mapping. Dual-illuminant
DNG profiles are still future work. Supported Nikon 34713 files use a fast
half-resolution preview and a full-resolution bilinear final JPEG. The D500 has
an exact native color profile while other Nikon models still use generic
camera RGB.
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
  -> JPEG or lossless 8-bit TIFF export
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
