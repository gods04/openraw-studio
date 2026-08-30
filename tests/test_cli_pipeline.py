import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from openraw_studio.core.domain import EngineInfo, ImageAsset, ImageRef, RawInspection, ImageMetadata
from openraw_studio.cli import main
from openraw_studio.export.local import LocalJpegExportEngine
from openraw_studio.pipeline.interfaces import PipelineRequest
from openraw_studio.pipeline.local import LocalPhotoPipeline
from openraw_studio.raw.backends import BackendCheck
from openraw_studio.raw.darktable import DarktableCliProcessor
from openraw_studio.raw.interfaces import RawRenderRequest
from fixtures_nikon import embedded_jpeg_bytes, synthetic_nikon_nef_metadata_bytes, synthetic_nikon_nef_sensor_bytes
from openraw_studio.raw.native import NativeRawProcessor, write_synthetic_dng


PNG_2X3 = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\r"
    b"IHDR"
    b"\x00\x00\x00\x02"
    b"\x00\x00\x00\x03"
    b"\x08\x02\x00\x00\x00"
)


class FakeRawProcessor:
    def engine_info(self) -> EngineInfo:
        return EngineInfo(name="fake-raw", version="0.1.0", backend="test")

    def inspect(self, source: ImageAsset) -> RawInspection:
        return RawInspection(source=source, metadata=ImageMetadata(), engine=self.engine_info())

    def create_preview(self, source: ImageAsset, output_path: Path, max_dimension: int) -> ImageRef:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(PNG_2X3)
        return ImageRef(path=output_path, width=2, height=3, color_space="sRGB", role="preview")

    def render_base(self, request: RawRenderRequest) -> ImageRef:
        request.output_path.parent.mkdir(parents=True, exist_ok=True)
        request.output_path.write_bytes(PNG_2X3)
        return ImageRef(path=request.output_path, width=2, height=3, color_space="sRGB", role="base")

    def export_intermediate(self, request: RawRenderRequest) -> ImageRef:
        return self.render_base(request)


class CliPipelineTests(unittest.TestCase):
    def test_default_pipeline_uses_native_raw_processor(self) -> None:
        pipeline = LocalPhotoPipeline()

        self.assertIsInstance(pipeline.raw_processor, NativeRawProcessor)
        self.assertIsInstance(pipeline.export_engine, LocalJpegExportEngine)

    def test_dry_run_pipeline_writes_recipe(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "IMG_0001.NEF"
            output = root / "output"
            source.write_bytes(b"fake raw bytes")

            result = LocalPhotoPipeline().process(PipelineRequest(source, output, dry_run=True))
            recipe_path = Path(result.diagnostics["recipe_path"])
            recipe = json.loads(recipe_path.read_text(encoding="utf-8"))

            self.assertTrue(recipe_path.exists())
            self.assertTrue(recipe["source"]["immutable"])
            self.assertEqual(recipe["source"]["checksum_sha256"], result.recipe["source"]["checksum_sha256"])
            self.assertEqual(recipe["pipeline"]["mode"], "dry_run")
            self.assertEqual(recipe["engines"][2]["name"], "openraw-native")
            self.assertEqual(recipe["planned_artifacts"]["export"], str(output / "exports" / "IMG_0001.auto.jpg"))

    def test_native_render_attempt_writes_recipe_before_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "IMG_0005.DNG"
            output = root / "output"
            source.write_bytes(b"fake raw bytes")

            with redirect_stderr(StringIO()):
                exit_code = main(["process", str(source), "--output", str(output)])
            recipe_path = output / "recipes" / "IMG_0005.DNG.recipe.json"
            recipe = json.loads(recipe_path.read_text(encoding="utf-8"))

            self.assertEqual(exit_code, 3)
            self.assertTrue(recipe_path.exists())
            self.assertEqual(recipe["engines"][2]["name"], "openraw-native")
            self.assertFalse(recipe["pipeline"]["rendered"])
            self.assertIn("OpenRAW Native preview failed", recipe["pipeline"]["message"])

    def test_nikon_nef_render_is_blocked_after_metadata_import(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "IMG_0007.NEF"
            output = root / "output"
            source.write_bytes(synthetic_nikon_nef_metadata_bytes())

            with redirect_stderr(StringIO()):
                exit_code = main(["process", str(source), "--output", str(output)])
            recipe_path = output / "recipes" / "IMG_0007.NEF.recipe.json"
            recipe = json.loads(recipe_path.read_text(encoding="utf-8"))

            self.assertEqual(exit_code, 3)
            self.assertTrue(recipe_path.exists())
            self.assertEqual(recipe["source"]["metadata"]["raw_format"], "nikon-nef")
            self.assertIn("Nikon", recipe["pipeline"]["message"])
            self.assertFalse((output / "exports" / "IMG_0007.auto.jpg").exists())

    def test_nikon_nef_native_sensor_render_writes_jpeg_export(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "IMG_0009.NEF"
            output = root / "output"
            source.write_bytes(synthetic_nikon_nef_sensor_bytes(width=4, height=4))

            with redirect_stdout(StringIO()):
                exit_code = main(["process", str(source), "--output", str(output)])
            recipe_path = output / "recipes" / "IMG_0009.NEF.recipe.json"
            recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
            preview_path = output / "previews" / "IMG_0009.preview.png"
            export_path = output / "exports" / "IMG_0009.auto.jpg"

            self.assertEqual(exit_code, 0)
            self.assertTrue(preview_path.exists())
            self.assertTrue(export_path.exists())
            self.assertEqual(recipe["source"]["metadata"]["raw_format"], "nikon-nef")
            self.assertEqual(recipe["planned_artifacts"]["preview"], str(preview_path))
            self.assertEqual(recipe["exports"][0]["path"], str(export_path))

    def test_nikon_nef_preview_only_writes_embedded_jpeg_preview(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "IMG_0008.NEF"
            output = root / "output"
            source.write_bytes(synthetic_nikon_nef_metadata_bytes(embedded_jpeg=embedded_jpeg_bytes()))

            with redirect_stdout(StringIO()):
                exit_code = main(["process", str(source), "--output", str(output), "--preview-only"])
            recipe_path = output / "recipes" / "IMG_0008.NEF.recipe.json"
            recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
            preview_path = output / "previews" / "IMG_0008.preview.jpg"

            self.assertEqual(exit_code, 0)
            self.assertTrue(preview_path.exists())
            self.assertFalse((output / "exports" / "IMG_0008.auto.jpg").exists())
            self.assertEqual(recipe["pipeline"]["mode"], "preview_only")
            self.assertEqual(
                recipe["pipeline"]["message"],
                "Embedded JPEG preview was extracted; final export was skipped by request.",
            )
            self.assertEqual(recipe["preview"]["path"], str(preview_path))
            self.assertEqual(recipe["preview"]["width"], 3)
            self.assertEqual(recipe["preview"]["height"], 2)
            self.assertEqual(recipe["planned_artifacts"]["preview"], str(preview_path))
            self.assertEqual(recipe["exports"], [])

    def test_render_pipeline_uses_raw_processor(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "IMG_0003.NEF"
            output = root / "output"
            source.write_bytes(b"fake raw bytes")

            result = LocalPhotoPipeline(raw_processor=FakeRawProcessor()).process(
                PipelineRequest(source, output, dry_run=False)
            )
            recipe_path = Path(result.diagnostics["recipe_path"])
            recipe = json.loads(recipe_path.read_text(encoding="utf-8"))

            self.assertFalse(result.diagnostics["dry_run"])
            self.assertTrue((output / "previews" / "IMG_0003.preview.png").exists())
            self.assertTrue((output / "exports" / "IMG_0003.auto.jpg").exists())
            self.assertEqual(recipe["pipeline"]["rendered"], True)
            self.assertEqual(recipe["engines"][3]["name"], "openraw-export")
            self.assertEqual(recipe["exports"][0]["width"], 2)
            self.assertEqual(recipe["exports"][0]["quality"], 92)
            self.assertEqual(recipe["exports"][0]["engine"], "openraw-export")

    def test_preview_only_pipeline_skips_export(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "IMG_0006.DNG"
            output = root / "output"
            source.write_bytes(b"fake raw bytes")

            result = LocalPhotoPipeline(raw_processor=FakeRawProcessor()).process(
                PipelineRequest(source, output, dry_run=False, preview_only=True)
            )
            recipe_path = Path(result.diagnostics["recipe_path"])
            recipe = json.loads(recipe_path.read_text(encoding="utf-8"))

            self.assertTrue(result.diagnostics["preview_only"])
            self.assertTrue((output / "previews" / "IMG_0006.preview.png").exists())
            self.assertFalse((output / "exports" / "IMG_0006.auto.jpg").exists())
            self.assertEqual(result.exports, ())
            self.assertEqual(recipe["pipeline"]["mode"], "preview_only")
            self.assertTrue(recipe["pipeline"]["preview_rendered"])
            self.assertFalse(recipe["pipeline"]["export_rendered"])

    def test_cli_dry_run_returns_success(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "IMG_0002.DNG"
            output = root / "output"
            source.write_bytes(b"fake raw bytes")

            with redirect_stdout(StringIO()):
                exit_code = main(["process", str(source), "--output", str(output), "--dry-run"])

            self.assertEqual(exit_code, 0)
            self.assertTrue((output / "recipes" / "IMG_0002.DNG.recipe.json").exists())

    def test_cli_rejects_invalid_auto_strength(self) -> None:
        with redirect_stderr(StringIO()):
            exit_code = main(["process", "missing.NEF", "--output", "output", "--dry-run", "--auto-strength", "2"])

        self.assertEqual(exit_code, 2)

    def test_cli_rejects_invalid_contrast(self) -> None:
        with redirect_stderr(StringIO()):
            exit_code = main(["process", "missing.DNG", "--output", "output", "--dry-run", "--contrast", "2"])

        self.assertEqual(exit_code, 2)

    def test_cli_rejects_invalid_warmth(self) -> None:
        with redirect_stderr(StringIO()):
            exit_code = main(["process", "missing.DNG", "--output", "output", "--dry-run", "--warmth", "-2"])

        self.assertEqual(exit_code, 2)

    def test_cli_rejects_dry_run_with_preview_only(self) -> None:
        with redirect_stderr(StringIO()):
            exit_code = main(["process", "missing.DNG", "--output", "output", "--dry-run", "--preview-only"])

        self.assertEqual(exit_code, 2)

    def test_doctor_reports_native_without_experimental_backend_noise(self) -> None:
        output = StringIO()

        with redirect_stdout(output):
            exit_code = main(["doctor"])

        text = output.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("openraw-native: available", text)
        self.assertNotIn("darktable-cli experimental", text)

    def test_cli_inspect_reports_supported_synthetic_dng(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = write_synthetic_dng(Path(temp) / "sample.DNG", width=8, height=8)
            output = StringIO()

            with redirect_stdout(output):
                exit_code = main(["inspect", str(source)])

        text = output.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("Native render: supported", text)
        self.assertIn("Supported by OpenRAW Native V0.1", text)
        self.assertIn("Storage: 1 strip", text)

    def test_cli_inspect_reports_nikon_raw_import_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "sample.NEF"
            source.write_bytes(synthetic_nikon_nef_metadata_bytes())
            output = StringIO()

            with redirect_stdout(output):
                exit_code = main(["inspect", str(source)])

        text = output.getvalue()
        self.assertEqual(exit_code, 1)
        self.assertIn("Import: metadata supported", text)
        self.assertIn("Native render: not supported yet", text)
        self.assertIn("Nikon RAW metadata import is supported", text)

    def test_cli_inspect_reports_nikon_raw_native_sensor_render(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "sample.NEF"
            source.write_bytes(synthetic_nikon_nef_sensor_bytes(width=4, height=4))
            output = StringIO()

            with redirect_stdout(output):
                exit_code = main(["inspect", str(source)])

        text = output.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("Preview: supported", text)
        self.assertIn("Native render: supported", text)
        self.assertIn("guarded Nikon sensor decode", text)

    def test_cli_inspect_reports_nikon_raw_embedded_preview(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "sample.NEF"
            source.write_bytes(synthetic_nikon_nef_metadata_bytes(embedded_jpeg=embedded_jpeg_bytes()))
            output = StringIO()

            with redirect_stdout(output):
                exit_code = main(["inspect", str(source)])

        text = output.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("Preview: supported", text)
        self.assertIn("Native render: not supported yet", text)
        self.assertIn("Preview: embedded JPEG", text)

    def test_cli_batch_exports_supported_folder_items(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source_dir = root / "input"
            source_dir.mkdir()
            write_synthetic_dng(source_dir / "sample.DNG", width=4, height=4)
            (source_dir / "sample.NEF").write_bytes(b"fake raw bytes")
            output_dir = root / "output"
            output = StringIO()

            with redirect_stdout(output):
                exit_code = main(["batch", str(source_dir), "--output", str(output_dir)])
            export_exists = (output_dir / "exports" / "sample.auto.jpg").exists()

        text = output.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("OpenRAW batch complete.", text)
        self.assertIn("Exported: 1", text)
        self.assertIn("Skipped: 1", text)
        self.assertTrue(export_exists)

    def test_cli_batch_returns_one_when_no_files_can_be_processed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source_dir = root / "input"
            source_dir.mkdir()
            (source_dir / "sample.NEF").write_bytes(b"fake raw bytes")

            with redirect_stdout(StringIO()):
                exit_code = main(["batch", str(source_dir), "--output", str(root / "output")])

        self.assertEqual(exit_code, 1)

    def test_cli_batch_rejects_invalid_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with redirect_stderr(StringIO()):
                exit_code = main(["batch", temp, "--output", temp, "--limit", "0"])

        self.assertEqual(exit_code, 2)

    def test_darktable_adapter_builds_preview_command(self) -> None:
        calls = []

        def fake_runner(command, **kwargs):
            calls.append((command, kwargs))
            output_path = Path(command[2])
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(PNG_2X3)
            return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "IMG_0004.NEF"
            preview = root / "preview.jpg"
            source.write_bytes(b"fake raw bytes")
            processor = DarktableCliProcessor(
                backend_check=BackendCheck(
                    name="darktable-cli",
                    available=True,
                    executable="darktable-cli",
                    version="darktable 5",
                ),
                runner=fake_runner,
            )

            image = processor.create_preview(ImageAsset(source), preview, max_dimension=1024)

        self.assertEqual(image.width, 2)
        self.assertEqual(image.height, 3)
        command = calls[0][0]
        self.assertEqual(command[:3], ["darktable-cli", str(source), str(preview)])
        self.assertIn("--width", command)
        self.assertIn("1024", command)
        self.assertIn("--upscale", command)
        self.assertIn("false", command)


if __name__ == "__main__":
    unittest.main()
