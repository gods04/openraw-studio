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
- unedited native RAW/current-edit comparison
- rendered-preview RGB/luminance histogram that follows Before/Camera Preview/After view
- rendered-preview shadow/highlight clipping percentages
- AUTO action
- conservative Auto Adjust action for native-renderable DNG/Nikon files
- exposure adjustment
- contrast, highlights, shadows, temperature, tint, and saturation adjustment
- automatic live preview while dragging, with no manual refresh action
- expandable advanced panels
- export controls
- batch export for currently supported folder files
- animated single-photo progress and determinate folder-export progress
- completed image dimensions and file-size feedback
- retained single-photo decode cache and unchanged-preview reuse for faster
  adjustment refresh and final export
- open generated JPEG action
- open embedded preview JPEG action for preview-only Nikon files
- open output folder action
- saved recipe detection for restoring basic adjustments
- automatic local edit retention, undo/redo, Auto strength, and preview zoom/pan
- per-photo Auto or saved-edit batch export with between-photo cancellation

The UI uses the pipeline and recipe contracts for saved artifacts and export.
`LivePreviewWorker` separately requests an in-memory, scene-linear display proxy
from the native RAW layer. It owns one background worker and a replaceable pending
request. Slider events are throttled (not debounced); an in-flight frame can be
displayed while a newer edit is pending, but switching photos invalidates old work.
Tk display changes happen only on the main thread. GPU failures fall back to CPU.
The current
shell can preview/export supported DNG files, guarded TIFF-style Nikon sensor
files, and supported Nikon 34713 lossless compressed files through a fast
half-resolution preview and full-resolution final renderer. Auto Adjust can analyze native-renderable files
through the current preview path. The shell can also extract embedded JPEG
previews from preview-only Nikon RAW files and import Nikon RAW metadata while
keeping advanced controls for later stages. Preview-only Nikon files can expose
an `Open Preview JPEG` action after automatic preview loading, while final export
controls stay disabled until native sensor rendering supports that file. Inline
before/after comparison remains on lightweight preview paths. Native Nikon
34713 files can show the camera-authored JPEG while the RAW loads. Once loaded,
comparison uses a cached unedited RAW render from the same linear proxy as the
edited image. The camera JPEG is never treated as the source for OpenRAW export.
`workspace.py` owns the photo-first layout; `editing.py` owns bounded history and
atomic local edit persistence. Worker callbacks enter Tk through a main-thread queue.
