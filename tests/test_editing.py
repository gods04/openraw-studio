import tempfile
import unittest
from pathlib import Path

from openraw_studio.ui.editing import EditHistory, SessionStore, clean_adjustments


class EditingTests(unittest.TestCase):
    def test_undo_redo_branch_and_bounded_history(self):
        history = EditHistory(limit=3)
        history.commit({"exposure": 0.2})
        history.commit({"exposure": 0.4})
        self.assertEqual(history.undo()["exposure"], 0.2)
        self.assertTrue(history.can_redo)
        history.commit({"exposure": 0.6})
        self.assertFalse(history.can_redo)
        history.commit({"exposure": 0.8})
        self.assertEqual(history.undo()["exposure"], 0.6)
        self.assertEqual(history.undo()["exposure"], 0.2)
        self.assertFalse(history.can_undo)

    def test_session_roundtrip_stale_file_and_bad_data(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "photo.NEF"
            source.write_bytes(b"original")
            store = SessionStore(Path(temp) / "sessions")
            store.save(source, {"exposure": 0.7})
            self.assertEqual(store.load(source)["exposure"], 0.7)
            self.assertEqual(source.read_bytes(), b"original")
            source.write_bytes(b"different bytes")
            self.assertIsNone(store.load(source))
            store._path(source).write_text("{broken", encoding="utf-8")
            self.assertIsNone(store.load(source))

    def test_invalid_values_rejected(self):
        with self.assertRaises(ValueError):
            clean_adjustments({"exposure": float("nan")})
        self.assertEqual(clean_adjustments({"exposure": 100})["exposure"], 2)
