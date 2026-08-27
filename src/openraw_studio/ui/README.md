# UI

The first local desktop shell is available with `openraw app`.

On Windows, the friendliest repo entry point is:

```powershell
.\scripts\run_app.ps1
```

The UI should keep the image workspace first:

- import/watch status
- current photo preview
- preview current/stale state
- built-in synthetic sample DNG creation
- before/after comparison
- AUTO action
- exposure adjustment
- contrast and warmth adjustment
- preview-only refresh
- expandable advanced panels
- export controls
- open generated JPEG action
- open output folder action

The UI consumes the pipeline and recipe contracts rather than directly calling
RAW, vision, portrait, color, film, QC, or export implementations. The current
shell is intentionally DNG-first and keeps advanced controls for later stages.
