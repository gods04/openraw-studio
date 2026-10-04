"""Publish verified local model artifacts, including redirected Windows folders."""

import errno
from pathlib import Path
import shutil

from openraw_studio.core.files import sha256_file


def publish_model_artifact(source: Path, destination: Path):
    expected = sha256_file(source)
    if destination.is_file() and sha256_file(destination) == expected:
        source.unlink()
        return
    try:
        source.replace(destination)
    except OSError as error:
        if error.errno != errno.EXDEV and getattr(error, "winerror", None) != 17:
            raise
        # Runtime hash validation rejects an incomplete redirected-folder copy.
        shutil.copyfile(source, destination)
        if sha256_file(destination) != expected:
            raise RuntimeError("Published model artifact checksum mismatch") from error
        source.unlink()
