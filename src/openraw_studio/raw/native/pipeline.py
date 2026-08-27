"""Native RAW render pipeline contracts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True)
class NativeRenderPlan:
    """OpenRAW-owned interpretation of a recipe before pixel rendering."""

    source_path: Path
    output_path: Path
    max_dimension: int | None
    recipe: Mapping[str, Any]
    stages: tuple[str, ...]


def build_native_render_plan(
    source_path: Path,
    output_path: Path,
    recipe: Mapping[str, Any],
    *,
    max_dimension: int | None,
) -> NativeRenderPlan:
    """Build the planned native RAW stages for future renderer work."""

    return NativeRenderPlan(
        source_path=source_path,
        output_path=output_path,
        max_dimension=max_dimension,
        recipe=recipe,
        stages=(
            "decode",
            "black_white_level",
            "white_balance",
            "demosaic",
            "camera_to_working_color",
            "tone_map",
            "resize",
            "encode",
        ),
    )
