import unittest
from pathlib import Path

from openraw_studio.core.domain import (
    EngineInfo,
    ImageAsset,
    ImageMetadata,
    ImageRef,
    RawInspection,
    VisionAnalysis,
)
from openraw_studio.decision.interfaces import DecisionEngine, DecisionRequest
from openraw_studio.export.interfaces import ExportEngine, ExportRequest, ExportResult
from openraw_studio.pipeline.interfaces import PhotoPipeline, PipelineRequest, PipelineResult
from openraw_studio.raw.interfaces import RawProcessor, RawRenderRequest
from openraw_studio.render.interfaces import (
    CameraColorConverter,
    Demosaicer,
    LinearImage,
    RawDecoder,
    RenderEngine,
    RenderRequest,
    RenderResult,
    SensorImage,
    ToneMapper,
    WorkingImage,
)
from openraw_studio.vision.interfaces import VisionEngine


class FakeRawProcessor:
    def engine_info(self) -> EngineInfo:
        return EngineInfo(name="fake-raw", version="0")

    def inspect(self, source: ImageAsset) -> RawInspection:
        return RawInspection(source=source, metadata=ImageMetadata(), engine=self.engine_info())

    def create_preview(self, source: ImageAsset, output_path: Path, max_dimension: int) -> ImageRef:
        return ImageRef(path=output_path, width=max_dimension, height=max_dimension, color_space="sRGB", role="preview")

    def render_base(self, request: RawRenderRequest) -> ImageRef:
        return ImageRef(path=request.output_path, width=1, height=1, color_space=request.color_space, role="base")

    def export_intermediate(self, request: RawRenderRequest) -> ImageRef:
        return ImageRef(path=request.output_path, width=1, height=1, color_space=request.color_space, role="intermediate")


class FakeVisionEngine:
    def engine_info(self) -> EngineInfo:
        return EngineInfo(name="fake-vision", version="0")

    def analyze(self, image: ImageRef, metadata: ImageMetadata) -> VisionAnalysis:
        return VisionAnalysis(engine=self.engine_info())


class FakeDecisionEngine:
    def engine_info(self) -> EngineInfo:
        return EngineInfo(name="fake-decision", version="0")

    def decide(self, request: DecisionRequest):
        raise NotImplementedError


class FakeExportEngine:
    def engine_info(self) -> EngineInfo:
        return EngineInfo(name="fake-export", version="0")

    def export(self, request: ExportRequest) -> ExportResult:
        return ExportResult(exported=request.image)

    def supported_formats(self):
        return ("jpeg", "tiff")


class FakePipeline:
    def process(self, request: PipelineRequest) -> PipelineResult:
        return PipelineResult(recipe={}, diagnostics={"source": str(request.source_path)})


class FakeRawDecoder:
    def engine_info(self) -> EngineInfo:
        return EngineInfo(name="fake-decoder", version="0")

    def decode(self, source: ImageAsset) -> SensorImage:
        return SensorImage(source=source, width=1, height=1)


class FakeDemosaicer:
    def engine_info(self) -> EngineInfo:
        return EngineInfo(name="fake-demosaic", version="0")

    def demosaic(self, sensor: SensorImage) -> LinearImage:
        return LinearImage(width=sensor.width, height=sensor.height, color_space="camera-linear")


class FakeCameraColorConverter:
    def engine_info(self) -> EngineInfo:
        return EngineInfo(name="fake-color-convert", version="0")

    def convert(self, image: LinearImage, metadata: ImageMetadata) -> WorkingImage:
        return WorkingImage(width=image.width, height=image.height, color_space="OpenRAW Working")


class FakeToneMapper:
    def engine_info(self) -> EngineInfo:
        return EngineInfo(name="fake-tone", version="0")

    def apply(self, image: WorkingImage, recipe) -> WorkingImage:
        return image


class FakeRenderEngine:
    def engine_info(self) -> EngineInfo:
        return EngineInfo(name="fake-render", version="0")

    def render(self, request: RenderRequest) -> RenderResult:
        image = ImageRef(path=request.output_path, width=1, height=1, color_space=request.output_color_space, role="render")
        return RenderResult(image=image, recipe=request.recipe)


class EngineContractTests(unittest.TestCase):
    def test_runtime_protocols_are_structural(self) -> None:
        self.assertIsInstance(FakeRawProcessor(), RawProcessor)
        self.assertIsInstance(FakeVisionEngine(), VisionEngine)
        self.assertIsInstance(FakeDecisionEngine(), DecisionEngine)
        self.assertIsInstance(FakeExportEngine(), ExportEngine)
        self.assertIsInstance(FakePipeline(), PhotoPipeline)
        self.assertIsInstance(FakeRawDecoder(), RawDecoder)
        self.assertIsInstance(FakeDemosaicer(), Demosaicer)
        self.assertIsInstance(FakeCameraColorConverter(), CameraColorConverter)
        self.assertIsInstance(FakeToneMapper(), ToneMapper)
        self.assertIsInstance(FakeRenderEngine(), RenderEngine)


if __name__ == "__main__":
    unittest.main()
