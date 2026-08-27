"""PyInstaller entry point for the OpenRAW Studio desktop app."""

from __future__ import annotations

from openraw_studio.ui.desktop import launch_desktop_app


def main() -> None:
    launch_desktop_app()


if __name__ == "__main__":
    main()
