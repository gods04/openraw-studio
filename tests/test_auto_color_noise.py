import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np

from openraw_studio.decision.color_noise import (
    suggest_color_noise_for_photo,
    suggest_color_noise_from_tiles,
)
from openraw_studio.raw.native.chroma import reduce_color_noise


def noisy_tiles(sigma=8, *, count=16):
    clean = np.full((count, 32, 32, 3), (112, 119, 105), dtype=np.uint8)
    noise = np.random.default_rng(20).normal(0, sigma, clean.shape)
    return clean, np.rint(np.clip(clean.astype(float) + noise, 0, 255)).astype(np.uint8)


def filtered_tiles(tiles, amount):
    return np.stack([reduce_color_noise(tile, amount, use_gpu=False) for tile in tiles])


def advise(tiles, *, grid_count=None, render=None):
    return suggest_color_noise_from_tiles(
        tiles,
        render or (lambda amount: filtered_tiles(tiles, amount)),
        grid_count=grid_count or len(tiles),
    )


class AutoColorNoiseTests(unittest.TestCase):
    def test_clean_constant_colors_and_planar_gradients_need_no_filter(self):
        clean, _ = noisy_tiles()
        y, x = np.indices((32, 32))
        gradient = np.repeat((x + y)[None, ..., None], 3, axis=-1)
        for tiles in (clean, (clean + gradient).astype(np.uint8)):
            with self.subTest(gradient=bool(tiles.std())):
                result = advise(
                    tiles, render=lambda _: self.fail("Rendered clean image")
                )
                self.assertEqual(result.strength, 0)
                self.assertEqual(result.status, "low-noise")

    def test_more_noise_gets_more_filtering_with_measured_ground_truth_benefit(self):
        amounts = []
        for sigma in (3, 6, 12):
            clean, tiles = noisy_tiles(sigma)
            original = tiles.copy()
            result = advise(tiles)
            self.assertEqual(result.status, "suggested", result)
            self.assertGreaterEqual(result.strength, 0.1)
            self.assertLessEqual(result.strength, 0.7)
            self.assertLess(
                result.metrics["chroma_dispersion_after"],
                result.metrics["chroma_dispersion_before"],
            )
            adjusted = filtered_tiles(tiles, result.strength)
            self.assertLess(
                np.mean((adjusted.astype(float) - clean) ** 2),
                np.mean((tiles.astype(float) - clean) ** 2),
            )
            self.assertLess(
                np.abs(
                    adjusted.astype(float).mean(axis=(0, 1, 2))
                    - clean.mean(axis=(0, 1, 2))
                ).max(),
                0.5,
            )
            np.testing.assert_array_equal(original, tiles)
            amounts.append(result.strength)
        self.assertLess(amounts[0], amounts[1])
        self.assertLess(amounts[1], amounts[2])

    def test_luminance_grain_is_not_called_color_noise(self):
        noise = np.random.default_rng(29).integers(
            80, 160, (16, 32, 32, 1), dtype=np.uint8
        )
        self.assertEqual(advise(noise.repeat(3, axis=-1)).strength, 0)

    def test_repeating_fine_color_texture_is_not_noise(self):
        y, x = np.indices((32, 32))
        for pattern in ((x % 2), (y % 4 < 2), ((x + y) % 2)):
            tiles = np.full((16, 32, 32, 3), 110, np.uint8)
            tiles[..., 0] += (pattern * 20).astype(np.uint8)
            result = advise(tiles)
            self.assertIsNone(result.strength)
            self.assertEqual(result.status, "insufficient-samples")

    def test_clipped_and_dark_patches_do_not_supply_evidence(self):
        for color in ((0, 0, 0), (255, 100, 100), (8, 9, 10)):
            self.assertIsNone(
                advise(np.full((16, 32, 32, 3), color, np.uint8)).strength
            )

    def test_not_enough_distinct_regions_abstains(self):
        _, tiles = noisy_tiles(count=4)
        result = advise(tiles)
        self.assertGreaterEqual(result.metrics["usable_blocks"], 12)
        self.assertIsNone(result.strength)

    def test_bright_biased_samples_never_set_noise_level(self):
        clean, noisy = noisy_tiles(12)
        result = advise(np.concatenate((clean, noisy)), grid_count=16)
        self.assertEqual(result.strength, 0)

    def test_no_measurable_benefit_preserves_existing_setting(self):
        _, tiles = noisy_tiles()
        result = advise(tiles, render=lambda _: tiles)
        self.assertEqual(result.status, "no-safe-benefit")
        self.assertIsNone(result.strength)

    def test_tone_and_coarse_color_guards_reject_damaging_candidates(self):
        _, tiles = noisy_tiles()
        for color in ((255, 255, 255), (0, 0, 0), (112, 119, 150)):
            result = advise(tiles, render=lambda _, c=color: np.full_like(tiles, c))
            self.assertIsNone(result.strength)
            self.assertEqual(result.status, "no-safe-benefit")

    def test_candidate_backoff_can_retain_a_safe_smaller_amount(self):
        _, tiles = noisy_tiles(12)
        result = advise(
            tiles,
            render=lambda amount: (
                np.full_like(tiles, 255)
                if amount > 0.5
                else filtered_tiles(tiles, amount)
            ),
        )
        self.assertEqual(result.status, "suggested")
        self.assertEqual(result.strength, 0.35)

    def test_invalid_and_unbounded_inputs_rejected_and_tiny_tiles_abstain(self):
        clean, tiles = noisy_tiles()
        for invalid in (
            tiles.astype(float),
            tiles[0],
            tiles[..., :2],
            tiles[:, :0],
            np.tile(tiles, (21, 1, 1, 1)),
            np.zeros((2, 33, 32, 3), np.uint8),
        ):
            with self.assertRaises(ValueError):
                advise(invalid)
        for count in (0, 17, 257, 1.5, None):
            with self.assertRaises(ValueError):
                suggest_color_noise_from_tiles(clean, lambda _: clean, grid_count=count)
        for result in (tiles[:1], tiles.astype(float)):
            with self.assertRaises(ValueError):
                advise(tiles, render=lambda _, r=result: r)
        self.assertEqual(advise(tiles[:, :8, :8]).status, "insufficient-samples")

    def test_dense_bounds_allow_uniform_grid_and_bright_guards(self):
        clean, _ = noisy_tiles(count=320)
        self.assertEqual(advise(clean, grid_count=256).strength, 0)
        with self.assertRaises(ValueError):
            advise(clean, grid_count=257)

    def test_additional_validation_can_back_off_or_veto_a_dense_candidate(self):
        _, tiles = noisy_tiles(12)
        for accepted in (0.35, None):
            validate = Mock(side_effect=lambda amount: amount == accepted)
            result = suggest_color_noise_from_tiles(
                tiles, lambda amount: filtered_tiles(tiles, amount),
                grid_count=16, validate=validate,
            )
            self.assertEqual(result.strength, accepted)
            self.assertEqual([call.args[0] for call in validate.call_args_list], [0.7, 0.35])

    @staticmethod
    def sample_atlas(tiles, render=None):
        return SimpleNamespace(
            grid_count=len(tiles),
            render_tiles=Mock(side_effect=render or (
                lambda values: filtered_tiles(tiles, values["color_noise"])
            )),
        )

    def test_dense_retry_finds_new_evidence_without_raw_decode_or_proxy_votes(self):
        clean, noisy = noisy_tiles(12, count=256)
        sparse = noisy[:16].copy()
        sparse[2:] = 255
        photo = SimpleNamespace(
            native_samples=self.sample_atlas(sparse),
            native_noise_samples=self.sample_atlas(noisy),
        )
        edits = {"exposure": .8, "color_noise": .95, "luminance_noise": .6}
        result = suggest_color_noise_for_photo(photo, edits)
        self.assertEqual(result.status, "suggested", result)
        self.assertEqual(result.metrics["dense_retry"], 1)
        self.assertEqual(result.metrics["sparse_usable_regions"], 2)
        self.assertGreaterEqual(result.metrics["usable_regions"], 6)
        self.assertIn("sparse_coarse_color_drift_p95", result.metrics)
        adjusted = filtered_tiles(noisy, result.strength)
        self.assertLess(np.mean((adjusted.astype(float) - clean) ** 2), np.mean((noisy.astype(float) - clean) ** 2))
        for atlas in (photo.native_samples, photo.native_noise_samples):
            self.assertLessEqual(atlas.render_tiles.call_count, 3)
            for call in atlas.render_tiles.call_args_list:
                self.assertEqual(call.args[0]["exposure"], .8)
                self.assertEqual(call.args[0]["luminance_noise"], .6)
        self.assertEqual(edits["color_noise"], .95)

    def test_dense_retry_cannot_dilute_sparse_tone_or_color_damage(self):
        _, noisy = noisy_tiles(12, count=256)
        _, sparse = noisy_tiles(12)
        # Repeating color structure is not usable noise evidence.
        sparse[2:] = 110
        sparse[2:, :, ::2, 0] = 130
        for damage in ("clip", "chroma"):
            def render(values):
                if values["color_noise"] == 0:
                    return sparse
                result = filtered_tiles(sparse, values["color_noise"])
                if damage == "clip":
                    result[2:3] = 255
                else:
                    result[2:, :, :, 0] += 2
                    result[2:, :, :, 2] -= 6
                return result

            photo = SimpleNamespace(
                native_samples=self.sample_atlas(sparse, render),
                native_noise_samples=self.sample_atlas(noisy),
            )
            result = suggest_color_noise_for_photo(photo, {})
            self.assertIsNone(result.strength, (damage, result))
            self.assertEqual(result.status, "no-safe-benefit")
            self.assertEqual(result.metrics["dense_retry"], 1)

    def test_dense_retry_does_not_override_existing_evidence_or_force_filtering(self):
        clean, noisy = noisy_tiles()
        for sparse, render in ((clean, None), (noisy, None), (noisy, lambda _: noisy)):
            dense = SimpleNamespace(render_tiles=Mock(side_effect=AssertionError("Unneeded retry")))
            result = suggest_color_noise_for_photo(SimpleNamespace(
                native_samples=self.sample_atlas(sparse, render), native_noise_samples=dense,
            ), {})
            self.assertNotEqual(result.status, "insufficient-samples")
            dense.render_tiles.assert_not_called()
        clipped = np.full((256, 32, 32, 3), 255, np.uint8)
        result = suggest_color_noise_for_photo(SimpleNamespace(
            native_samples=self.sample_atlas(clipped[:16]), native_noise_samples=self.sample_atlas(clipped),
        ), {})
        self.assertEqual(result.status, "insufficient-samples")
        self.assertIsNone(result.strength)

    def test_dense_retry_rejects_malformed_sparse_validation_and_skips_tiny_tiles(self):
        _, noisy = noisy_tiles(12, count=256)
        sparse = noisy[:16].copy()
        sparse[2:] = 255
        for invalid in (sparse[:1], sparse.astype(float)):
            photo = SimpleNamespace(
                native_samples=self.sample_atlas(
                    sparse, lambda values: invalid if values["color_noise"] else sparse,
                ),
                native_noise_samples=self.sample_atlas(noisy),
            )
            with self.assertRaisesRegex(ValueError, "preserve native tile shape"):
                suggest_color_noise_for_photo(photo, {})
        dense = SimpleNamespace(render_tiles=Mock(side_effect=AssertionError("Tiny source")))
        result = suggest_color_noise_for_photo(SimpleNamespace(
            native_samples=self.sample_atlas(sparse[:, :8, :8]), native_noise_samples=dense,
        ), {})
        self.assertEqual(result.status, "insufficient-samples")
        dense.render_tiles.assert_not_called()

    def test_readonly_inputs_work_and_absent_native_data_is_not_proxy_analysis(self):
        _, tiles = noisy_tiles()
        tiles.flags.writeable = False
        self.assertEqual(advise(tiles).status, "suggested")
        result = suggest_color_noise_for_photo(object(), {"color_noise": 0.5})
        self.assertIsNone(result.strength)
        self.assertEqual(result.status, "native-samples-unavailable")

    def test_photo_advice_retains_tones_and_does_not_analyze_existing_denoise(self):
        from types import SimpleNamespace
        from unittest.mock import Mock

        _, tiles = noisy_tiles(6)
        edits = {"exposure": 0.3, "shadows": 0.4, "color_noise": 0.99, "luminance_noise": .5}

        def render(values):
            self.assertEqual(values["exposure"], 0.3)
            self.assertEqual(values["shadows"], 0.4)
            self.assertEqual(values["luminance_noise"], .5)
            return filtered_tiles(tiles, values["color_noise"])

        native = SimpleNamespace(grid_count=16, render_tiles=Mock(side_effect=render))
        result = suggest_color_noise_for_photo(
            SimpleNamespace(native_samples=native), edits
        )
        self.assertEqual(result.status, "suggested")
        self.assertEqual(
            native.render_tiles.call_args_list[0].args[0]["color_noise"], 0
        )
        self.assertLessEqual(native.render_tiles.call_count, 3)
        self.assertEqual(edits["color_noise"], 0.99)
