"""Retain the native source package for content-checked frozen JIT caches."""

# Adding .py as data while retaining PYZ bytecode leaves virtual co_filenames.
module_collection_mode = {"openraw_studio.raw.native": "py"}
