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
simple PNG preview support for narrow DNG files
preview-derived JPEG export support for narrow DNG files
high-quality JPEG/TIFF export not implemented yet
```

The native engine scaffold is implemented in:

```text
src/openraw_studio/raw/native/
```

See `docs/OPENRAW_RENDER_ENGINE.md` for the implementation route.

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
- metadata extraction is filesystem-only in V0.1
- errors are surfaced, but retry/recovery policy is not built yet

This is enough for the first real preview/export milestone. Later versions can
translate OpenRAW recipes into backend-specific settings or replace darktable
entirely.

## Licensing Note

darktable is GPL-licensed. OpenRAW Studio currently treats it as a user-installed
external tool. Do not bundle darktable binaries in OpenRAW Studio releases until
distribution obligations are reviewed and documented in `docs/MODEL_LICENSES.md`.
