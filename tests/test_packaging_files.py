import runpy
import errno
from tempfile import TemporaryDirectory
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class PackagingFilesTests(unittest.TestCase):
    def test_pyinstaller_is_available_as_packaging_extra(self) -> None:
        pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

        packaging_deps = pyproject["project"]["optional-dependencies"]["packaging"]

        self.assertTrue(any(dependency.startswith("pyinstaller") for dependency in packaging_deps))

    def test_windows_build_workflow_uploads_zip_artifact(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "build-windows.yml").read_text(encoding="utf-8")

        self.assertIn("Build Windows App", workflow)
        self.assertIn("scripts\\build_windows.ps1", workflow)
        self.assertIn("dist\\OpenRAW-Studio-windows-x64.zip", workflow)

    def test_windows_bundle_includes_license_notice_and_readme(self) -> None:
        script = (ROOT / "scripts" / "build_windows.ps1").read_text(encoding="utf-8")

        self.assertIn('@("README.md", "LICENSE", "NOTICE")', script)
        self.assertIn('"THIRD_PARTY_LICENSES"', script)
        self.assertIn('@{ Name = "NumPy"; Pattern = "numpy-*.dist-info" }', script)
        self.assertIn('@{ Name = "Pillow"; Pattern = "pillow-*.dist-info" }', script)
        self.assertIn('@{ Name = "Tifffile"; Pattern = "tifffile-*.dist-info" }', script)
        self.assertIn("Copy-Item", script)
        self.assertTrue((ROOT / "NOTICE").is_file())

    def test_scene_runtime_is_explicit_and_retains_notices_without_weights(self):
        script = (ROOT / "scripts" / "build_windows.ps1").read_text(encoding="utf-8")
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertIn("onnxruntime==1.30.0", project["project"]["optional-dependencies"]["scene"])
        self.assertIn(".[packaging,scene]", script)
        self.assertIn("ThirdPartyNotices.txt", script)
        self.assertNotIn("scene.onnx", script)
        self.assertTrue((ROOT / "packaging/licenses/flatbuffers/Apache-2.0.txt").is_file())

    def test_scene_publisher_handles_identical_and_redirected_files(self):
        publish = runpy.run_path(str(ROOT / "scripts/prepare_scene_model.py"))["_publish"]
        with TemporaryDirectory() as directory:
            source, destination = Path(directory, "pending"), Path(directory, "model")
            source.write_bytes(b"same")
            destination.write_bytes(b"same")
            with patch.object(Path, "replace", side_effect=AssertionError("Unnecessary replace")):
                publish(source, destination)
            self.assertFalse(source.exists())
            source.write_bytes(b"updated")
            with patch.object(Path, "replace", side_effect=OSError(errno.EXDEV, "Cross-device")):
                publish(source, destination)
            self.assertEqual(destination.read_bytes(), b"updated")
            self.assertFalse(source.exists())

    def test_scene_publisher_does_not_hide_other_filesystem_errors(self):
        publish = runpy.run_path(str(ROOT / "scripts/prepare_scene_model.py"))["_publish"]
        with TemporaryDirectory() as directory:
            source, destination = Path(directory, "pending"), Path(directory, "model")
            source.write_bytes(b"new")
            destination.write_bytes(b"old")
            with patch.object(Path, "replace", side_effect=PermissionError("Denied")):
                with self.assertRaises(PermissionError):
                    publish(source, destination)
            self.assertEqual(destination.read_bytes(), b"old")
            self.assertEqual(source.read_bytes(), b"new")

    def test_numba_sources_are_present_for_frozen_cache_locator(self):
        script = (ROOT / "scripts" / "build_windows.ps1").read_text(encoding="utf-8")
        self.assertIn('--additional-hooks-dir "$RepoRoot/packaging/hooks"', script)
        hook = runpy.run_path(str(ROOT / "packaging" / "hooks" / "hook-openraw_studio.raw.native.py"))
        self.assertEqual(hook["module_collection_mode"], {"openraw_studio.raw.native": "py"})
        native = ROOT / "src" / "openraw_studio" / "raw" / "native"
        for module in ("compiled_decode", "compiled_he", "compiled_he_transform", "compiled_tone",
                       "compiled_bayer", "compiled_chroma", "compiled_luminance", "malvar", "he_cpu"):
            self.assertTrue((native / (module + ".py")).is_file(), module)

    def test_pyinstaller_entrypoint_is_import_safe(self) -> None:
        module_globals = runpy.run_path(str(ROOT / "packaging" / "openraw_app.py"))

        self.assertTrue(callable(module_globals["main"]))


if __name__ == "__main__":
    unittest.main()
