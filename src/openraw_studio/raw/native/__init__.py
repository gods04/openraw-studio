"""OpenRAW Native RAW engine package."""

from openraw_studio.raw.native.dng import DngMetadataReader, EmbeddedPreview
from openraw_studio.raw.native.color import (
    ColorTransformError,
    apply_as_shot_neutral,
    apply_camera_matrix,
    camera_profile_to_linear_srgb_matrix,
)
from openraw_studio.raw.native.decoder import NativeRawDecoder
from openraw_studio.raw.native.demosaic import LinearRgbImage, demosaic_simple
from openraw_studio.raw.native.engine import NativeRawProcessor
from openraw_studio.raw.native.fullres import FullResolutionRgbImage, render_bayer_full_resolution, render_bayer_full_resolution_rgb8
from openraw_studio.raw.native.jpeg import write_jpeg
from openraw_studio.raw.native.nikon import (
    NIKON_COMPRESSED_RAW,
    NikonCompressionError,
    NikonDecodedPixelData,
    NikonMakerNoteSummary,
    NikonRenderedRgbImage,
    NikonWhiteBalance,
    can_decode_nikon_34713_lossless,
    decode_nikon_34713_lossless,
    extract_nikon_as_shot_white_balance,
    render_decoded_nikon_34713_image,
    render_decoded_nikon_34713_to_file,
    render_nikon_34713_to_file,
    summarize_nikon_makernote,
    summarize_nikon_makernote_payload,
)
from openraw_studio.raw.native.png import encode_png_rgb8, write_png
from openraw_studio.raw.native.profiles import CameraColorProfile, find_camera_color_profile
from openraw_studio.raw.native.preview import render_png_preview, render_ppm_preview, render_preview_image, resize_preview, write_ppm
from openraw_studio.raw.native.sensor import LinearSensorImage, normalize_sensor_data
from openraw_studio.raw.native.support import NativeSupportReport, inspect_native_support
from openraw_studio.raw.native.synthetic import (
    synthetic_dng_bytes,
    synthetic_nikon_nef_bytes,
    write_synthetic_dng,
    write_synthetic_nikon_nef,
)
from openraw_studio.raw.native.tone import PreviewRgbImage, tone_map_preview
from openraw_studio.raw.native.tiff import write_tiff_rgb8, write_tiff_rgb16

__all__ = [
    "DngMetadataReader",
    "EmbeddedPreview",
    "FullResolutionRgbImage",
    "ColorTransformError",
    "CameraColorProfile",
    "LinearRgbImage",
    "LinearSensorImage",
    "NativeRawDecoder",
    "NativeRawProcessor",
    "NativeSupportReport",
    "NIKON_COMPRESSED_RAW",
    "NikonCompressionError",
    "NikonDecodedPixelData",
    "NikonMakerNoteSummary",
    "NikonRenderedRgbImage",
    "NikonWhiteBalance",
    "PreviewRgbImage",
    "demosaic_simple",
    "apply_as_shot_neutral",
    "apply_camera_matrix",
    "camera_profile_to_linear_srgb_matrix",
    "encode_png_rgb8",
    "can_decode_nikon_34713_lossless",
    "decode_nikon_34713_lossless",
    "extract_nikon_as_shot_white_balance",
    "find_camera_color_profile",
    "render_decoded_nikon_34713_image",
    "render_decoded_nikon_34713_to_file",
    "render_bayer_full_resolution_rgb8",
    "render_bayer_full_resolution",
    "render_nikon_34713_to_file",
    "normalize_sensor_data",
    "inspect_native_support",
    "render_png_preview",
    "render_ppm_preview",
    "render_preview_image",
    "resize_preview",
    "summarize_nikon_makernote",
    "summarize_nikon_makernote_payload",
    "synthetic_dng_bytes",
    "synthetic_nikon_nef_bytes",
    "tone_map_preview",
    "write_synthetic_dng",
    "write_synthetic_nikon_nef",
    "write_jpeg",
    "write_png",
    "write_ppm",
    "write_tiff_rgb8",
    "write_tiff_rgb16",
]
