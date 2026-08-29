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
Import DNG/NEF or folder -> choose output folder -> renderable DNG: Auto Adjust -> Update Preview -> refine exposure/contrast/warmth -> Export JPEG or Export Folder
```

The app also shows whether the selected file is supported by the current
OpenRAW Native path before rendering. Nikon `.NEF` / `.NRW` files can be
imported for metadata today, but preview/export rendering is not implemented
yet. Folder import scans RAW-like files in the selected folder and marks each
one as renderable, import-only, or not supported yet.

For supported simple uncompressed DNG files, the app writes:

- preview PNG
- preview-derived JPEG
- recipe JSON sidecar

`Export Folder` processes currently renderable files from the imported folder
using the current basic adjustments and reports skipped/import-only/failed files.

To try the app without using a private photo, click `Create Sample DNG` inside
the desktop app.

You can also generate the same synthetic DNG from the command line:

```powershell
python scripts\create_sample_dng.py
```

Then open the app and import:

```text
sample-data\openraw-synthetic.DNG
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
- `openraw inspect` can import Nikon `.NEF` / `.NRW` metadata
- `openraw process` writes a PNG preview and local JPEG export for
  narrow supported uncompressed DNG files

Check the environment:

```powershell
openraw doctor
```

Expected meaning:

```text
OpenRAW Native engine foundation is available.
Nikon NEF/NRW metadata import is available.
Narrow uncompressed DNG preview and local JPEG export are available.
```

Inspect one file before processing it. Nikon `.NEF` / `.NRW` files currently
show as import-only when metadata can be read:

```powershell
openraw inspect "E:\Photos\input\IMG_0001.NEF"
```

Plan one source file:

```powershell
openraw process "E:\Photos\input\IMG_0001.NEF" --output "E:\Photos\openraw-output" --dry-run
```

Render the first native preview-only path:

```powershell
openraw process "E:\Photos\input\IMG_0001.DNG" --output "E:\Photos\openraw-output" --preview-only
```

This writes a PNG preview only for narrow uncompressed DNG files supported by
the current native engine and intentionally skips final JPEG export.

Render the first native end-to-end path:

```powershell
openraw process "E:\Photos\input\IMG_0001.DNG" --output "E:\Photos\openraw-output"
```

For supported simple uncompressed DNG files, this writes both
`IMG_0001.preview.png` and `IMG_0001.auto.jpg`. The JPEG is written through the
local export engine, but the image data is still V0.1 preview-derived and does
not represent final camera-aware color science yet.

Batch export the supported files in one folder:

```powershell
openraw batch "E:\Photos\input" --output "E:\Photos\openraw-output"
```

The batch command skips import-only or unsupported files outside the current
OpenRAW Native render path instead of treating the whole folder as failed.

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
