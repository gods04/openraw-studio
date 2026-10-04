from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from PIL import Image

from openraw_studio.ui import desktop
from openraw_studio.ui.live_preview import LiveFrame
from openraw_studio.pipeline.batch import BatchItemResult, BatchResult


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
        app.preview_failure = None
        app.support_notice_label.pack_forget()
        app.retry_preview_button.grid_remove()
        app.is_busy = app.batch_running = False
        app._set_adjustment_values({})
        app.history.reset(app._current_overrides())
        app._set_busy(False)
        app.live_worker.reset_mock()
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

    def fail_preview(self, *, camera=True):
        image = Image.new('RGB', (12, 8), 'blue') if camera else None
        self.publish(LiveFrame(5, self.app.source_path, {}, image, error='Unreadable RAW',
                               reference=camera, preparation_failed=True))

    def test_preparation_failure_preserves_edits_but_disables_raw_actions(self):
        app = self.app
        app.exposure_var.set(.4)
        app._commit_edit()
        edits = app._current_overrides()
        saved = app.session_store.load(app.source_path)
        self.fail_preview()
        self.assertFalse(app.current_can_render)
        self.assertTrue(app.current_can_preview)
        self.assertEqual(app.view_var.get(), 'Camera')
        self.assertIsNone(app.last_preview_overrides)
        self.assertEqual(app.live_image.getpixel((0, 0)), (0, 0, 255))
        for widget in (app.auto_adjust_button, app.process_button, app.compare_button,
                       app.auto_color_noise_button, app.undo_button, app.redo_button, *app.edit_scales):
            self.assertTrue(widget.instate(['disabled']))
        self.assertFalse(app.retry_preview_button.instate(['disabled']))
        self.assertTrue(app.retry_preview_button.grid_info())
        self.assertIn('RAW unavailable', app._refresh_preview_state())
        app._undo()
        app._redo()
        app._reset_adjustments()
        app._reset_one('exposure')
        with patch.object(app, '_start_pipeline') as start, patch.object(desktop.threading, 'Thread') as thread:
            app._export_photo()
            app._auto_adjust()
            app._auto_color_noise()
            app._select_subject()
            start.assert_not_called()
            thread.assert_not_called()
        self.assertEqual(app._current_overrides(), edits)
        self.assertEqual(app.session_store.load(app.source_path), saved)
        self.assertTrue(app.history.can_undo)

    def test_failure_without_camera_discards_stale_raw_and_histogram(self):
        app = self.app
        raw = Image.new('RGB', (12, 8), 'red')
        self.publish(LiveFrame(5, app.source_path, {}, raw, original_image=raw))
        self.fail_preview(camera=False)
        self.assertIsNone(app.live_image)
        self.assertIsNone(app.reference_image)
        self.assertIsNone(app.last_preview_overrides)
        self.assertFalse(app.current_can_preview)
        self.assertEqual(app.preview_label.cget('text'), 'RAW preview unavailable')

    def test_stale_failure_does_not_disable_current_frame(self):
        app = self.app
        for revision, source in ((4, app.source_path), (6, app.source_path),
                                 (5, app.source_path.with_name('other.NEF'))):
            self.publish(LiveFrame(revision, source, {}, error='Old failure', preparation_failed=True))
            self.assertIsNone(app.preview_failure)
            self.assertTrue(app.current_can_render)
        app.live_worker.invalidate.assert_not_called()

    def test_edit_or_detail_error_does_not_disable_working_raw(self):
        app = self.app
        raw = Image.new('RGB', (12, 8), 'red')
        self.publish(LiveFrame(5, app.source_path, {}, raw, original_image=raw))
        self.publish(LiveFrame(5, app.source_path, {}, error='Temporary render failure'))
        self.assertTrue(app.current_can_render)
        self.assertIsNone(app.preview_failure)
        self.assertEqual(app.live_image.tobytes(), raw.tobytes())
        self.assertFalse(app.auto_adjust_button.instate(['disabled']))
        self.assertFalse(app.retry_preview_button.grid_info())

    def test_retry_is_single_flight_and_recovery_restores_controls_without_resetting_edits(self):
        app = self.app
        app.exposure_var.set(.4)
        app._commit_edit()
        edits = app._current_overrides()
        self.fail_preview()
        with patch.object(desktop.threading, 'Thread') as thread:
            app._retry_live_preview()
            app._retry_live_preview()
            thread.assert_called_once()
            self.assertIsNone(app.current_can_render)
            self.assertTrue(app.retry_preview_button.instate(['disabled']))
        support = SimpleNamespace(can_render=True, can_preview=True, can_inspect=True, metadata={})
        with patch.object(app, '_schedule_live_preview'):
            app._show_photo_info(app.source_path, 'Fixture metadata', support, run_id=app.run_counter)
        self.assertTrue(app.auto_adjust_button.instate(['disabled']))
        self.assertTrue(app.process_button.instate(['disabled']))
        app._reset_one('exposure')
        self.assertEqual(app._current_overrides(), edits)
        with patch.object(desktop.threading, 'Thread') as thread:
            app._auto_adjust()
            app._select_subject()
            thread.assert_not_called()
        raw = Image.new('RGB', (12, 8), 'red')
        self.publish(LiveFrame(5, app.source_path, edits, raw, original_image=raw))
        self.assertIsNone(app.preview_failure)
        self.assertFalse(app.retry_preview_button.grid_info())
        self.assertEqual(app.support_notice_var.get(), '')
        self.assertFalse(app.auto_adjust_button.instate(['disabled']))
        self.assertFalse(app.process_button.instate(['disabled']))
        self.assertEqual(app._current_overrides(), edits)
        self.assertEqual(app.session_store.load(app.source_path), edits)
        app._undo()
        self.assertEqual(app.exposure_var.get(), 0)

    def test_failed_metadata_read_and_failed_retry_remain_retryable(self):
        app = self.app
        app._show_photo_info(app.source_path, 'Cannot read file', None, run_id=app.run_counter)
        self.assertEqual(app.preview_failure, 'Cannot read file')
        with patch.object(desktop.threading, 'Thread'):
            app._retry_live_preview()
        app._show_photo_info(app.source_path, 'Still offline', None, run_id=app.run_counter)
        self.assertEqual(app.preview_failure, 'Still offline')
        self.assertFalse(app.retry_preview_button.instate(['disabled']))
        self.assertFalse(app.current_can_render)

    def test_failed_preview_does_not_cancel_a_running_folder_job(self):
        app = self.app
        app.batch_running = True
        app._set_busy(True)
        run_id = app.run_counter
        self.fail_preview()
        self.assertEqual(app.run_counter, run_id)
        self.assertTrue(app.is_busy)
        self.assertTrue(app.batch_running)
        self.assertFalse(app.cancel_batch_button.instate(['disabled']))
        self.assertTrue(app.retry_preview_button.instate(['disabled']))
        app.batch_running = False
        app._set_busy(False)
        self.assertFalse(app.retry_preview_button.instate(['disabled']))

    def test_new_import_clears_failure_and_rejects_old_callbacks(self):
        app = self.app
        source = app.source_path
        self.fail_preview()
        stale_run = app.run_counter
        with patch.object(desktop.threading, 'Thread'):
            app._select_source(source, ready_status='Reopened')
        self.assertIsNone(app.preview_failure)
        self.assertFalse(app.retry_preview_button.grid_info())
        app._show_photo_info(source, 'Stale failure', None, run_id=stale_run)
        self.assertIsNone(app.preview_failure)

    def test_completed_folder_job_preserves_failed_preview_and_restores_retry(self):
        app = self.app
        self.fail_preview()
        app.batch_running = True
        app._set_busy(True)
        result = BatchResult(Path(self.temp.name), (
            BatchItemResult(app.source_path, 'failed', 'Unreadable RAW'),
            BatchItemResult(app.source_path.with_name('good.NEF'), 'exported', 'Done'),
        ))
        app._show_batch_result(app.run_counter, result)
        self.assertFalse(app.batch_running)
        self.assertFalse(app.is_busy)
        self.assertTrue(app.preview_failure)
        self.assertIn('RAW unavailable', app.preview_state_var.get())
        self.assertIn('1 failed, 1 processed', app.status_var.get())
        self.assertFalse(app.retry_preview_button.instate(['disabled']))
        self.assertTrue(app.process_button.instate(['disabled']))

    def test_retry_that_discovers_unsupported_source_discards_previous_camera_pixels(self):
        app = self.app
        self.fail_preview()
        support = SimpleNamespace(can_render=False, can_preview=False, can_inspect=True,
                                  metadata={}, reason='Unsupported encoding')
        app._show_photo_info(app.source_path, 'New metadata', support, run_id=app.run_counter)
        self.assertIsNone(app.live_image)
        self.assertIsNone(app.preview_failure)
        self.assertFalse(app.current_can_render)
        self.assertIn('Unsupported encoding', app.support_notice_var.get())
