"""Artifact path planning for one source photo."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ArtifactPlan:
    """Planned output paths for one processing run."""

    output_dir: Path
    preview_path: Path
    export_path: Path
    recipe_path: Path
    intermediate_path: Path

    @classmethod
    def for_source(cls, source_path: str | Path, output_dir: str | Path) -> "ArtifactPlan":
        source = Path(source_path)
        root = Path(output_dir)
        stem = source.stem
        return cls(
            output_dir=root,
            preview_path=root / "previews" / f"{stem}.preview.png",
            export_path=root / "exports" / f"{stem}.auto.jpg",
            recipe_path=root / "recipes" / f"{source.name}.recipe.json",
            intermediate_path=root / "intermediates" / f"{stem}.base.tif",
        )

    def ensure_directories(self) -> None:
        """Create all directories needed by the planned artifacts."""

        for path in (
            self.preview_path.parent,
            self.export_path.parent,
            self.recipe_path.parent,
            self.intermediate_path.parent,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def as_dict(self) -> dict[str, str]:
        """Return a JSON-friendly representation."""

        return {
            "output_dir": str(self.output_dir),
            "preview": str(self.preview_path),
            "export": str(self.export_path),
            "recipe": str(self.recipe_path),
            "intermediate": str(self.intermediate_path),
        }
