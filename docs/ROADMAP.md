# Roadmap

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
- `openraw process --preview-only` writes a native preview for supported narrow DNG files

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
5. expand preview-derived native JPEG export into a proper export stage
6. improve preview dimension and color-space metadata
7. add export writer abstraction for final derivatives
8. add command-line smoke flow documentation using a local RAW file
9. add fixture-free tests around recipe, decisions, and artifact paths
10. add source discovery and import-folder watcher contract
11. [done] add locally generated synthetic DNG for safe smoke tests
12. [done] add first Windows ZIP packaging workflow
13. [done] add basic exposure, contrast, and warmth controls to preview/export recipes
14. [done] split desktop preview refresh from final JPEG export
15. [done] mark desktop previews stale when adjustments change
16. [done] add conservative desktop Auto Adjust starter action

Exit criteria:

- one public or local test RAW can be processed end to end
- a generated synthetic DNG can be created locally for onboarding smoke tests
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
