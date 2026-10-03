import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openraw_studio.core.runtime import configure_numba_cache


class RuntimeCacheTests(unittest.TestCase):
    def test_bundle_uses_resolved_per_user_cache(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(
            os.environ, {"LOCALAPPDATA": folder}, clear=True
        ), patch.object(sys, "frozen", True, create=True):
            configure_numba_cache()
            expected = (Path(folder) / "OpenRAW Studio" / "numba").resolve()
            self.assertTrue(expected.is_dir())
            self.assertEqual(os.environ["NUMBA_CACHE_DIR"], str(expected))

    def test_explicit_cache_and_source_environment_are_preserved(self):
        with patch.dict(os.environ, {"NUMBA_CACHE_DIR": "custom-cache"}, clear=True), patch.object(
            sys, "frozen", True, create=True
        ), patch.object(Path, "mkdir") as mkdir:
            configure_numba_cache()
            self.assertEqual(os.environ["NUMBA_CACHE_DIR"], "custom-cache")
            mkdir.assert_not_called()
        with patch.dict(os.environ, {}, clear=True), patch.object(sys, "frozen", False, create=True):
            configure_numba_cache()
            self.assertNotIn("NUMBA_CACHE_DIR", os.environ)

    def test_unwritable_directory_does_not_stop_app_launch(self):
        with patch.dict(os.environ, {"LOCALAPPDATA": "local-app-data"}, clear=True), patch.object(sys, "frozen", True, create=True), patch.object(
            Path, "mkdir", side_effect=PermissionError("read only")
        ):
            configure_numba_cache()
            self.assertNotIn("NUMBA_CACHE_DIR", os.environ)

    def test_missing_home_keeps_in_memory_fallback_available(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(sys, "frozen", True, create=True), patch.object(
            Path, "home", side_effect=RuntimeError("No home directory")
        ):
            configure_numba_cache()
            self.assertNotIn("NUMBA_CACHE_DIR", os.environ)
