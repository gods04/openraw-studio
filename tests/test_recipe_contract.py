import json
import unittest
from pathlib import Path

from openraw_studio.core.recipe import (
    CREATIVE_LOOK_SCHEMA_VERSION,
    PROCESSING_PRESET_SCHEMA_VERSION,
    RECIPE_SCHEMA_VERSION,
    new_recipe,
    recipe_sidecar_path,
    validate_recipe_shape,
)
from openraw_studio.core.artifacts import ArtifactPlan


ROOT = Path(__file__).resolve().parents[1]


class RecipeContractTests(unittest.TestCase):
    def test_new_recipe_is_non_destructive(self) -> None:
        recipe = new_recipe("sample.NEF")

        self.assertEqual(recipe["schema_version"], RECIPE_SCHEMA_VERSION)
        self.assertTrue(recipe["source"]["immutable"])
        self.assertEqual(recipe["processing_profile"], "general")
        self.assertEqual(recipe["creative_look"], "clean")
        validate_recipe_shape(recipe)

    def test_recipe_sidecar_keeps_raw_extension_visible(self) -> None:
        sidecar = recipe_sidecar_path(Path("IMG_0001.NEF"), Path("recipes"))

        self.assertEqual(sidecar, Path("recipes") / "IMG_0001.NEF.recipe.json")

    def test_artifact_plan_uses_v0_1_layout(self) -> None:
        plan = ArtifactPlan.for_source(Path("IMG_0001.NEF"), Path("output"))

        self.assertEqual(plan.preview_path, Path("output/previews/IMG_0001.preview.png"))
        self.assertEqual(plan.export_path, Path("output/exports/IMG_0001.auto.jpg"))
        self.assertEqual(plan.recipe_path, Path("output/recipes/IMG_0001.NEF.recipe.json"))
        self.assertEqual(plan.intermediate_path, Path("output/intermediates/IMG_0001.base.tif"))

    def test_json_contract_files_are_parseable(self) -> None:
        paths = [
            ROOT / "schemas" / "processing_recipe.schema.json",
            ROOT / "schemas" / "processing_preset.schema.json",
            ROOT / "schemas" / "creative_look.schema.json",
            ROOT / "configs" / "app.schema.json",
            ROOT / "configs" / "app.example.json",
            ROOT / "presets" / "processing" / "general.v1.json",
            ROOT / "presets" / "processing" / "portrait.v1.json",
            ROOT / "presets" / "looks" / "clean.v1.json",
            ROOT / "presets" / "looks" / "warm_film.v1.json",
        ]

        for path in paths:
            with self.subTest(path=path):
                self.assertIsInstance(json.loads(path.read_text(encoding="utf-8")), dict)

    def test_example_presets_use_expected_versions(self) -> None:
        processing = json.loads((ROOT / "presets" / "processing" / "portrait.v1.json").read_text())
        look = json.loads((ROOT / "presets" / "looks" / "warm_film.v1.json").read_text())

        self.assertEqual(processing["schema_version"], PROCESSING_PRESET_SCHEMA_VERSION)
        self.assertEqual(look["schema_version"], CREATIVE_LOOK_SCHEMA_VERSION)


if __name__ == "__main__":
    unittest.main()
