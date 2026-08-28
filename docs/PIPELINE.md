# Pipeline

## V0.1 Vertical Slice

The first implementation should prove that all major stages can communicate.
The algorithms can be simple; the contracts and artifacts should be real.

```text
Source RAW
  -> inspect metadata
  -> create preview
  -> basic analysis
  -> decision and recipe
  -> base render
  -> export JPEG
  -> save recipe JSON
```

Current developer command:

```powershell
openraw process "E:\Photos\input\IMG_0001.NEF" --output "E:\Photos\openraw-output" --dry-run
```

The current dry run writes recipe JSON and planned artifact paths. It does not
decode RAW pixels yet.

Current native support inspection command:

```powershell
openraw inspect "E:\Photos\input\IMG_0001.DNG"
```

This reports whether the file matches the current OpenRAW Native render path
before starting preview or export.

Current native preview command:

```powershell
openraw process "E:\Photos\input\IMG_0001.DNG" --output "E:\Photos\openraw-output" --preview-only
```

The preview-only path writes a `.preview.png` file for narrow supported
uncompressed DNG files and skips final export.

Current native render command for narrow supported uncompressed DNG files:

```powershell
openraw process "E:\Photos\input\IMG_0001.DNG" --output "E:\Photos\openraw-output"
```

This path writes a `.preview.png` preview, a preview-derived `.auto.jpg` export,
and a recipe sidecar. It is an honest V0.1 render proof, not the final
camera-aware color pipeline.

## Inputs

V0.1 accepts:

- a manually selected RAW path
- a future watched import folder path

The source file is opened read-only. The pipeline must not overwrite, move, or
rewrite the RAW.

## Artifacts

Recommended artifact layout for each processed source:

```text
output/
  previews/
    IMG_0001.preview.png
  intermediates/
    IMG_0001.base.tif
  exports/
    IMG_0001.auto.jpg
  recipes/
    IMG_0001.NEF.recipe.json
```

Intermediates can be optional in V0.1, but the recipe sidecar is required.

## Stage Responsibilities

### 1. Inspect

Read source metadata:

- camera make/model
- lens
- ISO
- exposure time
- aperture
- focal length
- capture time
- image dimensions
- orientation

### 2. Preview

Generate a preview suitable for fast analysis and UI display. The preview should
record dimensions, color space, backend name, and source path.

### 3. Analyze

V0.1 can start with heuristic analysis:

- portrait candidate: face/person model if available, otherwise no portrait
- scene candidates from metadata and image statistics
- image quality hints such as underexposure or highlight clipping risk

All analysis outputs include confidence.

### 4. Decide

The decision engine chooses:

- processing profile
- creative look
- conservative adjustment strengths
- confidence-driven constraints

Low confidence should reduce edit strength rather than force a style.

### 5. Render

The RAW engine applies the base recipe using the configured backend. For V0.1,
`darktable-cli` may be used as a developer/experimental external backend, but
the app must call it through `RawProcessor` rather than embedding darktable
assumptions throughout the code. The long-term product should not require users
to understand or install darktable manually.

### 6. QC

V0.1 QC can be minimal:

- missing output file
- unreadable recipe
- export dimensions
- obvious clipping warnings if histogram data is available

### 7. Export

Write the finished derivative and sidecar recipe. Exports must include enough
metadata to trace the source and engine versions.

## Failure Handling

The pipeline should fail with structured errors:

- source missing
- unsupported RAW format
- backend unavailable
- model unavailable
- preview generation failed
- export failed
- recipe write failed

Partial artifacts should be tracked so a failed run can be diagnosed without
silently leaving misleading outputs.
