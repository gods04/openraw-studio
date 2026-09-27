import unittest
from fractions import Fraction

from openraw_studio.export.metadata import extract_derivative_photo_metadata


class ExportMetadataTests(unittest.TestCase):
    def test_extracts_safe_photographic_fields_from_recipe(self) -> None:
        recipe = {
            "source": {
                "metadata": {
                    "camera_make": "NIKON CORPORATION",
                    "camera_model": "NIKON D500",
                    "lens_model": "35mm f/1.8",
                    "captured_at": "2026:08:09 20:10:52",
                    "iso": 320,
                    "exposure_time": 0.025,
                    "aperture": 2.8,
                    "focal_length_mm": 35.0,
                    "gps_latitude": -33.9,
                }
            }
        }

        metadata = extract_derivative_photo_metadata(recipe)

        self.assertEqual(metadata.make, "NIKON CORPORATION")
        self.assertEqual(metadata.model, "NIKON D500")
        self.assertEqual(metadata.lens_model, "35mm f/1.8")
        self.assertEqual(metadata.captured_at, "2026:08:09 20:10:52")
        self.assertEqual(metadata.iso, 320)
        self.assertEqual(metadata.exposure_time, Fraction(1, 40))
        self.assertEqual(metadata.aperture, Fraction(14, 5))
        self.assertEqual(metadata.focal_length, Fraction(35, 1))
        self.assertFalse(hasattr(metadata, "gps_latitude"))

    def test_normalizes_iso_datetime_and_ignores_invalid_values(self) -> None:
        recipe = {
            "source": {
                "metadata": {
                    "make": "OpenRAW\N{TRADE MARK SIGN}",
                    "captured_at": "2026-09-27T21:42:00+02:00",
                    "iso": "800",
                    "exposure_time": float("nan"),
                    "aperture": -1,
                }
            }
        }

        metadata = extract_derivative_photo_metadata(recipe)

        self.assertEqual(metadata.make, "OpenRAW?")
        self.assertEqual(metadata.captured_at, "2026:09:27 21:42:00")
        self.assertEqual(metadata.iso, 800)
        self.assertIsNone(metadata.exposure_time)
        self.assertIsNone(metadata.aperture)

    def test_accepts_fraction_text_from_alternate_metadata_sources(self) -> None:
        recipe = {"source": {"metadata": {"exposure_time": "1/125"}}}

        metadata = extract_derivative_photo_metadata(recipe)

        self.assertEqual(metadata.exposure_time, Fraction(1, 125))


if __name__ == "__main__":
    unittest.main()
