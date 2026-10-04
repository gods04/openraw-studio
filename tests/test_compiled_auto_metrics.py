import os
import subprocess
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

import numpy as np

from openraw_studio.decision import compiled_metrics
from openraw_studio.decision.auto_adjust import _RenderGuard


def reference_counts(pixels, luma, usable, headroom, detail, margin):
    clipped = pixels >= 254 / 255
    crushed = float(np.mean(usable & (luma <= 2 / 255)))
    risk = crushed
    if margin:
        near_black = np.clip((4 / 255 - luma[usable]) * (255 / 2), 0, 1)
        risk = max(crushed, float(near_black.sum(dtype=np.float64) / len(luma)))
    return (
        float(np.mean(np.any(clipped, axis=1))), crushed, risk,
        float(np.mean(np.any(headroom & clipped, axis=1))),
        int(np.count_nonzero(detail & clipped)),
    )


class CompiledAutoMetricsTests(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(20261004)
        self.pixels = rng.random((4800, 3), dtype=np.float32)
        self.luma = self.pixels @ np.array([.2126, .7152, .0722], np.float32)
        self.usable = rng.random(4800) < .4
        self.headroom = rng.random((4800, 3)) < .8
        self.detail = (rng.random((4800, 3)) < .2) & self.headroom
        self.arrays = (self.pixels, self.luma, self.usable, self.headroom, self.detail)

    def require_compiler(self):
        if compiled_metrics.njit is None:
            self.skipTest("Numba unavailable")

    def assert_counts(self, arrays, margin):
        self.assertEqual(compiled_metrics.measure_counts(*arrays, margin), reference_counts(*arrays, margin))

    def test_exact_reference_randomized_across_domains_and_masks(self):
        self.require_compiler()
        rng = np.random.default_rng(271)
        for size in (1, 2, 17, 123, 43520, 122880, 614400):
            for kind in ('rgb8', 'float', 'dark'):
                pixels = rng.integers(0, 256, (size, 3)).astype(np.float32) / 255
                if kind != 'rgb8':
                    pixels = rng.random((size, 3), dtype=np.float32) * np.float32(.025 if kind == 'dark' else 1)
                luma = pixels @ np.array([.2126, .7152, .0722], np.float32)
                for masks in ('none', 'all', 'mixed'):
                    usable = rng.random(size) < .6
                    headroom = rng.random((size, 3)) < .7
                    detail = (rng.random((size, 3)) < .5) & headroom
                    if masks != 'mixed':
                        usable[:] = headroom[:] = detail[:] = masks == 'all'
                    for margin in (False, True):
                        with self.subTest(size=size, kind=kind, masks=masks, margin=margin):
                            self.assert_counts((pixels, luma, usable, headroom, detail), margin)
        self.assertIsNone(compiled_metrics.last_error)

    def test_float32_adjacent_thresholds_and_all_rgb8_gray_codes(self):
        self.require_compiler()
        levels = [np.float32(x / 255) for x in range(256)]
        for code in (2, 4, 254):
            value = np.float32(code / 255)
            levels.extend((np.nextafter(value, np.float32(0)), value, np.nextafter(value, np.float32(1))))
        luma = np.array(levels, np.float32)
        pixels = np.stack((luma, luma[::-1], np.roll(luma, 5)), axis=1)
        for channel in range(3):
            headroom = np.zeros_like(pixels, np.bool_)
            headroom[:, channel] = True
            for margin in (False, True):
                self.assert_counts((pixels, luma, np.ones(len(luma), np.bool_), headroom, headroom), margin)

    def test_strided_readonly_inputs_share_one_signature_and_remain_unchanged(self):
        self.require_compiler()
        saved = tuple(value.copy() for value in self.arrays)
        self.assert_counts(self.arrays, True)
        self.assertTrue(all(value.flags.writeable for value in self.arrays))
        for value in self.arrays:
            value.flags.writeable = False
        self.assert_counts(self.arrays, False)
        self.assert_counts(tuple(value[::-2] for value in self.arrays), True)
        for value, before in zip(self.arrays, saved):
            np.testing.assert_array_equal(value, before)
            self.assertFalse(value.flags.writeable)
        self.assertEqual(len(compiled_metrics.counts.signatures), 1)

    def test_contiguous_inputs_are_views_not_copies(self):
        expected = reference_counts(*self.arrays, True)
        def kernel(*arguments):
            for actual, original in zip(arguments[:-1], self.arrays):
                self.assertTrue(np.shares_memory(actual, original))
                self.assertFalse(actual.flags.writeable)
            return expected
        with patch.object(compiled_metrics, 'counts', side_effect=kernel):
            self.assert_counts(self.arrays, True)
        self.assertTrue(all(value.flags.writeable for value in self.arrays))

    def test_bad_shapes_and_dtypes_never_enter_unchecked_kernel(self):
        invalid = ((0, self.pixels[:, :2]), (0, self.pixels.reshape(60, 80, 3)),
                   (0, self.pixels.astype(np.float64)), (1, self.luma[:, None]),
                   (1, self.luma.astype(np.float64)), (2, self.usable[:2]),
                   (2, self.usable.astype(np.uint8)), (3, self.headroom[:, :2]),
                   (4, self.detail.astype(np.float32)))
        with patch.object(compiled_metrics, 'counts') as kernel:
            for index, value in invalid:
                arrays = list(self.arrays)
                arrays[index] = value
                with self.assertRaisesRegex(ValueError, 'matching bool masks'):
                    compiled_metrics.measure_counts(*arrays, True)
            with self.assertRaises(ValueError):
                compiled_metrics.measure_counts(*(value[:0] for value in self.arrays), False)
            kernel.assert_not_called()

    def test_compiler_failure_keeps_exact_numpy_fallback_without_repeated_retries(self):
        for error in (RuntimeError('compiler unavailable'), compiled_metrics.NumbaError('compile failed')):
            broken = Mock(side_effect=error)
            with patch.object(compiled_metrics, 'counts', broken), patch.object(compiled_metrics, 'last_error', None):
                self.assert_counts(self.arrays, True)
                self.assert_counts(self.arrays, False)
                broken.assert_called_once()
                self.assertIsNone(compiled_metrics.counts)
                self.assertIn(str(error), compiled_metrics.last_error)

    def test_unwritable_cache_retries_real_uncached_compilation(self):
        self.require_compiler()
        with (patch.object(compiled_metrics, 'counts', side_effect=OSError('cache denied')),
              patch.object(compiled_metrics, 'cache_disabled_reason', None),
              patch.object(compiled_metrics, 'last_error', None)):
            self.assert_counts(self.arrays, True)
            self.assertTrue(compiled_metrics.counts.signatures)
            self.assertIn('cache denied', compiled_metrics.cache_disabled_reason)
            self.assertIsNone(compiled_metrics.last_error)

    def test_failed_uncached_retry_falls_back(self):
        retry = Mock(side_effect=OSError('also unavailable'))
        with (patch.object(compiled_metrics, 'counts', side_effect=OSError('cache denied')),
              patch.object(compiled_metrics, 'njit', return_value=lambda _: retry),
              patch.object(compiled_metrics, 'last_error', None)):
            self.assert_counts(self.arrays, True)
            self.assertIsNone(compiled_metrics.counts)
            self.assertIn('also unavailable', compiled_metrics.last_error)
            retry.assert_called_once()

    def test_programming_errors_remain_visible(self):
        with patch.object(compiled_metrics, 'counts', side_effect=ValueError('bad input')):
            with self.assertRaisesRegex(ValueError, 'bad input'):
                compiled_metrics.measure_counts(*self.arrays, False)

    def test_disabled_jit_uses_vectorized_fallback(self):
        script = '''
import numpy as np
from unittest.mock import patch
from openraw_studio.decision import compiled_metrics as module
assert module.counts is None
assert 'NUMBA_DISABLE_JIT' in module.last_error
pixels = np.full((100, 3), .5, np.float32)
luma = np.full(100, 3 / 255, np.float32)
usable = np.ones(100, np.bool_)
headroom = np.ones((100, 3), np.bool_)
with patch.object(module, '_counts', side_effect=AssertionError('Python pixel loop')):
    actual = module.measure_counts(pixels, luma, usable, headroom, headroom, True)
    assert actual == module._numpy_counts(pixels, luma, usable, headroom, headroom, True)
'''
        result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True,
                                timeout=30, env={**os.environ, 'NUMBA_DISABLE_JIT': '1'})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_concurrent_calls_do_not_share_mutable_state(self):
        self.require_compiler()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(compiled_metrics.measure_counts, *self.arrays, margin) for margin in (True, False)]
            for future, margin in zip(futures, (True, False)):
                self.assertEqual(future.result(), reference_counts(*self.arrays, margin))

    def test_guard_cache_median_and_neutral_measurements_match_without_compiler(self):
        rng = np.random.default_rng(123)
        original = rng.integers(0, 256, (65, 83, 3), dtype=np.uint8)
        candidate = rng.integers(0, 256, original.shape, dtype=np.uint8)
        candidate[:4] = 3
        candidate.flags.writeable = False
        saved = candidate.copy()
        callback = Mock(return_value=candidate)
        optimized = _RenderGuard(original, callback, preserve_midtones=True, balance=True, shadow_margin=True)
        reference = _RenderGuard(original, lambda _: candidate, preserve_midtones=True, balance=True, shadow_margin=True)
        actual = optimized.measure({'exposure': .2})
        with patch.object(compiled_metrics, 'counts', None):
            expected = reference.measure({'exposure': .2})
        for name in ('clipping', 'median', 'crushed_shadows', 'shadow_risk', 'new_clipping',
                     'lost_highlight_channels', 'shadow_midtone_mean'):
            self.assertEqual(getattr(actual, name), getattr(expected, name))
        np.testing.assert_array_equal(actual.neutral_bias, expected.neutral_bias)
        self.assertIs(actual, optimized.measure({'exposure': .2}))
        callback.assert_called_once()
        np.testing.assert_array_equal(candidate, saved)


if __name__ == '__main__':
    unittest.main()
