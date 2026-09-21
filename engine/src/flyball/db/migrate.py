"""Apply the numbered SQL files in `flyball/db/migrations` in order.

Each file (`NNNN_name.sql`) is one transaction; `schema_version` records the
last applied.
"""

from __future__ import annotations

import re
import sqlite3
from importlib import resources
from pathlib import Path

from .errors import SchemaError

_MIGRATIONS = resources.files("flyball.db") / "migrations"
_NAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


def available() -> dict[int, Path]:
    """Version -> file, for every migration shipped with the package."""
    found: dict[int, Path] = {}
    for entry in _MIGRATIONS.iterdir():
        if (match := _NAME.match(entry.name)) is not None:
            version = int(match.group(1))
            if version in found:
                raise SchemaError(f"two migrations claim version {version}")
            found[version] = Path(str(entry))
    return found


def current(connection: sqlite3.Connection) -> int:
    """The applied version; 0 for an empty database."""
    row = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'schema_version'"
    ).fetchone()
    if row is None:
        return 0
    version = connection.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
    return int(version or 0)


def migrate(connection: sqlite3.Connection) -> int:
    """Bring `connection` up to the newest migration. Returns the version now in force."""
    applied = current(connection)
    pending = sorted(v for v in available() if v > applied)
    for version in pending:
        sql = available()[version].read_text()
        try:
            with connection:
                connection.executescript("BEGIN;\n" + sql + "\nCOMMIT;")
                connection.execute("DELETE FROM schema_version")
                connection.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
        except sqlite3.Error as e:
            raise SchemaError(f"migration {version:04d} failed: {e}") from e
    return current(connection)
