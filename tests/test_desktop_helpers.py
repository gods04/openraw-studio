import tempfile
import unittest
from pathlib import Path

from fixtures_nikon import (
    embedded_jpeg_bytes,
    synthetic_nikon_nef_metadata_bytes,
    synthetic_nikon_nef_sensor_bytes,
)
from openraw_studio.core.domain import ImageRef
from openraw_studio.decision.auto_adjust import AutoAdjustSuggestion
from openraw_studio.pipeline.batch import BatchItemResult, BatchResult
from openraw_studio.pipeline.errors import BackendUnavailableError, SourceFileError
from openraw_studio.pipeline.interfaces import PipelineResult
from openraw_studio.core.recipe import new_recipe, write_recipe
from openraw_studio.ui.desktop import (
    _adjustments_match,
    _auto_adjust_status,
    _batch_progress_text,
    _batch_result_status,
    _candidate_raw_files,
    _can_build_inline_before_preview,
    _can_use_embedded_camera_preview,
    _default_sample_path,
    _default_sample_nikon_nef_path,
    _flatten_rgb_pixels,
    _folder_status_text,
    _format_adjustment_label,
    _format_bytes,
    _format_exposure_label,
    _format_image_artifact,
    _format_quality_summary,
    _histogram_coordinates,
    _histogram_status_text,
    _format_native_support_summary,
    _format_batch_result_summary,
    _format_photo_info,
    _format_result_summary,
    _friendly_error_message,
    _library_sources,
    _library_item_label,
    _load_embedded_camera_preview,
    _load_recipe_adjustments,
    _load_recipe_export_options,
    _manual_overrides,
    _open_export_target,
    _open_jpeg_target,
    _planned_output_summary,
    _preview_state_text,
    _recipe_adjustment_overrides,
    _recipe_export_options,
    _result_export_format,
    _read_photo_info,
    _result_status,
    _scan_library_folder,
    _short_path,
    _supported_library_sources,
)
from openraw_studio.raw.native.synthetic import write_synthetic_dng, write_synthetic_nikon_nef
from openraw_studio.raw.native.support import NativeSupportReport
from openraw_studio.qc.histogram import analyze_rgb_pixels


class DesktopHelperTests(unittest.TestCase):
    def test_format_exposure_label_uses_editor_style_ev(self) -> None:
        self.assertEqual(_format_exposure_label(0.0), "0.0 EV")
        self.assertEqual(_format_exposure_label(0.04), "0.0 EV")
        self.assertEqual(_format_exposure_label(0.74), "+0.7 EV")
        self.assertEqual(_format_exposure_label(-1.26), "-1.3 EV")

    def test_format_adjustment_label_uses_signed_percent_points(self) -> None:
        self.assertEqual(_format_adjustment_label(0.0), "0")
        self.assertEqual(_format_adjustment_label(0.253), "+25")
        self.assertEqual(_format_adjustment_label(-0.727), "-73")

    def test_histogram_status_names_view_and_clipping(self) -> None:
        clean = analyze_rgb_pixels(((80, 90, 100), (120, 130, 140)))
        clipped = analyze_rgb_pixels(((0, 0, 0), (255, 200, 180), (100, 100, 100), (80, 80, 80)))

        self.assertEqual(_histogram_status_text(None, view="After"), "No histogram yet")
        self.assertEqual(_histogram_status_text(clean, view="Before"), "Before: no clipped pixels")
        self.assertEqual(_histogram_status_text(clipped, view="After"), "After: Highlights 25.0% | Shadows 25.0%")

    def test_histogram_coordinates_fit_canvas_and_share_peak(self) -> None:
        points = _histogram_coordinates((0, 1, 3, 0), width=101, height=51, peak=3)

        self.assertEqual(len(points), 8)
        self.assertEqual(points[0], 0.0)
        self.assertEqual(points[-2], 100.0)
        self.assertTrue(all(0.0 <= value <= 50.0 for value in points[1::2]))
        self.assertEqual(points[5], 0.0)

    def test_histogram_coordinates_reject_invalid_geometry_or_counts(self) -> None:
        with self.assertRaises(ValueError):
            _histogram_coordinates((), width=100, height=50)
        with self.assertRaises(ValueError):
            _histogram_coordinates((1, -1), width=100, height=50)
        with self.assertRaises(ValueError):
            _histogram_coordinates((1, 2), width=1, height=50)
        with self.assertRaises(ValueError):
            _histogram_coordinates((1, 2), width=100, height=50, peak=1)

    def test_format_bytes_uses_photo_friendly_units(self) -> None:
        self.assertEqual(_format_bytes(0), "0 B")
        self.assertEqual(_format_bytes(1024), "1.0 KB")
        self.assertEqual(_format_bytes(1536), "1.5 KB")
        self.assertEqual(_format_bytes(1024 * 1024), "1.0 MB")

    def test_short_path_keeps_tail_of_long_paths(self) -> None:
        shortened = _short_path(Path("C:/Users/Example/Pictures/OpenRAW/Very/Long/Folder/IMG_0001.DNG"), max_chars=24)

        self.assertTrue(shortened.startswith("..."))
        self.assertTrue(shortened.endswith("IMG_0001.DNG"))
        self.assertLessEqual(len(shortened), 24)

    def test_format_photo_info_lists_core_metadata(self) -> None:
        info = _format_photo_info(
            Path("IMG_0001.DNG"),
            {
                "width": 6000,
                "height": 4000,
                "unique_camera_model": "OpenRAW NativeCam",
                "bits_per_sample": 16,
                "iso": 400,
                "exposure_time": 0.008,
                "aperture": 2.8,
                "focal_length_mm": 50.0,
                "lens_model": "NIKKOR Z 50mm f/1.8 S",
                "dng_version_text": "1.4.0.0",
            },
            size_bytes=1536,
        )

        self.assertIn("File: IMG_0001.DNG", info)
        self.assertIn("Dimensions: 6000 x 4000", info)
        self.assertIn("Camera: OpenRAW NativeCam", info)
        self.assertIn("RAW: 16-bit", info)
        self.assertIn("ISO: 400", info)
        self.assertIn("Shutter: 1/125s", info)
        self.assertIn("Aperture: f/2.8", info)
        self.assertIn("Focal: 50 mm", info)
        self.assertIn("Lens: NIKKOR Z 50mm f/1.8 S", info)
        self.assertIn("DNG: 1.4.0.0", info)
        self.assertIn("Size: 1.5 KB", info)

    def test_read_photo_info_reads_synthetic_dng_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = write_synthetic_dng(Path(temp) / "sample.DNG", width=18, height=12)

            info = _read_photo_info(path)

        self.assertIn("File: sample.DNG", info)
        self.assertIn("Dimensions: 18 x 12", info)
        self.assertIn("Camera: OpenRAW Synthetic NativeCam", info)
        self.assertIn("RAW: 16-bit", info)
        self.assertIn("Support: Supported by OpenRAW Native V0.1", info)

    def test_read_photo_info_reads_synthetic_nikon_nef_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = write_synthetic_nikon_nef(Path(temp) / "sample.NEF", width=18, height=12)

            info = _read_photo_info(path)

        self.assertIn("File: sample.NEF", info)
        self.assertIn("Dimensions: 18 x 12", info)
        self.assertIn("Camera: NIKON CORPORATION OpenRAW Synthetic NEF", info)
        self.assertIn("RAW: 14-bit", info)
        self.assertIn("Support: Supported by OpenRAW Native V0.1", info)

    def test_read_photo_info_includes_next_step_for_import_only_nikon_nef(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "sample.NEF"
            path.write_bytes(synthetic_nikon_nef_metadata_bytes())

            info = _read_photo_info(path)

        self.assertIn("Support: Import supported", info)
        self.assertIn("Next:", info)
        self.assertIn("Create Sample DNG/NEF", info)

    def test_format_native_support_summary_explains_unsupported_files(self) -> None:
        report = NativeSupportReport(
            source_path=Path("sample.NEF"),
            file_exists=True,
            can_inspect=True,
            can_render=False,
            status="import_only",
            reason="Nikon RAW metadata import is supported; NEF/NRW preview and export rendering are not implemented yet.",
            next_steps=("Use Create Sample DNG/NEF or a supported uncompressed file to test rendering today.",),
        )

        summary = _format_native_support_summary(report)

        self.assertIn("Support: Import supported", summary)
        self.assertIn("NEF/NRW preview", summary)
        self.assertIn("Next:", summary)
        self.assertIn("Create Sample DNG/NEF", summary)

    def test_format_native_support_summary_explains_preview_only_files(self) -> None:
        report = NativeSupportReport(
            source_path=Path("sample.NEF"),
            file_exists=True,
            can_inspect=True,
            can_preview=True,
            can_render=False,
            status="preview_only",
            reason="Nikon RAW embedded preview is supported; final NEF/NRW export rendering is not implemented yet.",
            next_steps=("Use Update Preview to view the embedded JPEG; final export needs native sensor decoding for this file.",),
        )

        summary = _format_native_support_summary(report)

        self.assertIn("Support: Preview supported", summary)
        self.assertIn("export not supported yet", summary)
        self.assertIn("Use Update Preview", summary)

    def test_candidate_raw_files_lists_raw_like_files_in_name_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "B.NEF").write_bytes(b"fake")
            (root / "a.DNG").write_bytes(b"fake")
            (root / "not-raw.jpg").write_bytes(b"fake")

            candidates = _candidate_raw_files(root)

        self.assertEqual([path.name for path in candidates], ["a.DNG", "B.NEF"])

    def test_library_item_label_marks_render_support(self) -> None:
        supported = NativeSupportReport(
            source_path=Path("sample.DNG"),
            file_exists=True,
            can_inspect=True,
            can_render=True,
            status="supported",
            reason="Supported by OpenRAW Native V0.1.",
        )
        unsupported = NativeSupportReport(
            source_path=Path("sample.NEF"),
            file_exists=True,
            can_inspect=False,
            can_render=False,
            status="unsupported",
            reason="OpenRAW Native V0.1 currently starts with DNG files.",
        )
        import_only = NativeSupportReport(
            source_path=Path("sample.NEF"),
            file_exists=True,
            can_inspect=True,
            can_render=False,
            status="import_only",
            reason="Nikon RAW metadata import is supported; NEF/NRW preview and export rendering are not implemented yet.",
        )
        preview_only = NativeSupportReport(
            source_path=Path("sample.NEF"),
            file_exists=True,
            can_inspect=True,
            can_preview=True,
            can_render=False,
            status="preview_only",
            reason="Nikon RAW embedded preview is supported; final NEF/NRW export rendering is not implemented yet.",
        )

        self.assertEqual(_library_item_label(Path("sample.DNG"), supported), "[OK] sample.DNG")
        self.assertEqual(_library_item_label(Path("sample.NEF"), preview_only), "[PREVIEW] sample.NEF")
        self.assertEqual(_library_item_label(Path("sample.NEF"), import_only), "[IMPORT] sample.NEF")
        self.assertEqual(_library_item_label(Path("sample.NEF"), unsupported), "[NO] sample.NEF")

    def test_scan_library_folder_reports_supported_and_unsupported_items(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_synthetic_dng(root / "sample.DNG", width=4, height=4)
            (root / "sample.NEF").write_bytes(b"fake")

            items = _scan_library_folder(root)

        self.assertEqual([item[0].name for item in items], ["sample.DNG", "sample.NEF"])
        self.assertEqual(items[0][1], "[OK] sample.DNG")
        self.assertTrue(items[0][2])
        self.assertEqual(items[1][1], "[NO] sample.NEF")
        self.assertFalse(items[1][2])

    def test_folder_status_text_summarizes_empty_and_nonempty_folders(self) -> None:
        self.assertIn("No RAW files", _folder_status_text(Path("empty"), 0))
        self.assertIn("2 RAW files", _folder_status_text(Path("photos"), 2))

    def test_supported_library_sources_filters_to_renderable_items(self) -> None:
        items = (
            (Path("a.DNG"), "[OK] a.DNG", True),
            (Path("b.NEF"), "[NO] b.NEF", False),
        )

        self.assertEqual(_library_sources(items), (Path("a.DNG"), Path("b.NEF")))
        self.assertEqual(_supported_library_sources(items), (Path("a.DNG"),))

    def test_batch_progress_and_result_text_summarize_batch_work(self) -> None:
        exported = BatchItemResult(
            source_path=Path("a.DNG"),
            status="exported",
            message="JPEG exported",
            export_path=Path("output/exports/a.auto.jpg"),
        )
        skipped = BatchItemResult(source_path=Path("b.NEF"), status="skipped", message="DNG only")
        result = BatchResult(output_dir=Path("output"), items=(exported, skipped))

        self.assertEqual(_batch_progress_text(1, 2, exported), "Batch 1/2: exported a.DNG")
        self.assertEqual(_batch_result_status(result), "Batch finished: 1 processed, 1 skipped")
        summary = _format_batch_result_summary(result)
        self.assertIn("Batch summary: 1 processed, 1 skipped, 0 failed", summary)
        self.assertIn("Exported: a.DNG", summary)
        self.assertIn("Skipped: b.NEF", summary)

    def test_planned_output_summary_uses_relative_artifact_paths(self) -> None:
        summary = _planned_output_summary(Path("IMG_0001.DNG"), Path("openraw-output"))

        self.assertIn("Folder: openraw-output", summary)
        self.assertIn(f"Preview: {Path('previews') / 'IMG_0001.preview.png'}", summary)
        self.assertIn(f"JPEG: {Path('exports') / 'IMG_0001.auto.jpg'}", summary)
        self.assertIn(f"Recipe: {Path('recipes') / 'IMG_0001.DNG.recipe.json'}", summary)

    def test_planned_output_summary_names_requested_tiff_artifact(self) -> None:
        summary = _planned_output_summary(
            Path("IMG_0001.DNG"),
            Path("openraw-output"),
            export_format="tiff",
        )

        self.assertIn(f"TIFF: {Path('exports') / 'IMG_0001.auto.tif'}", summary)

    def test_planned_output_summary_uses_jpeg_preview_for_nikon_raw(self) -> None:
        summary = _planned_output_summary(Path("IMG_0001.NEF"), Path("openraw-output"))

        self.assertIn(f"Preview: {Path('previews') / 'IMG_0001.preview.jpg'}", summary)
        self.assertIn(f"JPEG: {Path('exports') / 'IMG_0001.auto.jpg'}", summary)
        self.assertIn(f"Recipe: {Path('recipes') / 'IMG_0001.NEF.recipe.json'}", summary)

    def test_planned_output_summary_uses_png_preview_for_renderable_nikon_raw(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "IMG_0001.NEF"
            source.write_bytes(synthetic_nikon_nef_sensor_bytes(width=4, height=4))

            summary = _planned_output_summary(source, root / "openraw-output")

        self.assertIn(f"Preview: {Path('previews') / 'IMG_0001.preview.png'}", summary)
        self.assertIn(f"JPEG: {Path('exports') / 'IMG_0001.auto.jpg'}", summary)
        self.assertIn(f"Recipe: {Path('recipes') / 'IMG_0001.NEF.recipe.json'}", summary)

    def test_recipe_adjustment_overrides_defaults_and_clamps_for_ui(self) -> None:
        recipe = new_recipe("IMG_0001.DNG")
        recipe["adjustments"]["raw"] = {
            "exposure": 3.0,
            "contrast": -1.5,
            "highlights": -1.4,
            "shadows": "0.4",
            "warmth": "0.25",
            "tint": -1.3,
            "saturation": 1.4,
            "color_noise": 3,
        }

        self.assertEqual(
            _recipe_adjustment_overrides(recipe),
            {
                "exposure": 2.0,
                "contrast": -1.0,
                "highlights": -1.0,
                "shadows": 0.4,
                "warmth": 0.25,
                "tint": -1.0,
                "saturation": 1.0,
                "color_noise": 1.0,
            },
        )

    def test_load_recipe_adjustments_reads_matching_recipe(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "IMG_0001.DNG"
            recipe = new_recipe(source)
            recipe["adjustments"]["raw"] = {
                "exposure": 0.7,
                "contrast": 0.2,
                "highlights": -0.15,
                "shadows": 0.25,
                "warmth": -0.1,
                "saturation": 0.3,
            }
            recipe_path = write_recipe(recipe, root / "IMG_0001.DNG.recipe.json")

            overrides = _load_recipe_adjustments(recipe_path, source)

        self.assertEqual(
            overrides,
            {
                "exposure": 0.7,
                "contrast": 0.2,
                "highlights": -0.15,
                "shadows": 0.25,
                "warmth": -0.1,
                "tint": 0.0,
                "saturation": 0.3,
                "color_noise": 0.0,
            },
        )

    def test_load_recipe_adjustments_rejects_mismatched_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            recipe_path = write_recipe(new_recipe(root / "IMG_0001.DNG"), root / "IMG_0001.DNG.recipe.json")

            with self.assertRaises(ValueError):
                _load_recipe_adjustments(recipe_path, root / "IMG_0002.DNG")

    def test_recipe_export_options_restore_tiff_and_default_safely(self) -> None:
        recipe = new_recipe("IMG_0001.DNG")
        recipe["output"] = {"format": "tiff", "quality": None}

        self.assertEqual(_recipe_export_options(recipe), ("tiff", 92))
        self.assertEqual(_recipe_export_options(new_recipe("IMG_0001.DNG")), ("jpeg", 92))

    def test_load_recipe_export_options_requires_matching_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "IMG_0001.DNG"
            recipe = new_recipe(source)
            recipe["output"] = {"format": "jpeg", "quality": 87}
            recipe_path = write_recipe(recipe, root / "IMG_0001.DNG.recipe.json")

            options = _load_recipe_export_options(recipe_path, source)
            with self.assertRaises(ValueError):
                _load_recipe_export_options(recipe_path, root / "IMG_0002.DNG")

        self.assertEqual(options, ("jpeg", 87))

    def test_flatten_rgb_pixels_returns_pillow_ready_bytes(self) -> None:
        self.assertEqual(_flatten_rgb_pixels(((1, 2, 3), (4, 5, 6))), b"\x01\x02\x03\x04\x05\x06")

    def test_format_image_artifact_includes_dimensions_and_file_size(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "export.jpg"
            path.write_bytes(b"123456")

            text = _format_image_artifact(ImageRef(path, width=3712, height=5568, color_space="sRGB", role="export"))

        self.assertIn("3712 x 5568", text)
        self.assertIn("6 B", text)

    def test_format_result_summary_lists_user_facing_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            preview = ImageRef(root / "preview.png", width=10, height=8, color_space="sRGB", role="preview")
            export = ImageRef(root / "export.jpg", width=10, height=8, color_space="sRGB", role="base")
            result = PipelineResult(
                recipe={},
                preview=preview,
                exports=(export,),
                diagnostics={"recipe_path": str(root / "recipe.json")},
            )

            summary = _format_result_summary(result)

        self.assertIn("Preview:", summary)
        self.assertIn("JPEG:", summary)
        self.assertIn("Recipe:", summary)

    def test_format_result_summary_names_embedded_preview_jpeg(self) -> None:
        preview = ImageRef(Path("preview.jpg"), width=10, height=8, color_space="embedded-jpeg", role="preview")
        result = PipelineResult(recipe={}, preview=preview)

        summary = _format_result_summary(result)

        self.assertIn("Preview JPEG: preview.jpg", summary)

    def test_quality_summary_and_status_surface_advisory_clipping_warning(self) -> None:
        recipe = {
            "analysis": {
                "quality": {
                    "status": "warning",
                    "highlight_clip_fraction": 0.125,
                    "shadow_clip_fraction": 0.025,
                }
            },
            "qc": {"status": "warning"},
            "exports": [{"format": "jpeg"}],
        }
        result = PipelineResult(
            recipe=recipe,
            exports=(ImageRef(Path("export.jpg"), width=1, height=1, color_space="sRGB", role="export"),),
        )

        self.assertEqual(
            _format_quality_summary(recipe),
            "Quality: Highlights 12.5% | Shadows 2.5% - check clipping",
        )
        self.assertIn("Quality: Highlights 12.5%", _format_result_summary(result))
        self.assertEqual(_result_status(result), "JPEG exported - check clipping")

    def test_open_jpeg_target_prefers_final_export(self) -> None:
        result = PipelineResult(
            recipe={},
            preview=ImageRef(Path("preview.jpg"), width=1, height=1, color_space="embedded-jpeg", role="preview"),
            exports=(ImageRef(Path("export.jpg"), width=1, height=1, color_space="sRGB", role="export"),),
        )

        path, label = _open_jpeg_target(result)

        self.assertEqual(path, Path("export.jpg"))
        self.assertEqual(label, "Open JPEG")

    def test_open_jpeg_target_uses_embedded_preview_when_no_export_exists(self) -> None:
        result = PipelineResult(
            recipe={},
            preview=ImageRef(Path("preview.jpg"), width=1, height=1, color_space="embedded-jpeg", role="preview"),
        )

        path, label = _open_jpeg_target(result)

        self.assertEqual(path, Path("preview.jpg"))
        self.assertEqual(label, "Open Preview JPEG")

    def test_open_jpeg_target_stays_disabled_for_png_preview_only(self) -> None:
        result = PipelineResult(
            recipe={},
            preview=ImageRef(Path("preview.png"), width=1, height=1, color_space="sRGB", role="preview"),
        )

        path, label = _open_jpeg_target(result)

        self.assertIsNone(path)
        self.assertEqual(label, "Open Export")

    def test_export_helpers_name_tiff_result_from_recipe(self) -> None:
        result = PipelineResult(
            recipe={"exports": [{"format": "tiff"}]},
            exports=(ImageRef(Path("export.tif"), width=1, height=1, color_space="sRGB", role="export"),),
        )

        path, label = _open_export_target(result)

        self.assertEqual(_result_export_format(result), "tiff")
        self.assertEqual(path, Path("export.tif"))
        self.assertEqual(label, "Open TIFF")
        self.assertIn("TIFF: export.tif", _format_result_summary(result))
        self.assertEqual(_result_status(result), "TIFF exported")

    def test_can_build_inline_before_preview_skips_large_or_camera_preview_paths(self) -> None:
        dng_result = PipelineResult(
            recipe={},
            preview=ImageRef(Path("preview.png"), width=1, height=1, color_space="preview-rgb", role="preview"),
        )
        nikon_result = PipelineResult(
            recipe={},
            preview=ImageRef(
                Path("preview.png"),
                width=1,
                height=1,
                color_space="openraw-nikon-34713-rgb",
                role="preview",
            ),
        )
        embedded_result = PipelineResult(
            recipe={},
            preview=ImageRef(Path("preview.jpg"), width=1, height=1, color_space="embedded-jpeg", role="preview"),
        )

        self.assertTrue(_can_build_inline_before_preview(dng_result))
        self.assertFalse(_can_build_inline_before_preview(nikon_result))
        self.assertFalse(_can_build_inline_before_preview(embedded_result))
        self.assertFalse(_can_use_embedded_camera_preview(dng_result))
        self.assertTrue(_can_use_embedded_camera_preview(nikon_result))
        self.assertFalse(_can_use_embedded_camera_preview(embedded_result))

    def test_load_embedded_camera_preview_is_bounded_and_read_only(self) -> None:
        from PIL import Image

        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "camera.NEF"
            source_bytes = synthetic_nikon_nef_metadata_bytes(
                width=900,
                height=600,
                embedded_jpeg=embedded_jpeg_bytes(width=900, height=600),
            )
            source.write_bytes(source_bytes)

            image, histogram = _load_embedded_camera_preview(source, Image)

            self.assertEqual(image.mode, "RGB")
            self.assertLessEqual(image.width, 700)
            self.assertLessEqual(image.height, 520)
            self.assertGreater(histogram.pixel_count, 0)
            self.assertEqual(source.read_bytes(), source_bytes)

    def test_result_status_distinguishes_preview_from_export(self) -> None:
        preview_result = PipelineResult(recipe={}, diagnostics={"preview_only": True})
        export_result = PipelineResult(
            recipe={},
            exports=(ImageRef(Path("export.jpg"), width=1, height=1, color_space="sRGB", role="export"),),
        )

        self.assertEqual(_result_status(preview_result), "Preview updated")
        self.assertEqual(_result_status(export_result), "JPEG exported")

    def test_result_status_names_embedded_preview_jpeg(self) -> None:
        preview_result = PipelineResult(
            recipe={},
            preview=ImageRef(Path("preview.jpg"), width=1, height=1, color_space="embedded-jpeg", role="preview"),
            diagnostics={"preview_only": True},
        )

        self.assertEqual(_result_status(preview_result), "Preview JPEG ready")

    def test_auto_adjust_status_summarizes_suggestion(self) -> None:
        suggestion = AutoAdjustSuggestion(
            exposure=0.3,
            contrast=0.12,
            highlights=-0.16,
            shadows=0.16,
            warmth=-0.06,
            tint=0.1,
            saturation=0.08,
            rationale=("test",),
        )

        status = _auto_adjust_status(suggestion)

        self.assertIn("+0.3 EV", status)
        self.assertIn("Contrast +12", status)
        self.assertIn("Highlights -16", status)
        self.assertIn("Shadows +16", status)
        self.assertIn("Temperature -6", status)
        self.assertIn("Tint +10", status)
        self.assertIn("Saturation +8", status)

    def test_manual_overrides_collects_tone_controls(self) -> None:
        self.assertEqual(
            _manual_overrides(0.5, -0.25, -0.3, 0.4, 0.75, -0.15, 0.2, .65),
            {
                "exposure": 0.5,
                "contrast": -0.25,
                "highlights": -0.3,
                "shadows": 0.4,
                "warmth": 0.75,
                "tint": -0.15,
                "saturation": 0.2,
                "color_noise": .65,
            },
        )

    def test_adjustments_match_with_small_tolerance(self) -> None:
        rendered = {
            "exposure": 0.5,
            "contrast": -0.25,
            "highlights": -0.3,
            "shadows": 0.4,
            "warmth": 0.75,
            "tint": -0.15,
            "saturation": 0.2,
        }
        current = {**rendered, "exposure": 0.50001}

        self.assertTrue(_adjustments_match(rendered, current))
        self.assertFalse(_adjustments_match(rendered, {**current, "warmth": 0.5}))
        self.assertFalse(_adjustments_match(rendered, {**current, "highlights": 0.2}))
        self.assertFalse(_adjustments_match(rendered, {**current, "shadows": -0.2}))
        self.assertFalse(_adjustments_match(rendered, {**current, "tint": 0.2}))
        self.assertFalse(_adjustments_match(rendered, {**current, "saturation": -0.2}))

    def test_preview_state_text_marks_stale_preview(self) -> None:
        current = {
            "exposure": 0.0,
            "contrast": 0.0,
            "highlights": 0.0,
            "shadows": 0.0,
            "warmth": 0.0,
            "tint": 0.0,
            "saturation": 0.0,
        }

        self.assertEqual(_preview_state_text(None, current), "No preview yet")
        self.assertEqual(_preview_state_text(current, current), "Preview current")
        self.assertEqual(_preview_state_text({**current, "contrast": 0.2}, current), "Preview needs update")

    def test_default_sample_path_uses_pictures_folder(self) -> None:
        sample_path = _default_sample_path(Path("C:/Users/Example"))

        self.assertEqual(sample_path, Path("C:/Users/Example/Pictures/OpenRAW Studio Samples/openraw-synthetic.DNG"))

    def test_default_sample_nikon_nef_path_uses_pictures_folder(self) -> None:
        sample_path = _default_sample_nikon_nef_path(Path("C:/Users/Example"))

        self.assertEqual(sample_path, Path("C:/Users/Example/Pictures/OpenRAW Studio Samples/openraw-synthetic-nikon.NEF"))

    def test_friendly_error_message_explains_unsupported_extension(self) -> None:
        message = _friendly_error_message(SourceFileError("Unsupported RAW extension: .jpg"))

        self.assertIn("not supported yet", message)
        self.assertIn("supported DNG", message)
        self.assertIn("Nikon RAW metadata", message)

    def test_friendly_error_message_explains_native_dng_limit(self) -> None:
        message = _friendly_error_message(
            BackendUnavailableError("OpenRAW Native preview failed: only uncompressed strips are supported")
        )

        self.assertIn("does not support yet", message)
        self.assertIn("sample DNG", message)


if __name__ == "__main__":
    unittest.main()
