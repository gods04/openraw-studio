"""Dependency-free V0.1 heuristic vision engine."""

from __future__ import annotations

from openraw_studio.core.domain import EngineInfo, ImageMetadata, ImageRef, SceneClassification, VisionAnalysis


class HeuristicVisionEngine:
    """Basic analysis used before real local AI models are wired in."""

    def engine_info(self) -> EngineInfo:
        return EngineInfo(
            name="heuristic-vision",
            version="0.1.0",
            backend="no-models",
            capabilities={"faces": False, "scene_heuristics": True},
        )

    def analyze(self, image: ImageRef, metadata: ImageMetadata) -> VisionAnalysis:
        scenes = [SceneClassification(label="general", confidence=0.35)]
        if metadata.iso is not None and metadata.iso >= 3200:
            scenes.append(SceneClassification(label="high_iso", confidence=0.45))

        return VisionAnalysis(
            scenes=tuple(scenes),
            faces=(),
            masks=(),
            engine=self.engine_info(),
        )
