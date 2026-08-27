import tempfile
import unittest
from pathlib import Path

from openraw_studio.core.domain import ImageRef
from openraw_studio.pipeline.errors import BackendUnavailableError, SourceFileError
from openraw_studio.pipeline.interfaces import PipelineResult
from openraw_studio.ui.desktop import (
    _adjustments_match,
    _default_sample_path,
    _flatten_rgb_pixels,
    _format_adjustment_label,
    _format_exposure_label,
    _format_result_summary,
    _friendly_error_message,
    _manual_overrides,
    _preview_state_text,
    _result_status,
)


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

    def test_flatten_rgb_pixels_returns_pillow_ready_bytes(self) -> None:
        self.assertEqual(_flatten_rgb_pixels(((1, 2, 3), (4, 5, 6))), b"\x01\x02\x03\x04\x05\x06")

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

    def test_result_status_distinguishes_preview_from_export(self) -> None:
        preview_result = PipelineResult(recipe={}, diagnostics={"preview_only": True})
        export_result = PipelineResult(
            recipe={},
            exports=(ImageRef(Path("export.jpg"), width=1, height=1, color_space="sRGB", role="export"),),
        )

        self.assertEqual(_result_status(preview_result), "Preview updated")
        self.assertEqual(_result_status(export_result), "JPEG exported")

    def test_manual_overrides_collects_tone_controls(self) -> None:
        self.assertEqual(
            _manual_overrides(0.5, -0.25, 0.75),
            {"exposure": 0.5, "contrast": -0.25, "warmth": 0.75},
        )

    def test_adjustments_match_with_small_tolerance(self) -> None:
        rendered = {"exposure": 0.5, "contrast": -0.25, "warmth": 0.75}
        current = {"exposure": 0.50001, "contrast": -0.25, "warmth": 0.75}

        self.assertTrue(_adjustments_match(rendered, current))
        self.assertFalse(_adjustments_match(rendered, {**current, "warmth": 0.5}))

    def test_preview_state_text_marks_stale_preview(self) -> None:
        current = {"exposure": 0.0, "contrast": 0.0, "warmth": 0.0}

        self.assertEqual(_preview_state_text(None, current), "No preview yet")
        self.assertEqual(_preview_state_text(current, current), "Preview current")
        self.assertEqual(_preview_state_text({**current, "contrast": 0.2}, current), "Preview needs update")

    def test_default_sample_path_uses_pictures_folder(self) -> None:
        sample_path = _default_sample_path(Path("C:/Users/Example"))

        self.assertEqual(sample_path, Path("C:/Users/Example/Pictures/OpenRAW Studio Samples/openraw-synthetic.DNG"))

    def test_friendly_error_message_explains_unsupported_extension(self) -> None:
        message = _friendly_error_message(SourceFileError("Unsupported RAW extension: .jpg"))

        self.assertIn("not supported yet", message)
        self.assertIn("DNG-first", message)

    def test_friendly_error_message_explains_native_dng_limit(self) -> None:
        message = _friendly_error_message(
            BackendUnavailableError("OpenRAW Native preview failed: only uncompressed strips are supported")
        )

        self.assertIn("does not support yet", message)
        self.assertIn("sample DNG", message)


if __name__ == "__main__":
    unittest.main()
