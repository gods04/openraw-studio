"""Advisory quality checks for rendered OpenRAW previews."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openraw_studio.qc.histogram import HistogramAnalysis, analyze_rgb_bytes


DEFAULT_CLIPPING_WARNING_THRESHOLD = 0.01


@dataclass(frozen=True)
class RenderedQualityReport:
    """Traceable clipping report computed from a bounded RGB preview sample."""

    sample_width: int
    sample_height: int
    histogram: HistogramAnalysis
    warning_threshold: float
    warnings: tuple[str, ...]

    @property
    def status(self) -> str:
        return "warning" if self.warnings else "pass"

    def as_recipe_dict(self) -> dict[str, Any]:
        return {
            "scope": "rendered-preview-rgb8",
            "status": self.status,
            "sample_width": self.sample_width,
            "sample_height": self.sample_height,
            "pixel_count": self.histogram.pixel_count,
            "shadow_clip_fraction": round(self.histogram.shadow_clip_fraction, 6),
            "highlight_clip_fraction": round(self.histogram.highlight_clip_fraction, 6),
            "warning_threshold": self.warning_threshold,
            "warnings": list(self.warnings),
        }


def analyze_rendered_image(
    path: str | Path,
    *,
    max_dimension: int = 512,
    warning_threshold: float = DEFAULT_CLIPPING_WARNING_THRESHOLD,
) -> RenderedQualityReport:
    """Analyze a bounded RGB copy of a rendered image without changing the file."""

    if max_dimension <= 0:
        raise ValueError("max_dimension must be positive")
    if not 0.0 <= warning_threshold <= 1.0:
        raise ValueError("warning_threshold must be between 0 and 1")

    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("Pillow is required for rendered-image quality checks") from exc

    with Image.open(Path(path)) as opened:
        sample = opened.convert("RGB")
    sample.thumbnail((max_dimension, max_dimension))
    histogram = analyze_rgb_bytes(sample.tobytes())

    warnings: list[str] = []
    if histogram.highlight_clip_fraction >= warning_threshold:
        warnings.append("highlight_clipping")
    if histogram.shadow_clip_fraction >= warning_threshold:
        warnings.append("shadow_clipping")
    return RenderedQualityReport(
        sample_width=sample.width,
        sample_height=sample.height,
        histogram=histogram,
        warning_threshold=warning_threshold,
        warnings=tuple(warnings),
    )
