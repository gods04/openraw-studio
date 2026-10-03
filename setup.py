"""Optional ahead-of-time builds of OpenRAW-owned kernels."""

import os

from setuptools import Extension, setup


setup(
    ext_modules=[] if os.environ.get("OPENRAW_BUILD_HE_CPU") == "off" else [
        Extension(
            "openraw_studio.raw.native._he_cpu",
            ["src/openraw_studio/raw/native/_he_cpu.cpp"],
            language="c++",
            extra_compile_args=["/std:c++14"] if os.name == "nt" else ["-std=c++11"],
            optional=True,
        )
    ],
)
