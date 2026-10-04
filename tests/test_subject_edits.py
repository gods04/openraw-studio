import base64
import json
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import zlib

import numpy as np
import tifffile

from openraw_studio.core.recipe import new_recipe, validate_recipe_shape
from openraw_studio.core.domain import ImageAsset
from openraw_studio.core.subject import (
    clean_subject, decode_mask, global_adjustments, make_subject, validate_subject_source,
)
from openraw_studio.pipeline.errors import PipelineError
from openraw_studio.pipeline.interfaces import PipelineRequest
from openraw_studio.pipeline.local import LocalPhotoPipeline
from openraw_studio.raw.native.detail import NikonDetailPhoto, prepare_detail_photo
from openraw_studio.raw.native.engine import NativeRawProcessor
from openraw_studio.raw.native.nikon import decode_nikon_34713_lossless, render_decoded_nikon_34713_image
from openraw_studio.raw.native.preview import render_preview_image
from openraw_studio.raw.native.subject import apply_subject, subject_weights
from openraw_studio.raw.native.synthetic import write_synthetic_dng
from openraw_studio.raw.interfaces import RawRenderRequest
from openraw_studio.ui.desktop import _adjustments_match, _recipe_adjustment_overrides
from openraw_studio.ui.editing import EditHistory, SessionStore
from fixtures_nikon import synthetic_nikon_nef_compressed_bytes


class SubjectEditTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = write_synthetic_dng(self.root / "sample.DNG", width=32, height=24)
        self.weights = np.array([[0, 0, 0, 0], [0, .3, 1, 0], [0, .8, 1, 0]], np.float32)
        self.subject = {**make_subject(self.weights, self.source), "exposure": .6}

    def test_mask_is_bounded_lossless_and_readonly(self):
        subject = clean_subject(self.subject)
        pixels = decode_mask(subject["mask_zlib"], subject["width"], subject["height"])
        np.testing.assert_array_equal(pixels, np.rint(self.weights * 255).astype(np.uint8))
        self.assertFalse(pixels.flags.writeable)
        self.assertIs(pixels, decode_mask(subject["mask_zlib"], subject["width"], subject["height"]))
        for edits in ({"width": True}, {"width": []}, {"height": 961}, {"mask_zlib": []},
                      {"mask_zlib": "!"}, {"enabled": 1}, {"source_sha256": "bad"},
                      {"exposure": True}, {"exposure": "0.5"}, {"exposure": 1.01},
                      {"exposure": float("nan")}, {"exposure": 10 ** 1000},
                      {"version": "unknown"}, {"extra": 1}):
            with self.subTest(edits=edits), self.assertRaises(ValueError):
                clean_subject({**subject, **edits})

    def test_compression_size_and_trailing_data_are_rejected(self):
        for packed in (zlib.compress(b"\0" * 13), zlib.compress(b"\0" * 11),
                       zlib.compress(b"\0" * 12) + b"tail", zlib.compress(b"\0" * 1_000_000)):
            with self.assertRaises(ValueError):
                decode_mask(base64.b64encode(packed).decode("ascii"), 4, 3)

    def test_source_binding_and_global_batch_copy(self):
        validate_subject_source(self.subject, self.source)
        other = self.root / "other.DNG"
        other.write_bytes(b"different source")
        with self.assertRaisesRegex(ValueError, "different RAW"):
            validate_subject_source(self.subject, other)
        with self.assertRaises(PipelineError):
            LocalPhotoPipeline().process(PipelineRequest(
                source_path=other, output_dir=self.root / "rejected", overrides={"subject": self.subject},
            ))
        self.assertFalse((self.root / "rejected").exists())
        self.assertEqual(global_adjustments({"exposure": .2, "subject": self.subject}), {"exposure": .2})

    def test_identity_background_and_highlight_protection(self):
        for dtype in (np.uint8, np.uint16):
            maximum = np.iinfo(dtype).max
            pixels = np.random.default_rng(4).integers(0, maximum + 1, (177, 239, 3), dtype=dtype)
            pixels[0, 0] = maximum
            for subject in (None, {**self.subject, "enabled": False}, {**self.subject, "exposure": 0}):
                self.assertIs(apply_subject(pixels, subject), pixels)
            background = subject_weights(self.subject, (239, 177)) == 0
            for exposure in (-1, .6, 1):
                result = apply_subject(pixels, {**self.subject, "exposure": exposure})
                np.testing.assert_array_equal(result[background], pixels[background])
                self.assertTrue(np.all(result >= pixels) if exposure > 0 else np.all(result <= pixels))
                self.assertTrue(np.all(np.max(result, axis=-1)[np.max(pixels, axis=-1) < maximum] < maximum))

    def test_roi_and_strip_projection_is_identical_to_full_frame(self):
        pixels = np.random.default_rng(3).integers(0, 65536, (263, 377, 3), dtype=np.uint16)
        full = apply_subject(pixels, self.subject)
        for x, y, w, h in ((0, 0, 1, 1), (13, 117, 37, 143), (300, 199, 77, 64)):
            region = apply_subject(pixels[y:y+h, x:x+w], self.subject, full_size=(377, 263), region=(x, y, w, h))
            np.testing.assert_array_equal(region, full[y:y+h, x:x+w])

    def test_compiled_and_numpy_paths_match_with_contiguous_and_rotated_input(self):
        from openraw_studio.raw.native import compiled_subject
        for dtype in (np.uint8, np.uint16):
            for rotated in (False, True):
                pixels = np.random.default_rng(12).integers(0, np.iinfo(dtype).max + 1, (137, 183, 3), dtype=dtype)
                if rotated:
                    pixels = pixels.swapaxes(0, 1)[::-1]
                for exposure in (-1, -.1, .01, .6, 1):
                    subject = {**self.subject, "exposure": exposure}
                    actual = apply_subject(pixels, subject)
                    with patch.object(compiled_subject, "render", return_value=None):
                        expected = apply_subject(pixels, subject)
                    np.testing.assert_array_equal(actual, expected)

    def test_precision_and_channel_ratios(self):
        pixels = np.random.default_rng(8).integers(1, 255, (77, 93, 3), dtype=np.uint8)
        result8 = apply_subject(pixels, self.subject)
        result16 = apply_subject(pixels.astype(np.uint16) * 257, self.subject)
        self.assertLessEqual(np.abs(result8.astype(float) - result16 / 257).max(), .51)
        ratio = result16.astype(float) / (pixels.astype(float) * 257)
        self.assertLess(np.max(np.ptp(ratio, axis=-1)), .004)

    def test_kernel_failure_falls_back_without_changing_pixels(self):
        from openraw_studio.raw.native import compiled_subject
        pixels = np.full((9, 13, 3), 100, np.uint8)
        with patch.object(compiled_subject, "render", return_value=None):
            expected = apply_subject(pixels, self.subject)
        with (patch.object(compiled_subject, "expose", side_effect=RuntimeError("kernel unavailable")),
              patch.object(compiled_subject, "last_error", None)):
            actual = apply_subject(pixels, self.subject)
            self.assertIn("kernel unavailable", compiled_subject.last_error)
            np.testing.assert_array_equal(actual, expected)

    def test_cache_failure_retries_without_disk_cache(self):
        from openraw_studio.raw.native import compiled_subject
        pixels = np.full((9, 13, 3), 100, np.uint8)
        with patch.object(compiled_subject, "render", return_value=None):
            expected = apply_subject(pixels, self.subject)
        with (patch.object(compiled_subject, "expose", side_effect=OSError("cache read-only")),
              patch.object(compiled_subject, "cache_disabled_reason", None),
              patch.object(compiled_subject, "njit", return_value=lambda function: function)):
            actual = apply_subject(pixels, self.subject)
            self.assertIn("cache read-only", compiled_subject.cache_disabled_reason)
            np.testing.assert_array_equal(actual, expected)

    def test_history_session_and_recipe_roundtrip_without_models(self):
        values = {"exposure": .2, "subject": self.subject}
        history = EditHistory()
        history.commit(values)
        snapshot = history.current
        snapshot["subject"]["exposure"] = -.5
        self.subject["exposure"] = .1
        self.assertEqual(history.current["subject"]["exposure"], .6)
        self.assertNotIn("subject", history.undo())
        restored = history.redo()
        store = SessionStore(self.root / "sessions")
        store.save(self.source, restored)
        self.assertEqual(store.load(self.source), restored)
        recipe = new_recipe(self.source)
        recipe["adjustments"]["raw"] = restored
        validate_recipe_shape(recipe)
        self.assertEqual(_recipe_adjustment_overrides(recipe), restored)
        self.assertFalse(_adjustments_match(restored, snapshot))

    def test_nikon_native_roi_all_orientations_and_bit_depths(self):
        source = self.root / "sample.NEF"
        source.write_bytes(synthetic_nikon_nef_compressed_bytes(
            width=32, height=24, active_area=(2, 2, 26, 20), model="NIKON D500",
            samples=tuple(map(int, np.random.default_rng(17).integers(128, 13000, 32 * 24))),
            white_balance_rb_levels=(1.8, 1.4, 1, 1), maker_black_levels=(32, 64, 48, 96),
        ))
        subject = {**make_subject(self.weights, source), "exposure": .7}
        decoded = decode_nikon_34713_lossless(source)
        edits = {"subject": subject, "exposure": -.3, "shadows": .2,
                 "color_noise": .6, "luminance_noise": .5}
        for gpu in (True, False):
            with patch.dict("os.environ", {"OPENRAW_GPU": "auto" if gpu else "off"}):
                for orientation in range(1, 9):
                    photo = NikonDetailPhoto(replace(decoded, orientation=orientation))
                    for bit_depth in (8, 16):
                        full = render_decoded_nikon_34713_image(photo.decoded, quality="full", bit_depth=bit_depth, **edits)
                        dtype = np.uint8 if bit_depth == 8 else np.dtype("<u2")
                        pixels = np.frombuffer(full.rgb_bytes, dtype=dtype).reshape(full.height, full.width, 3)
                        for x, y, w, h in ((0, 0, 1, 1), (3, 5, 7, 9), (full.width - 2, full.height - 3, 2, 3)):
                            with self.subTest(gpu=gpu, orientation=orientation, bit_depth=bit_depth):
                                tile = render_decoded_nikon_34713_image(photo.decoded, quality="full", bit_depth=bit_depth,
                                                                     region=(x, y, w, h), **edits)
                                np.testing.assert_array_equal(np.frombuffer(tile.rgb_bytes, dtype=dtype).reshape(h, w, 3),
                                                              pixels[y:y+h, x:x+w])
                        resized = render_decoded_nikon_34713_image(photo.decoded, quality="full", bit_depth=bit_depth,
                                                                 max_dimension=12, **edits)
                        self.assertEqual(max(resized.width, resized.height), 12)

    def test_dng_detail_and_disabled_export(self):
        edits = {"subject": self.subject, "exposure": -.2, "color_noise": .4, "luminance_noise": .4}
        photo = prepare_detail_photo(NativeRawProcessor(), self.source)
        full = render_preview_image(self.source, **edits)
        pixels = np.asarray(full.pixels).reshape(full.height, full.width, 3)
        detail = np.asarray(photo.render_region(edits, (3, 5, 7, 8)), dtype=int)
        self.assertLessEqual(np.abs(detail - pixels[5:13, 3:10]).max(), 2)
        base = render_preview_image(self.source, bit_depth=16)
        disabled = render_preview_image(self.source, bit_depth=16, subject={**self.subject, "enabled": False})
        self.assertEqual(base, disabled)

    def test_pipeline_saves_and_exports_portable_layer_at_both_bit_depths(self):
        nef = self.root / "sample.NEF"
        nef.write_bytes(synthetic_nikon_nef_compressed_bytes(
            width=32, height=24, model="NIKON D500",
            samples=tuple(map(int, np.random.default_rng(31).integers(128, 13000, 32 * 24))),
        ))
        for source in (self.source, nef):
            original = source.read_bytes()
            subject = {**make_subject(self.weights, source), "exposure": -.5}
            pipeline = LocalPhotoPipeline()
            for bits in (8, 16):
                folder = self.root / f"export-{source.suffix}-{bits}"
                result = pipeline.process(PipelineRequest(
                    source, folder, overrides={"exposure": -.3, "subject": subject},
                    export_format="tiff", export_bit_depth=bits,
                ))
                recipe = json.loads(next((folder / "recipes").glob("*.json")).read_text())
                self.assertEqual(recipe["adjustments"]["raw"]["subject"], subject)
                recipe["adjustments"]["raw"].pop("subject")
                reference = folder / "without-subject.tif"
                pipeline.raw_processor.render_base(RawRenderRequest(ImageAsset(source), recipe, reference, bit_depth=bits))
                expected = apply_subject(tifffile.imread(reference), subject)
                np.testing.assert_array_equal(tifffile.imread(result.exports[0].path), expected)
            self.assertEqual(source.read_bytes(), original)
