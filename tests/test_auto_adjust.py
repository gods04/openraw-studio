import tempfile
import unittest
from pathlib import Path

import numpy as np
from fixtures_nikon import synthetic_nikon_nef_compressed_bytes

from openraw_studio.decision.auto_adjust import (
    suggest_auto_adjustments,
    suggest_auto_adjustments_for_photo,
    suggest_auto_adjustments_from_preview,
)
from openraw_studio.raw.native.interactive import InteractivePhoto
from openraw_studio.raw.native.synthetic import write_synthetic_dng
from openraw_studio.raw.native.tone import PreviewRgbImage


class AutoAdjustTests(unittest.TestCase):
    @staticmethod
    def shadow_recovery_scene(*, lift=12, clip_shadows=False, intermediate_clip=False):
        pixels = np.full((100, 100, 3), 60, dtype=np.uint8)
        pixels[45:50, 48:52] = 210

        def render(values):
            candidate = pixels.copy()
            candidate[:40] = 60 + round(lift * values["shadows"])
            candidate[50:] = 60 + round(lift * values["shadows"])
            if (
                values["exposure"] > .4
                or (clip_shadows and values["shadows"] > .2)
                or (intermediate_clip and .15 < values["shadows"] < .3)
            ):
                candidate[45:50, 48:52] = 255
            return candidate

        return pixels, render

    def test_shadow_refinement_lifts_subject_after_highlights_limit_exposure(self):
        pixels, render = self.shadow_recovery_scene()
        original = pixels.copy()
        result = suggest_auto_adjustments_from_preview(pixels, render=render)
        self.assertEqual(result.metrics["shadows_refined"], 1)
        self.assertLessEqual(result.exposure, .4)
        self.assertGreater(result.shadows, .4)
        self.assertLessEqual(result.shadows, .65)
        self.assertEqual(result.metrics["highlight_detail_loss_fraction"], 0)
        self.assertGreater(result.metrics["shadow_midtone_mean_after"], result.metrics["shadow_midtone_mean_before"])
        self.assertTrue(np.array_equal(original, pixels))
        self.assertIn("Lifted usable shadows", " ".join(result.rationale))

    def test_shadow_refinement_rechecks_clipping_at_both_resolutions(self):
        pixels, render = self.shadow_recovery_scene()
        _, unsafe = self.shadow_recovery_scene(clip_shadows=True)
        for primary, detail in ((unsafe, render), (render, unsafe)):
            with self.subTest(primary=primary):
                result = suggest_auto_adjustments_from_preview(
                    pixels, render=primary, detail_preview=pixels, render_detail=detail
                )
                self.assertEqual(result.metrics["shadows_refined"], 0)
                self.assertLessEqual(result.shadows, .2)

    def test_shadow_refinement_rechecks_intermediate_strength(self):
        pixels, base_render = self.shadow_recovery_scene(intermediate_clip=True)

        def render(values):
            candidate = base_render(values)
            if values["exposure"] > .25 and values["highlights"] > -.2:
                candidate[45:50, 48:52] = 255
            return candidate

        endpoint = suggest_auto_adjustments_from_preview(pixels, render=render)
        self.assertEqual(endpoint.metrics["shadows_refined"], 1)
        self.assertGreater(endpoint.shadows, .3)
        self.assertEqual(render({k: v*.5 for k, v in endpoint.as_overrides().items()})[45, 48, 0], 255)
        result = suggest_auto_adjustments_from_preview(
            pixels, render=render, validation_strengths=(.7, .5, .25),
            detail_preview=pixels, render_detail=render,
        )
        self.assertEqual(result.metrics["shadows_refined"], 0)
        for strength in (.25, .5, .7, 1):
            self.assertLess(render({k: v*strength for k, v in result.as_overrides().items()})[45, 48, 0], 254)

    def test_shadow_refinement_backs_off_excessive_lift_at_either_resolution(self):
        pixels, ordinary = self.shadow_recovery_scene()
        _, aggressive = self.shadow_recovery_scene(lift=40)
        baseline = suggest_auto_adjustments_from_preview(pixels, render=ordinary)
        for primary, detail in ((aggressive, ordinary), (ordinary, aggressive)):
            result = suggest_auto_adjustments_from_preview(
                pixels, render=primary, detail_preview=pixels, render_detail=detail
            )
            self.assertEqual(result.metrics["shadows_refined"], 1)
            self.assertLess(result.shadows, baseline.shadows)
            for prefix in ("", "detail_"):
                self.assertLessEqual(result.metrics[f"{prefix}shadow_midtone_mean_after"], .30)

    def test_shadow_refinement_keeps_unconstrained_exposure_and_night_scenes(self):
        for scene in ("dim", "night", "near-black", "bright"):
            with self.subTest(scene=scene):
                pixels = np.full((100, 100, 3), 60 if scene == "dim" else 150, dtype=np.uint8)
                if scene == "night":
                    pixels[:90] = 8
                elif scene == "near-black":
                    pixels[:60] = 20
                def render(values, pixels=pixels, scene=scene):
                    candidate = np.clip(pixels.astype(np.int32) + round(12 * values["shadows"]), 0, 255).astype(np.uint8)
                    if scene in ("night", "near-black") and values["exposure"] > .1:
                        candidate[95:97] = 255
                    return candidate

                result = suggest_auto_adjustments_from_preview(pixels, render=render)
                self.assertEqual(result.metrics["shadows_refined"], 0)

    def test_shadow_refinement_requires_a_measurable_gain(self):
        pixels, render = self.shadow_recovery_scene(lift=0)
        result = suggest_auto_adjustments_from_preview(pixels, render=render)
        self.assertEqual(result.metrics["shadows_refined"], 0)

    def test_shadow_refinement_requires_gain_at_detail_resolution_too(self):
        pixels, render = self.shadow_recovery_scene()
        _, unchanged = self.shadow_recovery_scene(lift=0)
        result = suggest_auto_adjustments_from_preview(
            pixels, render=render, detail_preview=pixels, render_detail=unchanged
        )
        self.assertEqual(result.metrics["shadows_refined"], 0)
        self.assertLess(result.shadows, .2)

    def test_shadow_refinement_requires_sufficient_subject_area(self):
        pixels, render = self.shadow_recovery_scene()
        pixels[:] = 120
        pixels[:53] = 15
        pixels[53:58] = 60
        result = suggest_auto_adjustments_from_preview(pixels, render=render)
        self.assertEqual(result.metrics["shadows_refined"], 0)

    def test_real_shadow_refinement_retains_black_and_highlight_detail(self):
        pixels = np.full((100, 100, 3), .025, dtype=np.float32)
        pixels[:10] = 0
        pixels[45:50, 48:52] = .75
        photo = InteractivePhoto(pixels, np.eye(3, dtype=np.float32), (1, 1, 1))
        result = suggest_auto_adjustments_for_photo(photo)
        self.assertEqual(result.metrics["shadows_refined"], 1)
        final = np.asarray(photo.render(result.as_overrides())[0])
        self.assertTrue(np.all(final[:10] == 0))
        self.assertTrue(np.all(final[45:50, 48:52] < 254))
        self.assertGreater(result.metrics["shadow_midtone_mean_after"], result.metrics["shadow_midtone_mean_before"])

    def test_intermediate_strength_cannot_escape_highlight_validation(self):
        pixels = np.full((100, 100, 3), 60, dtype=np.uint8)
        pixels[40:60, 45:55] = 220
        renders = []

        def render(values):
            renders.append(tuple(sorted(values.items())))
            candidate = pixels.copy()
            # Strong compression hides clipping that occurs at mid-strength.
            if values["exposure"] > .1 and values["highlights"] > -.25:
                candidate[40:60, 45:55] = 255
            return candidate

        endpoint_only = suggest_auto_adjustments_from_preview(pixels, render=render)
        self.assertGreater(endpoint_only.exposure, .2)
        middle = {key: value * .5 for key, value in endpoint_only.as_overrides().items()}
        self.assertEqual(render(middle)[40, 45, 0], 255)
        renders.clear()
        result = suggest_auto_adjustments_from_preview(
            pixels, render=render, detail_preview=pixels, render_detail=render,
            validation_strengths=(.7, .5, .25),
        )
        self.assertGreater(result.exposure, 0)
        for amount in (.25, .5, .7, 1):
            edits = {key: value * amount for key, value in result.as_overrides().items()}
            self.assertLess(render(edits)[40, 45, 0], 254)
        self.assertIn("validated_strength_samples", result.metrics)

    def test_strength_validation_rejects_invalid_inputs(self):
        pixels = np.full((4, 4, 3), 100, dtype=np.uint8)
        for strengths in ((0,), (-.5,), (1.1,), (float("nan"),), (float("inf"),)):
            with self.assertRaisesRegex(ValueError, "Validation strengths"):
                suggest_auto_adjustments_from_preview(pixels, render=lambda _: pixels, validation_strengths=strengths)
        with self.assertRaisesRegex(ValueError, "requires a renderer"):
            suggest_auto_adjustments_from_preview(pixels, validation_strengths=(.5,))

    def test_detail_guard_protects_highlights_missing_from_analysis(self):
        small = np.full((4, 4, 3), 60, dtype=np.uint8)
        detail = np.full((100, 100, 3), 60, dtype=np.uint8)
        detail[45:50, 48:52] = 210
        rendered = []

        def render_detail(values):
            rendered.append(tuple(sorted(values.items())))
            candidate = detail.copy()
            if values["exposure"] > .4 and values["highlights"] > -.2:
                candidate[45:50, 48:52] = 255
            return candidate

        result = suggest_auto_adjustments_from_preview(
            small, render=lambda _: small, detail_preview=detail, render_detail=render_detail
        )
        self.assertGreater(result.exposure, .4)
        self.assertEqual(result.highlights, -.3)
        self.assertEqual(result.metrics["detail_highlight_detail_loss_fraction"], 0)
        self.assertEqual(result.metrics["detail_validation_pixels"], 10000)
        self.assertEqual(len(rendered), len(set(rendered)))

    def test_detail_guard_rejects_shadow_crushing_hidden_in_analysis(self):
        small = np.full((4, 4, 3), 100, dtype=np.uint8)
        detail = np.full((100, 100, 3), 100, dtype=np.uint8)
        detail[:10] = 25

        def render_detail(values):
            candidate = detail.copy()
            if values["contrast"] > 0:
                candidate[:10] = 0
            return candidate

        result = suggest_auto_adjustments_from_preview(
            small, render=lambda _: small, detail_preview=detail, render_detail=render_detail
        )
        self.assertEqual(result.contrast, 0)
        self.assertEqual(result.metrics["contrast_guarded"], 1)
        self.assertEqual(result.metrics["detail_new_shadow_clipping_fraction"], 0)

    def test_rejected_analysis_candidates_skip_expensive_detail_renders(self):
        small = np.full((4, 4, 3), 60, dtype=np.uint8)
        detail = np.full((100, 100, 3), 60, dtype=np.uint8)

        def render(values):
            return np.full_like(small, 255 if values["exposure"] > .15 else 60)

        result = suggest_auto_adjustments_from_preview(
            small, render=render, detail_preview=detail, render_detail=lambda _: detail
        )
        self.assertLessEqual(result.exposure, .15)
        self.assertLessEqual(result.metrics["detail_validation_renders"], 2)
        self.assertGreater(result.metrics["validation_renders"], 10)
        self.assertLessEqual(result.metrics["validation_renders"], 42)

    def test_detail_validation_requires_matching_baseline_and_renderer(self):
        pixels = np.full((4, 4, 3), 100, dtype=np.uint8)
        for options in (
            {"detail_preview": pixels},
            {"render_detail": lambda _: pixels},
            {"detail_preview": pixels, "render_detail": lambda _: pixels, "render": None},
        ):
            with self.assertRaisesRegex(ValueError, "Detail validation requires"):
                suggest_auto_adjustments_from_preview(pixels, **{ "render": lambda _: pixels, **options})
        with self.assertRaisesRegex(ValueError, "baseline preview dimensions"):
            suggest_auto_adjustments_from_preview(
                pixels, render=lambda _: pixels, detail_preview=pixels,
                render_detail=lambda _: np.zeros((3, 3, 3)),
            )

    def test_real_detail_guard_retains_tiny_bright_subject(self):
        values = np.full((640, 960, 3), .05, dtype=np.float32)
        values[300:302, 460:462] = .8
        photo = InteractivePhoto(values, np.eye(3, dtype=np.float32), (1, 1, 1))
        analysis = photo.resized(256)
        baseline = analysis.render({})[0]
        self.assertLess(np.asarray(baseline).max(), 153)
        coarse_only = suggest_auto_adjustments_from_preview(
            baseline, render=lambda edits: analysis.render(edits)[0]
        )
        coarse_result = np.asarray(photo.render(coarse_only.as_overrides())[0])
        self.assertGreaterEqual(coarse_result[300:302, 460:462].max(), 254)
        result = suggest_auto_adjustments_for_photo(photo)
        self.assertGreater(result.exposure, 0)
        self.assertEqual(result.metrics["detail_highlight_detail_loss_fraction"], 0)
        self.assertGreater(result.metrics["detail_median_luma_after"], result.metrics["detail_median_luma"])
        self.assertTrue(np.all(values[300:302, 460:462] == .8))
        self.assertLessEqual(result.metrics["detail_validation_renders"], 42)

    def test_small_interactive_photo_needs_no_second_guard(self):
        photo = InteractivePhoto(np.full((4, 4, 3), .2, dtype=np.float32), np.eye(3), (1, 1, 1))
        result = suggest_auto_adjustments_for_photo(photo)
        self.assertNotIn("detail_validation_renders", result.metrics)

    def test_low_key_scene_keeps_its_intent(self) -> None:
        pixels = ((12, 12, 12),) * 90 + ((220, 220, 220),) * 10
        suggestion = suggest_auto_adjustments_from_preview(PreviewRgbImage(10, 10, pixels, "gamma-2.2"))
        self.assertEqual(suggestion.scene, "Low-key")
        self.assertLessEqual(suggestion.exposure, .35)
        self.assertLessEqual(suggestion.shadows, .10)

    def test_near_neutral_cast_and_grayscale(self) -> None:
        cool = PreviewRgbImage(4, 4, ((115, 120, 136),) * 16, "gamma-2.2")
        suggestion = suggest_auto_adjustments_from_preview(cool)
        self.assertGreater(suggestion.warmth, 0)
        gray = PreviewRgbImage(4, 4, ((128, 128, 128),) * 16, "gamma-2.2")
        neutral = suggest_auto_adjustments_from_preview(gray)
        self.assertEqual((neutral.warmth, neutral.tint, neutral.saturation), (0, 0, 0))

    def test_render_guard_reduces_a_clipping_candidate(self) -> None:
        preview = PreviewRgbImage(10, 10, ((50, 50, 50),) * 100, "gamma-2.2")
        renders = []
        def render(settings):
            renders.append(settings)
            return np.full((10, 10, 3), 255 if settings['exposure'] > .4 else 100, dtype=np.uint8)
        result = suggest_auto_adjustments_from_preview(preview, render=render)
        self.assertLessEqual(result.exposure, .4)
        self.assertEqual(result.metrics["exposure_guarded"], 1)
        self.assertEqual(result.metrics["highlight_fraction_after"], 0)
        self.assertGreater(len(renders), 1)

    def test_small_bright_subject_is_not_hidden_by_global_clipping_budget(self):
        pixels = np.full((100, 100, 3), 60, dtype=np.uint8)
        pixels[45:50, 48:52] = 200
        preview = PreviewRgbImage(100, 100, tuple(map(tuple, pixels.reshape(-1, 3))), "gamma-2.2")

        def render(values):
            result = pixels.copy()
            result[45:50, 48:52] = 255 if values["exposure"] > .4 else 220
            return result

        result = suggest_auto_adjustments_from_preview(preview, render=render)
        self.assertGreater(result.exposure, 0)
        self.assertLessEqual(result.exposure, .4)
        self.assertGreater(result.shadows, 0)
        self.assertEqual(result.metrics["highlight_detail_loss_fraction"], 0)
        self.assertEqual(result.metrics["exposure_guarded"], 1)

    def test_clipped_red_does_not_hide_newly_clipped_green_detail(self):
        pixels = np.full((100, 100, 3), 60, dtype=np.uint8)
        pixels[45:50, 48:52] = (255, 200, 160)
        preview = PreviewRgbImage(100, 100, tuple(map(tuple, pixels.reshape(-1, 3))), "gamma-2.2")

        def render(values):
            result = pixels.copy()
            if values["exposure"] > .4:
                result[45:50, 48:52, 1] = 255
            return result

        result = suggest_auto_adjustments_from_preview(preview, render=render)
        self.assertEqual(result.metrics["highlight_fraction_before"], result.metrics["highlight_fraction_after"])
        self.assertLessEqual(result.exposure, .4)
        self.assertEqual(result.metrics["highlight_detail_loss_fraction"], 0)

    def test_isolated_bright_outlier_does_not_disable_exposure_lift(self):
        pixels = np.full((100, 100, 3), 60, dtype=np.uint8)
        pixels[45, 48, 0] = 200
        preview = PreviewRgbImage(100, 100, tuple(map(tuple, pixels.reshape(-1, 3))), "gamma-2.2")

        def render(values):
            result = pixels.copy()
            result[45, 48, 0] = 255
            return result

        result = suggest_auto_adjustments_from_preview(preview, render=render)
        self.assertGreater(result.exposure, .4)
        self.assertEqual(result.metrics["exposure_guarded"], 0)

    def test_saturation_clipping_backs_off_when_exposure_alone_cannot_help(self):
        pixels = np.full((100, 100, 3), 60, dtype=np.uint8)
        pixels[:, :, 2] = 63
        pixels[45:50, 48:52] = (200, 195, 195)
        preview = PreviewRgbImage(100, 100, tuple(map(tuple, pixels.reshape(-1, 3))), "gamma-2.2")

        rendered_settings = []
        def render(values):
            rendered_settings.append(tuple(sorted(values.items())))
            result = pixels.copy()
            if values["saturation"] > .01:
                result[45:50, 48:52, 0] = 255
            return result

        result = suggest_auto_adjustments_from_preview(preview, render=render)
        self.assertLess(result.metrics["guard_strength"], 1)
        self.assertEqual(result.metrics["highlight_detail_loss_fraction"], 0)
        self.assertEqual(len(rendered_settings), len(set(rendered_settings)))
        self.assertEqual(result.metrics["validation_renders"], len(rendered_settings))
        self.assertLessEqual(len(rendered_settings), 42)

    def test_limited_exposure_rechecks_contrast_and_compresses_highlights(self):
        pixels = np.full((100, 100, 3), (60, 62, 64), dtype=np.uint8)
        pixels[45:50, 48:52] = (220, 210, 200)

        def render(values):
            delta = round(30 * values["exposure"] - 80 * values["contrast"])
            candidate = np.clip(pixels.astype(np.int32) + delta, 0, 255).astype(np.uint8)
            if values["exposure"] > .31 or values["highlights"] > -.2:
                candidate[45:50, 48:52] = 255
            return candidate

        result = suggest_auto_adjustments_from_preview(pixels, render=render)
        self.assertGreater(result.exposure, 0)
        self.assertLessEqual(result.exposure, .31)
        self.assertEqual(result.contrast, 0)
        self.assertEqual(result.highlights, -.3)
        self.assertGreater(result.shadows, 0)
        self.assertGreater(result.saturation, 0)
        self.assertEqual(result.metrics["guard_strength"], 1)
        self.assertEqual(result.metrics["highlight_detail_loss_fraction"], 0)
        self.assertGreater(result.metrics["median_luma_after"], result.metrics["median_luma"])
        self.assertEqual(result.metrics["highlights_guarded"], 1)
        self.assertLessEqual(result.metrics["validation_renders"], 42)

    def test_joint_tone_guard_can_help_without_positive_exposure(self):
        pixels = np.full((100, 100, 3), 110, dtype=np.uint8)
        pixels[45:50, 48:52] = 220

        def render(values):
            candidate = pixels.copy()
            if values["contrast"] > .01 or values["highlights"] > -.2:
                candidate[45:50, 48:52] = 255
            return candidate

        result = suggest_auto_adjustments_from_preview(pixels, render=render)
        self.assertEqual(result.exposure, 0)
        self.assertEqual(result.contrast, 0)
        self.assertEqual(result.highlights, -.3)
        self.assertEqual(result.metrics["guard_strength"], 1)
        self.assertEqual(result.metrics["highlight_detail_loss_fraction"], 0)

    def test_real_linear_renderer_lifts_dim_scene_without_losing_small_bright_subject(self):
        values = np.full((100, 100, 3), .05, dtype=np.float32)
        values[45:50, 48:52] = .75
        photo = InteractivePhoto(values, np.eye(3, dtype=np.float32), (1, 1, 1))
        original, _ = photo.render({})
        result = suggest_auto_adjustments_from_preview(original, render=lambda edits: photo.render(edits)[0])
        self.assertGreater(result.exposure, 0)
        self.assertGreater(result.metrics["median_luma_after"], result.metrics["median_luma"])
        self.assertEqual(result.metrics["highlight_detail_loss_fraction"], 0)
        self.assertLessEqual(result.metrics["new_shadow_clipping_fraction"], .005)

    def test_safe_suggestion_only_renders_once(self):
        pixels = np.full((10, 10, 3), 100, dtype=np.uint8)
        renders = []

        def render(values):
            renders.append(values)
            return pixels

        result = suggest_auto_adjustments_from_preview(pixels, render=render)
        self.assertEqual(len(renders), 1)
        self.assertEqual(result.metrics["validation_renders"], 1)
        self.assertEqual(result.metrics["guard_strength"], 1)

    def test_bright_scene_can_recover_highlights_without_undoing_negative_exposure(self):
        pixels = np.full((100, 100, 3), 150, dtype=np.uint8)
        pixels[45:50, 48:52] = 220

        def render(values):
            candidate = pixels.copy()
            candidate[:40] = 140
            if values["highlights"] > -.2:
                candidate[45:50, 48:52] = 255
            return candidate

        result = suggest_auto_adjustments_from_preview(pixels, render=render)
        self.assertLess(result.exposure, 0)
        self.assertEqual(result.highlights, -.3)
        self.assertEqual(result.metrics["highlight_detail_loss_fraction"], 0)
        self.assertEqual(result.metrics["exposure_guarded"], 0)

    def test_balanced_input_is_deterministic_and_rejects_invalid_pixels(self) -> None:
        preview = PreviewRgbImage(2, 2, ((100, 110, 120),) * 4, "gamma-2.2")
        self.assertEqual(suggest_auto_adjustments_from_preview(preview), suggest_auto_adjustments_from_preview(preview))
        with self.assertRaises(ValueError):
            suggest_auto_adjustments_from_preview(PreviewRgbImage(0, 0, (), "gamma-2.2"))

    def test_render_guard_reduces_contrast_that_darkens_a_dim_subject(self):
        preview = PreviewRgbImage(10, 10, ((90, 90, 90),) * 100, "gamma-2.2")
        def render(values):
            return np.full((10, 10, 3), 65 if values["contrast"] > .01 else 105, dtype=np.uint8)
        suggestion = suggest_auto_adjustments_from_preview(preview, render=render)
        self.assertEqual(suggestion.contrast, 0)
        self.assertGreater(suggestion.exposure, 0)
        self.assertEqual(suggestion.metrics["contrast_guarded"], 1)
        self.assertGreater(suggestion.metrics["median_luma_after"], 90 / 255)

    def test_render_guard_protects_shadow_detail_even_when_median_is_bright(self):
        pixels = ((25, 25, 25),) * 20 + ((150, 150, 150),) * 80
        preview = PreviewRgbImage(10, 10, pixels, "gamma-2.2")
        def render(values):
            candidate = np.array(pixels, dtype=np.uint8)
            if values["contrast"] > .01:
                candidate[:20] = 0
            return candidate
        suggestion = suggest_auto_adjustments_from_preview(preview, render=render)
        self.assertEqual(suggestion.contrast, 0)
        self.assertEqual(suggestion.metrics["new_shadow_clipping_fraction"], 0)

    def test_render_guard_rejects_mismatched_sampling(self):
        preview = PreviewRgbImage(2, 2, ((90, 90, 90),) * 4, "gamma-2.2")
        with self.assertRaisesRegex(ValueError, "baseline preview dimensions"):
            suggest_auto_adjustments_from_preview(preview, render=lambda _: np.zeros((3, 3, 3)))

    def test_small_bright_background_does_not_trigger_positive_contrast(self):
        pixels = ((15, 15, 15),) * 30 + ((70, 70, 70),) * 68 + ((245, 245, 245),) * 2
        suggestion = suggest_auto_adjustments_from_preview(PreviewRgbImage(10, 10, pixels, "gamma-2.2"))
        self.assertEqual(suggestion.scene, "High contrast")
        self.assertEqual(suggestion.contrast, 0)

    def test_dark_flat_preview_gets_lift_and_contrast(self) -> None:
        preview = PreviewRgbImage(
            width=2,
            height=2,
            pixels=((45, 45, 45), (52, 52, 52), (60, 60, 60), (66, 66, 66)),
            transfer="gamma-1",
        )

        suggestion = suggest_auto_adjustments_from_preview(preview)

        self.assertGreater(suggestion.exposure, 0.0)
        self.assertGreater(suggestion.contrast, 0.0)
        self.assertGreater(suggestion.shadows, 0.0)
        self.assertIn("dark", " ".join(suggestion.rationale))

    def test_bright_preview_gets_exposure_reduction(self) -> None:
        preview = PreviewRgbImage(
            width=2,
            height=2,
            pixels=((220, 220, 220), (230, 230, 230), (240, 240, 240), (250, 250, 250)),
            transfer="gamma-1",
        )

        suggestion = suggest_auto_adjustments_from_preview(preview)

        self.assertLess(suggestion.exposure, 0.0)
        self.assertLess(suggestion.highlights, 0.0)

    def test_blue_lighting_is_not_mistaken_for_a_white_balance_cast(self) -> None:
        preview = PreviewRgbImage(
            width=2,
            height=2,
            pixels=((90, 110, 155), (90, 110, 155), (90, 110, 155), (90, 110, 155)),
            transfer="gamma-1",
        )

        suggestion = suggest_auto_adjustments_from_preview(preview)

        self.assertEqual(suggestion.warmth, 0.0)

    def test_sparse_bright_subject_keeps_night_background_dark(self) -> None:
        preview = PreviewRgbImage(100, 100, ((2, 2, 3),) * 9900 + ((180, 190, 200),) * 100, "gamma-2.2")
        suggestion = suggest_auto_adjustments_from_preview(preview)
        self.assertEqual(suggestion.scene, "Low-key")
        self.assertLessEqual(suggestion.exposure, .35)
        self.assertLessEqual(suggestion.shadows, .10)
        self.assertEqual(suggestion.contrast, 0)

    def test_saturated_green_scene_retains_its_color(self) -> None:
        preview = PreviewRgbImage(
            width=2,
            height=2,
            pixels=((90, 150, 100), (90, 150, 100), (90, 150, 100), (90, 150, 100)),
            transfer="gamma-1",
        )

        suggestion = suggest_auto_adjustments_from_preview(preview)

        self.assertEqual(suggestion.tint, 0.0)

    def test_warm_sky_with_nearly_neutral_clouds_is_not_a_gray_card(self):
        pixels = ((170, 110, 70),) * 75 + ((150, 130, 120),) * 25
        suggestion = suggest_auto_adjustments_from_preview(PreviewRgbImage(10, 10, pixels, "gamma-2.2"))
        self.assertGreater(suggestion.metrics["neutral_fraction"], .08)
        self.assertEqual((suggestion.warmth, suggestion.tint), (0, 0))

    def test_blue_sea_with_pale_clouds_keeps_its_color(self):
        pixels = ((40, 100, 150),) * 85 + ((120, 130, 150),) * 15
        suggestion = suggest_auto_adjustments_from_preview(PreviewRgbImage(10, 10, pixels, "gamma-2.2"))
        self.assertGreater(suggestion.metrics["neutral_fraction"], .08)
        self.assertEqual((suggestion.warmth, suggestion.tint), (0, 0))

    def test_suggestion_exports_recipe_overrides(self) -> None:
        preview = PreviewRgbImage(width=1, height=1, pixels=((128, 128, 128),), transfer="gamma-1")

        overrides = suggest_auto_adjustments_from_preview(preview).as_overrides()

        self.assertEqual(set(overrides), {"exposure", "contrast", "highlights", "shadows", "warmth", "tint", "saturation"})

    def test_muted_preview_gets_saturation_lift(self) -> None:
        preview = PreviewRgbImage(
            width=2,
            height=2,
            pixels=((110, 112, 114), (118, 120, 122), (128, 130, 132), (138, 140, 142)),
            transfer="gamma-1",
        )

        suggestion = suggest_auto_adjustments_from_preview(preview)

        self.assertGreater(suggestion.saturation, 0.0)

    def test_suggest_auto_adjustments_reads_synthetic_dng(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = write_synthetic_dng(Path(temp) / "sample.DNG", width=8, height=8)

            suggestion = suggest_auto_adjustments(source)

        self.assertLessEqual(abs(suggestion.exposure), 1.2)
        self.assertLessEqual(abs(suggestion.contrast), 0.22)
        self.assertLessEqual(abs(suggestion.highlights), 0.3)
        self.assertLessEqual(abs(suggestion.shadows), 0.28)
        self.assertLessEqual(abs(suggestion.warmth), 0.12)
        self.assertLessEqual(abs(suggestion.tint), 0.1)
        self.assertLessEqual(abs(suggestion.saturation), 0.14)

    def test_suggest_auto_adjustments_reads_nikon_34713_lossless_nef(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "sample.NEF"
            source.write_bytes(synthetic_nikon_nef_compressed_bytes(width=8, height=8))

            suggestion = suggest_auto_adjustments(source)

        self.assertLessEqual(abs(suggestion.exposure), 1.2)
        self.assertLessEqual(abs(suggestion.contrast), 0.22)
        self.assertLessEqual(abs(suggestion.highlights), 0.3)
        self.assertLessEqual(abs(suggestion.shadows), 0.28)
        self.assertLessEqual(abs(suggestion.warmth), 0.12)
        self.assertLessEqual(abs(suggestion.tint), 0.1)
        self.assertLessEqual(abs(suggestion.saturation), 0.14)


if __name__ == "__main__":
    unittest.main()
