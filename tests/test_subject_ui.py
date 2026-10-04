from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from openraw_studio.core.subject import make_subject
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
        app._apply_subject(self.subject, None, run_id=app.run_counter)
        app.subject_exposure_var.set(.7)
        app._subject_changed()
        app._commit_edit()
        app.last_auto_suggestion = SimpleNamespace(as_overrides=lambda: {"exposure": .5})
        app.auto_strength_var.set(0)
        app._change_auto_strength()
        self.assertEqual(app._current_overrides()["subject"]["exposure"], .7)
        app._reset_adjustments()
        self.assertNotIn("subject", app._current_overrides())
        app._undo()
        self.assertEqual(app._current_overrides()["subject"]["exposure"], .7)

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
