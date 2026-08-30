# Release Builds

OpenRAW Studio does not have a polished installer yet. The current release path
is a Windows ZIP package created from the local desktop app.

## Current Windows Package

Build locally from the repository root:

```powershell
.\scripts\build_windows.ps1
```

The script will:

- create `.venv-build` if needed
- install OpenRAW Studio with packaging dependencies
- run the test suite
- build the desktop app with PyInstaller
- write `dist\OpenRAW-Studio-windows-x64.zip`

To skip tests during a quick local packaging experiment:

```powershell
.\scripts\build_windows.ps1 -SkipTests
```

Do not use `-SkipTests` for release builds.

## GitHub Actions

The `Build Windows App` workflow creates the same ZIP package on
`windows-latest`.

It runs when:

- a maintainer starts it manually from GitHub Actions
- a tag matching `v*` is pushed

The uploaded artifact is named:

```text
OpenRAW-Studio-windows-x64
```

## Manual Release Checklist

Before creating a GitHub Release:

- run the full test suite locally
- build the Windows ZIP locally or through GitHub Actions
- open the packaged app on Windows
- click `Create Sample DNG`
- process the synthetic DNG through the packaged app
- click `Create Sample NEF`
- process the synthetic Nikon NEF through the packaged app
- confirm preview PNG, JPEG export, and recipe JSON are created
- confirm README still describes the true current limitations
- confirm no private photos, model weights, LUTs, or generated outputs are committed

## Version Tags

Use semantic-style tags for public builds:

```powershell
git tag v0.1.0
git push origin v0.1.0
```

Tagging triggers the Windows package workflow. After it completes, download the
artifact, test it, and attach it to the GitHub Release.

## Installer Later

A normal installer is a later milestone. The ZIP package comes first because it
is easier to inspect, easier to debug, and easier for early open-source users to
trust.
