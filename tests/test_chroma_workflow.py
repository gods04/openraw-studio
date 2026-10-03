import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

import numpy as np
from fixtures_nikon import synthetic_nikon_nef_compressed_bytes
from PIL import Image

from openraw_studio.cli import main
from openraw_studio.pipeline.interfaces import PipelineRequest
from openraw_studio.pipeline.local import LocalPhotoPipeline
from openraw_studio.raw.native.chroma import reduce_color_noise
from openraw_studio.raw.native.engine import _recipe_render_adjustments
from openraw_studio.raw.native.nikon import (
    decode_nikon_34713_lossless,
    render_decoded_nikon_34713_image,
)
from openraw_studio.raw.native.synthetic import write_synthetic_dng
from openraw_studio.ui.desktop import _adjustments_match, _recipe_adjustment_overrides
from openraw_studio.ui.editing import EditHistory, SessionStore, clean_adjustments


class ChromaWorkflowTests(unittest.TestCase):
    def test_history_session_and_recipe_retain_color_noise_with_old_default_off(self):
        self.assertEqual(clean_adjustments({})["color_noise"], 0)
        self.assertEqual(clean_adjustments({"color_noise": -1})["color_noise"], 0)
        self.assertEqual(clean_adjustments({"color_noise": 2})["color_noise"], 1)
        self.assertFalse(_adjustments_match({"color_noise": 0.4}, {"color_noise": 0.5}))
        self.assertEqual(_recipe_render_adjustments({}).color_noise, 0)
        history = EditHistory()
        history.commit({"color_noise": 0.65})
        self.assertEqual(history.undo()["color_noise"], 0)
        self.assertEqual(history.redo()["color_noise"], 0.65)
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "photo.NEF"
            source.write_bytes(b"original")
            store = SessionStore(Path(folder) / "sessions")
            store.save(source, history.current)
            self.assertEqual(store.load(source)["color_noise"], 0.65)

    def test_pipeline_tiff_is_filtered_before_encoding_and_source_is_immutable(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "noise.NEF"
            raw = synthetic_nikon_nef_compressed_bytes(
                width=32,
                height=24,
                model="NIKON D500",
                samples=tuple(
                    map(int, np.random.default_rng(9).integers(100, 13000, 32 * 24))
                ),
            )
            source.write_bytes(raw)
            decoded = decode_nikon_34713_lossless(source)
            unedited = render_decoded_nikon_34713_image(decoded, quality="full")
            baseline = np.frombuffer(unedited.rgb_bytes, np.uint8).reshape(
                unedited.height, unedited.width, 3
            )
            expected = reduce_color_noise(baseline, 0.7)
            self.assertTrue(np.any(expected != baseline))
            result = LocalPhotoPipeline().process(
                PipelineRequest(
                    source,
                    root / "out",
                    overrides={"color_noise": 0.7},
                    export_format="tiff",
                )
            )
            with Image.open(result.exports[0].path) as image:
                np.testing.assert_array_equal(np.asarray(image), expected)
            self.assertEqual(result.recipe["adjustments"]["raw"]["color_noise"], 0.7)
            self.assertEqual(
                _recipe_adjustment_overrides(result.recipe)["color_noise"], 0.7
            )
            self.assertEqual(source.read_bytes(), raw)

    def test_cli_process_and_batch_accept_color_noise_and_reject_invalid_values(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            sources = root / "sources"
            sources.mkdir()
            source = write_synthetic_dng(sources / "sample.DNG", width=16, height=16)
            for command, target in (("process", source), ("batch", sources)):
                output = root / command
                with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                    status = main(
                        [
                            command,
                            str(target),
                            "--output",
                            str(output),
                            "--color-noise",
                            ".6",
                        ]
                    )
                self.assertEqual(status, 0)
                recipe = json.loads(
                    next((output / "recipes").glob("*.json")).read_text()
                )
                self.assertEqual(recipe["adjustments"]["raw"]["color_noise"], 0.6)
                for invalid in ("-1", "1.1", "nan", "inf"):
                    with redirect_stderr(StringIO()):
                        self.assertEqual(
                            main(
                                [
                                    command,
                                    str(target),
                                    "--output",
                                    str(output),
                                    "--color-noise",
                                    invalid,
                                ]
                            ),
                            2,
                        )
