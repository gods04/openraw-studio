import runpy
import tomllib
import unittest
from pathlib import Path


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

    def test_pyinstaller_entrypoint_is_import_safe(self) -> None:
        module_globals = runpy.run_path(str(ROOT / "packaging" / "openraw_app.py"))

        self.assertTrue(callable(module_globals["main"]))


if __name__ == "__main__":
    unittest.main()
