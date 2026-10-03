"""Load our JIT kernels as source so Numba can locate persistent caches."""

# Adding .py as data while retaining PYZ bytecode leaves virtual co_filenames.
module_collection_mode = {
    "openraw_studio.raw.native.compiled_decode": "py",
    "openraw_studio.raw.native.compiled_he": "py",
    "openraw_studio.raw.native.compiled_he_transform": "py",
    "openraw_studio.raw.native.compiled_tone": "py",
    "openraw_studio.raw.native.compiled_bayer": "py",
    "openraw_studio.raw.native.compiled_chroma": "py",
    "openraw_studio.raw.native.compiled_luminance": "py",
}
