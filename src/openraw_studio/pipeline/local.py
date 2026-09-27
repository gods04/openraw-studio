"""Local V0.1 pipeline implementation."""

from __future__ import annotations

from pathlib import Path
import inspect
from typing import Any, Mapping

from openraw_studio.core.artifacts import ArtifactPlan
from openraw_studio.core.domain import ImageAsset, ImageMetadata, ImageRef
from openraw_studio.core.files import is_supported_raw_path, sha256_file
from openraw_studio.core.image_info import read_image_size
from openraw_studio.core.recipe import new_recipe, write_recipe
from openraw_studio.decision.interfaces import DecisionRequest
from openraw_studio.decision.rules import RuleBasedDecisionEngine
from openraw_studio.export.errors import ExportError
from openraw_studio.export.formats import export_display_name, normalize_export_format, validate_export_quality
from openraw_studio.export.interfaces import ExportEngine, ExportRequest
from openraw_studio.export.local import LocalImageExportEngine
from openraw_studio.pipeline.errors import BackendUnavailableError, PipelineError, SourceFileError
from openraw_studio.pipeline.interfaces import PipelineRequest, PipelineResult
from openraw_studio.qc.rendered import analyze_rendered_image
from openraw_studio.raw.errors import RawProcessingError
from openraw_studio.raw.interfaces import RawProcessor, RawRenderRequest
from openraw_studio.raw.native import NativeRawProcessor
from openraw_studio.raw.native.support import inspect_native_support
from openraw_studio.vision.heuristic import HeuristicVisionEngine


NIKON_RAW_EXTENSIONS = {".nef", ".nrw"}


class LocalPhotoPipeline:
    """A dependency-free pipeline spine for V0.1 development."""

    def __init__(
        self,
        *,
        raw_processor: RawProcessor | None = None,
        export_engine: ExportEngine | None = None,
        processing_presets: Mapping[str, Mapping[str, Any]] | None = None,
        creative_looks: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> None:
        self.raw_processor = raw_processor or NativeRawProcessor()
        self.export_engine = export_engine or LocalImageExportEngine()
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

        try:
            export_format = normalize_export_format(request.export_format)
            export_quality = validate_export_quality(request.export_quality)
        except ValueError as exc:
            raise PipelineError(str(exc)) from exc

        plan = ArtifactPlan.for_source(source, request.output_dir, export_format=export_format)
        plan.ensure_directories()
        preview_path = _preview_path_for_source(plan, source, self.raw_processor)
        planned_artifacts = _planned_artifacts_with_preview(plan, preview_path)

        source_asset = ImageAsset(path=source.resolve())
        inspection = self.raw_processor.inspect(source_asset)
        metadata = inspection.metadata
        metadata_dict = dict(metadata.raw)
        metadata_dict["checksum_sha256"] = metadata_dict.get("checksum_sha256") or sha256_file(source)
        source_asset = ImageAsset(path=source.resolve(), checksum_sha256=metadata_dict["checksum_sha256"])

        preview_ref = ImageRef(
            path=preview_path,
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
        raw_adjustments = _raw_adjustments_with_overrides(recipe["adjustments"].get("raw", {}), request.overrides)
        recipe["adjustments"]["raw"] = raw_adjustments
        recipe["engines"] = [
            self.vision.engine_info().__dict__,
            self.decision.engine_info().__dict__,
            self.raw_processor.engine_info().__dict__,
            self.export_engine.engine_info().__dict__,
        ]
        recipe["exports"] = []
        recipe["output"] = {
            "format": export_format,
            "quality": export_quality if export_format == "jpeg" else None,
            "bit_depth": 8,
            "compression": "jpeg" if export_format == "jpeg" else "tiff_deflate",
            "metadata_policy": "safe-capture-no-gps-v0.1",
        }
        recipe["qc"] = {
            "status": "not_run",
            "reason": "Rendered-preview QC runs after preview creation.",
        }
        recipe["planned_artifacts"] = planned_artifacts

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
                preview_reused = request.reuse_existing_preview and _preview_is_fresh(source, preview_path)
                if preview_reused:
                    preview_ref = _existing_preview_ref(source, preview_path, self.raw_processor)
                else:
                    preview_ref = _create_preview_with_recipe(
                        self.raw_processor,
                        source_asset,
                        preview_path,
                        recipe,
                    )
                _record_rendered_preview_qc(recipe, preview_ref)
                if request.preview_only:
                    recipe["pipeline"] = {
                        "mode": "preview_only",
                        "rendered": True,
                        "preview_rendered": True,
                        "export_rendered": False,
                        "message": _preview_only_message(preview_ref),
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
                            "planned_artifacts": planned_artifacts,
                        },
                    )
                rendered_ref = self.raw_processor.render_base(
                    RawRenderRequest(
                        source=source_asset,
                        recipe=recipe,
                        output_path=plan.export_path,
                        max_dimension=None,
                        color_space="sRGB",
                        quality=export_quality,
                    )
                )
                export_result = self.export_engine.export(
                    ExportRequest(
                        image=rendered_ref,
                        recipe=recipe,
                        output_path=plan.export_path,
                        format=export_format,
                        quality=export_quality,
                        write_recipe_sidecar=False,
                    )
                )
                export_ref = export_result.exported
            except RawProcessingError as exc:
                recipe["pipeline"] = {
                    "mode": "render",
                    "rendered": False,
                    "message": str(exc),
                }
                if preview_ref.role == "preview" and preview_ref.path.exists():
                    recipe["preview"] = {
                        "path": str(preview_ref.path),
                        "width": preview_ref.width,
                        "height": preview_ref.height,
                    }
                write_recipe(recipe, plan.recipe_path)
                raise BackendUnavailableError(
                    f"{exc} A recipe was written to {plan.recipe_path}. "
                    "Run with --dry-run for recipe-only planning, or use an experimental backend for development."
                ) from exc
            except ExportError as exc:
                recipe["pipeline"] = {
                    "mode": "render",
                    "rendered": False,
                    "message": f"Export failed: {exc}",
                }
                write_recipe(recipe, plan.recipe_path)
                raise PipelineError(f"Export failed: {exc}") from exc

            recipe["pipeline"] = {
                "mode": "render",
                "rendered": True,
                "preview_reused": preview_reused,
                "message": (
                    f"Existing preview was reused and {export_display_name(export_format)} export was rendered."
                    if preview_reused
                    else f"Preview and {export_display_name(export_format)} export were rendered."
                ),
            }
            recipe["exports"] = [
                {
                    "path": str(export_ref.path),
                    "format": export_format,
                    "width": export_ref.width,
                    "height": export_ref.height,
                    "quality": export_result.metadata.get("quality"),
                    "bit_depth": export_result.metadata.get("bit_depth", 8),
                    "compression": export_result.metadata.get("compression"),
                    "metadata_policy": export_result.metadata.get("metadata_policy"),
                    "engine": self.export_engine.engine_info().name,
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
                    "planned_artifacts": planned_artifacts,
                    "export_format": export_format,
                    "preview_reused": preview_reused,
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
                "planned_artifacts": planned_artifacts,
                "export_format": export_format,
            },
        )


def _pipeline_mode(request: PipelineRequest) -> str:
    if request.dry_run:
        return "dry_run"
    if request.preview_only:
        return "preview_only"
    return "render"


def _preview_path_for_source(plan: ArtifactPlan, source: Path, raw_processor: RawProcessor) -> Path:
    if isinstance(raw_processor, NativeRawProcessor) and source.suffix.lower() in NIKON_RAW_EXTENSIONS:
        support = inspect_native_support(source)
        if support.can_render:
            return plan.preview_path
        return plan.preview_path.with_name(f"{source.stem}.preview.jpg")
    return plan.preview_path


def _planned_artifacts_with_preview(plan: ArtifactPlan, preview_path: Path) -> dict[str, str]:
    planned = plan.as_dict()
    planned["preview"] = str(preview_path)
    return planned


def _preview_only_message(preview: ImageRef) -> str:
    if preview.color_space == "embedded-jpeg":
        return "Embedded JPEG preview was extracted; final export was skipped by request."
    return "Preview was rendered; final export was skipped by request."


def _create_preview_with_recipe(raw_processor: RawProcessor, source: ImageAsset, output_path: Path, recipe: Mapping[str, Any]) -> ImageRef:
    """Pass recipes to new backends while keeping older adapters compatible."""

    parameters = inspect.signature(raw_processor.create_preview).parameters
    if "recipe" in parameters:
        return raw_processor.create_preview(source, output_path, max_dimension=2048, recipe=recipe)
    return raw_processor.create_preview(source, output_path, max_dimension=2048)


def _existing_preview_ref(source: Path, preview_path: Path, raw_processor: RawProcessor) -> ImageRef:
    width, height = read_image_size(preview_path)
    if width <= 0 or height <= 0:
        raise OSError(f"Existing preview is not a readable image: {preview_path}")
    if preview_path.suffix.lower() in {".jpg", ".jpeg"}:
        color_space = "embedded-jpeg"
    elif isinstance(raw_processor, NativeRawProcessor) and source.suffix.lower() in NIKON_RAW_EXTENSIONS:
        color_space = "openraw-nikon-34713-rgb"
    else:
        color_space = "preview-rgb"
    return ImageRef(
        path=preview_path,
        width=width,
        height=height,
        color_space=color_space,
        role="preview",
    )


def _preview_is_fresh(source: Path, preview_path: Path) -> bool:
    try:
        return preview_path.is_file() and preview_path.stat().st_mtime_ns >= source.stat().st_mtime_ns
    except OSError:
        return False


def _record_rendered_preview_qc(recipe: dict[str, Any], preview: ImageRef) -> None:
    if preview.color_space == "embedded-jpeg":
        recipe["analysis"]["quality"] = {
            "scope": "camera-embedded-preview",
            "status": "not_run",
        }
        recipe["qc"] = {
            "status": "not_run",
            "reason": "The camera-authored embedded JPEG is not an OpenRAW render.",
        }
        return

    try:
        report = analyze_rendered_image(preview.path)
    except (OSError, RuntimeError, ValueError) as exc:
        recipe["analysis"]["quality"] = {
            "scope": "rendered-preview-rgb8",
            "status": "not_run",
        }
        recipe["qc"] = {
            "status": "not_run",
            "reason": f"Rendered-preview QC could not run: {exc}",
        }
        return

    quality = report.as_recipe_dict()
    recipe["analysis"]["quality"] = quality
    recipe["qc"] = {
        "status": report.status,
        "scope": quality["scope"],
        "warnings": list(report.warnings),
        "message": _quality_message(report.warnings),
    }


def _quality_message(warnings: tuple[str, ...]) -> str:
    if not warnings:
        return "Rendered preview passed the clipping check."
    labels = {
        "highlight_clipping": "highlight clipping",
        "shadow_clipping": "shadow clipping",
    }
    return "Check " + " and ".join(labels[warning] for warning in warnings) + "."


def _raw_adjustments_with_overrides(
    raw_adjustments: Mapping[str, Any],
    overrides: Mapping[str, Any],
) -> dict[str, Any]:
    updated = dict(raw_adjustments)
    for key in ("exposure", "contrast", "highlights", "shadows", "warmth", "tint", "saturation"):
        if key in overrides:
            updated[key] = overrides[key]
    return updated
