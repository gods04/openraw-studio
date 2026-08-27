"""darktable-cli RAW processor adapter."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
from typing import Callable

from openraw_studio.core.domain import EngineInfo, ImageAsset, ImageMetadata, ImageRef, RawInspection
from openraw_studio.core.files import sha256_file, source_file_metadata
from openraw_studio.core.image_info import read_image_size
from openraw_studio.raw.backends import BackendCheck, check_darktable_cli
from openraw_studio.raw.errors import RawProcessingError
from openraw_studio.raw.interfaces import RawRenderRequest

CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class DarktableExportOptions:
    """Options passed to darktable-cli exports."""

    max_width: int | None = None
    max_height: int | None = None
    high_quality: bool = True
    upscale: bool = False
    apply_custom_presets: bool = False


class DarktableCliProcessor:
    """RAW processor backed by a user-installed darktable-cli executable."""

    def __init__(
        self,
        *,
        executable: str | None = None,
        backend_check: BackendCheck | None = None,
        runner: CommandRunner = subprocess.run,
        timeout_seconds: int = 300,
    ) -> None:
        self._executable = executable
        self._runner = runner
        self._timeout_seconds = timeout_seconds
        self._check: BackendCheck | None = backend_check

    def engine_info(self) -> EngineInfo:
        check = self.check()
        return EngineInfo(
            name="darktable-cli-raw",
            version="0.1.0",
            backend=check.version or check.name,
            capabilities={
                "available": check.available,
                "executable": check.executable,
                "preview": True,
                "base_render": True,
                "metadata": "filesystem-only-v0.1",
            },
        )

    def check(self) -> BackendCheck:
        if self._check is None:
            self._check = check_darktable_cli(self._executable)
        return self._check

    def inspect(self, source: ImageAsset) -> RawInspection:
        metadata = source_file_metadata(source.path)
        metadata["checksum_sha256"] = source.checksum_sha256 or sha256_file(source.path)
        return RawInspection(
            source=source,
            metadata=ImageMetadata(raw=metadata),
            engine=self.engine_info(),
        )

    def create_preview(self, source: ImageAsset, output_path: Path, max_dimension: int) -> ImageRef:
        return self._export(
            source=source.path,
            output_path=output_path,
            role="preview",
            options=DarktableExportOptions(max_width=max_dimension, max_height=max_dimension),
        )

    def render_base(self, request: RawRenderRequest) -> ImageRef:
        return self._export(
            source=request.source.path,
            output_path=request.output_path,
            role="base",
            options=DarktableExportOptions(
                max_width=request.max_dimension,
                max_height=request.max_dimension,
            ),
        )

    def export_intermediate(self, request: RawRenderRequest) -> ImageRef:
        return self.render_base(request)

    def _export(
        self,
        *,
        source: Path,
        output_path: Path,
        role: str,
        options: DarktableExportOptions,
    ) -> ImageRef:
        check = self.check()
        if not check.available or check.executable is None:
            raise RawProcessingError(check.message or "darktable-cli is not available.")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        command = self._build_export_command(check.executable, source, output_path, options)
        try:
            completed = self._runner(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=self._timeout_seconds,
            )
        except OSError as exc:
            raise RawProcessingError(f"Could not run darktable-cli: {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise RawProcessingError(f"darktable-cli timed out after {self._timeout_seconds} seconds.") from exc

        if completed.returncode != 0:
            stderr = (completed.stderr or completed.stdout or "").strip()
            detail = f": {stderr}" if stderr else ""
            raise RawProcessingError(f"darktable-cli export failed{detail}")
        if not output_path.exists():
            raise RawProcessingError(f"darktable-cli finished but did not create {output_path}")

        width, height = read_image_size(output_path)
        return ImageRef(
            path=output_path,
            width=width,
            height=height,
            color_space="sRGB",
            role=role,
        )

    @staticmethod
    def _build_export_command(
        executable: str,
        source: Path,
        output_path: Path,
        options: DarktableExportOptions,
    ) -> list[str]:
        command = [executable, str(source), str(output_path)]
        if options.max_width is not None:
            command.extend(["--width", str(options.max_width)])
        if options.max_height is not None:
            command.extend(["--height", str(options.max_height)])
        command.extend(["--hq", _bool_arg(options.high_quality)])
        command.extend(["--upscale", _bool_arg(options.upscale)])
        command.extend(["--apply-custom-presets", _bool_arg(options.apply_custom_presets)])
        return command


def _bool_arg(value: bool) -> str:
    return "true" if value else "false"
