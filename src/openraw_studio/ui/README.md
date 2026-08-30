# UI

The first local desktop shell is available with `openraw app`.

On Windows, the friendliest repo entry point is:

```powershell
.\scripts\run_app.ps1
```

The UI should keep the image workspace first:

- import/watch status
- folder import and photo list
- Nikon `.NEF` / `.NRW` metadata import with renderable, preview-only, or import-only state
- selected photo metadata summary
- current OpenRAW Native support status
- next missing render-engine step for import-only or preview-only Nikon files
- planned preview/JPEG/recipe output paths
- current photo preview
- preview current/stale state
- built-in synthetic sample DNG/NEF creation
- before/after comparison
- AUTO action
- conservative Auto Adjust action
- exposure adjustment
- contrast and warmth adjustment
- preview-only refresh
- expandable advanced panels
- export controls
- batch export for currently supported folder files
- open generated JPEG action
- open output folder action
- saved recipe detection for restoring basic adjustments

The UI consumes the pipeline and recipe contracts rather than directly calling
RAW, vision, portrait, color, film, QC, or export implementations. The current
shell can preview/export supported DNG files and guarded TIFF-style Nikon sensor
files, including row-aligned 12/14-bit packed strip payloads, preview Nikon RAW
files that include embedded JPEGs, and import Nikon RAW metadata while keeping
advanced controls for later stages.
