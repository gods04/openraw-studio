# OpenRAW Render Engine

OpenRAW Studio should become its own photo application with its own rendering
identity.

The render engine is where OpenRAW owns:

- recipe interpretation
- RAW-to-preview flow
- exposure and tone decisions
- color rendering
- skin protection
- portrait-aware processing
- creative looks
- film simulation
- QC feedback
- final export behavior

External libraries may help decode early RAW data, but the product behavior
should belong to OpenRAW.

## Native Engine Goal

Long-term direction:

```text
RAW file
  -> OpenRAW native metadata/decoder layer
  -> OpenRAW sensor normalization
  -> OpenRAW demosaic
  -> OpenRAW color pipeline
  -> OpenRAW tone pipeline
  -> OpenRAW portrait/color/film stages
  -> OpenRAW export
```

The native engine does not need to support every camera on day one. It should
start narrow and become deeper over time.

## Build Strategy

### Stage 1 - Native Engine Skeleton

Status: started.

Own the product-level engine class and render plan:

- `NativeRawProcessor`
- native decode contract
- native render plan
- default pipeline backend
- recipe records `openraw-native`

This proves that OpenRAW, not darktable, is the default product path.

### Stage 2 - DNG-First Preview

Goal: generate a basic preview from the most approachable RAW path.

Preferred first target:

```text
DNG or simple Bayer RAW with documented metadata
```

Why DNG first:

- more standardized than many proprietary RAW formats
- good bridge for learning RAW internals
- can become the first public native-engine milestone

The output does not need to be beautiful yet. It needs to be traceable and
correct enough to inspect:

```text
read metadata
  -> read sensor data
  -> black/white level normalize
  -> simple demosaic
  -> apply camera matrix if available
  -> tone map to sRGB
  -> write preview
```

Status:

- lightweight TIFF/DNG metadata reader exists
- OpenRAW Native inspection records DNG metadata in recipes
- first uncompressed strip- and tile-based pixel extraction exists
- black/white level sensor normalization exists
- simple Bayer demosaic exists
- simple tone mapping and PNG preview encoding exist
- `openraw process --preview-only` can complete without final export
- local JPEG export engine records final derivative exports for narrow supported uncompressed DNG files
- DNG AsShotNeutral white balance and ColorMatrix1 transform are applied when available
- recipe-driven exposure, contrast, and warmth controls are applied to preview and JPEG export
- `openraw inspect` reports current Native support status before preview/export
- full camera-aware color conversion and high-quality JPEG/TIFF export are next

Current native metadata scope:

- TIFF byte order and IFD structure
- image width/height
- camera make/model
- DNG version
- CFA repeat pattern and CFA pattern
- black level
- white level
- color matrix
- as-shot neutral
- calibration illuminant

Current native pixel scope:

- `Compression=1`
- `BitsPerSample=16`
- `SamplesPerPixel=1`
- `StripOffsets`
- `StripByteCounts`
- `TileWidth`
- `TileLength`
- `TileOffsets`
- `TileByteCounts`
- simple 16-bit sample unpacking
- scalar black/white level normalization to 0.0-1.0 linear sensor values
- simple local-average Bayer demosaic for RGGB, GRBG, GBRG, and BGGR
- simple gamma preview transform
- PNG preview output
- preview-derived JPEG export output
- recipe-driven exposure/contrast/warmth adjustment

Not supported yet:

- compressed DNG
- packed 10/12/14-bit data
- multi-sample RGB DNG
- SubIFD selection beyond the simplest payload
- per-channel black level arrays
- high-quality demosaic
- full camera-aware color conversion, chromatic adaptation, and gamut mapping
- final tone mapping
- JPEG/TIFF native export

### Stage 3 - OpenRAW Color And Tone

Goal: make the preview look intentional.

Add:

- white balance
- exposure compensation
- highlight rolloff
- shadow recovery
- camera-to-working color transform
- working-to-sRGB transform
- simple local/global tone mapping

This is where OpenRAW starts to have its own look.

### Stage 4 - Better Demosaic And Quality

Goal: improve visual quality.

Add:

- better demosaic algorithm
- hot/dead pixel handling
- chroma noise handling
- edge-aware sharpening
- resolution-aware preview/export
- basic camera profiles

### Stage 5 - More RAW Formats

Goal: expand compatibility.

Add formats carefully:

- DNG first
- then one chosen camera family
- then broader support through researched decoders or custom parsers

Do not chase every camera format before the render pipeline is solid.

## Why Not Start With Every RAW Format?

RAW files are not one format. They are many camera-specific containers and sensor
interpretations. Starting with all formats would make the project huge before the
product has a working user experience.

The right move is:

```text
narrow format support
  + strong OpenRAW pipeline
  + honest roadmap
```

Then expand.

## Development Rule

Never fake rendering success.

If the native engine cannot render yet, it should say so clearly and still save
a recipe. That keeps the app truthful and testable.
