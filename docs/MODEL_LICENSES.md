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

An optional garment-color head uses that same encoder/checkpoint and license,
with project-authored photographic clothing descriptions. The checked combined
export is 351,820,318 bytes, SHA-256
`3813e10c680732d1b709736bdf3886113e24373d883a2abe71a2009cf08286d2`.
It adds no new model weights, training data, or runtime dependency. It remains
an uncalibrated local experiment; coarse clothing-color corroboration is not
identity/skin recognition or a general material classification guarantee.

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

### Local Person-Mask Experiment

The optional person model is the unmodified float ONNX
[PPHumanSeg artifact from OpenCV Zoo](https://github.com/opencv/opencv_zoo/tree/47534e27c9851bb1128ccc0102f1145e27f23f98/models/human_segmentation_pphumanseg).
That directory publishes an Apache-2.0 license with PaddlePaddle attribution;
the setup script retains its full `LICENSE` beside the model. The model is
6,163,938 bytes, SHA-256
`552d8a984054e59b5d773d24b9b12022b22046ceb2bbc4c9aaeaceb36a9ddf24`, matching
the pinned Git LFS pointer. The source revision is
`47534e27c9851bb1128ccc0102f1145e27f23f98`. Runtime checks the pinned hash, not
just a user-editable manifest. It uses the existing CPU ONNX Runtime; OpenCV
and Paddle are not app runtime dependencies.

OpenRAW's preprocessing uses RGB normalization and full-frame 192x192 resizing;
Pillow bilinear downsampling differs from the upstream OpenCV implementation.
This is evaluated locally, not a claim to reproduce upstream accuracy numbers.
The model can mark non-person shapes and miss small people. High-score area
gates plus CLIP crop corroboration limit which masks reach Auto. No identity,
face recognition, demographic inference, or training on user photographs occurs.
Public model distribution and broad photographic suitability remain under
review; weights, masks, and private validation photos are not published. The
training dataset is not redistributed or independently licensed by this app.

### Local Face-Metering Experiment

The optional face locator is the unmodified
[YuNet 2023mar ONNX artifact](https://github.com/opencv/opencv_zoo/tree/47534e27c9851bb1128ccc0102f1145e27f23f98/models/face_detection_yunet),
232,589 bytes, SHA-256
`8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4`.
The pinned directory publishes an
[MIT license with Shiqi Yu attribution](https://github.com/opencv/opencv_zoo/blob/47534e27c9851bb1128ccc0102f1145e27f23f98/models/face_detection_yunet/LICENSE),
retained in full beside the separately installed artifact. Runtime checks the
pinned hash before constructing a CPU ONNX session. No weights are bundled.

The fixed input is BGR float32, raw 0-255, 640x640. OpenRAW preserves aspect
ratio, uses Pillow bilinear resizing and bottom/right zero padding, and decodes
stride-center boxes with floating-point IoU suppression. OpenCV's
[FaceDetectorYN implementation](https://github.com/opencv/opencv/blob/4.x/modules/objdetect/src/face_detect.cpp)
was an external development oracle, not copied or required by the app. The
application uses face locations only as a photographic exposure-metering aid;
it does not perform identity recognition or infer demographics. The training
dataset is not redistributed. Public/commercial model distribution and general
photographic suitability still require review and broader evaluation.

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
