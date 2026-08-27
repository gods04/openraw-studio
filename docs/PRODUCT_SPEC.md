# Product Spec

## Product Goal

OpenRAW Studio is intended to become a real, usable desktop computational
photography application. It should import RAW files, preserve the original
files, analyze each image locally, choose conservative adaptive processing,
apply modular image processing, and export high-quality JPEG/TIFF derivatives
with a complete recipe that can reproduce the edit later.

The product should eventually combine:

- professional RAW processing
- computational photography
- intelligent automatic editing
- portrait-aware enhancement
- creative color grading
- film simulation

The architecture must stay modular so RAW engines, AI models, algorithms, and UI
components can be replaced independently.

## Primary Platform

Initial target:

- Windows 11
- local filesystem workflow
- NVIDIA RTX acceleration where available
- CPU fallback by design

Future acceleration options may include DirectML or other runtimes. The core
contracts must not depend on one GPU vendor.

## Initial Workflow

```text
RAW file
  -> manual import or watched import folder
  -> RAW metadata and preview
  -> scene and portrait analysis
  -> decision engine creates recipe
  -> base RAW processing
  -> portrait/color/film stages as enabled
  -> QC pass
  -> JPEG/TIFF export
  -> recipe sidecar saved
```

Camera tethering is explicitly out of scope until a later release.

## Non-Destructive Editing

RAW files are immutable. OpenRAW Studio writes derivative image files and a
versioned processing recipe. The recipe is the source of truth for undo,
re-editing, re-exporting, debugging, regression testing, and preset evolution.

The recipe must record:

- source file identity
- metadata snapshot
- engine versions
- vision analysis
- selected processing profile
- selected creative look
- per-face adjustments where needed
- export settings
- QC findings

## User Types

Beginner users need one clear primary action: AUTO. The application should make
conservative, natural edits and expose before/after comparison.

Advanced users need expandable controls for:

- RAW adjustments
- portrait controls
- color controls
- creative looks
- film profile controls
- export settings

## UI Direction

The app should be simple to operate and visually premium. The intended feeling is
minimal, calm, photo-first, and polished, with Apple-style design principles as a
reference point: clean hierarchy, careful spacing, restrained visuals, and direct
interaction.

Do not copy Apple branding or assets. Use the design quality bar.

The beginner flow should be:

```text
Open app
  -> import RAW
  -> click AUTO
  -> compare before/after
  -> export
```

Advanced controls should be available through expandable panels, not pushed onto
the first-time user.

Mobile support may be considered much later. Current design decisions should not
block future mobile adaptation, but V0.1 remains desktop-first.

## Product Principles

1. AI understands the image; deterministic algorithms perform controlled edits.
2. Presets define targets and constraints, not only fixed slider values.
3. Portrait edits are mask-aware and distance-aware.
4. Creative grading protects skin tones.
5. Low-confidence analysis produces conservative processing.
6. RAW source files are never modified.
7. Every adjustment is reproducible from a saved recipe.
8. Processing should work locally without mandatory cloud APIs.
9. AI model providers and runtimes must be replaceable.
10. Third-party licenses are reviewed before bundling or distribution.

## V0.1 Product Scope

V0.1 proves the end-to-end pipeline with minimal algorithms:

- import or select RAW files
- read metadata and EXIF where possible
- generate a preview through a replaceable RAW backend
- run basic scene and portrait/non-portrait detection
- choose a processing profile
- render a base processed image
- export JPEG
- save recipe JSON

V0.1 does not implement face slimming, detailed segmentation, beauty retouching,
LUT import, film emulation, or production QC.
