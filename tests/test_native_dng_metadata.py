import struct
import tempfile
import unittest
from pathlib import Path

from openraw_studio.core.domain import ImageAsset
from openraw_studio.core.image_info import read_image_size
from openraw_studio.pipeline.interfaces import PipelineRequest
from openraw_studio.pipeline.local import LocalPhotoPipeline
from openraw_studio.raw.native import (
    DngMetadataReader,
    NativeRawDecoder,
    NativeRawProcessor,
    PreviewRgbImage,
    apply_as_shot_neutral,
    apply_camera_matrix,
    demosaic_simple,
    normalize_sensor_data,
    render_preview_image,
    resize_preview,
    tone_map_preview,
    write_png,
)
from openraw_studio.raw.native.decoder import RawSensorData
from openraw_studio.raw.native.demosaic import DemosaicError
from openraw_studio.raw.native.sensor import LinearSensorImage
from openraw_studio.raw.native.sensor import SensorNormalizationError


class NativeDngMetadataTests(unittest.TestCase):
    def test_reader_extracts_core_dng_tags(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "sample.DNG"
            path.write_bytes(_minimal_dng_bytes())

            metadata = DngMetadataReader().read(path).as_dict()

        self.assertEqual(metadata["byte_order"], "little")
        self.assertEqual(metadata["width"], 4000)
        self.assertEqual(metadata["height"], 3000)
        self.assertEqual(metadata["make"], "OpenRAW")
        self.assertEqual(metadata["model"], "NativeCam")
        self.assertEqual(metadata["unique_camera_model"], "OpenRAW NativeCam")
        self.assertEqual(metadata["dng_version"], (1, 4, 0, 0))
        self.assertEqual(metadata["dng_version_text"], "1.4.0.0")
        self.assertEqual(metadata["black_level"], 64)
        self.assertEqual(metadata["white_level"], 4095)
        self.assertEqual(metadata["cfa_repeat_pattern_dim"], (2, 2))
        self.assertEqual(metadata["cfa_pattern"], (0, 1, 1, 2))
        self.assertEqual(len(metadata["color_matrix_1"]), 9)
        self.assertEqual(metadata["as_shot_neutral"], (0.5, 1.0, 0.75))

    def test_native_inspect_adds_dng_metadata_to_image_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "sample.DNG"
            path.write_bytes(_minimal_dng_bytes())

            inspection = NativeRawProcessor().inspect(ImageAsset(path=path))

        self.assertEqual(inspection.metadata.width, 4000)
        self.assertEqual(inspection.metadata.height, 3000)
        self.assertEqual(inspection.metadata.camera_make, "OpenRAW")
        self.assertEqual(inspection.metadata.camera_model, "OpenRAW NativeCam")
        self.assertEqual(inspection.metadata.raw["dng"]["white_level"], 4095)

    def test_pipeline_recipe_includes_native_dng_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "sample.DNG"
            output = root / "output"
            source.write_bytes(_minimal_dng_bytes())

            result = LocalPhotoPipeline().process(PipelineRequest(source, output, dry_run=True))

        dng = result.recipe["source"]["metadata"]["dng"]
        self.assertEqual(dng["width"], 4000)
        self.assertEqual(dng["height"], 3000)
        self.assertEqual(result.recipe["source"]["metadata"]["camera_model"], "OpenRAW NativeCam")

    def test_reader_extracts_uncompressed_strip_pixels(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "pixels.DNG"
            path.write_bytes(_minimal_pixel_dng_bytes())

            pixel_data = DngMetadataReader().read_pixel_data(path)

        self.assertEqual(pixel_data.width, 2)
        self.assertEqual(pixel_data.height, 2)
        self.assertEqual(pixel_data.bits_per_sample, 16)
        self.assertEqual(pixel_data.samples_per_pixel, 1)
        self.assertEqual(pixel_data.black_level, 64)
        self.assertEqual(pixel_data.white_level, 4095)
        self.assertEqual(pixel_data.samples_u16(), (64, 1024, 2048, 4095))

    def test_native_decoder_returns_sensor_data_for_simple_dng(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "pixels.DNG"
            path.write_bytes(_minimal_pixel_dng_bytes())

            sensor = NativeRawDecoder().decode(path)

        self.assertEqual(sensor.width, 2)
        self.assertEqual(sensor.height, 2)
        self.assertEqual(sensor.color_filter_array, "RGGB")
        self.assertEqual(sensor.bits_per_sample, 16)
        self.assertEqual(sensor.black_level, 64)
        self.assertEqual(sensor.white_level, 4095)
        self.assertEqual(len(sensor.raw_bytes), 8)

    def test_sensor_normalization_maps_black_and_white_levels(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "pixels.DNG"
            path.write_bytes(_minimal_pixel_dng_bytes())

            sensor = NativeRawDecoder().decode(path)
            linear = normalize_sensor_data(sensor)

        self.assertEqual(linear.width, 2)
        self.assertEqual(linear.height, 2)
        self.assertEqual(linear.color_filter_array, "RGGB")
        self.assertEqual(linear.sample_at(0, 0), 0.0)
        self.assertAlmostEqual(linear.sample_at(0, 1), (1024 - 64) / (4095 - 64))
        self.assertAlmostEqual(linear.sample_at(1, 0), (2048 - 64) / (4095 - 64))
        self.assertEqual(linear.sample_at(1, 1), 1.0)

    def test_sensor_normalization_clamps_out_of_range_samples(self) -> None:
        sensor = RawSensorData(
            source_path=Path("synthetic.DNG"),
            width=2,
            height=2,
            color_filter_array="RGGB",
            raw_bytes=_pack_shorts([0, 64, 4095, 65535]),
            bits_per_sample=16,
            samples_per_pixel=1,
            black_level=64,
            white_level=4095,
            metadata={"byte_order": "little"},
        )

        linear = normalize_sensor_data(sensor)

        self.assertEqual(linear.samples, (0.0, 0.0, 1.0, 1.0))

    def test_sensor_normalization_rejects_missing_levels(self) -> None:
        sensor = RawSensorData(
            source_path=Path("synthetic.DNG"),
            width=1,
            height=1,
            color_filter_array="RGGB",
            raw_bytes=_pack_shorts([128]),
            bits_per_sample=16,
            samples_per_pixel=1,
            black_level=None,
            white_level=4095,
            metadata={"byte_order": "little"},
        )

        with self.assertRaises(SensorNormalizationError):
            normalize_sensor_data(sensor)

    def test_as_shot_neutral_applies_cfa_channel_gains(self) -> None:
        sensor = LinearSensorImage(
            width=2,
            height=2,
            color_filter_array="RGGB",
            samples=(0.5, 0.5, 0.5, 0.5),
            black_level=0,
            white_level=1,
            source_bit_depth=16,
        )

        balanced = apply_as_shot_neutral(sensor, (0.5, 1.0, 0.25))

        self.assertEqual(balanced.samples, (1.0, 0.5, 0.5, 1.0))
        self.assertEqual(balanced.metadata["white_balance"], "as-shot")

    def test_camera_matrix_transforms_linear_rgb(self) -> None:
        image = demosaic_simple(
            LinearSensorImage(
                width=2,
                height=2,
                color_filter_array="RGGB",
                samples=(1.0, 0.5, 0.25, 0.0),
                black_level=0,
                white_level=1,
                source_bit_depth=16,
            )
        )

        transformed = apply_camera_matrix(
            image,
            (0.0, 1.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0),
        )

        self.assertEqual(transformed.pixel_at(0, 0), (0.375, 1.0, 0.0))

    def test_simple_demosaic_preserves_known_bayer_samples(self) -> None:
        sensor = LinearSensorImage(
            width=2,
            height=2,
            color_filter_array="RGGB",
            samples=(1.0, 0.5, 0.25, 0.0),
            black_level=0,
            white_level=1,
            source_bit_depth=16,
        )

        rgb = demosaic_simple(sensor)

        self.assertEqual(rgb.pixel_at(0, 0)[0], 1.0)
        self.assertEqual(rgb.pixel_at(0, 1)[1], 0.5)
        self.assertEqual(rgb.pixel_at(1, 0)[1], 0.25)
        self.assertEqual(rgb.pixel_at(1, 1)[2], 0.0)

    def test_simple_demosaic_fills_missing_channels_from_neighbors(self) -> None:
        sensor = LinearSensorImage(
            width=2,
            height=2,
            color_filter_array="RGGB",
            samples=(1.0, 0.5, 0.25, 0.0),
            black_level=0,
            white_level=1,
            source_bit_depth=16,
        )

        rgb = demosaic_simple(sensor)

        self.assertEqual(rgb.pixel_at(0, 0), (1.0, 0.375, 0.0))
        self.assertEqual(rgb.pixel_at(1, 1), (1.0, 0.375, 0.0))

    def test_simple_demosaic_rejects_unknown_cfa(self) -> None:
        sensor = LinearSensorImage(
            width=1,
            height=1,
            color_filter_array="unknown",
            samples=(0.5,),
            black_level=0,
            white_level=1,
            source_bit_depth=16,
        )

        with self.assertRaises(DemosaicError):
            demosaic_simple(sensor)

    def test_tone_map_preview_encodes_8_bit_pixels(self) -> None:
        sensor = LinearSensorImage(
            width=2,
            height=2,
            color_filter_array="RGGB",
            samples=(1.0, 0.5, 0.25, 0.0),
            black_level=0,
            white_level=1,
            source_bit_depth=16,
        )

        preview = tone_map_preview(demosaic_simple(sensor), gamma=1.0)

        self.assertEqual(preview.width, 2)
        self.assertEqual(preview.height, 2)
        self.assertEqual(preview.pixel_at(0, 0), (255, 96, 0))

    def test_tone_map_preview_applies_contrast_and_warmth(self) -> None:
        sensor = LinearSensorImage(
            width=2,
            height=2,
            color_filter_array="RGGB",
            samples=(0.4, 0.4, 0.4, 0.4),
            black_level=0,
            white_level=1,
            source_bit_depth=16,
        )
        linear_rgb = demosaic_simple(sensor)

        neutral = tone_map_preview(linear_rgb, gamma=1.0)
        adjusted = tone_map_preview(linear_rgb, contrast=0.8, warmth=1.0, gamma=1.0)

        self.assertNotEqual(neutral.pixel_at(0, 0), adjusted.pixel_at(0, 0))
        self.assertGreater(adjusted.pixel_at(0, 0)[0], adjusted.pixel_at(0, 0)[2])

    def test_png_writer_outputs_readable_dimensions(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "preview.png"
            image = PreviewRgbImage(
                width=2,
                height=2,
                pixels=((255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255)),
                transfer="gamma-1",
            )

            write_png(image, path)
            preview_bytes = path.read_bytes()
            preview_size = read_image_size(path)

        self.assertTrue(preview_bytes.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(preview_size, (2, 2))

    def test_preview_resize_keeps_aspect_ratio_and_pixel_count(self) -> None:
        image = PreviewRgbImage(
            width=4,
            height=2,
            pixels=tuple((index, index, index) for index in range(8)),
            transfer="gamma-2.2",
        )

        resized = resize_preview(image, max_dimension=2)

        self.assertEqual((resized.width, resized.height), (2, 1))
        self.assertEqual(len(resized.pixels), 2)

    def test_native_processor_writes_png_preview_for_simple_dng(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "pixels.DNG"
            preview_path = root / "preview.png"
            source.write_bytes(_minimal_pixel_dng_bytes())

            image = NativeRawProcessor().create_preview(ImageAsset(source), preview_path, max_dimension=2048)
            preview_bytes = preview_path.read_bytes()
            preview_size = read_image_size(preview_path)

        self.assertEqual(image.width, 2)
        self.assertEqual(image.height, 2)
        self.assertEqual(image.path, preview_path)
        self.assertTrue(preview_bytes.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(preview_size, (2, 2))

    def test_preview_can_render_before_and_after_color_stages(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "pixels.DNG"
            path.write_bytes(_minimal_pixel_dng_bytes())

            before = render_preview_image(path, apply_color=False)
            after = render_preview_image(path, apply_color=True)

        self.assertEqual(before.width, after.width)
        self.assertEqual(before.height, after.height)
        self.assertEqual(len(before.pixels), len(after.pixels))

    def test_native_pipeline_writes_jpeg_export_for_simple_dng(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "pixels.DNG"
            output = root / "output"
            source.write_bytes(_minimal_pixel_dng_bytes())

            result = LocalPhotoPipeline().process(PipelineRequest(source, output))
            preview_path = output / "previews" / "pixels.preview.png"
            export_path = output / "exports" / "pixels.auto.jpg"
            preview_exists = preview_path.exists()
            export_exists = export_path.exists()
            export_bytes = export_path.read_bytes()
            export_size = read_image_size(export_path)

        self.assertTrue(preview_exists)
        self.assertTrue(export_exists)
        self.assertTrue(export_bytes.startswith(b"\xff\xd8"))
        self.assertEqual(export_size, (2, 2))
        self.assertEqual(result.preview.path, preview_path)
        self.assertEqual(len(result.exports), 1)
        self.assertEqual(result.exports[0].path, export_path)
        self.assertEqual(result.exports[0].role, "export")
        self.assertEqual(result.recipe["pipeline"]["mode"], "render")
        self.assertTrue(result.recipe["pipeline"]["rendered"])
        self.assertEqual(result.recipe["exports"][0]["format"], "jpeg")

    def test_native_pipeline_records_manual_tone_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "pixels.DNG"
            output = root / "output"
            source.write_bytes(_minimal_pixel_dng_bytes())

            result = LocalPhotoPipeline().process(
                PipelineRequest(source, output, overrides={"exposure": 0.5, "contrast": 0.4, "warmth": -0.25})
            )

        raw_adjustments = result.recipe["adjustments"]["raw"]
        self.assertEqual(raw_adjustments["exposure"], 0.5)
        self.assertEqual(raw_adjustments["contrast"], 0.4)
        self.assertEqual(raw_adjustments["warmth"], -0.25)


def _minimal_dng_bytes() -> bytes:
    entries = [
        _entry_inline(256, 4, 1, _pack_long(4000)),
        _entry_inline(257, 4, 1, _pack_long(3000)),
        _entry_inline(258, 3, 1, _pack_short(16)),
        _entry_inline(259, 3, 1, _pack_short(1)),
        _entry_inline(262, 3, 1, _pack_short(32803)),
        _entry_external(271, 2, b"OpenRAW\x00"),
        _entry_external(272, 2, b"NativeCam\x00"),
        _entry_inline(277, 3, 1, _pack_short(1)),
        _entry_inline(33421, 3, 2, _pack_short(2) + _pack_short(2)),
        _entry_inline(33422, 1, 4, bytes([0, 1, 1, 2])),
        _entry_inline(50706, 1, 4, bytes([1, 4, 0, 0])),
        _entry_external(50708, 2, b"OpenRAW NativeCam\x00"),
        _entry_inline(50714, 3, 1, _pack_short(64)),
        _entry_inline(50717, 3, 1, _pack_short(4095)),
        _entry_external(50721, 10, _pack_srationals([(1, 1), (0, 1), (0, 1), (0, 1), (1, 1), (0, 1), (0, 1), (0, 1), (1, 1)])),
        _entry_external(50728, 5, _pack_rationals([(1, 2), (1, 1), (3, 4)])),
        _entry_inline(50778, 3, 1, _pack_short(21)),
    ]
    return _build_tiff(entries)


def _minimal_pixel_dng_bytes() -> bytes:
    pixel_bytes = _pack_shorts([64, 1024, 2048, 4095])
    entries = [
        _entry_inline(256, 4, 1, _pack_long(2)),
        _entry_inline(257, 4, 1, _pack_long(2)),
        _entry_inline(258, 3, 1, _pack_short(16)),
        _entry_inline(259, 3, 1, _pack_short(1)),
        _entry_inline(262, 3, 1, _pack_short(32803)),
        _entry_inline(277, 3, 1, _pack_short(1)),
        _entry_inline(278, 4, 1, _pack_long(2)),
        _entry_pixel_offset(273),
        _entry_inline(279, 4, 1, _pack_long(len(pixel_bytes))),
        _entry_inline(33421, 3, 2, _pack_short(2) + _pack_short(2)),
        _entry_inline(33422, 1, 4, bytes([0, 1, 1, 2])),
        _entry_inline(50706, 1, 4, bytes([1, 4, 0, 0])),
        _entry_inline(50714, 3, 1, _pack_short(64)),
        _entry_inline(50717, 3, 1, _pack_short(4095)),
    ]
    return _build_tiff(entries, trailing_payload=pixel_bytes)


def _build_tiff(entries: list[dict], trailing_payload: bytes = b"") -> bytes:
    header = b"II" + struct.pack("<H", 42) + struct.pack("<I", 8)
    ifd_size = 2 + len(entries) * 12 + 4
    data_offset = 8 + ifd_size
    external_data = bytearray()
    encoded_entries = []

    for entry in entries:
        if entry["inline"]:
            encoded_entries.append(
                struct.pack("<HHI", entry["tag"], entry["field_type"], entry["count"]) + entry["payload"].ljust(4, b"\x00")
            )
        elif entry.get("pixel_offset"):
            offset = data_offset + len(external_data)
            encoded_entries.append(struct.pack("<HHII", entry["tag"], entry["field_type"], entry["count"], offset))
        else:
            payload = entry["payload"]
            offset = data_offset + len(external_data)
            encoded_entries.append(struct.pack("<HHII", entry["tag"], entry["field_type"], entry["count"], offset))
            external_data.extend(payload)
            if len(external_data) % 2:
                external_data.extend(b"\x00")

    ifd = struct.pack("<H", len(entries)) + b"".join(encoded_entries) + struct.pack("<I", 0)
    return header + ifd + bytes(external_data) + trailing_payload


def _entry_inline(tag: int, field_type: int, count: int, payload: bytes) -> dict:
    return {"tag": tag, "field_type": field_type, "count": count, "payload": payload, "inline": True}


def _entry_external(tag: int, field_type: int, payload: bytes) -> dict:
    type_size = {2: 1, 5: 8, 10: 8}[field_type]
    return {"tag": tag, "field_type": field_type, "count": len(payload) // type_size, "payload": payload, "inline": False}


def _entry_pixel_offset(tag: int) -> dict:
    return {"tag": tag, "field_type": 4, "count": 1, "payload": b"", "inline": False, "pixel_offset": True}


def _pack_short(value: int) -> bytes:
    return struct.pack("<H", value)


def _pack_long(value: int) -> bytes:
    return struct.pack("<I", value)


def _pack_shorts(values: list[int]) -> bytes:
    return b"".join(_pack_short(value) for value in values)


def _pack_rationals(values: list[tuple[int, int]]) -> bytes:
    return b"".join(struct.pack("<II", numerator, denominator) for numerator, denominator in values)


def _pack_srationals(values: list[tuple[int, int]]) -> bytes:
    return b"".join(struct.pack("<ii", numerator, denominator) for numerator, denominator in values)


if __name__ == "__main__":
    unittest.main()
