import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openraw_studio.pipeline.interfaces import PipelineRequest
from openraw_studio.pipeline.local import LocalPhotoPipeline
from openraw_studio.raw.native.synthetic import write_synthetic_dng


class PreviewReuseTests(unittest.TestCase):
    def test_export_can_reuse_current_preview_without_rendering_it_again(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = write_synthetic_dng(root / "sample.DNG", width=16, height=12)
            output = root / "output"
            pipeline = LocalPhotoPipeline()
            preview_result = pipeline.process(PipelineRequest(source, output, preview_only=True))
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
