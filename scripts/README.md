# Scripts

This directory contains small operational scripts that make the project easier
to run from a fresh clone.

## Run The Desktop App On Windows

Use:

```powershell
.\scripts\run_app.ps1
```

Or double-click:

```text
scripts\run_app.cmd
```

The launcher creates `.venv` when needed, installs OpenRAW Studio locally, and
starts the desktop app with `python -m openraw_studio app`.

## Create A Safe Sample DNG

Use:

```powershell
python scripts\create_sample_dng.py
```

This writes `sample-data\openraw-synthetic.DNG`, a tiny synthetic RAW-like test
file that is generated locally and ignored by Git.

## Build A Windows Package

Use:

```powershell
.\scripts\build_windows.ps1
```

This writes:

```text
dist\OpenRAW-Studio-windows-x64.zip
```

Future scripts may include:

- model download helpers
- license audit helpers
- reference regression runners
- recipe migration tools
- local environment checks

Scripts should be deterministic and safe to run from the repository root.
