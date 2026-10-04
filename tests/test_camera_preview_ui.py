from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PIL import Image

from openraw_studio.ui import desktop
from openraw_studio.ui.live_preview import LiveFrame


class CameraPreviewUiTests(unittest.TestCase):
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
        with (
            patch("tkinter.Tk", return_value=root),
            patch.object(desktop, "LivePreviewWorker"),
            patch.object(desktop, "LocalPhotoPipeline"),
        ):
            cls.app = desktop.launch_desktop_app(
                run_mainloop=False, session_dir=Path(cls.temp.name) / "sessions",
            )
        cls.app.source_path = Path(cls.temp.name) / "sample.NEF"
        cls.app.source_path.write_bytes(b"fixture")

    def setUp(self):
        app = self.app
        app._clear_result()
        app.current_can_render = app.current_can_preview = True
        app.live_revision = 5
        app.zoom_var.set("Fit")

    def publish(self, frame):
        self.app.live_worker.take.return_value = frame
        with patch.object(self.app, "_fit_live_image"):
            self.app._poll_live_preview()

    def test_camera_is_not_labeled_or_recorded_as_an_edited_raw_frame(self):
        app = self.app
        camera = Image.new("RGB", (12, 8), "blue")
        self.publish(LiveFrame(4, app.source_path, {"exposure": 1}, camera, reference=True))
        self.assertEqual(app.view_var.get(), "Camera")
        self.assertIn("Preparing RAW", app.preview_state_var.get())
        self.assertIsNone(app.last_preview_overrides)
        self.assertTrue(app.compare_button.instate(["disabled"]))
        self.assertEqual(app.live_image.tobytes(), camera.tobytes())
        app._refresh_preview_state()
        self.assertIn("Preparing RAW", app.preview_state_var.get())
        raw = Image.new("RGB", (12, 8), "red")
        original = Image.new("RGB", (12, 8), "green")
        self.publish(LiveFrame(5, app.source_path, {"exposure": 1}, raw, original_image=original))
        self.assertEqual(app.view_var.get(), "Edited")
        self.assertEqual(app.before_view_name, "Original")
        self.assertEqual(app.last_preview_overrides, {"exposure": 1})
        self.assertFalse(app.compare_button.instate(["disabled"]))
        self.assertEqual(app.live_image.tobytes(), raw.tobytes())
        self.assertEqual(app.reference_image.tobytes(), original.tobytes())

    def test_wrong_source_and_future_camera_frames_cannot_replace_current_photo(self):
        app = self.app
        camera = Image.new("RGB", (12, 8), "blue")
        for frame in (
            LiveFrame(4, app.source_path.with_name("other.NEF"), {}, camera, reference=True),
            LiveFrame(6, app.source_path, {}, camera, reference=True),
        ):
            self.publish(frame)
            self.assertIsNone(app.live_image)
            self.assertIsNone(app.last_preview_overrides)
            self.assertEqual(app.view_var.get(), "Edited")
