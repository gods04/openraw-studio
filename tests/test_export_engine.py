import json
import tempfile
import unittest
from pathlib import Path

from openraw_studio.core.domain import ImageRef
from openraw_studio.core.image_info import read_image_size
from openraw_studio.export.errors import ExportError
from openraw_studio.export.interfaces import ExportRequest
from openraw_studio.export.local import LocalJpegExportEngine
from openraw_studio.raw.native.png import write_png
from openraw_studio.raw.native.tone import PreviewRgbImage


class LocalJpegExportEngineTests(unittest.TestCase):
    def test_exporter_writes_jpeg_from_existing_image(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "render.png"
            output = root / "exports" / "render.auto.jpg"
            write_png(
                PreviewRgbImage(width=2, height=1, pixels=((255, 0, 0), (0, 255, 0)), transfer="srgb"),
                source,
            )

            result = LocalJpegExportEngine().export(
                ExportRequest(
                    image=ImageRef(source, width=2, height=1, color_space="preview-rgb", role="base"),
                    recipe={"source": {"path": "sample.DNG"}},
                    output_path=output,
                    quality=88,
                    write_recipe_sidecar=True,
                )
            )

            output_bytes = output.read_bytes()
            output_size = read_image_size(output)
            recipe_payload = json.loads(result.recipe_path.read_text(encoding="utf-8")) if result.recipe_path else {}

        self.assertTrue(output_bytes.startswith(b"\xff\xd8"))
        self.assertEqual(output_size, (2, 1))
        self.assertEqual(result.exported.path, output)
        self.assertEqual(result.exported.role, "export")
        self.assertEqual(result.metadata["quality"], 88)
        self.assertEqual(recipe_payload["source"]["path"], "sample.DNG")

    def test_exporter_rejects_unsupported_format_and_quality(self) -> None:
        engine = LocalJpegExportEngine()
        image = ImageRef(Path("render.png"), width=1, height=1, color_space="sRGB", role="base")

        with self.assertRaises(ExportError):
            engine.export(ExportRequest(image=image, recipe={}, output_path=Path("out.tif"), format="tiff"))
        with self.assertRaises(ExportError):
            engine.export(ExportRequest(image=image, recipe={}, output_path=Path("out.jpg"), quality=101))


if __name__ == "__main__":
    unittest.main()
