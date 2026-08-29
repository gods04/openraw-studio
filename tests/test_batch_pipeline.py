import tempfile
import unittest
from pathlib import Path

from fixtures_nikon import embedded_jpeg_bytes, synthetic_nikon_nef_metadata_bytes
from openraw_studio.pipeline.batch import discover_batch_sources, run_batch_export
from openraw_studio.raw.native.synthetic import write_synthetic_dng


class BatchPipelineTests(unittest.TestCase):
    def test_discover_batch_sources_lists_raw_like_files_with_support(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_synthetic_dng(root / "a.DNG", width=4, height=4)
            (root / "b.NEF").write_bytes(b"fake")
            (root / "ignore.jpg").write_bytes(b"fake")

            sources = discover_batch_sources(root)

        self.assertEqual([source.path.name for source in sources], ["a.DNG", "b.NEF"])
        self.assertTrue(sources[0].can_render)
        self.assertFalse(sources[1].can_render)

    def test_run_batch_export_processes_supported_and_skips_unsupported(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            supported = write_synthetic_dng(root / "a.DNG", width=4, height=4)
            unsupported = root / "b.NEF"
            unsupported.write_bytes(b"fake")
            output = root / "output"

            result = run_batch_export([supported, unsupported], output, overrides={"exposure": 0.2})

            export_path = output / "exports" / "a.auto.jpg"
            recipe_path = output / "recipes" / "a.DNG.recipe.json"
            export_exists = export_path.exists()
            recipe_exists = recipe_path.exists()

        self.assertEqual(result.total, 2)
        self.assertEqual(result.processed, 1)
        self.assertEqual(result.exported, 1)
        self.assertEqual(result.skipped, 1)
        self.assertEqual(result.failed, 0)
        self.assertTrue(export_exists)
        self.assertTrue(recipe_exists)
        self.assertEqual(result.items[0].status, "exported")
        self.assertEqual(result.items[1].status, "skipped")

    def test_run_batch_preview_only_skips_final_export(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = write_synthetic_dng(root / "a.DNG", width=4, height=4)
            output = root / "output"

            result = run_batch_export([source], output, preview_only=True)

            preview_path = output / "previews" / "a.preview.png"
            export_path = output / "exports" / "a.auto.jpg"
            preview_exists = preview_path.exists()
            export_exists = export_path.exists()

        self.assertEqual(result.previewed, 1)
        self.assertEqual(result.exported, 0)
        self.assertTrue(preview_exists)
        self.assertFalse(export_exists)

    def test_run_batch_preview_only_processes_nikon_embedded_preview(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "b.NEF"
            source.write_bytes(synthetic_nikon_nef_metadata_bytes(embedded_jpeg=embedded_jpeg_bytes()))
            output = root / "output"

            result = run_batch_export([source], output, preview_only=True)

            preview_path = output / "previews" / "b.preview.jpg"
            export_path = output / "exports" / "b.auto.jpg"
            preview_exists = preview_path.exists()
            export_exists = export_path.exists()

        self.assertEqual(result.previewed, 1)
        self.assertEqual(result.exported, 0)
        self.assertEqual(result.items[0].message, "Embedded preview extracted")
        self.assertTrue(preview_exists)
        self.assertFalse(export_exists)

    def test_batch_result_as_dict_is_json_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = write_synthetic_dng(root / "a.DNG", width=4, height=4)

            payload = run_batch_export([source], root / "output").as_dict()

        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["exported"], 1)
        self.assertIsInstance(payload["items"], list)


if __name__ == "__main__":
    unittest.main()
