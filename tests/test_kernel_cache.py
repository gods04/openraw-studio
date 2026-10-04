import ast
from contextlib import ExitStack
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from openraw_studio.core import kernel_cache


NATIVE = Path(__file__).resolve().parents[1] / "src/openraw_studio/raw/native"


class KernelCacheTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.folder = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.bundle = self.folder / "bundle"
        self.native = self.bundle / "openraw_studio/raw/native"
        self.native.mkdir(parents=True)
        for name in kernel_cache._REQUIRED:
            (self.native / name).write_text("# test source\n", encoding="utf-8")
        self.file = self.native / "compiled_tone.py"
        self.file.write_text("def sample():\n    return 1\n", encoding="utf-8")
        namespace = {"__name__": "openraw_studio.raw.native.compiled_tone"}
        exec(compile(self.file.read_text(), str(self.file), "exec"), namespace)
        self.function = namespace["sample"]
        self.stack.enter_context(patch.object(sys, "frozen", True, create=True))
        self.stack.enter_context(patch.object(sys, "_MEIPASS", str(self.bundle), create=True))
        self.stack.enter_context(patch.object(kernel_cache.config, "CACHE_DIR", str(self.folder / "cache")))

    def locate(self):
        return kernel_cache.FrozenKernelCacheLocator.from_function(self.function, str(self.file))

    def test_stable_identity_ignores_executable_and_source_timestamps(self):
        first = self.locate()
        self.assertIsNotNone(first)
        os.utime(self.file, (1_000_000, 1_000_000))
        with patch.object(sys, "executable", "a different app.exe"):
            second = self.locate()
        self.assertEqual(first.get_source_stamp(), second.get_source_stamp())
        self.assertEqual(first.get_cache_path(), second.get_cache_path())
        self.assertEqual(first.get_disambiguator(), str(self.function.__code__.co_firstlineno))

    def test_same_size_same_mtime_source_edit_invalidates_cache(self):
        first = self.locate()
        stamp = self.file.stat()
        self.file.write_text(self.file.read_text().replace("return 1", "return 2"), encoding="utf-8")
        os.utime(self.file, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        second = self.locate()
        self.assertNotEqual(first.get_source_stamp(), second.get_source_stamp())
        self.assertNotEqual(first.get_cache_path(), second.get_cache_path())

    def test_helper_and_new_native_source_invalidate_cache(self):
        first = self.locate().get_source_stamp()
        (self.native / "malvar.py").write_text("# updated helper\n", encoding="utf-8")
        second = self.locate().get_source_stamp()
        self.assertNotEqual(first, second)
        (self.native / "new_helper.py").write_text("# new helper\n", encoding="utf-8")
        self.assertNotEqual(second, self.locate().get_source_stamp())

    def test_runtime_versions_and_resolved_compilation_flags_invalidate_cache(self):
        first = self.locate().get_source_stamp()
        for module in (kernel_cache.np, kernel_cache.numba, kernel_cache.llvmlite):
            with self.subTest(module=module.__name__), patch.object(module, "__version__", "changed"):
                self.assertNotEqual(first, self.locate().get_source_stamp())
        with patch.object(kernel_cache.config, "BOUNDSCHECK", "different"):
            self.assertNotEqual(first, self.locate().get_source_stamp())

    def test_cache_location_and_logging_do_not_invalidate_code(self):
        first = self.locate()
        with patch.object(kernel_cache.config, "CACHE_DIR", str(self.folder / "other")), patch.object(
            kernel_cache.config, "DEBUG_CACHE", 1
        ), patch.object(kernel_cache.config, "CACHE_LOCATOR_CLASSES", "different"):
            second = self.locate()
        self.assertEqual(first.get_source_stamp(), second.get_source_stamp())
        self.assertNotEqual(first.get_cache_path(), second.get_cache_path())

    def test_unknown_module_nonfrozen_or_missing_cache_uses_standard_fallback(self):
        for obj, name, value in ((self.function, "__module__", "other.kernel"),
                                 (sys, "frozen", False), (sys, "_MEIPASS", None),
                                 (kernel_cache.config, "CACHE_DIR", "")):
            with self.subTest(name=name), patch.object(obj, name, value):
                self.assertIsNone(self.locate())

    def test_file_and_function_must_belong_to_the_expected_bundle(self):
        outside = self.folder / "compiled_tone.py"
        outside.write_text(self.file.read_text(), encoding="utf-8")
        self.assertIsNone(kernel_cache.FrozenKernelCacheLocator.from_function(self.function, str(outside)))
        other = dict(__name__=self.function.__module__)
        exec(compile(outside.read_text(), str(outside), "exec"), other)
        self.assertIsNone(kernel_cache.FrozenKernelCacheLocator.from_function(other["sample"], str(self.file)))

    def test_incomplete_unreadable_or_unwritable_bundle_falls_back(self):
        with patch.object(Path, "read_bytes", side_effect=PermissionError("denied")):
            self.assertIsNone(self.locate())
        with patch("openraw_studio.core.kernel_cache.tempfile.TemporaryFile", side_effect=PermissionError("denied")):
            self.assertIsNone(self.locate())
        (self.native / "malvar.py").unlink()
        self.assertIsNone(self.locate())

    def test_reviewed_kernels_have_no_external_project_jit_dependencies(self):
        allowed = {"__future__", "numpy", "numba", "numba.core.errors", "numba.extending",
                   "openraw_studio.raw.native.malvar"}
        for name in kernel_cache._KERNELS:
            tree = ast.parse((NATIVE / (name + ".py")).read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    self.assertTrue(all(alias.name in allowed for alias in node.names), name)
                elif isinstance(node, ast.ImportFrom):
                    if node.level:
                        self.assertEqual((node.level, node.module, [a.name for a in node.names]), (1, None, ["he_cpu"]))
                    else:
                        self.assertIn(node.module, allowed, name)


class KernelCacheProcessTests(unittest.TestCase):
    def test_real_jit_cache_hits_after_repack_and_move_but_not_source_or_flag_change(self):
        script = '''
import json, os, sys
from pathlib import Path
sys.dont_write_bytecode = True
sys.frozen = True
sys._MEIPASS = sys.argv[1]
sys.executable = sys.argv[2]
from openraw_studio.core.runtime import configure_numba_cache
configure_numba_cache()
import openraw_studio.raw.native as native
native.__path__ = [str(Path(sys._MEIPASS) / 'openraw_studio/raw/native')]
from openraw_studio.raw.native import compiled_tone
import numpy as np
pixels = np.full((4, 4, 3), .3, np.float32)
params = np.array([1,0,0,0,1,0,0,0,1,1,1,1,1,0,0,1,0,-1], np.float32)
result = {}
for bits, kernel in ((8, compiled_tone.tone), (16, compiled_tone.tone16)):
    image = compiled_tone.render(pixels, params, bit_depth=bits)
    assert image is not None, compiled_tone.last_error
    result[str(bits)] = dict(pixel=image[0,0].tolist(),
        hits=sum(kernel.stats.cache_hits.values()), misses=sum(kernel.stats.cache_misses.values()),
        locator=type(kernel._cache._impl.locator).__name__)
from openraw_studio.core.subject import make_subject, subject_with_color
from openraw_studio.raw.native.subject import apply_subject
from openraw_studio.raw.native import compiled_subject, compiled_subject_color
subject = {**make_subject(np.ones((2, 2), np.float32), Path(sys.argv[2])), 'exposure': .5}
for bits, dtype in ((8, np.uint8), (16, np.uint16)):
    kernel = compiled_subject.expose
    hits, misses = sum(kernel.stats.cache_hits.values()), sum(kernel.stats.cache_misses.values())
    image = apply_subject(np.full((4, 4, 3), 100, dtype), subject)
    assert compiled_subject.last_error is None, compiled_subject.last_error
    result['subject' + str(bits)] = dict(pixel=image[0,0].tolist(),
        hits=sum(kernel.stats.cache_hits.values()) - hits,
        misses=sum(kernel.stats.cache_misses.values()) - misses,
        locator=type(kernel._cache._impl.locator).__name__)
subject = subject_with_color(subject, .2, -.3)
for bits, dtype in ((8, np.uint8), (16, np.uint16)):
    kernel = compiled_subject_color.balance
    hits, misses = sum(kernel.stats.cache_hits.values()), sum(kernel.stats.cache_misses.values())
    image = apply_subject(np.full((4, 4, 3), 100, dtype), subject)
    assert compiled_subject_color.last_error is None, compiled_subject_color.last_error
    result['color' + str(bits)] = dict(pixel=image[0,0].tolist(),
        hits=sum(kernel.stats.cache_hits.values()) - hits,
        misses=sum(kernel.stats.cache_misses.values()) - misses,
        locator=type(kernel._cache._impl.locator).__name__)
print(json.dumps(result))
'''
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            bundle = root / "app"
            target = bundle / "openraw_studio/raw/native"
            shutil.copytree(NATIVE, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyd", "*.so"))
            exe = bundle / "test.exe"
            exe.write_bytes(b"first build")
            env = {k: v for k, v in os.environ.items() if not k.startswith("NUMBA_")}
            env["NUMBA_CACHE_DIR"] = str(root / "cache")

            def run(expected_hits, expected_misses):
                output = subprocess.check_output([sys.executable, "-c", script, str(bundle), str(exe)], env=env, text=True, timeout=90)
                result = json.loads(output.strip().splitlines()[-1])
                for item in result.values():
                    self.assertEqual(item["locator"], "FrozenKernelCacheLocator")
                    self.assertEqual(item["hits"], expected_hits)
                    self.assertEqual(item["misses"], expected_misses)
                return result

            first = run(0, 1)
            warm = run(1, 0)
            self.assertEqual(first["8"]["pixel"], warm["8"]["pixel"])
            exe.write_bytes(b"rebuilt user interface")
            for path in target.rglob("*.py"):
                os.utime(path, (1_000_000, 1_000_000))
            run(1, 0)
            moved = root / "moved"
            shutil.copytree(bundle, moved)
            bundle, exe = moved, moved / "test.exe"
            target = moved / "openraw_studio/raw/native"
            run(1, 0)
            changed = target / "compiled_tone.py"
            stamp = changed.stat()
            changed.write_text(changed.read_text().replace("np.float32(255)", "np.float32(127)"), encoding="utf-8")
            os.utime(changed, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
            updated = run(0, 1)
            self.assertNotEqual(updated["8"]["pixel"], first["8"]["pixel"])
            env["NUMBA_BOUNDSCHECK"] = "1"
            run(0, 1)
            env["NUMBA_DEBUG_CACHE"] = "1"
            run(1, 0)


if __name__ == "__main__":
    unittest.main()
