"""Discovery helpers for external RAW backends."""

from __future__ import annotations

from dataclasses import dataclass
from shutil import which
import subprocess


@dataclass(frozen=True)
class BackendCheck:
    """Availability check for an external processing backend."""

    name: str
    available: bool
    executable: str | None = None
    version: str | None = None
    message: str = ""


def check_darktable_cli(executable: str | None = None) -> BackendCheck:
    """Check whether darktable-cli is available on this machine."""

    candidate = executable or which("darktable-cli")
    if candidate is None:
        return BackendCheck(
            name="darktable-cli",
            available=False,
            message="darktable-cli was not found on PATH.",
        )

    try:
        completed = subprocess.run(
            [candidate, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except OSError as exc:
        return BackendCheck(
            name="darktable-cli",
            available=False,
            executable=candidate,
            message=f"Could not run darktable-cli: {exc}",
        )
    except subprocess.TimeoutExpired:
        return BackendCheck(
            name="darktable-cli",
            available=False,
            executable=candidate,
            message="darktable-cli --version timed out.",
        )

    version_text = (completed.stdout or completed.stderr).strip().splitlines()
    version = version_text[0] if version_text else None
    return BackendCheck(
        name="darktable-cli",
        available=completed.returncode == 0,
        executable=candidate,
        version=version,
        message="darktable-cli is available." if completed.returncode == 0 else "darktable-cli returned a non-zero status.",
    )
