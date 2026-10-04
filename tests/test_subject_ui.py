from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from openraw_studio.core.subject import make_subject, subject_with_color
from openraw_studio.decision.auto_adjust import AutoAdjustSuggestion
from openraw_studio.decision.subject_exposure import SubjectExposureSuggestion
from openraw_studio.decision.subject_color import SubjectColorSuggestion
from openraw_studio.ui import desktop
from openraw_studio.ui.mask_overlay import subject_overlay


class SubjectUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tkinter as tk
        try:
            root = tk.Tk()
        except tk.TclError as error:
            raise unittest.SkipTest(f"Tk display unavailable: {error}") from error
        root.withdraw()
        cls.addClassCleanup(root.destroy)
        cls.temp = TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        with (patch("tkinter.Tk", return_value=root), patch.object(desktop, "LivePreviewWorker"),
              patch.object(desktop, "LocalPhotoPipeline")):
            cls.app = desktop.launch_desktop_app(run_mainloop=False, session_dir=Path(cls.temp.name) / "sessions")
        cls.source = Path(cls.temp.name) / "sample.NEF"
        cls.source.write_bytes(b"original")
        cls.subject = make_subject(np.array([[0, 0, 0], [.2, 1, 0]], np.float32), cls.source)

    def setUp(self):
        app = self.app
        app.source_path = self.source
        app.current_can_render = app.current_can_preview = True
        app.last_auto_suggestion = app.last_person_analysis = None
        app.is_busy = False
        app._clear_result()
        app._set_adjustment_values({})
        app.history.reset({})
        app._set_busy(False)

    def test_selection_is_zero_effect_and_undoable(self):
        app = self.app
        app._apply_subject(self.subject, None, run_id=app.run_counter)
        self.assertEqual(app._current_overrides()["subject"]["exposure"], 0)
        self.assertFalse(app.subject_exposure_scale.instate(["disabled"]))
        self.assertTrue(app.person_mask_var.get())
        app._undo()
        self.assertNotIn("subject", app._current_overrides())
        self.assertTrue(app.subject_exposure_scale.instate(["disabled"]))
        app._redo()
        self.assertEqual(app._current_overrides()["subject"], self.subject)

    def test_auto_strength_preserves_subject_and_reset_is_reversible(self):
        app = self.app
        app._set_adjustment_values({'subject': subject_with_color(self.subject, .3, -.2)})
        app.subject_exposure_var.set(.7)
        app._subject_changed()
        app._commit_edit()
        app.last_auto_suggestion = SimpleNamespace(as_overrides=lambda: {"exposure": .5})
        app.auto_strength_var.set(0)
        app._change_auto_strength()
        self.assertEqual(app._current_overrides()["subject"]["exposure"], .7)
        expected = app._current_overrides()['subject']
        self.assertEqual((expected['warmth'], expected['tint']), (.3, -.2))
        app._reset_adjustments()
        self.assertNotIn("subject", app._current_overrides())
        app._undo()
        self.assertEqual(app._current_overrides()['subject'], expected)

    def test_disabled_and_stale_or_missing_selection_do_not_lose_edits(self):
        app = self.app
        app._apply_subject(self.subject, None, run_id=app.run_counter)
        app.subject_enabled_button.invoke()
        self.assertFalse(app._current_overrides()["subject"]["enabled"])
        self.assertTrue(app.subject_exposure_scale.instate(["disabled"]))
        before = app._current_overrides()
        app._apply_subject(self.subject, None, run_id=app.run_counter - 1)
        app._apply_subject(None, None, run_id=app.run_counter)
        self.assertEqual(before, app._current_overrides())

    def test_saved_selection_inspection_needs_no_model(self):
        app = self.app
        app._set_adjustment_values({"subject": self.subject})
        self.assertIsNone(app.last_person_analysis)
        self.assertFalse(app.person_mask_button.instate(["disabled"]))
        image = Image.new("RGB", (9, 6), (70, 100, 30))
        result = subject_overlay(image, self.subject)
        self.assertNotEqual(result.tobytes(), image.tobytes())
        self.assertEqual(result.getpixel((8, 0)), image.getpixel((8, 0)))

    def test_first_auto_uses_metered_subject_but_preserves_existing_manual_layer(self):
        app = self.app
        suggestion = AutoAdjustSuggestion(.4, 0, 0, 0, 0, 0, 0, ())
        app._apply_auto_adjustment(suggestion, subject={**self.subject, 'exposure': .6}, run_id=app.run_counter)
        self.assertEqual(app._current_overrides()['subject']['exposure'], .6)
        app.subject_exposure_var.set(-.3)
        app._subject_changed()
        app._commit_edit()
        app._apply_auto_adjustment(suggestion, subject={**self.subject, 'exposure': .8}, run_id=app.run_counter)
        self.assertEqual(app._current_overrides()['subject']['exposure'], -.3)

    def test_subject_auto_is_undoable_and_preserves_global_controls(self):
        app = self.app
        app._set_adjustment_values({'subject': subject_with_color(self.subject, .3, -.2)})
        app.exposure_var.set(.4)
        app.warmth_var.set(-.2)
        app._commit_edit()
        before = app._current_overrides()
        app._apply_subject_auto(SubjectExposureSuggestion(.6, 'suggested'), run_id=app.run_counter)
        after = app._current_overrides()
        self.assertEqual(after['exposure'], .4)
        self.assertEqual(after['warmth'], -.2)
        self.assertEqual(after['subject']['exposure'], .6)
        self.assertEqual((after['subject']['warmth'], after['subject']['tint']), (.3, -.2))
        app._undo()
        self.assertEqual(app._current_overrides(), before)
        app._redo()
        self.assertEqual(app._current_overrides(), after)

    def test_abstention_and_stale_auto_leave_edits_unchanged(self):
        app = self.app
        app._apply_subject(self.subject, None, run_id=app.run_counter)
        app.subject_exposure_var.set(-.2)
        app._commit_edit()
        before = app._current_overrides()
        app._apply_subject_auto(SubjectExposureSuggestion(.7, 'suggested'), run_id=app.run_counter - 1)
        for status in ('balanced', 'face-not-installed', 'face-unavailable', 'conflicting-faces', 'no-safe-benefit'):
            app._apply_subject_auto(SubjectExposureSuggestion(None, status), run_id=app.run_counter)
            self.assertEqual(app._current_overrides(), before)
            self.assertIn('unchanged', app.status_var.get())

    def test_subject_auto_requires_selection_and_is_disabled_during_work(self):
        app = self.app
        self.assertTrue(app.subject_auto_button.instate(['disabled']))
        self.assertTrue(app.subject_color_auto_button.instate(['disabled']))
        app._apply_subject(self.subject, None, run_id=app.run_counter)
        self.assertFalse(app.subject_auto_button.instate(['disabled']))
        self.assertFalse(app.subject_color_auto_button.instate(['disabled']))
        app._set_busy(True)
        self.assertTrue(app.subject_auto_button.instate(['disabled']))
        self.assertTrue(app.subject_color_auto_button.instate(['disabled']))
        app._set_busy(False)

    def test_color_auto_preserves_exposure_and_globals_with_reversible_history(self):
        app = self.app
        app._set_adjustment_values({'exposure': .4, 'warmth': -.2,
                                    'subject': {**self.subject, 'exposure': .6}})
        app._commit_edit()
        before = app._current_overrides()
        app._apply_subject_color_auto(SubjectColorSuggestion(.15, -.3, 'suggested'), run_id=app.run_counter)
        after = app._current_overrides()
        self.assertEqual(after, {**before, 'subject': subject_with_color(before['subject'], .15, -.3)})
        app._undo()
        self.assertEqual(app._current_overrides(), before)
        app._redo()
        self.assertEqual(app._current_overrides(), after)
        app.subject_enabled_button.invoke()
        for scale in (app.subject_exposure_scale, app.subject_warmth_scale, app.subject_tint_scale):
            self.assertTrue(scale.instate(['disabled']))

    def test_color_abstention_and_stale_result_preserve_manual_color(self):
        app = self.app
        app._set_adjustment_values({'subject': subject_with_color(self.subject, -.7, .5)})
        app._commit_edit()
        before = app._current_overrides()
        app._apply_subject_color_auto(SubjectColorSuggestion(.3, -.3, 'suggested'), run_id=app.run_counter - 1)
        for status in ('balanced', 'ambient-light', 'material-uncertain', 'material-not-installed', 'no-safe-benefit'):
            app._apply_subject_color_auto(SubjectColorSuggestion(None, None, status), run_id=app.run_counter)
            self.assertEqual(app._current_overrides(), before)
            self.assertIn('unchanged', app.status_var.get())

    def test_first_auto_accepts_color_but_never_overwrites_an_existing_layer(self):
        app = self.app
        suggestion = AutoAdjustSuggestion(.4, 0, 0, 0, 0, 0, 0, ())
        layer = subject_with_color({**self.subject, 'exposure': .6}, .1, -.2)
        app._apply_auto_adjustment(suggestion, subject=layer, run_id=app.run_counter)
        self.assertEqual(app._current_overrides()['subject'], layer)
        app.subject_enabled_var.set(False)
        app.subject_warmth_var.set(.4)
        app._subject_changed()
        before = app._current_overrides()['subject']
        app._apply_auto_adjustment(suggestion, subject=layer, run_id=app.run_counter)
        self.assertEqual(app._current_overrides()['subject'], before)

    def test_auto_batch_uses_each_photos_own_selection_and_keeps_noise_controls(self):
        app = self.app
        other = Path(self.temp.name) / 'second.NEF'
        other.write_bytes(b'second original')
        subjects = {self.source: self.subject, other: make_subject(np.ones((2, 2)), other)}
        recorded = []
        photo = SimpleNamespace(render=lambda _: (Image.new('RGB', (100, 100)), None))
        suggestion = AutoAdjustSuggestion(.4, 0, 0, 0, 0, 0, 0, ())
        def export(*_args, **kwargs):
            for source in (self.source, other):
                recorded.append(kwargs['adjustments_for_source'](source))
            return None
        def select(_person, source):
            return dict(subjects[source])
        with patch.object(desktop, 'prepare_interactive_photo', return_value=photo), \
                patch('openraw_studio.vision.person.analyze_person', return_value=None), \
                patch.object(desktop, 'suggest_auto_adjustments_for_photo', return_value=suggestion), \
                patch.object(desktop, 'subject_for_person', side_effect=select), \
                patch.object(desktop, 'suggest_subject_exposure_for_photo', return_value=SubjectExposureSuggestion(.5, 'suggested')), \
                patch.object(desktop, 'suggest_subject_color_for_photo', side_effect=[
                    SubjectColorSuggestion(.2, -.1, 'suggested'), SubjectColorSuggestion(None, None, 'material-uncertain')]), \
                patch.object(desktop, 'run_batch_export', side_effect=export), patch.object(app, '_post'):
            app._batch_export_worker(app.run_counter, (self.source, other), Path(self.temp.name),
                                     {'subject': self.subject, 'color_noise': .3, 'luminance_noise': .2},
                                     'jpeg', 92, 'Auto each photo', .7)
        self.assertEqual(len(recorded), 2)
        self.assertEqual((recorded[0]['subject']['warmth'], recorded[0]['subject']['tint']), (.2, -.1))
        self.assertNotIn('warmth', recorded[1]['subject'])
        for edits, source in zip(recorded, (self.source, other)):
            self.assertEqual(edits['subject']['source_sha256'], subjects[source]['source_sha256'])
            self.assertEqual(edits['subject']['exposure'], .5)
            self.assertAlmostEqual(edits['exposure'], .28)
            self.assertEqual(edits['color_noise'], .3)
            self.assertEqual(edits['luminance_noise'], .2)
