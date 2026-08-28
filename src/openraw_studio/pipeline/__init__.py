"""Pipeline orchestration interfaces."""

from openraw_studio.pipeline.batch import BatchItemResult, BatchResult, BatchSource, discover_batch_sources, run_batch_export
from openraw_studio.pipeline.errors import BackendUnavailableError, PipelineError, SourceFileError
from openraw_studio.pipeline.interfaces import PhotoPipeline, PipelineRequest, PipelineResult
from openraw_studio.pipeline.local import LocalPhotoPipeline

__all__ = [
    "BatchItemResult",
    "BatchResult",
    "BatchSource",
    "BackendUnavailableError",
    "LocalPhotoPipeline",
    "PhotoPipeline",
    "PipelineError",
    "PipelineRequest",
    "PipelineResult",
    "SourceFileError",
    "discover_batch_sources",
    "run_batch_export",
]
