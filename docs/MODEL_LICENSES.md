# Model and Third-Party License Register

No third-party model weights, LUTs, datasets, or proprietary photographic assets
are bundled in this repository yet.

Every third-party component must be reviewed before it is added to a
distributable build. This includes libraries, command-line tools, model code,
model weights, datasets, LUTs, sample images, icons, and other assets.

## Required Review Fields

| Name | Type | Source | Version | License | Commercial use | Redistribution | Attribution | Status | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Python standard library | library | python.org | project runtime | PSF | yes | yes | yes | allowed | Used for CLI, JSON, filesystem, hashing, and PNG preview encoding. |
| NumPy | numerical array library | numpy.org / PyPI | >=1.26 | BSD-3-Clause | yes | yes | yes | allowed | Used for chunked full-resolution Bayer interpolation and color processing; not a RAW format decoder. |
| Pillow | image encoding library | python-pillow.github.io / PyPI | >=10.0 | MIT-CMU / HPND-style permissive license | yes | yes | yes | allowed | Used for JPEG export encoding only; not a RAW engine. |
| Nikon D500 camera calibration constants | profile data | Adobe DNG Converter data published in LibRaw `colordata.cpp` | 9-value D500 matrix | factual calibration data; provenance documented | review before closed-source commercial distribution | yes with source notice | yes | open-source-ok | OpenRAW's matrix math is project-authored; no LibRaw code or binary is bundled. |

## Candidate Components To Review

These are candidates from the product brief. They are not approved for bundling
until the review fields above are completed with source evidence.

| Name | Type | Source | Version | License | Commercial use | Redistribution | Attribution | Status | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| darktable-cli | external RAW backend | darktable.org | 5.x user-installed | GPL-3.0-or-later | yes | review before bundling | GPL attribution/license required if distributed | external-ok | Used as a user-installed executable in V0.1; do not bundle until distribution obligations are reviewed. |
| RawTherapee CLI | external RAW backend | TBD | TBD | TBD | TBD | TBD | TBD | needs review | Alternative RAW backend. |
| LibRaw | RAW library/reference implementation | github.com/LibRaw/LibRaw | current upstream reference only | LGPL-2.1 or CDDL-1.0 | yes under license terms | review before bundling | required if distributed | reference-only | Used only as a development oracle today; not a runtime dependency or bundled component. |
| MediaPipe Face Landmarker | model/runtime | TBD | TBD | TBD | TBD | TBD | TBD | needs review | Candidate for landmarks; model weight terms must be checked separately from code. |
| MediaPipe Image Segmenter | model/runtime | TBD | TBD | TBD | TBD | TBD | TBD | needs review | Candidate for person segmentation; model weight terms must be checked separately from code. |
| SigLIP or SigLIP 2 | model | TBD | TBD | TBD | TBD | TBD | TBD | needs review | Candidate scene classifier; code, weights, and dataset terms require separate review. |
| EasyPortrait-style face parsing | model | TBD | TBD | TBD | TBD | TBD | TBD | needs review | Must avoid research-only or non-commercial weights without explicit approval. |
| ONNX Runtime | inference runtime | TBD | TBD | TBD | TBD | TBD | TBD | needs review | Candidate local inference runtime for CPU/CUDA/DirectML paths. |

## Policy

- Do not commit large model weights to Git.
- Do not commit private personal photographs to Git.
- Do not add research-only or non-commercial models to production builds unless
  the project explicitly decides to make that limitation visible to users.
- Track code license, model weight license, dataset license, redistribution
  permission, commercial-use permission, and attribution separately.
- Use Apache-2.0, MIT, or BSD style components when practical.
