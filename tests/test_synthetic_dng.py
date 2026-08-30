import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from openraw_studio.pipeline.interfaces import PipelineRequest
from openraw_studio.pipeline.local import LocalPhotoPipeline
from openraw_studio.raw.native import DngMetadataReader, write_synthetic_dng, write_synthetic_nikon_nef


class SyntheticDngTests(unittest.TestCase):
    def test_synthetic_dng_can_be_read_by_native_metadata_reader(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = write_synthetic_dng(Path(temp) / "openraw-synthetic.DNG", width=8, height=6)

            metadata = DngMetadataReader().read(path).as_dict()
            pixels = DngMetadataReader().read_pixel_data(path)

        self.assertEqual(metadata["width"], 8)
        self.assertEqual(metadata["height"], 6)
        self.assertEqual(metadata["make"], "OpenRAW")
        self.assertEqual(metadata["model"], "Synthetic DNG")
        self.assertEqual(pixels.width, 8)
        self.assertEqual(pixels.height, 6)
        self.assertEqual(pixels.bits_per_sample, 16)

    def test_pipeline_processes_synthetic_dng_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = write_synthetic_dng(root / "openraw-synthetic.DNG", width=8, height=8)
            output = root / "output"

            result = LocalPhotoPipeline().process(PipelineRequest(source, output))

            preview_path = output / "previews" / "openraw-synthetic.preview.png"
            export_path = output / "exports" / "openraw-synthetic.auto.jpg"
            recipe_path = output / "recipes" / "openraw-synthetic.DNG.recipe.json"

            self.assertTrue(preview_path.exists())
            self.assertTrue(export_path.exists())
            self.assertTrue(recipe_path.exists())
            self.assertEqual(result.preview.path, preview_path)
            self.assertEqual(result.exports[0].path, export_path)

    def test_synthetic_nikon_nef_can_be_read_by_native_metadata_reader(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = write_synthetic_nikon_nef(Path(temp) / "openraw-synthetic-nikon.NEF", width=8, height=6)

            metadata = DngMetadataReader().read(path).as_dict()
            pixels = DngMetadataReader().read_pixel_data(path)

        self.assertEqual(metadata["width"], 8)
        self.assertEqual(metadata["height"], 6)
        self.assertEqual(metadata["make"], "NIKON CORPORATION")
        self.assertEqual(metadata["model"], "OpenRAW Synthetic NEF")
        self.assertEqual(pixels.width, 8)
        self.assertEqual(pixels.height, 6)
        self.assertEqual(pixels.bits_per_sample, 14)

    def test_pipeline_processes_synthetic_nikon_nef_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = write_synthetic_nikon_nef(root / "openraw-synthetic-nikon.NEF", width=8, height=8)
            output = root / "output"

            result = LocalPhotoPipeline().process(PipelineRequest(source, output))

            preview_path = output / "previews" / "openraw-synthetic-nikon.preview.png"
            export_path = output / "exports" / "openraw-synthetic-nikon.auto.jpg"
            recipe_path = output / "recipes" / "openraw-synthetic-nikon.NEF.recipe.json"

            self.assertTrue(preview_path.exists())
            self.assertTrue(export_path.exists())
            self.assertTrue(recipe_path.exists())
            self.assertEqual(result.preview.path, preview_path)
            self.assertEqual(result.exports[0].path, export_path)

    def test_create_sample_dng_script_writes_sample(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "sample.DNG"
            completed = subprocess.run(
                [
                    sys.executable,
                    "scripts/create_sample_dng.py",
                    "--output",
                    str(output),
                    "--width",
                    "4",
                    "--height",
                    "4",
                ],
                cwd=Path(__file__).resolve().parents[1],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue(output.exists())
            self.assertIn("Synthetic DNG:", completed.stdout)

    def test_create_sample_nikon_nef_script_writes_sample(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "sample.NEF"
            completed = subprocess.run(
                [
                    sys.executable,
                    "scripts/create_sample_nikon_nef.py",
                    "--output",
                    str(output),
                    "--width",
                    "4",
                    "--height",
                    "4",
                    "--bits",
                    "14",
                ],
                cwd=Path(__file__).resolve().parents[1],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue(output.exists())
            self.assertIn("Synthetic Nikon NEF:", completed.stdout)


if __name__ == "__main__":
    unittest.main()
