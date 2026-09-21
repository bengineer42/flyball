"""Where this package's board profiles live -- what the `flyball.board_dirs` entry point loads."""

from __future__ import annotations

from pathlib import Path

board_dir = Path(__file__).parent

__all__ = ["board_dir"]
