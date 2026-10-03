# Install And Run

OpenRAW Studio does not have a polished installable desktop release yet.

This page explains how to run the early local desktop app and the developer
setup behind it.

## Fastest Windows Start

Clone the repo, open PowerShell in the repository folder, and run:

```powershell
.\scripts\run_app.ps1
```

You can also double-click this file in File Explorer:

```text
scripts\run_app.cmd
```

The startup script will:

- create `.venv` if it does not exist
- install OpenRAW Studio in editable local mode
- open the desktop app

Current app flow:

```text
Import DNG/NEF or folder -> automatic preview -> Auto Adjust or drag adjustments -> choose JPEG/TIFF -> Export JPEG/TIFF or Export Folder
```

The app also shows whether the selected file is supported by the current
OpenRAW Native path before rendering. Nikon `.NEF` / `.NRW` files can be
imported for metadata today; files with embedded JPEG previews show them
automatically, and files with supported TIFF-style uncompressed Bayer payloads
can use the native preview/export path, including row-aligned 12/14-bit packed
strip payloads and 16-bit strip/tile payloads. Supported Nikon 34713 lossless
compressed files can also use the native preview/export path through the first
half-resolution preview and full-resolution bilinear export renderer. Folder import scans RAW-like files in the
selected folder and marks each one as renderable, preview-only, import-only, or
not supported yet.

For supported simple uncompressed DNG files and guarded TIFF-style Nikon sensor
files, plus supported Nikon 34713 lossless compressed files, the app writes:

- preview PNG
- selected JPEG or lossless 8-bit TIFF derivative
- recipe JSON sidecar

For Nikon preview-only files, the app writes:

- embedded-preview JPEG
- recipe JSON sidecar

Once the camera preview loads, the app enables `Open Preview JPEG` for these
preview-only Nikon files. Final export remains disabled until that file can be
rendered through the native sensor path, so the UI does not present an embedded
camera preview as a finished RAW export.

### Automatic Acceleration And Live Preview

For renderable photos, moving a slider updates the image automatically, including
during continuous dragging. Metadata and support details are in the `Info` tab.
The screen-size preview is never used as the full-resolution export source.

`Auto` suggests a conservative correction; `Auto strength` varies it from 0 to
100%. Light and Color groups contain manual sliders. The comparison icon switches
between an unedited RAW render and the current edit. Undo/redo buttons (Ctrl+Z /
Ctrl+Y) restore edits during the current photo session. Fit/2x/4x zoom and dragging
allow closer preview inspection, not full-resolution 1:1 pixel inspection.

Edits save automatically after a short idle interval, on slider release, and on
normal close. Windows stores them under `%LOCALAPPDATA%\OpenRAW Studio\edits`.
These local records are keyed by source path, size, and modification time; moved
or externally modified files do not inherit stale edits. Exported recipe sidecars
remain portable artifacts. No edit operation modifies the source RAW.

Nikon 1 J5 12-bit D20 non-split compressed files are also supported, with an
exact camera color profile and a 5584 x 3724 output for the tested landscape files.
The tested Z f encoding and D20 split-row streams remain preview-only.

The app probes installed OpenCL GPU devices and validates a small render before
using one. A working discrete GPU is preferred; integrated GPUs are also eligible.
If no usable GPU/driver is present, or GPU rendering fails, processing falls back
to the CPU. No driver installation or GPU selection is required inside the app.
The preview status identifies the active backend.

Nikon lossless decoding can compile OpenRAW's own decoder using Numba. The first
launch may take longer while machine code and GPU kernels are initialized/cached.
This is separate from subsequent slider response; it does not replace our RAW
engine with another photo application's decoder.

For troubleshooting, force CPU rendering for the current PowerShell session:

```powershell
$env:OPENRAW_GPU = "off"
.\scripts\run_app.ps1
```

Remove that environment variable or start a new terminal to restore automatic
selection. GPU acceleration currently covers the interactive tone renderer and
the supported full-resolution Nikon lossless path, not every RAW stage/format.

In the `Export` tab, `Export folder` processes renderable photos with current
adjustments, each photo's saved edits, or an independent Auto correction.
`Stop batch` finishes the current photo before stopping. Existing derivatives
require replacement confirmation. Small windows can scroll the inspector tabs.

To try the app without using a private photo, open `Library` > `Sample photos`
and choose a sample DNG or NEF.

You can also generate the same synthetic samples from the command line:

```powershell
python scripts\create_sample_dng.py
python scripts\create_sample_nikon_nef.py
```

Then open the app and import either sample:

```text
sample-data\openraw-synthetic.DNG
sample-data\openraw-synthetic-nikon.NEF
```

## Future Normal User Download

When the first packaged build exists, the README will link to GitHub Releases
and this page will include:

- Windows installer or portable ZIP download
- first-launch instructions
- import-folder setup
- export-folder setup
- GPU/CPU selection notes
- common troubleshooting

## Build A Windows ZIP

Maintainers and testers can build the current desktop app package locally:

```powershell
.\scripts\build_windows.ps1
```

For a local executable without producing a ZIP, use
`.\scripts\build_windows.ps1 -SkipZip`. Then double-click
`dist\OpenRAW Studio\OpenRAW Studio.exe`. Keep its `_internal` folder beside the
EXE; moving only the EXE will break the app. You may create a Windows shortcut to
that EXE, or drag a RAW file onto it to open the photo.

The output is:

```text
dist\OpenRAW-Studio-windows-x64.zip
```

GitHub can also build this package through the `Build Windows App` workflow.
See `docs\RELEASE.md` for the release checklist.

## Manual Developer Setup

Requirements:

- Python 3.11+
- Git
- PowerShell on Windows

Clone the repo:

```powershell
git clone https://github.com/gods04/openraw-studio.git
cd openraw-studio
```

Create and activate a virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Install the package in editable mode:

```powershell
python -m pip install -e .
```

Open the desktop app:

```powershell
openraw app
```

Run tests:

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests
```

## Current Output

Phase 0 has a developer CLI skeleton. It validates the repository foundation and
can write a dry-run recipe/artifact plan for a RAW-like source file:

- engine contracts import correctly
- recipe helpers preserve non-destructive RAW assumptions
- schema and preset JSON files parse correctly
- `openraw doctor` reports the OpenRAW Native engine foundation
- `openraw process --dry-run` writes a recipe sidecar without rendering pixels
- `openraw inspect` can import Nikon `.NEF` / `.NRW` metadata, detect embedded
  JPEG preview support, detect supported Nikon 34713 lossless renderable files,
  list render blockers, and show the next missing engine capability
- `openraw process` writes a PNG preview and local JPEG or lossless 8-bit TIFF export for
  narrow supported uncompressed 12/14/16-bit DNG/Nikon sensor files and
  supported Nikon 34713 lossless files
- `openraw process --preview-only` writes a JPEG preview for Nikon files with
  embedded previews

Check the environment:

```powershell
openraw doctor
```

Expected meaning:

```text
OpenRAW Native engine foundation is available.
Nikon NEF/NRW metadata import is available.
Nikon NEF/NRW embedded JPEG preview is available when the file contains one.
Guarded Nikon NEF/NRW native sensor rendering is available for TIFF-style uncompressed Bayer payloads.
Narrow uncompressed 12/14/16-bit DNG/Nikon preview and local JPEG/TIFF export are available.
Supported Nikon 34713 lossless files use an optimized half-resolution preview and full-resolution bilinear export path.
Supported Nikon 34713 lossless files use conservative inactive-border black-level estimation.
```

Inspect one file before processing it. Nikon `.NEF` / `.NRW` files show
renderable when they expose supported TIFF-style uncompressed Bayer payloads or
the supported Nikon 34713 lossless path, preview-only when an embedded JPEG
preview is available, or import-only when only metadata can be read. For files
that are not renderable yet, the report also lists the current render blockers
and the next engine step:

```powershell
openraw inspect "E:\Photos\input\IMG_0001.NEF"
```

Plan one source file:

```powershell
openraw process "E:\Photos\input\IMG_0001.NEF" --output "E:\Photos\openraw-output" --dry-run
```

Render the first native preview-only paths:

```powershell
openraw process "E:\Photos\input\IMG_0001.DNG" --output "E:\Photos\openraw-output" --preview-only
openraw process "E:\Photos\input\IMG_0001.NEF" --output "E:\Photos\openraw-output" --preview-only
```

This writes a PNG preview for narrow uncompressed 12/14/16-bit DNG/Nikon files
and supported Nikon 34713 lossless files that match the current Native path, or
a JPEG preview for Nikon files with embedded previews. It intentionally skips
final export.

Render the first native end-to-end path:

```powershell
openraw process "E:\Photos\input\IMG_0001.DNG" --output "E:\Photos\openraw-output"
openraw process "E:\Photos\input\IMG_0001.NEF" --output "E:\Photos\openraw-output"
openraw process "E:\Photos\input\IMG_0001.NEF" --output "E:\Photos\openraw-output" --format tiff
```

For supported simple uncompressed DNG files, guarded TIFF-style Nikon sensor
files, and supported Nikon 34713 lossless files, this writes a preview plus
`IMG_0001.auto.jpg` by default or `IMG_0001.auto.tif` with `--format tiff`.
The TIFF is an 8-bit sRGB Deflate-compressed derivative, not a 16-bit linear
working file. The image data is still an early V0.1 render and does
not represent final camera-aware color science yet.
JPEG/TIFF derivatives retain camera, lens, ISO, shutter, aperture, focal length,
capture date, normalized orientation, sRGB, and OpenRAW software metadata. GPS
metadata is intentionally not copied.

Batch export the supported files in one folder:

```powershell
openraw batch "E:\Photos\input" --output "E:\Photos\openraw-output"
```

The normal batch command skips preview-only/import-only/unsupported files outside
the current OpenRAW Native render path instead of treating the whole folder as
failed. With `--preview-only`, Nikon files with embedded JPEG previews can be
previewed in batch without final export.

Expected dry-run output:

```text
openraw-output/
  previews/
  exports/
  intermediates/
  recipes/
    IMG_0001.NEF.recipe.json
```

## Developer Experimental Backend Notes

V0.1 includes a `darktable-cli` adapter for development experiments. This is not
the intended long-term normal-user setup.

Developers who want to test that adapter can install darktable from the official
project site, then run:

```powershell
openraw doctor --include-experimental-backends
```

When `darktable-cli` is available, this command should report it as available.
See `docs/BACKENDS.md` and `docs/RAW_ENGINE_STRATEGY.md` for backend details.

Normal users should eventually download OpenRAW Studio and process photos
without knowing which RAW backend is inside the app.

No model weights should be downloaded or bundled until their licenses are
documented in `docs/MODEL_LICENSES.md`.
