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

    def test_cross_volume_atomic_rename_uses_private_home_fallback(self):
        replace = os.replace
        with tempfile.TemporaryDirectory() as folder:
            local, home = Path(folder) / "local", Path(folder) / "home"

            def redirected(source, target):
                if local in Path(source).parents:
                    raise OSError(18, "Cross-device rename")
                return replace(source, target)

            with patch.dict(os.environ, {"LOCALAPPDATA": str(local)}, clear=True), patch.object(
                sys, "frozen", True, create=True
            ), patch.object(Path, "home", return_value=home), patch("openraw_studio.core.runtime.os.replace", side_effect=redirected):
                configure_numba_cache()
                expected = (home / ".cache" / "OpenRAW Studio" / "numba").resolve()
                self.assertEqual(os.environ["NUMBA_CACHE_DIR"], str(expected))
                self.assertFalse(list(Path(folder).rglob(".openraw-cache-*")))

    def test_probe_cleanup_preserves_existing_files_and_cached_data(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(
            os.environ, {"LOCALAPPDATA": folder}, clear=True
        ), patch.object(sys, "frozen", True, create=True):
            cache = Path(folder) / "OpenRAW Studio" / "numba"
            cache.mkdir(parents=True)
            existing = cache / ".openraw-cache-existing.nbi"
            existing.write_bytes(b"existing data")
            configure_numba_cache()
            self.assertEqual(existing.read_bytes(), b"existing data")
            self.assertEqual(list(cache.iterdir()), [existing])

    def test_failure_in_both_private_directories_does_not_stop_launch(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(
            os.environ, {"LOCALAPPDATA": str(Path(folder) / "local")}, clear=True
        ), patch.object(sys, "frozen", True, create=True), patch.object(
            Path, "home", return_value=Path(folder) / "home"
        ), patch("openraw_studio.core.runtime.os.replace", side_effect=PermissionError("replacement denied")):
            configure_numba_cache()
            self.assertNotIn("NUMBA_CACHE_DIR", os.environ)
            self.assertFalse(list(Path(folder).rglob(".openraw-cache-*")))

    def test_missing_local_app_data_uses_tested_home_cache(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=True), patch.object(
            sys, "frozen", True, create=True
        ), patch.object(Path, "home", return_value=Path(folder)):
            configure_numba_cache()
            self.assertEqual(os.environ["NUMBA_CACHE_DIR"], str((Path(folder) / ".cache" / "OpenRAW Studio" / "numba").resolve()))

    def test_failed_replacement_verification_rejects_cache(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(
            os.environ, {"LOCALAPPDATA": str(Path(folder) / "local")}, clear=True
        ), patch.object(sys, "frozen", True, create=True), patch.object(
            Path, "home", return_value=Path(folder) / "home"
        ), patch("openraw_studio.core.runtime.os.replace", return_value=None):
            configure_numba_cache()
            self.assertNotIn("NUMBA_CACHE_DIR", os.environ)
            self.assertFalse(list(Path(folder).rglob(".openraw-cache-*")))
