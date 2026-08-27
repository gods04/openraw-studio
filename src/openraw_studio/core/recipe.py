"""Versioned processing recipe helpers."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

RECIPE_SCHEMA_VERSION = "recipe.v1"
PROCESSING_PRESET_SCHEMA_VERSION = "processing-preset.v1"
CREATIVE_LOOK_SCHEMA_VERSION = "creative-look.v1"


def utc_now_iso() -> str:
    """Return an ISO timestamp suitable for recipe metadata."""

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def new_recipe(
    source_path: str | Path,
    *,
    processing_profile: str = "general",
    creative_look: str = "clean",
    recipe_id: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a minimal non-destructive recipe dictionary."""

    return {
        "schema_version": RECIPE_SCHEMA_VERSION,
        "recipe_id": recipe_id or str(uuid4()),
        "created_at": utc_now_iso(),
        "source": {
            "path": str(source_path),
            "checksum_sha256": None,
            "immutable": True,
            "metadata": dict(metadata or {}),
        },
        "processing_profile": processing_profile,
        "creative_look": creative_look,
        "analysis": {
            "scenes": [],
            "faces": [],
            "quality": {},
        },
        "decisions": {
            "confidence": 0.0,
            "rationale": [],
        },
        "adjustments": {
            "raw": {},
            "portrait": {
                "global": {},
                "faces": [],
            },
            "color": {},
            "film": {},
        },
        "engines": [],
        "exports": [],
    }


def recipe_sidecar_path(source_path: str | Path, recipe_dir: str | Path | None = None) -> Path:
    """Return the default sidecar path for a source RAW file."""

    source = Path(source_path)
    filename = f"{source.name}.recipe.json"
    if recipe_dir is not None:
        return Path(recipe_dir) / filename
    return source.with_name(filename)


def write_recipe(recipe: Mapping[str, Any], output_path: str | Path) -> Path:
    """Write a recipe JSON sidecar after lightweight invariant validation."""

    validate_recipe_shape(recipe)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(recipe, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def validate_recipe_shape(recipe: Mapping[str, Any]) -> None:
    """Run lightweight validation for core invariants before full JSON Schema."""

    if recipe.get("schema_version") != RECIPE_SCHEMA_VERSION:
        raise ValueError("unsupported recipe schema_version")
    source = recipe.get("source")
    if not isinstance(source, Mapping):
        raise ValueError("recipe source must be an object")
    if source.get("immutable") is not True:
        raise ValueError("recipe source must mark the RAW as immutable")
    for key in ("analysis", "decisions", "adjustments", "engines", "exports"):
        if key not in recipe:
            raise ValueError(f"recipe missing required key: {key}")
