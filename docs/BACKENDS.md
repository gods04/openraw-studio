# RAW Backends

OpenRAW Studio uses replaceable RAW backends. The application code talks to the
`RawProcessor` interface, not directly to one specific RAW engine.

The user-facing app should remain OpenRAW Studio. Backends are implementation
details.

## Default Backend: OpenRAW Native

The default product path is `openraw-native`.

Status:

```text
foundation ready
Nikon NEF/NRW metadata import support
Nikon NEF/NRW embedded JPEG preview support
Nikon MakerNote compression metadata summary for 34713 render blockers
Nikon 34713 lossless Huffman sensor decode support for supported files
Nikon 1 J5 12-bit D20 non-split sensor decode
Nikon Z f HE* 14-bit verified-profile sensor decode with approximate nonlinear mapping
optimized Python bitstream/render loops for the current Nikon 34713 path
Nikon MakerNote black levels with conservative inactive-border fallback
standard Nikon as-shot white balance from MakerNote tag 0x000c
exact-model Nikon D500, 1 J5, and Z f camera profiles to linear sRGB
EXIF orientation handling for native Nikon renders
simple PNG preview support for narrow uncompressed DNG/Nikon files
local JPEG quality and lossless 8-bit TIFF export support for renderable DNG/Nikon files
safe camera/capture derivative metadata without GPS passthrough
12/14-bit packed strip payloads supported for the current DNG/Nikon path
16-bit strip and tile payloads supported for the current DNG/Nikon path
guarded Nikon NEF/NRW native sensor decode for TIFF-style uncompressed Bayer payloads
compressed Nikon 34713 uses half-resolution preview and full-resolution bilinear final export
camera-aware color profiles cover D500, 1 J5, and Z f; other models use generic color
other compressed/proprietary Nikon NEF/NRW sensor payload variants not decoded yet
edge-aware demosaic and 16-bit linear TIFF export not implemented yet
```

The native engine scaffold is implemented in:

```text
src/openraw_studio/raw/native/
```

See `docs/OPENRAW_RENDER_ENGINE.md` for the implementation route.
Use `openraw inspect <path-to-photo>` to check whether one file fits the current
Native render path, whether a Nikon embedded preview can be extracted, or
whether it can be imported for metadata only, before processing.

## Experimental Backend: darktable-cli

V0.1 includes a `darktable-cli` adapter as a developer/experimental backend.

Why keep this adapter:

- mature open-source RAW developer
- available on Windows, macOS, and Linux
- supports command-line export
- useful for developer comparison while OpenRAW Native grows

The adapter is implemented in:

```text
src/openraw_studio/raw/darktable.py
```

This does not mean OpenRAW Studio is intended to become a darktable wrapper.
See `docs/RAW_ENGINE_STRATEGY.md` and `docs/COMMERCIALIZATION_STRATEGY.md`.

## Install

Install darktable from the official project site:

```text
https://www.darktable.org/install/
```

After installation, check whether `darktable-cli` is visible to OpenRAW Studio:

```powershell
openraw doctor --include-experimental-backends
```

If the command reports that `darktable-cli` is missing, dry-run planning still
works. Normal users should not be expected to understand this backend.

## Current Export Command Shape

The adapter follows the official `darktable-cli` invocation pattern:

```text
darktable-cli <input file> <output file> --width <max> --height <max> --hq true --upscale false --apply-custom-presets false
```

For full-size JPEG export, the adapter omits width/height limits.

## Current Limitations

- no OpenRAW-generated XMP sidecar yet
- no darktable style integration yet
- no advanced RAW exposure/color recipe translation yet
- experimental backend metadata translation is minimal in V0.1
- errors are surfaced, but retry/recovery policy is not built yet

This is enough for the first real preview/export milestone. Later versions can
translate OpenRAW recipes into backend-specific settings or replace darktable
entirely.

## Licensing Note

darktable is GPL-licensed. OpenRAW Studio currently treats it as a user-installed
external tool. Do not bundle darktable binaries in OpenRAW Studio releases until
distribution obligations are reviewed and documented in `docs/MODEL_LICENSES.md`.
