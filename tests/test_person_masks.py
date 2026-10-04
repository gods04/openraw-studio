import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from openraw_studio.ui.mask_overlay import person_overlay
from openraw_studio.vision.mask import _box_mean, guided_selection, project_selection
from openraw_studio.vision.person import PersonAnalysis, PersonEvidence


class PersonMaskTests(unittest.TestCase):
    def test_auto_reuses_supplied_analysis_instead_of_running_inference_again(self):
        from openraw_studio.decision import auto_adjust
        class Photo:
            pixels = np.zeros((100, 100, 3), np.float32)
            def resized(self, _):
                return self
            def render(self, _):
                return Image.new("RGB", (100, 100)), "test"
        analysis = PersonAnalysis(PersonEvidence("not-installed"))
        with patch.object(auto_adjust, "analyze_person", side_effect=AssertionError("Duplicate inference")), \
                patch.object(auto_adjust, "analyze_scene", return_value=None), \
                patch.object(auto_adjust, "suggest_auto_adjustments_from_preview") as suggest:
            auto_adjust.suggest_auto_adjustments_for_photo(Photo(), person_analysis=analysis)
            self.assertIs(suggest.call_args.kwargs["person"], analysis)

    def test_box_means_match_direct_clipped_windows(self):
        values = np.random.default_rng(43).normal(size=(7, 9, 3)).astype(np.float32)
        for radius in (1, 3, 12):
            expected = np.empty_like(values)
            for y in range(7):
                for x in range(9):
                    expected[y, x] = values[max(0, y-radius):y+radius+1,
                                            max(0, x-radius):x+radius+1].mean(axis=(0, 1))
            np.testing.assert_allclose(_box_mean(values, radius), expected, atol=1e-6)

    def test_constant_selection_is_preserved_at_every_edge(self):
        image = Image.fromarray(np.random.default_rng(44).integers(0, 256, (60, 80, 3), dtype=np.uint8))
        for weight in (0, .6, 1):
            result = guided_selection(image, np.full((12, 16), weight, np.float32))
            np.testing.assert_allclose(result, weight, atol=1e-5)
            self.assertFalse(result.flags.writeable)

    def test_rgb_guidance_reduces_boundary_leakage_without_inventing_selection(self):
        pixels = np.zeros((192, 192, 3), np.uint8)
        pixels[:, :96] = (210, 60, 100)
        pixels[:, 96:] = (35, 140, 50)
        selection = np.zeros((192, 192), np.float32)
        selection[:, :98] = .99
        result = guided_selection(Image.fromarray(pixels), selection)
        self.assertLess(float(result[:, 96:98].mean()), .8)
        self.assertGreater(float(result[:, :80].min()), .98)
        self.assertTrue(np.all(result <= selection))
        self.assertTrue(np.all(result >= 0))

    def test_refinement_is_bounded_and_does_not_modify_inputs(self):
        image = Image.new("RGB", (2000, 1000), (80, 140, 70))
        selection = np.zeros((24, 24), np.float32)
        selection[6:18, 6:18] = .99
        original = selection.copy()
        result = guided_selection(image, selection)
        self.assertEqual(image.size, (2000, 1000))
        self.assertEqual(result.shape, (480, 960))
        np.testing.assert_array_equal(selection, original)
        self.assertFalse(np.shares_memory(selection, result))
        projected = project_selection(selection, result.shape)
        self.assertTrue(np.all(result <= projected))
        self.assertGreater(float(result[240, 480]), .98)

    def test_malformed_selection_fails_explicitly(self):
        image = Image.new("RGB", (24, 24))
        for bad in (np.ones((3, 3, 3)), np.zeros((0, 0)), np.full((3, 3), np.nan),
                    np.full((3, 3), -1), np.full((3, 3), 1.1)):
            with self.assertRaises(ValueError):
                guided_selection(image, bad)

    def test_normalized_viewport_projection_matches_full_frame_crop(self):
        selection = np.random.default_rng(10).random((100, 120), dtype=np.float32)
        full = project_selection(selection, (200, 240))
        tile = project_selection(selection, (100, 120), box=(.25, .25, .75, .75))
        np.testing.assert_allclose(tile, full[50:150, 60:180], atol=1e-7)
        rounded = project_selection(selection, (200, 240), box=(-1e-16, 0, 1 + 2e-16, 1))
        np.testing.assert_array_equal(rounded, full)
        for box in ((0, 0, 1.1, 1), (.5, 0, .5, 1), (0, 0, np.nan, 1)):
            with self.assertRaises(ValueError):
                project_selection(selection, (20, 20), box=box)

    def test_refined_core_is_used_by_both_auto_and_display(self):
        raw = np.full((60, 80), .99, np.float32)
        selected = np.zeros_like(raw)
        selected[10:50, 20:60] = .99
        analysis = PersonAnalysis(PersonEvidence("ready"), raw, selected)
        image = Image.new("RGB", (80, 60), (65, 90, 125))
        original = image.tobytes()
        shown = person_overlay(image, analysis)
        changed = np.any(np.asarray(image) != np.asarray(shown), axis=-1)
        np.testing.assert_array_equal(changed, analysis.core_mask((60, 80)))
        self.assertEqual(image.tobytes(), original)
        self.assertIs(person_overlay(image, PersonAnalysis(PersonEvidence("unconfirmed"))), image)

    def test_display_tile_never_reframes_the_full_mask_into_a_crop(self):
        weights = np.zeros((100, 100), np.float32)
        weights[10:50, 10:50] = .99
        analysis = PersonAnalysis(PersonEvidence("ready"), weights)
        image = Image.new("RGB", (200, 200), (65, 90, 125))
        full = person_overlay(image, analysis)
        left = person_overlay(image.crop((0, 0, 100, 100)), analysis, box=(0, 0, .5, .5))
        right = person_overlay(image.crop((100, 100, 200, 200)), analysis, box=(.5, .5, 1, 1))
        self.assertEqual(left.tobytes(), full.crop((0, 0, 100, 100)).tobytes())
        self.assertEqual(right.tobytes(), image.crop((100, 100, 200, 200)).tobytes())


if __name__ == "__main__":
    unittest.main()
