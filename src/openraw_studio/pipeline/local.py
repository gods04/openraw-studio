"""Local V0.1 pipeline implementation."""

from __future__ import annotations

from pathlib import Path
import inspect
from typing import Any, Mapping

from openraw_studio.core.artifacts import ArtifactPlan
from openraw_studio.core.domain import ImageAsset, ImageMetadata, ImageRef
from openraw_studio.core.files import is_supported_raw_path, sha256_file
from openraw_studio.core.recipe import new_recipe, write_recipe
from openraw_studio.decision.interfaces import DecisionRequest
from openraw_studio.decision.rules import RuleBasedDecisionEngine
from openraw_studio.pipeline.errors import BackendUnavailableError, SourceFileError
from openraw_studio.pipeline.interfaces import PipelineRequest, PipelineResult
from openraw_studio.raw.errors import RawProcessingError
from openraw_studio.raw.interfaces import RawProcessor, RawRenderRequest
from openraw_studio.raw.native import NativeRawProcessor
from openraw_studio.vision.heuristic import HeuristicVisionEngine


class LocalPhotoPipeline:
    """A dependency-free pipeline spine for V0.1 development."""

    def __init__(
        self,
        *,
        raw_processor: RawProcessor | None = None,
        processing_presets: Mapping[str, Mapping[str, Any]] | None = None,
        creative_looks: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> None:
        self.raw_processor = raw_processor or NativeRawProcessor()
        self.processing_presets = dict(processing_presets or {"general": {}, "portrait": {}})
        self.creative_looks = dict(creative_looks or {"clean": {}, "warm_film": {}})
        self.vision = HeuristicVisionEngine()
        self.decision = RuleBasedDecisionEngine()

    def process(self, request: PipelineRequest) -> PipelineResult:
        source = request.source_path.expanduser()
        if not source.exists() or not source.is_file():
            raise SourceFileError(f"Source file does not exist: {source}")
        if not is_supported_raw_path(source):
            raise SourceFileError(f"Unsupported RAW extension: {source.suffix or '<none>'}")

        plan = ArtifactPlan.for_source(source, request.output_dir)
        plan.ensure_directories()

        source_asset = ImageAsset(path=source.resolve())
        inspection = self.raw_processor.inspect(source_asset)
        metadata = inspection.metadata
        metadata_dict = dict(metadata.raw)
        metadata_dict["checksum_sha256"] = metadata_dict.get("checksum_sha256") or sha256_file(source)
        source_asset = ImageAsset(path=source.resolve(), checksum_sha256=metadata_dict["checksum_sha256"])

        preview_ref = ImageRef(
            path=plan.preview_path,
            width=0,
            height=0,
            color_space="unknown",
            role="planned-preview",
        )
        analysis = self.vision.analyze(preview_ref, metadata)
        decision = self.decision.decide(
            DecisionRequest(
                metadata=metadata,
                analysis=analysis,
                processing_presets=self.processing_presets,
                creative_looks=self.creative_looks,
                default_processing_profile=request.processing_profile or "general",
                default_creative_look=request.creative_look or "clean",
                user_constraints={
                    "auto_strength": request.auto_strength,
                    "low_confidence_edit_scale": 0.35,
                },
                requested_look=request.creative_look,
            )
        )

        recipe = new_recipe(
            source.resolve(),
            processing_profile=decision.processing_profile,
            creative_look=decision.creative_look,
            metadata=metadata_dict,
        )
        recipe["source"]["checksum_sha256"] = metadata_dict["checksum_sha256"]
        recipe["analysis"] = {
            "scenes": [
                {
                    "label": scene.label,
                    "confidence": scene.confidence,
                    "evidence": dict(scene.evidence),
                }
                for scene in analysis.scenes
            ],
            "faces": [
                {
                    "face_id": face.face_id,
                    "confidence": face.confidence,
                    "distance_class": face.distance_class.value,
                    "bounding_box": {
                        "x": face.bounding_box.x,
                        "y": face.bounding_box.y,
                        "width": face.bounding_box.width,
                        "height": face.bounding_box.height,
                    },
                }
                for face in analysis.faces
            ],
            "quality": {},
        }
        recipe["decisions"] = {
            "confidence": decision.confidence,
            "constraints": dict(decision.constraints),
            "rationale": list(decision.rationale),
        }
        recipe["adjustments"] = dict(decision.adjustments)
        raw_adjustments = dict(recipe["adjustments"].get("raw", {}))
        if "exposure" in request.overrides:
            raw_adjustments["exposure"] = request.overrides["exposure"]
        recipe["adjustments"]["raw"] = raw_adjustments
        recipe["engines"] = [
            self.vision.engine_info().__dict__,
            self.decision.engine_info().__dict__,
            self.raw_processor.engine_info().__dict__,
        ]
        recipe["exports"] = []
        recipe["qc"] = {
            "status": "not_run",
            "reason": "V0.1 QC is not implemented yet.",
        }
        recipe["planned_artifacts"] = plan.as_dict()

        recipe["pipeline"] = {
            "mode": _pipeline_mode(request),
            "rendered": False,
            "message": "Recipe and artifact paths were planned; no image pixels were rendered."
            if request.dry_run
            else "Rendering started.",
        }
        write_recipe(recipe, plan.recipe_path)

        if not request.dry_run:
            try:
                preview_ref = _create_preview_with_recipe(
                    self.raw_processor,
                    source_asset,
                    plan.preview_path,
                    recipe,
                )
                if request.preview_only:
                    recipe["pipeline"] = {
                        "mode": "preview_only",
                        "rendered": True,
                        "preview_rendered": True,
                        "export_rendered": False,
                        "message": "Preview was rendered; final export was skipped by request.",
                    }
                    recipe["preview"] = {
                        "path": str(preview_ref.path),
                        "width": preview_ref.width,
                        "height": preview_ref.height,
                    }
                    recipe_path = write_recipe(recipe, plan.recipe_path)
                    return PipelineResult(
                        recipe=recipe,
                        preview=preview_ref,
                        exports=(),
                        diagnostics={
                            "dry_run": False,
                            "preview_only": True,
                            "recipe_path": str(recipe_path),
                            "planned_artifacts": plan.as_dict(),
                        },
                    )
                export_ref = self.raw_processor.render_base(
                    RawRenderRequest(
                        source=source_asset,
                        recipe=recipe,
                        output_path=plan.export_path,
                        max_dimension=None,
                        color_space="sRGB",
                    )
                )
            except RawProcessingError as exc:
                recipe["pipeline"] = {
                    "mode": "render",
                    "rendered": False,
                    "message": str(exc),
                }
                write_recipe(recipe, plan.recipe_path)
                raise BackendUnavailableError(
                    f"{exc} A recipe was written to {plan.recipe_path}. "
                    "Run with --dry-run for recipe-only planning, or use an experimental backend for development."
                ) from exc

            recipe["pipeline"] = {
                "mode": "render",
                "rendered": True,
                "message": "Preview and preview-derived JPEG export were rendered.",
            }
            recipe["exports"] = [
                {
                    "path": str(export_ref.path),
                    "format": "jpeg",
                    "width": export_ref.width,
                    "height": export_ref.height,
                }
            ]
            recipe["preview"] = {
                "path": str(preview_ref.path),
                "width": preview_ref.width,
                "height": preview_ref.height,
            }
            recipe_path = write_recipe(recipe, plan.recipe_path)
            return PipelineResult(
                recipe=recipe,
                preview=preview_ref,
                exports=(export_ref,),
                diagnostics={
                    "dry_run": False,
                    "recipe_path": str(recipe_path),
                    "planned_artifacts": plan.as_dict(),
                },
            )

        recipe_path = write_recipe(recipe, plan.recipe_path)

        return PipelineResult(
            recipe=recipe,
            preview=preview_ref,
            exports=(),
            diagnostics={
                "dry_run": True,
                "preview_only": False,
                "recipe_path": str(recipe_path),
                "planned_artifacts": plan.as_dict(),
            },
        )


def _pipeline_mode(request: PipelineRequest) -> str:
    if request.dry_run:
        return "dry_run"
    if request.preview_only:
        return "preview_only"
    return "render"


def _create_preview_with_recipe(raw_processor: RawProcessor, source: ImageAsset, output_path: Path, recipe: Mapping[str, Any]) -> ImageRef:
    """Pass recipes to new backends while keeping older adapters compatible."""

    parameters = inspect.signature(raw_processor.create_preview).parameters
    if "recipe" in parameters:
        return raw_processor.create_preview(source, output_path, max_dimension=2048, recipe=recipe)
    return raw_processor.create_preview(source, output_path, max_dimension=2048)
