# Commercialization Strategy

OpenRAW Studio starts as an open-source project, but the architecture should not
block future commercial options.

This document is planning guidance, not legal advice. Before selling a packaged
app, bundling third-party binaries, or changing the license strategy, get a real
legal review.

## Current Recommendation

Keep OpenRAW Studio open-source for now, but design it so future commercial
paths remain possible.

Recommended direction:

```text
Open-source core foundation
  + commercial-friendly RAW engine choices
  + optional paid packaging/services/features later
```

Do not build the product around a GPL dependency that forces the whole app into
one licensing shape unless we intentionally choose that route.

## darktable Commercial Use

darktable is open-source and GPL-licensed. GPL software can be used commercially:
selling copies or charging for services is allowed.

The important issue is distribution and integration:

- If users install darktable separately and OpenRAW Studio only calls it as an
  external executable, OpenRAW Studio can usually remain a separate program.
- If OpenRAW Studio bundles darktable binaries in its installer, distribution
  obligations apply.
- If OpenRAW Studio incorporates or links darktable code directly, the app may
  need to be GPL-compatible and source-available.

Therefore, darktable should be treated as:

```text
developer/experimental external backend
not the long-term required product engine
```

## Better Long-Term RAW Engine Direction

For a future commercial desktop app, investigate a RAW engine that can be
embedded more cleanly:

- LibRaw through C/C++ integration
- rawpy for Python prototyping
- a custom OpenRAW processing pipeline later

LibRaw is more promising for commercial flexibility because its official
licensing options include LGPL 2.1 and CDDL 1.0. The exact build/linking choice
still needs review before release packaging.

## Open Source vs Commercial

There are three realistic paths.

### Path A - Fully Open Source

Everything remains open-source.

Pros:

- easiest community trust
- easier contributor adoption
- fits photography/developer culture
- avoids suspicion around local photo processing

Cons:

- competitors can reuse the code
- monetization must come from services, packaging, support, presets, cloud, or
  optional pro features

### Path B - Open Core

Core processing remains open-source. Paid parts can be packaging, sync, pro UI,
team workflows, premium looks, or model packs if licenses allow.

Pros:

- keeps public trust
- allows a business later
- lets contributors help the foundation
- avoids locking the whole project too early

Cons:

- requires clear boundaries between free and paid parts
- contribution licensing needs discipline

### Path C - Proprietary App Later

The app starts open but later moves some or all future code closed-source.

Pros:

- strongest control over commercial product
- easier to protect product differentiation

Cons:

- earlier MIT releases remain open forever
- contributors may resist unless licensing policy is clear early
- relicensing community contributions can become difficult without a CLA

## Recommended Path For OpenRAW Studio

Use Path B: open core.

Suggested policy:

- keep the current foundation open-source
- keep engine interfaces open
- avoid GPL-only mandatory internals for the main product
- use commercial-friendly dependencies where possible
- document every third-party component
- consider a Contributor License Agreement before accepting large external
  contributions
- leave room for paid packaging, Pro features, model packs, presets, or mobile
  companion features later

## Immediate Engineering Consequence

Do not present darktable as something normal users must install.

In docs and UI:

```text
OpenRAW Studio processes your photo.
```

Not:

```text
Install darktable first, then OpenRAW Studio can work.
```

Internally, darktable may still be useful for early development, but the product
roadmap should prioritize an OpenRAW-controlled RAW engine path.
