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
| Pillow | image encoding library | python-pillow.github.io / PyPI | >=10.0 | MIT-CMU / HPND-style permissive license | yes | yes | yes | allowed | Used for JPEG/TIFF derivative encoding and desktop display; not a RAW engine. |
| Tifffile | TIFF container library | https://pypi.org/project/tifffile/2026.3.3/ | 2026.3.3 | BSD-3-Clause | yes | yes | yes | allowed | Encodes genuine uint16 RGB TIFF with stdlib Deflate; not a RAW engine. Pinned to retain Python 3.11 support. Full license bundled. |
| Nikon D500 camera calibration constants | profile data | Adobe DNG Converter data published in LibRaw `colordata.cpp` | 9-value D500 matrix | factual calibration data; provenance documented | review before closed-source commercial distribution | yes with source notice | yes | open-source-ok | OpenRAW's matrix math is project-authored; no LibRaw code or binary is bundled. |

## Candidate Components To Review

### Local Scene Experiment

The optional local scene beta uses [OpenAI CLIP](https://github.com/openai/CLIP)
at source revision `d05afc436d78f1c48dc0dbf8e5980a9d471f35f6`, under the upstream
[MIT license](https://github.com/openai/CLIP/blob/main/LICENSE). The separately
downloaded ViT-B/32 checkpoint is the upstream published artifact with SHA-256
`40d365715913c9da98579312b702a82c18be219cc2a73407c4526f58eba950af`.
The exported local encoder is 351,751,892 bytes in the checked toolchain; its
SHA-256 and prompt hash are stored in its generated manifest. The MIT text is
retained beside it. No model or private image is committed or bundled with the
Windows app, and the source model's training data is not distributed here.

**Public/commercial model distribution remains under review.** The upstream
[model card](https://github.com/openai/CLIP/blob/main/model-card.md) describes
research use, warns against untested deployment, and calls for domain-specific
evaluation. A repository code license is not a blanket clearance of training
data rights or a claim of suitability for every downstream deployment. This
local experiment is limited to photography scene/lighting hints, with no
identity, demographic inference, or face recognition. Color changes remain
bounded and reversible. Broader evaluation is needed before a public release.

The optional CPU runtime is ONNX Runtime 1.30.0, under its
[MIT license](https://github.com/microsoft/onnxruntime/blob/v1.30.0/LICENSE).
Windows builds retain the installed runtime's complete `ThirdPartyNotices.txt`
and license, plus Protobuf's installed license, FlatBuffers' Apache-2.0 license
and Google attribution, and packaging's installed licenses. ONNX Runtime
telemetry events are disabled before creating the inference session.

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
| ONNX Runtime | inference runtime | microsoft/onnxruntime | 1.30.0 | MIT plus third-party notices | per license terms | retain notices | yes | runtime included | CPU-only local scene beta; model weights remain separate. CUDA/DirectML paths are not implemented for this classifier. |

## Policy

- Do not commit large model weights to Git.
- Do not commit private personal photographs to Git.
- Do not add research-only or non-commercial models to production builds unless
  the project explicitly decides to make that limitation visible to users.
- Track code license, model weight license, dataset license, redistribution
  permission, commercial-use permission, and attribution separately.
- Use Apache-2.0, MIT, or BSD style components when practical.
