"""Focused export option tests; RAW rendering and final writers are stubbed."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import fields
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from openraw_studio.cli import build_parser, main
from openraw_studio.core.domain import EngineInfo, ImageAsset, ImageMetadata, ImageRef, RawInspection
from openraw_studio.core.recipe import new_recipe, write_recipe
from openraw_studio.export.formats import EXPORT_FORMATS, validate_export_bit_depth
from openraw_studio.export.interfaces import ExportRequest, ExportResult
from openraw_studio.pipeline.batch import BatchItemResult, BatchResult, run_batch_export
from openraw_studio.pipeline.errors import PipelineError
from openraw_studio.pipeline.interfaces import PipelineRequest, PipelineResult
from openraw_studio.pipeline.local import LocalPhotoPipeline
from openraw_studio.raw.interfaces import RawRenderRequest
from openraw_studio.ui import desktop


class ExportBitDepthOptionsTests(unittest.TestCase):
    def test_request_fields_are_appended_with_legacy_defaults(self):
        source = ImageAsset(Path("sample.DNG"))
        image = ImageRef(Path("sample.tif"), 2, 2, "sRGB", role="export")
        requests = (
            (RawRenderRequest(source, {}, image.path, None, "sRGB", 90), "bit_depth"),
            (ExportRequest(image, {}, image.path, "tiff", 90, False), "bit_depth"),
            (PipelineRequest(source.path, Path("output")), "export_bit_depth"),
        )
        for request, name in requests:
            with self.subTest(request=type(request).__name__):
                self.assertEqual(getattr(request, name), 8)
                self.assertEqual(fields(request)[-1].name, name)

    def test_validator_accepts_only_integer_depths_and_supported_combinations(self):
        self.assertEqual(EXPORT_FORMATS, ("jpeg", "tiff"))
        for format, bits in (("jpeg", 8), ("JPG", 8), ("tiff", 8), ("tiff", 16), (" tif ", 16)):
            with self.subTest(format=format, bits=bits):
                self.assertEqual(validate_export_bit_depth(bits, export_format=format), bits)
        for bits in (True, False, "8", "16", 8.0, 16.0, None, 0, 12, 32):
            with self.subTest(bits=bits), self.assertRaises(ValueError):
                validate_export_bit_depth(bits, export_format="tiff")
        for format in ("jpeg", "jpg", "tiff16", "png"):
            with self.subTest(format=format), self.assertRaises(ValueError):
                validate_export_bit_depth(16, export_format=format)

    def test_pipeline_rejects_invalid_depth_before_inspection_or_output(self):
        with TemporaryDirectory() as temp:
            source = Path(temp) / "sample.DNG"
            source.write_bytes(b"raw fixture")
            output = Path(temp) / "output"
            raw, exporter = Mock(), Mock()
            pipeline = LocalPhotoPipeline(raw_processor=raw, export_engine=exporter)
            for format, bits in (("jpeg", 16), ("tiff", 12), ("tiff", "16"), ("tiff", 16.0)):
                with self.subTest(format=format, bits=bits), self.assertRaises(PipelineError):
                    pipeline.process(PipelineRequest(source, output, export_format=format, export_bit_depth=bits))
                self.assertFalse(output.exists())
            raw.inspect.assert_not_called()
            exporter.export.assert_not_called()

    def test_pipeline_forwards_depth_and_reuses_eight_bit_preview(self):
        with TemporaryDirectory() as temp:
            source = Path(temp) / "sample.DNG"
            source.write_bytes(b"raw fixture")
            output = Path(temp) / "output"
            raw, exporter = Mock(), Mock()
            raw.engine_info.return_value = EngineInfo("stub-raw", "0")
            raw.inspect.return_value = RawInspection(ImageAsset(source), ImageMetadata(), raw.engine_info())
            exporter.engine_info.return_value = EngineInfo("stub-export", "0")

            def preview(source, output_path, max_dimension):
                Image.new("RGB", (2, 2), (64, 128, 192)).save(output_path)
                return ImageRef(output_path, 2, 2, "sRGB", role="preview")

            raw.create_preview.side_effect = preview
            raw.render_base.side_effect = lambda request: ImageRef(request.output_path, 2, 2, "sRGB", role="base")
            exporter.export.side_effect = lambda request: ExportResult(request.image)
            pipeline = LocalPhotoPipeline(raw_processor=raw, export_engine=exporter)
            first = pipeline.process(PipelineRequest(source, output, preview_only=True, export_format="tiff"))
            raw.render_base.assert_not_called()
            original_preview = first.preview.path.read_bytes()
            result = pipeline.process(PipelineRequest(
                source, output, export_format="tiff", export_bit_depth=16, reuse_existing_preview=True,
            ))
            self.assertEqual(raw.create_preview.call_count, 1)
            self.assertTrue(result.diagnostics["preview_reused"])
            self.assertEqual(result.preview.path.read_bytes(), original_preview)
            self.assertEqual(raw.render_base.call_args.args[0].bit_depth, 16)
            self.assertEqual(exporter.export.call_args.args[0].bit_depth, 16)
            self.assertEqual(result.recipe["output"]["bit_depth"], 16)
            self.assertEqual(result.recipe["exports"][0]["bit_depth"], 16)
            self.assertEqual(result.exports[0].path.suffix, ".tif")
            raw.render_base.reset_mock()
            exporter.export.reset_mock()
            planned = pipeline.process(PipelineRequest(
                source, output, dry_run=True, export_format="tiff", export_bit_depth=16,
            ))
            self.assertEqual(planned.recipe["output"]["bit_depth"], 16)
            raw.render_base.assert_not_called()
            exporter.export.assert_not_called()

    def test_batch_validates_even_empty_input_before_pipeline_creation(self):
        with TemporaryDirectory() as temp, patch("openraw_studio.pipeline.batch.LocalPhotoPipeline") as pipeline:
            output = Path(temp) / "output"
            for format, bits in (("jpeg", 16), ("tiff", 12), ("tiff", "16"), ("tiff", True)):
                with self.subTest(format=format, bits=bits), self.assertRaises(ValueError):
                    run_batch_export([], output, export_format=format, export_bit_depth=bits)
            pipeline.assert_not_called()
            self.assertFalse(output.exists())

    def test_batch_forwards_selected_and_default_depth(self):
        source, output = Path("sample.DNG"), Path("output")
        pipeline = Mock()
        pipeline.process.return_value = PipelineResult({}, exports=(ImageRef(output / "sample.tif", 2, 2, "sRGB", role="export"),))
        with patch("openraw_studio.pipeline.batch.inspect_native_support", return_value=SimpleNamespace(can_render=True)):
            for options, expected in (({}, 8), ({"export_bit_depth": 16}, 16)):
                result = run_batch_export([source], output, pipeline=pipeline, export_format="tiff", **options)
                self.assertEqual(result.exported, 1)
                self.assertEqual(pipeline.process.call_args.args[0].export_bit_depth, expected)

    def test_cli_choices_and_default(self):
        parser = build_parser()
        for command in ("process", "batch"):
            args = [command, "input", "--output", "output"]
            self.assertEqual(parser.parse_args(args).export_bit_depth, 8)
            self.assertEqual(parser.parse_args(args + ["--format", "tiff", "--bit-depth", "16"]).export_bit_depth, 16)
            for invalid in (["--bit-depth", "12"], ["--bit-depth", "16.0"], ["--format", "tiff16"]):
                with self.subTest(command=command, invalid=invalid), redirect_stderr(StringIO()):
                    with self.assertRaises(SystemExit) as error:
                        parser.parse_args(args + invalid)
                    self.assertEqual(error.exception.code, 2)

    def test_cli_rejects_jpeg_sixteen_before_starting_work(self):
        with TemporaryDirectory() as temp, patch("openraw_studio.cli.LocalPhotoPipeline") as pipeline, patch(
            "openraw_studio.cli.discover_batch_sources"
        ) as discover:
            output = Path(temp) / "output"
            for command in ("process", "batch"):
                error = StringIO()
                with redirect_stderr(error):
                    code = main([command, "missing", "--output", str(output), "--bit-depth", "16"])
                self.assertEqual(code, 2)
                self.assertIn("JPEG only supports 8-bit", error.getvalue())
            pipeline.assert_not_called()
            discover.assert_not_called()
            self.assertFalse(output.exists())

    def test_cli_forwards_depth_for_process_and_batch(self):
        with patch("openraw_studio.cli._build_raw_processor"), patch("openraw_studio.cli.LocalPhotoPipeline") as pipeline:
            pipeline.return_value.process.return_value = PipelineResult({})
            for flags, expected in (([], 8), (["--bit-depth", "16"], 16)):
                with redirect_stdout(StringIO()):
                    self.assertEqual(main(["process", "sample.DNG", "-o", "output", "--format", "tiff"] + flags), 0)
                self.assertEqual(pipeline.return_value.process.call_args.args[0].export_bit_depth, expected)
        result = BatchResult(Path("output"), (BatchItemResult(Path("sample.DNG"), "exported", "ok"),))
        with patch("openraw_studio.cli.discover_batch_sources", return_value=[]), patch(
            "openraw_studio.cli.run_batch_export", return_value=result
        ) as run:
            for flags, expected in (([], 8), (["--bit-depth", "16"], 16)):
                with redirect_stdout(StringIO()):
                    self.assertEqual(main(["batch", "input", "-o", "output", "--format", "tiff"] + flags), 0)
                self.assertEqual(run.call_args.kwargs["export_bit_depth"], expected)

    def test_doctor_reports_tiff_depths(self):
        output = StringIO()
        with redirect_stdout(output), patch("openraw_studio.cli.NativeRawProcessor"):
            self.assertEqual(main(["doctor"]), 0)
        self.assertIn("8-bit JPEG", output.getvalue())
        self.assertIn("8/16-bit TIFF", output.getvalue())

    def test_recipe_options_restore_depth_and_legacy_defaults(self):
        for output, expected in (
            (None, ("jpeg", 92, 8)),
            ({"format": "tiff"}, ("tiff", 92, 8)),
            ({"format": "tiff", "bit_depth": 16}, ("tiff", 92, 16)),
            ({"format": "jpeg", "quality": 87, "bit_depth": 16}, ("jpeg", 87, 8)),
            ({"format": "tiff", "bit_depth": "16"}, ("tiff", 92, 8)),
            ({"format": "tiff", "bit_depth": 16.0}, ("tiff", 92, 8)),
            ({"format": "tiff", "bit_depth": True}, ("tiff", 92, 8)),
        ):
            with self.subTest(output=output):
                recipe = {"output": output}
                self.assertEqual(desktop._recipe_export_options(recipe, include_bit_depth=True), expected)
                self.assertEqual(desktop._recipe_export_options(recipe), expected[:2])


class DesktopBitDepthOptionsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tkinter as tk

        try:
            root = tk.Tk()
        except tk.TclError as error:
            raise unittest.SkipTest(f"Tk display unavailable: {error}") from error
        root.withdraw()
        cls.addClassCleanup(root.destroy)
        cls.temp = TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        with patch("tkinter.Tk", return_value=root), patch.object(desktop, "LivePreviewWorker"), patch.object(
            desktop, "LocalPhotoPipeline"
        ):
            cls.app = desktop.launch_desktop_app(run_mainloop=False, session_dir=Path(cls.temp.name) / "sessions")
        cls.app.source_path = Path(cls.temp.name) / "sample.DNG"
        cls.app.source_path.write_bytes(b"raw fixture")
        cls.app.output_dir = Path(cls.temp.name) / "output"
        cls.app.library_items = [(cls.app.source_path, "Supported", True)]

    def setUp(self):
        self.app.is_busy = False
        self.app.pipeline = Mock()
        self.app.export_format_var.set("JPEG")
        self.app._sync_export_options(update_status=False)

    def tearDown(self):
        self.app._set_busy(False)

    def test_combobox_and_jpeg_forcing_preserve_edit_state(self):
        app = self.app
        self.assertEqual(tuple(map(str, app.export_bit_depth_combo["values"])), ("8", "16"))
        self.assertEqual(str(app.export_bit_depth_combo["state"]), "disabled")
        overrides = app._current_overrides()
        with patch.object(app, "_schedule_live_preview") as preview, patch.object(app, "_commit_edit") as history:
            app.export_format_var.set("TIFF")
            app.export_bit_depth_var.set(16)
            app._sync_export_options(update_status=False)
            self.assertEqual(str(app.export_bit_depth_combo["state"]), "readonly")
            self.assertEqual(app._selected_export_bit_depth(), 16)
            self.assertEqual(app.jpeg_quality_label_var.get(), "Lossless 16-bit")
            app.export_format_var.set("JPEG")
            app._sync_export_options(update_status=False)
            self.assertEqual(app._selected_export_bit_depth(), 8)
            self.assertEqual(str(app.export_bit_depth_combo["state"]), "disabled")
            self.assertEqual(app._current_overrides(), overrides)
            preview.assert_not_called()
            history.assert_not_called()

    def test_restore_recipe_sets_depth_and_legacy_default(self):
        app = self.app
        for output, expected in (({"format": "tiff", "bit_depth": 16}, 16), ({"format": "tiff"}, 8)):
            recipe = new_recipe(app.source_path)
            recipe["output"] = output
            write_recipe(recipe, app._current_recipe_path())
            self.assertEqual(app._restore_recipe_if_available(), "Saved recipe loaded")
            self.assertEqual(app._selected_export_bit_depth(), expected)

    def test_single_and_batch_workers_use_captured_depth(self):
        app = self.app
        for batch in (False, True):
            with self.subTest(batch=batch):
                app.is_busy = False
                app.export_format_var.set("TIFF")
                app.export_bit_depth_var.set(16)
                app._sync_export_options(update_status=False)
                app.pipeline.process.return_value = PipelineResult({})
                with patch.object(desktop.threading, "Thread") as thread, patch.object(app, "_commit_edit"):
                    if batch:
                        app._export_folder()
                    else:
                        app._start_pipeline(preview_only=False)
                    thread.return_value.start.assert_called_once()
                    launch = thread.call_args.kwargs
                app.export_bit_depth_var.set(8)
                with patch.object(app.export_bit_depth_var, "get", side_effect=AssertionError("Tk read in worker")) as get, patch.object(
                    desktop, "run_batch_export", return_value=BatchResult(app.output_dir)
                ) as run, ThreadPoolExecutor(max_workers=1) as pool:
                    pool.submit(launch["target"], *launch["args"]).result(timeout=5)
                    get.assert_not_called()
                    if batch:
                        self.assertEqual(run.call_args.kwargs["export_bit_depth"], 16)
                    else:
                        self.assertEqual(app.pipeline.process.call_args.args[0].export_bit_depth, 16)


if __name__ == "__main__":
    unittest.main()
