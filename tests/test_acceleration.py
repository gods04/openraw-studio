import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from fixtures_nikon import (
    pack_nikon_34713_lossless,
    synthetic_nikon_nef_compressed_bytes,
)

from openraw_studio.raw.native import compiled_decode
from openraw_studio.raw.native.acceleration import (
    color_parameters,
    get_gpu,
    render_tone,
    tone_cpu,
)
from openraw_studio.raw.native.engine import NativeRawProcessor
from openraw_studio.raw.native.fullres import render_bayer_full_resolution_rgb8
from openraw_studio.raw.native.interactive import prepare_interactive_photo
from openraw_studio.raw.native.nikon import (
    NikonCompressionError,
    NikonCompressionSetup,
    _decode_nikon_lossless_samples,
    _decode_nikon_lossless_samples_python,
    decode_nikon_34713_lossless,
    render_decoded_nikon_34713_image,
)
from openraw_studio.raw.native.preview import render_preview_image
from openraw_studio.raw.native.synthetic import write_synthetic_dng


class AccelerationTests(unittest.TestCase):
    def test_dng_live_tones_match_existing_renderer(self):
        with tempfile.TemporaryDirectory() as temp:
            source = write_synthetic_dng(Path(temp) / "sample.DNG", width=16, height=12)
            photo = prepare_interactive_photo(NativeRawProcessor(), source)
            for settings in (
                {},
                {
                    "exposure": 0.5,
                    "contrast": 0.2,
                    "warmth": -0.3,
                    "tint": 0.1,
                    "saturation": 0.4,
                    "highlights": -0.2,
                    "shadows": 0.3,
                },
            ):
                live, _ = photo.render(settings)
                reference = render_preview_image(source, **settings)
                expected = np.array(reference.pixels).reshape(
                    reference.height, reference.width, 3
                )
                self.assertLessEqual(
                    np.abs(np.array(live).astype(int) - expected).max(), 1
                )

    def test_full_resolution_gpu_failure_falls_back_without_changing_pixels(self):
        class Broken:
            def bayer(self, *args):
                raise RuntimeError("Device lost")

        options = dict(
            raw_bytes=np.arange(64, dtype=np.uint16).tobytes(),
            source_width=8,
            source_height=8,
            crop=(0, 0, 8, 8),
            cfa_pattern=(0, 1, 1, 2),
            black_levels=(0, 0, 0, 0),
            white_level=255,
            channel_gains=(1, 1, 1),
            camera_to_linear_srgb=None,
        )
        expected = render_bayer_full_resolution_rgb8(**options, use_gpu=False)
        with (
            patch(
                "openraw_studio.raw.native.acceleration.get_gpu", return_value=Broken()
            ),
            patch("openraw_studio.raw.native.acceleration.disable_gpu"),
        ):
            actual = render_bayer_full_resolution_rgb8(**options)
        self.assertEqual(actual, expected)

    def test_compiled_decode_matches_reference_and_rejects_truncation(self):
        for bits in (12, 14):
            rng = np.random.default_rng(bits)
            values = tuple(int(v) for v in rng.integers(0, 2**bits, size=64 * 32))
            payload = pack_nikon_34713_lossless(
                values, width=64, height=32, bits_per_sample=bits
            )
            initial = 512 if bits == 12 else 2048
            setup = NikonCompressionSetup(
                "F0",
                2 if bits == 12 else 5,
                ((initial, initial), (initial, initial)),
                None,
                None,
            )
            args = dict(width=64, height=32, setup=setup, maximum=2**bits - 1)
            self.assertEqual(
                _decode_nikon_lossless_samples(payload, **args),
                _decode_nikon_lossless_samples_python(payload, **args),
            )
            self.assertEqual(
                tuple(_decode_nikon_lossless_samples(payload, **args)), values
            )
            with self.assertRaises(NikonCompressionError):
                _decode_nikon_lossless_samples(payload[:8], **args)
            with patch.object(compiled_decode, "decode", None):
                self.assertEqual(
                    tuple(_decode_nikon_lossless_samples(payload, **args)), values
                )
            with patch.object(
                compiled_decode, "decode", side_effect=RuntimeError("JIT unavailable")
            ):
                self.assertEqual(
                    tuple(_decode_nikon_lossless_samples(payload, **args)), values
                )

    def test_gpu_failure_falls_back_to_cpu(self):
        class Broken:
            def tone(self, *args):
                raise RuntimeError("driver unavailable")

        pixels = np.ones((5, 6, 3), dtype=np.float32) * 0.3
        params = color_parameters(np.eye(3), (1, 1, 1), contrast=0.2, saturation=-0.1)
        with (
            patch(
                "openraw_studio.raw.native.acceleration.get_gpu", return_value=Broken()
            ),
            patch("openraw_studio.raw.native.acceleration.disable_gpu"),
        ):
            actual, name = render_tone(pixels, params)
        self.assertEqual(name, "CPU")
        np.testing.assert_array_equal(actual, tone_cpu(pixels, params))

    def test_gpu_matches_cpu_for_all_bayer_layouts_and_offset_crops(self):
        gpu = get_gpu()
        if gpu is None:
            self.skipTest("No usable OpenCL GPU")
        raw = (
            np.random.default_rng(3)
            .integers(0, 4096, (12, 18), dtype=np.uint16)
            .tobytes()
        )
        for pattern in ((0, 1, 1, 2), (1, 0, 2, 1), (1, 2, 0, 1), (2, 1, 1, 0)):
            args = dict(
                raw_bytes=raw,
                source_width=18,
                source_height=12,
                crop=(1, 1, 15, 9),
                cfa_pattern=pattern,
                black_levels=(40, 80, 120, 160),
                white_level=4095,
                channel_gains=(1.8, 1, 0.9),
                camera_to_linear_srgb=(
                    (1.3, -0.2, -0.1),
                    (-0.1, 1.2, -0.1),
                    (0.1, -0.2, 1.1),
                ),
                contrast=0.15,
                highlights=-0.3,
                shadows=0.2,
                saturation=0.4,
            )
            cpu = render_bayer_full_resolution_rgb8(**args, use_gpu=False)
            accelerated = render_bayer_full_resolution_rgb8(**args, use_gpu=True)
            delta = np.abs(
                np.frombuffer(cpu.rgb_bytes, dtype=np.uint8).astype(int)
                - np.frombuffer(accelerated.rgb_bytes, dtype=np.uint8).astype(int)
            )
            self.assertLessEqual(delta.max(), 1)

    def test_prepared_preview_reacts_without_decoding_and_matches_export_tones(self):
        # Uniform per-channel planes make the half-size preview and full-size
        # demosaic directly comparable, independent of downsampling.
        samples = tuple(
            (2400, 1800, 1800, 1200)[(r % 2) * 2 + c % 2]
            for r in range(12)
            for c in range(16)
        )
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "sample.NEF"
            source.write_bytes(
                synthetic_nikon_nef_compressed_bytes(
                    width=16,
                    height=12,
                    samples=samples,
                    active_area=(0, 0, 16, 12),
                    model="NIKON D500",
                    orientation=8,
                )
            )
            processor = NativeRawProcessor()
            photo = prepare_interactive_photo(processor, source)
            decoded = decode_nikon_34713_lossless(source)
            adjustments = dict(
                exposure=0.2,
                contrast=0.15,
                shadows=0.1,
                highlights=-0.2,
                warmth=0.2,
                tint=-0.2,
                saturation=0.3,
            )
            with patch.object(
                processor,
                "_decode_supported_nikon_34713",
                side_effect=AssertionError("decoded again"),
            ):
                live, _ = photo.render(adjustments)
                changed, _ = photo.render({**adjustments, "exposure": 1})
            full = render_decoded_nikon_34713_image(
                decoded, quality="full", **adjustments
            )
            self.assertEqual(live.size, (6, 8))
            self.assertNotEqual(live.tobytes(), changed.tobytes())
            self.assertLessEqual(
                np.abs(
                    np.array(live)[0, 0].astype(int)
                    - np.frombuffer(full.rgb_bytes, dtype=np.uint8)[:3].astype(int)
                ).max(),
                1,
            )
