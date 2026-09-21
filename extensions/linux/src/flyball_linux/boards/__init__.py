"""Board profiles shipped as package data, found through the `flyball.board_dirs` entry point."""

from __future__ import annotations

from pathlib import Path

board_dir = Path(__file__).parent

__all__ = ["board_dir"]
