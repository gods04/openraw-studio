import unittest
from unittest.mock import patch

import numpy as np

from openraw_studio.decision.auto_adjust import suggest_auto_adjustments_for_photo, suggest_auto_adjustments_from_preview
from openraw_studio.decision.white_balance import NeutralCast, refine_white_balance
from openraw_studio.raw.native.interactive import InteractivePhoto
from openraw_studio.raw.native.tone import PreviewRgbImage


class AutoWhiteBalanceTests(unittest.TestCase):
    @staticmethod
    def scene(matrix=None):
        if matrix is None:
            matrix = np.eye(3, dtype=np.float32)
        ramp = np.linspace(.045, .24, 80, dtype=np.float32)
        linear = np.broadcast_to(ramp[:, None, None], (80, 120, 3)).copy()
        sensor = (linear @ np.linalg.inv(matrix).T) * np.array([1.08, .95, 1.02], np.float32)
        return InteractivePhoto(sensor, matrix, (1, 1, 1))

    @staticmethod
    def baseline(photo):
        with patch('openraw_studio.decision.auto_adjust.refine_white_balance', side_effect=lambda _e, _m, values, *_a, **_k: (values, {'white_balance_refined':0.0})):
            return suggest_auto_adjustments_for_photo(photo)

    def test_actual_renderer_response_reduces_consistent_cast_and_preserves_tones(self):
        for matrix in (np.eye(3, dtype=np.float32), np.array([[1.6,-.5,-.1],[-.2,1.4,-.2],[0,-.4,1.4]], np.float32)):
            photo = self.scene(matrix)
            original = photo.pixels.copy()
            before = self.baseline(photo)
            result = suggest_auto_adjustments_for_photo(photo)
            self.assertEqual(result.metrics['white_balance_refined'], 1)
            self.assertLess(result.metrics['white_balance_error_after'], result.metrics['white_balance_error_before'] * .9)
            self.assertLessEqual(abs(result.warmth), .35)
            self.assertLessEqual(abs(result.tint), .25)
            for key in ('exposure','contrast','highlights','shadows','saturation'):
                self.assertEqual(result.as_overrides()[key], before.as_overrides()[key])
            self.assertIn('measured renderer response', ' '.join(result.rationale))
            np.testing.assert_array_equal(photo.pixels, original)

    def test_spatial_confidence_rejects_mixed_light_small_patches_and_dominated_color(self):
        for kind in ('mixed', 'small', 'blue', 'warm', 'gray', 'dim-blue', 'dim-warm', 'dark'):
            pixels = np.full((80, 120, 3), (130,120,124), np.uint8)
            if kind == 'mixed':
                pixels[:, :60] = (113,120,119)
            elif kind == 'small':
                pixels[:] = (30,70,160)
                pixels[20:40,20:40] = (130,120,124)
            elif kind == 'blue':
                pixels[:] = (30,70,160)
                pixels[::4] = (120,130,150)
            elif kind == 'warm':
                pixels[:] = (170,110,70)
                pixels[::4] = (150,130,120)
            elif kind == 'dim-blue':
                pixels[:] = (61,65,70)
            elif kind == 'dim-warm':
                pixels[:] = (74,66,65)
            elif kind == 'dark':
                pixels[:] = (39,43,40)
            else:
                pixels[:] = 120
            with self.subTest(kind=kind):
                evidence = NeutralCast(pixels)
                self.assertIsNone(evidence.mask)

    def test_spatial_support_and_sample_memory_are_bounded(self):
        pixels = np.full((960, 1440, 3), (130,120,124), np.uint8)
        evidence = NeutralCast(pixels)
        self.assertIsNotNone(evidence.mask)
        self.assertEqual(evidence.metrics['neutral_tiles'], 16)
        self.assertLessEqual(len(evidence.indices), 4096)
        flat = PreviewRgbImage(120,80,((130,120,124),)*9600,'gamma-2.2')
        self.assertIsNotNone(NeutralCast(flat).mask)
        self.assertIsNone(NeutralCast(np.full((8,8,3),120,np.uint8)).mask)
        self.assertIsNone(NeutralCast(np.full((64,3),120,np.uint8)).mask)

    def test_uncertain_small_proxy_does_not_analyze_display_colors_again(self):
        photo = InteractivePhoto(np.full((300,400,3),(.01,.06,.2),np.float32),np.eye(3,dtype=np.float32),(1,1,1))
        with patch('openraw_studio.decision.auto_adjust.NeutralCast',wraps=NeutralCast) as evidence:
            result = suggest_auto_adjustments_for_photo(photo)
        self.assertEqual(result.metrics['white_balance_refined'],0)
        self.assertEqual(evidence.call_count,1)

    def test_ill_conditioned_or_unresponsive_renderer_does_not_change_settings(self):
        evidence = NeutralCast(np.full((80,120,3),(130,120,124),np.uint8))
        edits = {'warmth':.03,'tint':.02,'exposure':.1}
        for measure in (lambda _: np.array([.08,.03]), lambda v: np.array([.08+v['warmth']*.1,.03+v['tint']*.0001])):
            result, metrics = refine_white_balance(evidence,measure,edits,lambda _: True)
            self.assertIs(result,edits)
            self.assertEqual(metrics['white_balance_refined'],0)

    def test_detail_resolution_requires_its_own_agreeing_cast_and_measurable_benefit(self):
        evidence = NeutralCast(np.full((80,120,3),(130,120,124),np.uint8))
        uncertain = NeutralCast(np.full((80,120,3),120,np.uint8))
        edits = {'warmth':0,'tint':0}
        measure = lambda v: np.array([.08+v['warmth']*.2,.03+v['tint']*.2])
        for other, fine in ((uncertain,measure),(evidence,lambda _:np.array([-.08,-.03])),(evidence,lambda _:np.array([.08,.03]))):
            result,metrics = refine_white_balance(evidence,measure,edits,lambda _:True,detail_evidence=other,measure_detail=fine)
            self.assertIs(result,edits)
            self.assertEqual(metrics['white_balance_refined'],0)

    def test_validation_rejects_both_candidates_and_backoff_can_preserve_a_smaller_improvement(self):
        evidence = NeutralCast(np.full((80,120,3),(130,120,124),np.uint8))
        edits = {'warmth':0,'tint':0}
        measure = lambda v: np.array([.08+v['warmth']*.2,.03+v['tint']*.2])
        rejected, metrics = refine_white_balance(evidence,measure,edits,lambda _:False)
        self.assertIs(rejected,edits)
        candidate, metrics = refine_white_balance(evidence,measure,edits,lambda v:abs(v['warmth'])<=.15)
        self.assertEqual(metrics['white_balance_refined'],1)
        self.assertEqual(candidate['warmth'],-.1)

    def test_low_key_scene_keeps_its_original_cast(self):
        photo = self.scene()
        photo.pixels[:60] = .001
        photo.pixels[60:] = (.35,.30,.33)
        before = self.baseline(photo)
        result = suggest_auto_adjustments_for_photo(photo)
        self.assertEqual(result.scene,'Low-key')
        self.assertEqual(result.as_overrides(),before.as_overrides())
        self.assertEqual(result.metrics['white_balance_refined'],0)

    def test_color_benefit_is_rechecked_at_intermediate_strengths(self):
        evidence = NeutralCast(np.full((80,120,3),(130,120,124),np.uint8))
        edits = {'warmth':0,'tint':0,'exposure':1}
        def measure(values):
            direction = 1 if values['exposure'] == 1 else -1
            return np.array([.08+direction*values['warmth']*.2,.03+direction*values['tint']*.2])
        _, endpoint = refine_white_balance(evidence,measure,edits,lambda _:True)
        self.assertEqual(endpoint['white_balance_refined'],1)
        for primary, fine in ((measure,None),(lambda v:np.array([.08+v['warmth']*.2,.03+v['tint']*.2]),measure)):
            result,metrics = refine_white_balance(evidence,primary,edits,lambda _:True,detail_evidence=evidence,measure_detail=fine,validation_strengths=(.25,.5,.7))
            self.assertIs(result,edits)
            self.assertEqual(metrics['white_balance_refined'],0)

    def test_new_balance_rechecks_native_highlights_and_intermediate_strengths(self):
        photo = self.scene()
        preview = photo.render({})[0]
        baseline = self.baseline(photo)
        self.assertEqual(suggest_auto_adjustments_for_photo(photo).metrics['white_balance_refined'],1)
        for mode in ('native','intermediate'):
            def checked(values):
                rendered = np.asarray(photo.render(values)[0]).copy()
                if abs(values['warmth']) > abs(baseline.warmth)+.02 and (mode=='native' or abs(values['warmth'])<.17):
                    rendered[:10] = 255
                return rendered
            result = suggest_auto_adjustments_from_preview(
                preview,render=checked if mode=='intermediate' else lambda v:photo.render(v)[0],
                native_preview=preview,render_native=checked,validation_strengths=(.25,.5,.7),
            )
            self.assertEqual(result.metrics['white_balance_refined'],0)
            self.assertEqual(result.as_overrides(),baseline.as_overrides())
