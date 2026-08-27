# Architecture

## Overview

OpenRAW Studio is organized as a set of replaceable engines connected by a
pipeline orchestrator. Each engine owns one responsibility and communicates
through explicit data contracts.

```text
Input
  -> Raw Engine
  -> Vision Engine
  -> Decision Engine
  -> Portrait Engine
  -> Color Engine
  -> Film Engine
  -> QC Engine
  -> Export Engine
```

The pipeline should never become a single giant photo-processing function. The
orchestrator coordinates stages, stores artifacts, and passes recipe data forward.

## Package Boundaries

```text
openraw_studio.core       Shared domain types and recipe helpers
openraw_studio.raw        RAW backend abstraction
openraw_studio.vision     Scene, face, person, mask, and quality analysis
openraw_studio.decision   Recipe creation from targets, constraints, confidence
openraw_studio.portrait   Face/person-aware pixel and geometry operations
openraw_studio.color      Scene-aware color transforms with skin protection
openraw_studio.film       Film profiles, grain, halation, bloom, LUT support
openraw_studio.qc         Artifact and quality checks
openraw_studio.export     JPEG/TIFF export and sidecar writing
openraw_studio.models     Model registry and inference runtime contracts
openraw_studio.presets    Processing preset and creative look formats
openraw_studio.pipeline   End-to-end orchestration contracts
openraw_studio.ui         Future desktop UI shell
```

## Data Flow

1. `PipelineRequest` identifies the source RAW file and output directory.
2. `RawProcessor.inspect` extracts metadata without modifying the RAW.
3. `RawProcessor.create_preview` creates a manageable preview artifact.
4. `VisionEngine.analyze` produces scene candidates, face observations, mask
   references, and quality hints.
5. `DecisionEngine.decide` chooses a processing profile and creative look, then
   creates a versioned recipe with conservative adjustments.
6. RAW, portrait, color, and film engines apply the recipe in sequence.
7. `QcEngine.evaluate` reports clipping, artifact, mask, color, and geometry
   concerns.
8. `ExportEngine.export` writes derivatives and recipe sidecars.

## Engine Rules

- RAW engines decode and prepare images. They do not classify scene content.
- Vision engines understand images. They do not make visual edits.
- Decision engines choose targets and constraints. They do not mutate pixels.
- Portrait engines operate from face IDs, landmarks, masks, and conservative
  strengths.
- Color engines accept scene intent and skin protection masks.
- Film engines keep tone/color/grain/halation/bloom/LUT behavior separate.
- QC engines flag or reduce risky processing where supported.
- Export engines write derivative images and recipe metadata only.

## Model Abstraction

Models are accessed through model descriptors and runtime interfaces, not direct
application calls to a specific package. A detector or classifier can therefore
be replaced without changing the decision, portrait, or pipeline layers.

Candidate model families from the brief, such as MediaPipe face landmarks,
MediaPipe segmentation, SigLIP/SigLIP 2 scene classification, ONNX Runtime, or
EasyPortrait-style parsing, must go through license and distribution review
before they are bundled.

## Configuration

The initial app config is defined in `configs/app.schema.json`.

It separates:

- import locations
- preview/output locations
- RAW backend choice
- inference device preference
- default processing profile
- default creative look
- safety limits

## Recipe Contract

The recipe schema is versioned as `recipe.v1` and stored in
`schemas/processing_recipe.schema.json`. Recipes must be treated as durable user
data. Future incompatible changes require a migration path.

## Preset Contract

Processing presets and creative looks are intentionally separate:

- Processing presets describe technical interpretation, targets, constraints,
  and confidence gates.
- Creative looks describe aesthetic transforms such as tone, color, LUT, grain,
  halation, bloom, and density.

Example files live under `presets/processing/` and `presets/looks/`.

## Desktop UI Direction

The future UI should prioritize the photo workspace, not a marketing page. The
first screen should support importing images, seeing processing status, comparing
original and processed versions, adjusting AUTO strength, and exporting results.

Beginner controls should be obvious and sparse. Advanced controls should expand
without hiding the current photo or comparison view.
