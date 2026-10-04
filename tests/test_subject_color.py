from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np

from openraw_studio.core.subject import clean_subject, make_subject, subject_with_color
from openraw_studio.raw.native.subject import apply_subject, subject_weights
from openraw_studio.raw.native import compiled_subject_color


class SubjectColorTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.source = Path(temp.name) / 'original.NEF'
        self.source.write_bytes(b'RAW original')
        self.subject = make_subject(np.array([[0, 0, 0], [0, .5, 1], [0, .8, .9]], np.float32), self.source)

    def test_v1_shape_is_retained_and_v2_is_strictly_validated(self):
        self.assertEqual(subject_with_color(self.subject, 0, 0), self.subject)
        colored = subject_with_color(self.subject, .3, -.4)
        self.assertEqual(colored['version'], 'subject.v2')
        self.assertEqual(clean_subject(colored), colored)
        for key in ('warmth', 'tint'):
            for value in (True, '0.3', [], 1.1, float('nan'), float('inf'), 10 ** 1000):
                with self.assertRaises(ValueError):
                    clean_subject({**colored, key: value})
            incomplete = colored.copy()
            incomplete.pop(key)
            with self.assertRaises(ValueError):
                clean_subject(incomplete)
        with self.assertRaises(ValueError):
            clean_subject({**self.subject, 'warmth': .3})
        with self.assertRaises(ValueError):
            clean_subject({**colored, 'unknown': 0})

    def test_zero_color_and_disabled_layer_preserve_old_pixels_exactly(self):
        pixels = np.random.default_rng(13).integers(0, 256, (80, 120, 3), dtype=np.uint8)
        for exposure in (-1, 0, 1):
            subject = {**self.subject, 'exposure': exposure}
            colored = {**subject, 'version': 'subject.v2', 'warmth': 0, 'tint': 0}
            np.testing.assert_array_equal(apply_subject(pixels, colored), apply_subject(pixels, subject))
        self.assertIs(apply_subject(pixels, {**subject_with_color(self.subject, .5, -.4), 'enabled': False}), pixels)

    def test_fast_and_fallback_match_for_both_depths_and_rotated_inputs(self):
        for dtype in (np.uint8, np.uint16):
            for rotated in (False, True):
                pixels = np.random.default_rng(14).integers(0, np.iinfo(dtype).max + 1, (131, 179, 3), dtype=dtype)
                if rotated:
                    pixels = pixels.swapaxes(0, 1)[::-1]
                for warmth, tint in ((0, -.5), (1, 1), (-1, -1), (.4, -.8), (0.01, 0)):
                    subject = subject_with_color(self.subject, warmth, tint)
                    actual = apply_subject(pixels, subject)
                    with patch.object(compiled_subject_color, 'render', return_value=None):
                        expected = apply_subject(pixels, subject)
                    np.testing.assert_array_equal(actual, expected)

    def test_background_luma_white_and_black_are_preserved(self):
        for dtype in (np.uint8, np.uint16):
            maximum = np.iinfo(dtype).max
            pixels = np.random.default_rng(15).integers(0, maximum + 1, (75, 117, 3), dtype=dtype)
            pixels[45, 70] = 0
            pixels[46, 70] = maximum
            background = subject_weights(self.subject, (117, 75)) == 0
            before = pixels.astype(float) @ np.array([.2126, .7152, .0722])
            for warmth, tint in ((1, -.7), (-1, 1)):
                result = apply_subject(pixels, subject_with_color(self.subject, warmth, tint))
                np.testing.assert_array_equal(result[background], pixels[background])
                np.testing.assert_array_equal(result[45:47, 70], pixels[45:47, 70])
                after = result.astype(float) @ np.array([.2126, .7152, .0722])
                self.assertLess(np.abs(before - after).max(), .51)
                self.assertFalse(np.any((result == maximum) & (pixels < maximum)))

    def test_color_direction_and_8_16_bit_agreement(self):
        subject = subject_with_color(make_subject(np.ones((2, 2)), self.source), .6, -.4)
        pixels = np.random.default_rng(9).integers(5, 256, (79, 121, 3), dtype=np.uint8)
        a = apply_subject(pixels, subject)
        b = apply_subject(pixels.astype(np.uint16) * 257, subject)
        self.assertLessEqual(np.abs(a.astype(float) - b / 257).max(), .51)
        gray = np.full((1, 1, 3), 100, np.uint8)
        self.assertGreater(apply_subject(gray, subject)[0, 0, 0], apply_subject(gray, subject)[0, 0, 2])

    def test_full_frame_and_region_use_identical_mask_coordinates(self):
        pixels = np.random.default_rng(11).integers(0, 65536, (199, 277, 3), dtype=np.uint16)
        subject = {**subject_with_color(self.subject, .6, -.3), 'exposure': .4}
        full = apply_subject(pixels, subject)
        for x, y, w, h in ((0, 0, 3, 3), (80, 88, 75, 110), (200, 180, 77, 19)):
            actual = apply_subject(pixels[y:y+h, x:x+w], subject, full_size=(277, 199), region=(x, y, w, h))
            np.testing.assert_array_equal(actual, full[y:y+h, x:x+w])

    def test_optional_kernel_failure_and_cache_failure_preserve_rendering(self):
        pixels = np.full((15, 21, 3), 120, np.uint8)
        subject = subject_with_color(self.subject, -.7, .4)
        with patch.object(compiled_subject_color, 'render', return_value=None):
            expected = apply_subject(pixels, subject)
        with patch.object(compiled_subject_color, 'balance', side_effect=RuntimeError('failed')):
            np.testing.assert_array_equal(apply_subject(pixels, subject), expected)
            self.assertIn('failed', compiled_subject_color.last_error)
        with patch.object(compiled_subject_color, 'balance', side_effect=OSError('readonly')), \
                patch.object(compiled_subject_color, 'njit', return_value=lambda fn: fn):
            np.testing.assert_array_equal(apply_subject(pixels, subject), expected)
            self.assertIn('readonly', compiled_subject_color.cache_disabled_reason)
