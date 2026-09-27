import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openraw_studio.pipeline.interfaces import PipelineRequest
from openraw_studio.pipeline.local import LocalPhotoPipeline
from openraw_studio.raw.native.synthetic import write_synthetic_dng


class PreviewReuseTests(unittest.TestCase):
    def test_changed_inputs_or_derivative_cannot_reuse_stale_preview(self) -> None:
        for change in (
            "adjustments",
            "source",
            "preview",
            "missing",
            "output",
            "new_session",
        ):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                source = write_synthetic_dng(root / "sample.DNG", width=16, height=12)
                output = root / "output"
                pipeline = LocalPhotoPipeline()
                result = pipeline.process(
                    PipelineRequest(source, output, preview_only=True)
                )
                overrides = {}
                if change == "adjustments":
                    overrides = {"exposure": 1.0}
                elif change == "source":
                    write_synthetic_dng(source, width=20, height=12)
                elif change == "preview":
                    result.preview.path.write_bytes(b"not the original preview")
                elif change == "missing":
                    result.preview.path.unlink()
                elif change == "output":
                    output = root / "other-output"
                elif change == "new_session":
                    pipeline = LocalPhotoPipeline()
                with patch.object(
                    pipeline.raw_processor,
                    "create_preview",
                    wraps=pipeline.raw_processor.create_preview,
                ) as render:
                    exported = pipeline.process(
                        PipelineRequest(
                            source,
                            output,
                            overrides=overrides,
                            reuse_existing_preview=True,
                        )
                    )
                render.assert_called_once()
                self.assertFalse(exported.diagnostics["preview_reused"])
                self.assertTrue(exported.exports[0].path.is_file())

    def test_export_can_reuse_current_preview_without_rendering_it_again(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = write_synthetic_dng(root / "sample.DNG", width=16, height=12)
            output = root / "output"
            pipeline = LocalPhotoPipeline()
            preview_result = pipeline.process(
                PipelineRequest(source, output, preview_only=True)
            )
            assert preview_result.preview is not None
            preview_bytes = preview_result.preview.path.read_bytes()

            with patch.object(
                pipeline.raw_processor,
                "create_preview",
                wraps=pipeline.raw_processor.create_preview,
            ) as create_preview:
                result = pipeline.process(
                    PipelineRequest(source, output, reuse_existing_preview=True)
                )

            create_preview.assert_not_called()
            self.assertTrue(result.diagnostics["preview_reused"])
            self.assertTrue(result.recipe["pipeline"]["preview_reused"])
            self.assertEqual(result.preview.path.read_bytes(), preview_bytes)
            self.assertTrue(result.exports[0].path.is_file())


if __name__ == "__main__":
    unittest.main()
