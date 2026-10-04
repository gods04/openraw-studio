import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np
import tifffile

from fixtures_nikon import synthetic_nikon_nef_compressed_bytes
from openraw_studio.core.subject import make_subject, subject_with_color, validate_subject_source
from openraw_studio.pipeline.interfaces import PipelineRequest
from openraw_studio.pipeline.local import LocalPhotoPipeline
from openraw_studio.raw.native.subject import apply_subject
from openraw_studio.raw.native.nikon import decode_nikon_34713_lossless, render_decoded_nikon_34713_image
from openraw_studio.raw.native.synthetic import write_synthetic_dng
from openraw_studio.ui.editing import SessionStore, clean_adjustments
from dataclasses import replace


class SubjectColorWorkflowTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        self.nef = self.folder / 'sample.NEF'
        self.nef.write_bytes(synthetic_nikon_nef_compressed_bytes(
            width=32, height=24, model='NIKON D500',
            samples=tuple(map(int, np.random.default_rng(17).integers(128, 13000, 32 * 24))),
        ))
        self.weights = np.array([[0, 0, .3, 0], [0, 1, .6, 0], [0, .7, 1, 0]], np.float32)

    def subject(self, source):
        return subject_with_color({**make_subject(self.weights, source), 'exposure': .6}, -.4, .3)

    def test_native_roi_color_matches_full_frame_in_every_orientation_and_depth(self):
        decoded = decode_nikon_34713_lossless(self.nef)
        edits = {'subject': self.subject(self.nef), 'exposure': -.2, 'color_noise': .4, 'luminance_noise': .3}
        for gpu in ('auto', 'off'):
            with patch.dict('os.environ', {'OPENRAW_GPU': gpu}):
                for orientation in range(1, 9):
                    photo = replace(decoded, orientation=orientation)
                    for bits, dtype in ((8, np.uint8), (16, np.dtype('<u2'))):
                        full = render_decoded_nikon_34713_image(photo, quality='full', bit_depth=bits, **edits)
                        pixels = np.frombuffer(full.rgb_bytes, dtype).reshape(full.height, full.width, 3)
                        for x, y, w, h in ((0, 0, 1, 1), (3, 5, 7, 9), (full.width - 2, full.height - 3, 2, 3)):
                            tile = render_decoded_nikon_34713_image(photo, quality='full', bit_depth=bits,
                                                                 region=(x, y, w, h), **edits)
                            np.testing.assert_array_equal(np.frombuffer(tile.rgb_bytes, dtype).reshape(h, w, 3),
                                                          pixels[y:y+h, x:x+w])

    def test_v2_session_recipe_and_export_need_no_models_and_remain_source_bound(self):
        dng = write_synthetic_dng(self.folder / 'sample.DNG', width=32, height=24)
        for source in (dng, self.nef):
            original = source.read_bytes()
            subject = self.subject(source)
            values = {'subject': subject, 'exposure': -.3}
            store = SessionStore(self.folder / 'sessions')
            store.save(source, values)
            self.assertEqual(store.load(source), clean_adjustments(values))
            with self.assertRaises(ValueError):
                validate_subject_source(subject, self.nef if source == dng else dng)
            pipeline = LocalPhotoPipeline()
            for bits in (8, 16):
                base = pipeline.process(PipelineRequest(source, self.folder / f'base-{source.suffix}-{bits}',
                    overrides={'exposure': -.3}, export_format='tiff', export_bit_depth=bits))
                edited = pipeline.process(PipelineRequest(source, self.folder / f'edit-{source.suffix}-{bits}',
                    overrides=values, export_format='tiff', export_bit_depth=bits))
                recipe = json.loads(next(edited.exports[0].path.parent.parent.glob('recipes/*.json')).read_text())
                self.assertEqual(recipe['adjustments']['raw']['subject'], subject)
                np.testing.assert_array_equal(tifffile.imread(edited.exports[0].path),
                                              apply_subject(tifffile.imread(base.exports[0].path), subject))
            self.assertEqual(source.read_bytes(), original)
