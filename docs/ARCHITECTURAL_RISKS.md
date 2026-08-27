# Architectural Risks and Missing Components

## Risks

### RAW backend coupling

`darktable-cli` is useful as a V0.1 developer backend, but it has its own
processing model, sidecar format, supported cameras, GPL distribution
constraints, and product identity. The app must isolate backend-specific behavior
behind `RawProcessor` and must not become a darktable wrapper.

### License ambiguity

The brief names useful model families, but model code, weights, and datasets can
have different licenses. A permissive code license does not automatically make
weights or training data safe to redistribute.

### Portrait artifact risk

Geometry edits such as face slimming and eye enlargement can visibly bend
background objects. The portrait engine must keep warp strengths conservative
and expose QC hooks before those features ship.

### Mask quality dependency

Skin, teeth, hair, and clothing operations depend on mask quality. Poor masks
can make edits look worse than no edit. Low-confidence masks should reduce or
disable local adjustments.

### Scene classification overconfidence

Time-of-day, EXIF, color histograms, and semantic labels can contradict each
other. The decision engine should combine signals and confidence instead of
forcing one scene label.

### GPU-specific design

The development machine has an NVIDIA GPU, but the product should keep CPU and
future DirectML paths viable. Inference runtime selection belongs in config and
model runtime adapters.

### Recipe migration

Recipes are long-lived user data. Schema versioning is not enough; future
releases need migration tools and tests.

## Missing Components To Add Early

- asset catalog or lightweight database for imported files
- job queue and cancellation model
- artifact storage policy and cleanup rules
- structured logging and run diagnostics
- recipe migration framework
- reference image regression harness
- public test asset sourcing policy
- camera profile format
- plugin/backend capability discovery
- UI state model for before/after comparisons
- privacy policy for local-only processing and optional future cloud features
