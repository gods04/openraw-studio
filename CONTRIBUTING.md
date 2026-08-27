# Contributing

OpenRAW Studio is intended to be understandable to people who arrive from
GitHub with no private context. New features should include enough documentation
and tests for another developer to run or verify them.

## Project Principles

- Never modify original RAW files.
- Save reproducible processing recipes.
- Keep engines replaceable.
- Keep AI models local-first where practical.
- Do not bundle model weights, LUTs, datasets, or photos without license review.
- Prefer small vertical slices over large rewrites.

## Before Adding A Dependency

Document why it is needed and update `docs/MODEL_LICENSES.md` when it is a
library, model, model weight, dataset, LUT, or asset. Code license, model weight
license, and dataset license are separate concerns.

## Before Adding Test Images

Do not commit private photos. Use public assets only when their licenses allow
the exact intended use, or keep local test photos outside Git.

## Development Checks

Run the current foundation tests:

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests
```

As implementation grows, add focused tests for the module you changed and a
pipeline smoke test when behavior crosses engine boundaries.

Every push and pull request is also checked by GitHub Actions on Python 3.11,
3.12, and 3.13. Keep changes focused, add a regression test for behavior, and
do not commit private RAW files, model weights, or generated output folders.
