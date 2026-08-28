# Start Here

This is the practical guide for turning OpenRAW Studio from an idea into a real
open-source app.

## The First Thing To Do

Do not start with face slimming, AI models, film simulation, or a polished UI.

Start with one honest vertical slice:

```text
Choose RAW file
  -> inspect metadata
  -> generate preview
  -> make a simple AUTO decision
  -> export JPEG
  -> save recipe JSON
```

That slice proves the product shape. Everything else can grow from it.

## Why This Comes First

OpenRAW Studio has many ambitious parts:

- RAW processing
- scene understanding
- portrait enhancement
- segmentation and masks
- color grading
- film simulation
- quality control
- desktop UI

If we build those horizontally, the project becomes a pile of unfinished parts.
If we build vertically, every milestone becomes usable, testable, and explainable.

## Rule Zero

These rules apply from the first commit:

- Original RAW files are never modified.
- Every edit must be reproducible from a recipe.
- AI models are replaceable.
- Local processing is the default.
- No private photos are committed.
- No model weights, LUTs, datasets, or assets are bundled without license review.
- The public README must always tell the truth about what works today.

## The Recommended Path

### Step 1 - Public Repo Foundation

Goal: make the GitHub repository understandable.

Already started:

- README
- MIT license
- install docs
- product spec
- architecture docs
- roadmap
- pipeline guide
- model license register
- initial Python package layout
- engine interfaces
- recipe and preset schemas
- foundation tests
- CLI skeleton
- dry-run recipe/artifact planner
- RAW backend availability check
- UI design direction
- OpenRAW render-engine strategy
- OpenRAW Native RAW engine scaffold
- Windows ZIP packaging workflow

Before the first public push, check:

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests
python -m pip install --dry-run -e .
openraw doctor
git status --short
```

For a Windows user who just wants to open the current local app, use:

```powershell
.\scripts\run_app.ps1
```

### Step 2 - V0.1 CLI Pipeline

Goal: process one RAW file from the command line.

Command target:

```powershell
openraw process "E:\Photos\input\IMG_0001.NEF" --output "E:\Photos\openraw-output"
```

Current native preview-only command:

```powershell
openraw process "E:\Photos\input\IMG_0001.DNG" --output "E:\Photos\openraw-output" --preview-only
```

Current native render command for narrow supported uncompressed DNG files:

```powershell
openraw process "E:\Photos\input\IMG_0001.DNG" --output "E:\Photos\openraw-output"
```

Expected render output:

```text
openraw-output/
  previews/
    IMG_0001.preview.png
  exports/
    IMG_0001.auto.jpg
  recipes/
    IMG_0001.DNG.recipe.json
```

V0.1 should include:

- source path validation
- output folder creation
- RAW metadata inspection
- preview generation
- basic scene/portrait heuristic
- rule-based decision engine
- recipe sidecar writer
- JPEG export
- structured errors
- tests that do not require private photos
- Windows startup script for the early desktop app

Current status:

- source path validation exists
- output folder creation exists
- file checksum and basic filesystem metadata exist
- dry-run recipe writing exists
- backend availability check exists
- `openraw inspect` reports whether a file fits the current Native render path
- OpenRAW Native is the default RAW engine identity
- DNG/TIFF metadata reader exists
- simple uncompressed DNG strip and tile pixel extraction exists
- black/white level sensor normalization exists
- simple Bayer demosaic exists
- simple PNG preview encoding exists
- `--preview-only` pipeline mode exists
- `darktable-cli` adapter exists only as an explicit experimental backend
- preview-derived native JPEG export exists for narrow supported uncompressed DNG files
- first-pass DNG white balance and ColorMatrix1 transform are now applied
- exposure, contrast, and warmth adjustments are available from the CLI and desktop shell and are saved in the recipe
- Windows startup script creates `.venv`, installs the local package, and opens the app
- Windows package script and GitHub Actions artifact build exist
- full camera-aware color conversion and a better export abstraction are next

### Step 3 - Minimal Desktop Shell

Goal: let a normal person open the app and process a photo without reading
developer commands.

The first desktop UI should feel minimal, clean, and premium, but the operation
must stay simple:

```text
Open app
  -> import RAW
  -> Auto Adjust
  -> update preview
  -> compare before/after
  -> export
```

The first desktop UI should have:

- import button
- output folder selector
- basic selected-photo information
- planned preview/JPEG/recipe output paths
- current OpenRAW Native support status
- processing status
- preview area
- preview current/stale state
- before/after comparison placeholder
- Auto Adjust button
- Update Preview button
- Export JPEG button
- export location
- saved recipe detection for the same photo/output folder

Keep advanced controls hidden until the pipeline works.

See `docs/UI_DESIGN.md` before implementing UI screens.

The first local desktop shell is now available through:

```powershell
.\scripts\run_app.ps1
```

It supports importing a DNG, choosing an output folder, running AUTO, viewing
the generated preview, checking selected-photo information and planned output
paths, comparing before/after, adjusting exposure, creating a safe sample DNG,
adjusting contrast/warmth, opening the exported JPEG, and opening the output
folder. When adjustments change, the UI marks the preview as needing an update
until the next preview/export render. If the current output folder already has a
matching recipe for the selected photo, the UI restores the saved basic
adjustments. It currently uses the same local pipeline as the CLI and is
intentionally DNG-first.

### Step 4 - Real RAW Backend

Goal: start moving from backend experiments toward OpenRAW's own render engine.

Default product direction:

- OpenRAW Native RAW Engine

Development-only candidate:

- optional user-installed `darktable-cli`

Optional research inputs:

- LibRaw
- rawpy for Python prototyping
- future custom RAW pipeline

The app should not feel like a wrapper around another RAW editor. Normal users
should experience OpenRAW Studio as the product.

Before building deeper RAW features, read:

- `docs/RAW_ENGINE_STRATEGY.md`
- `docs/OPENRAW_RENDER_ENGINE.md`

### Step 5 - Public Test Assets

Goal: build confidence without committing private photos.

The current safe test path is the `Create Sample DNG` button in the desktop app.

The same sample can be created from the command line:

```powershell
python scripts\create_sample_dng.py
```

This creates `sample-data\openraw-synthetic.DNG`, a tiny generated DNG that can
be imported into the desktop app. It is not a real camera sample, but it is
useful for checking that the first local pipeline runs end to end.

Create a test asset policy before adding images:

- only public photos with clear licenses
- record source URL and license
- keep large RAW files out of normal Git history
- allow local private test folders through config

### Step 6 - V0.2 Vision Foundation

Goal: add faces, landmarks, people, and masks after V0.1 works.

Add:

- face detector interface implementation
- facial landmarks
- person segmentation
- face IDs
- distance-aware portrait rules
- basic face exposure

Do not bundle models until `docs/MODEL_LICENSES.md` is complete for each model.

### Step 7 - V0.3 Portrait Editing

Goal: add controlled portrait edits.

Start with subtle pixel operations:

- face exposure
- skin color balance
- light skin smoothing

Only then add geometry operations:

- face slimming
- eye sizing
- jaw/chin shaping

Geometry edits require QC hooks because they can distort backgrounds.

### Step 8 - Color, Looks, And Film

Goal: make photos look good without destroying skin tones.

Add in this order:

1. scene-aware color targets
2. skin protection blending
3. creative look profiles
4. LUT import
5. film tone and color behavior
6. grain
7. halation and bloom

## What Not To Do First

Avoid these until the V0.1 pipeline works:

- polished marketing website
- full desktop UI redesign
- face slimming
- teeth whitening
- large AI model downloads
- cloud AI processing
- film simulation details
- complex database design
- camera tethering

These are real features, just not first features.

## First Good GitHub Issues

Good early issues:

- Add `openraw process` CLI skeleton.
- Add artifact path planner for previews, exports, and recipes.
- Add external command runner for RAW backends.
- Add RAW backend availability check.
- Add recipe JSON writer.
- Add simple heuristic vision engine.
- Add public test asset policy.
- Add app status badge once CI exists.

## Definition Of Done For V0.1

V0.1 is done when:

- a developer can clone the repo and run tests
- a developer can process one RAW file through one command
- the original RAW file remains unchanged
- a preview is generated
- a JPEG export is generated
- a recipe JSON sidecar is generated
- the recipe records engine versions and decisions
- errors are understandable
- README explains exactly how to try it
- Windows users can open the current local desktop app without manually setting `PYTHONPATH`

That is the first real version of the app.
