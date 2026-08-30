# RAW Engine Strategy

OpenRAW Studio needs its own product-level RAW engine and render pipeline.

The user-facing product should be:

```text
OpenRAW Studio
```

not:

```text
a wrapper around darktable
```

## Engine Layers

Recommended architecture:

```text
OpenRAW Studio UI / CLI
  -> OpenRAW recipe and pipeline
  -> OpenRAW Render Engine
  -> RAW decoder / backend adapter
  -> backend implementation or library
```

Backends can change without changing the product identity. The render engine is
where OpenRAW should own the photo look.

See `docs/OPENRAW_RENDER_ENGINE.md` for the detailed render-engine plan.

## Backend Roles

### darktable-cli

Role:

```text
developer / experimental external backend
```

Use it to prove pipeline behavior quickly. Do not make it the long-term required
consumer dependency.

### LibRaw

Role:

```text
commercial-friendly RAW decoder candidate
```

Investigate as an optional decoder input, not the product identity. It can feed
an OpenRAW-controlled render pipeline more cleanly than launching a full external
app.

### rawpy

Role:

```text
Python prototype bridge to LibRaw
```

Useful for rapid experiments, tests, and early preview generation. Packaging
needs separate review because rawpy wraps LibRaw.

### Custom OpenRAW Pipeline

Role:

```text
main product direction
```

OpenRAW should own recipe interpretation, tone mapping, color rendering, skin
protection, portrait processing, film simulation, QC, and final export behavior.
Deeper custom demosaicing and camera-format parsing can come later.

## Priority

1. Keep the current `RawProcessor` interface.
2. Keep `openraw-native` as the default backend identity.
3. Keep darktable optional and developer-facing.
4. Expand DNG-first metadata extraction.
5. Import Nikon NEF/NRW metadata through the native TIFF reader.
6. Expand DNG-first pixel extraction.
7. Add tone mapping and preview encoding.
8. Extract Nikon NEF/NRW embedded JPEG previews when available.
9. Decode guarded Nikon NEF/NRW TIFF-style uncompressed Bayer payloads for final export.
10. Add support for common compressed Nikon NEF sensor payloads.
11. Research LibRaw/rawpy as optional decoder references or fallback inputs.
12. Move more processing into OpenRAW-owned rendering over time.

## User Experience Rule

Normal users should not need to know what RAW backend is being used.

If a backend is missing, the app should say something product-level:

```text
RAW processing is not available in this build yet.
```

not something developer-heavy unless the user opens diagnostics.
