import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

import numpy as np
from PIL import Image
from fixtures_nikon import synthetic_nikon_nef_compressed_bytes

from openraw_studio.cli import main
from openraw_studio.pipeline.interfaces import PipelineRequest
from openraw_studio.pipeline.local import LocalPhotoPipeline
from openraw_studio.raw.native.engine import _recipe_render_adjustments
from openraw_studio.raw.native.nikon import decode_nikon_34713_lossless, render_decoded_nikon_34713_image
from openraw_studio.raw.native.noise import reduce_noise
from openraw_studio.raw.native.synthetic import write_synthetic_dng
from openraw_studio.ui.desktop import _adjustments_match, _manual_overrides, _recipe_adjustment_overrides
from openraw_studio.ui.editing import EditHistory, SessionStore, clean_adjustments


class LuminanceWorkflowTests(unittest.TestCase):
    def test_history_sessions_old_recipes_clamps_and_preview_identity(self):
        self.assertEqual(clean_adjustments({})['luminance_noise'], 0)
        self.assertEqual(clean_adjustments({'luminance_noise':-1})['luminance_noise'], 0)
        self.assertEqual(clean_adjustments({'luminance_noise':2})['luminance_noise'], 1)
        self.assertEqual(_recipe_render_adjustments({}).luminance_noise, 0)
        self.assertFalse(_adjustments_match({'luminance_noise':.5}, {'luminance_noise':.6}))
        self.assertEqual(_manual_overrides(0, 0, 0, 0, 0, 0, 0, .4, .6)['luminance_noise'], .6)
        history = EditHistory({'color_noise':.4})
        history.commit({'color_noise':.4, 'luminance_noise':.65})
        self.assertEqual(history.undo()['luminance_noise'], 0)
        self.assertEqual(history.redo()['luminance_noise'], .65)
        self.assertEqual(history.current['color_noise'], .4)
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'photo.NEF'
            source.write_bytes(b'original')
            store = SessionStore(Path(folder) / 'sessions')
            store.save(source, history.current)
            self.assertEqual(store.load(source), history.current)

    def test_tiff_export_uses_both_filters_and_retains_recipe_and_source(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'noisy.NEF'
            payload = synthetic_nikon_nef_compressed_bytes(width=32, height=24, model='NIKON D500', samples=tuple(map(int,np.random.default_rng(21).integers(100,13000,32*24))))
            source.write_bytes(payload)
            decoded = decode_nikon_34713_lossless(source)
            unedited = render_decoded_nikon_34713_image(decoded, quality='full')
            baseline = np.frombuffer(unedited.rgb_bytes, np.uint8).reshape(unedited.height, unedited.width, 3)
            expected = reduce_noise(baseline, color_noise=.7, luminance_noise=.6)
            self.assertTrue(np.any(expected != baseline))
            result = LocalPhotoPipeline().process(PipelineRequest(source, root / 'out', overrides={'color_noise':.7, 'luminance_noise':.6}, export_format='tiff'))
            with Image.open(result.exports[0].path) as image:
                np.testing.assert_array_equal(np.asarray(image), expected)
            self.assertEqual(result.recipe['adjustments']['raw']['luminance_noise'], .6)
            self.assertEqual(_recipe_adjustment_overrides(result.recipe)['luminance_noise'], .6)
            self.assertEqual(source.read_bytes(), payload)

    def test_cli_process_and_batch_accept_both_controls_and_reject_invalid_luminance(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            sources = root / 'sources'
            sources.mkdir()
            source = write_synthetic_dng(sources / 'sample.DNG', width=16, height=16)
            original = source.read_bytes()
            for command, target in (('process', source), ('batch', sources)):
                args = [command, str(target), '--output', str(root / command)]
                with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                    self.assertEqual(main([*args, '--luminance-noise', '.6', '--color-noise', '.4']), 0)
                recipe = json.loads(next((root / command / 'recipes').glob('*.json')).read_text())
                self.assertEqual(recipe['adjustments']['raw']['luminance_noise'], .6)
                self.assertEqual(recipe['adjustments']['raw']['color_noise'], .4)
                for invalid in ('-1', '1.1', 'nan', 'inf'):
                    with redirect_stderr(StringIO()):
                        self.assertEqual(main([*args, '--luminance-noise', invalid]), 2)
            self.assertEqual(source.read_bytes(), original)

    def test_noise_stages_have_explicit_order_and_zero_is_exact_bypass(self):
        from openraw_studio.raw.native.chroma import reduce_color_noise
        from openraw_studio.raw.native.luminance import reduce_luminance_noise

        pixels = np.random.default_rng(82).integers(0, 256, (21, 31, 3), dtype=np.uint8)
        self.assertIs(reduce_noise(pixels), pixels)
        expected = reduce_color_noise(reduce_luminance_noise(pixels, .6), .7)
        np.testing.assert_array_equal(expected, reduce_noise(pixels, color_noise=.7, luminance_noise=.6))

    def test_fit_proxy_tracks_native_size_and_scales_only_luminance_amount(self):
        from unittest.mock import patch
        from openraw_studio.raw.native.interactive import InteractivePhoto

        photo = InteractivePhoto(np.full((100, 160, 3), .2, np.float32), np.eye(3, dtype=np.float32), (1, 1, 1))
        proxy = photo.resized(40)
        self.assertEqual(proxy.native_size, (160, 100))
        self.assertEqual(proxy.resized(20).native_size, (160, 100))
        with patch('openraw_studio.raw.native.noise.reduce_noise', side_effect=lambda pixels, **_: pixels) as noise:
            proxy.render({'color_noise':.7, 'luminance_noise':.6})
            self.assertEqual(noise.call_args.kwargs['color_noise'], .7)
            self.assertEqual(noise.call_args.kwargs['luminance_noise'], .15)

    def test_native_linear_detail_does_not_scale_by_region_size(self):
        from openraw_studio.raw.native.detail import LinearDetailPhoto
        from openraw_studio.raw.native.interactive import InteractivePhoto

        pixels = np.random.default_rng(15).uniform(.1, .3, (40, 60, 3)).astype(np.float32)
        photo = InteractivePhoto(pixels, np.eye(3, dtype=np.float32), (1, 1, 1), native_size=(60, 40))
        edits = {'color_noise':.7, 'luminance_noise':.6}
        expected = np.asarray(photo.render(edits)[0])
        detail = LinearDetailPhoto(photo)
        for x, y, w, h in ((5, 6, 13, 9), (0, 0, 10, 10), (59, 39, 1, 1)):
            np.testing.assert_array_equal(np.asarray(detail.render_region(edits, (x,y,w,h))), expected[y:y+h,x:x+w])
