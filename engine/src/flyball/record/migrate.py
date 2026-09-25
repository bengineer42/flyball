"""Apply the numbered SQL files in `flyball/record/migrations` in order.

Each file (`NNNN_name.sql`) is one transaction, together with the
`schema_version` row that records it as the last applied. `0001_initial.sql` is
the baseline: it makes today's schema on an empty file and stamps it with
[APPLICATION_ID][flyball.record.migrate.APPLICATION_ID]. A store without the
stamp was made before the baseline and is refused, never read.
"""

from __future__ import annotations

import re
import sqlite3
from functools import cache
from importlib import resources
from pathlib import Path

from .errors import SchemaError

_MIGRATIONS = resources.files("flyball.record") / "migrations"
_NAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")

APPLICATION_ID = 0x666C7962
"""`PRAGMA application_id` of a store made from the baseline: "flyb"."""

PRE_RESET = (
    "this store was made by a pre-reset flyball (before the R1 rename); delete it:"
    " nothing in it needs keeping (D-064)"
)
"""Why a store with tables but without the baseline's stamp is refused."""


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


def check(connection: sqlite3.Connection) -> None:
    """Refuse a database this flyball did not make: an empty one, or one it stamped, passes.

    Raises:
        SchemaError: The file has tables but not the baseline's `application_id`: a store
            from before the baseline (it has a `schema_version`), or not a flyball store.
    """
    tables = {
        name
        for (name,) in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    if not tables:
        return
    (stamp,) = connection.execute("PRAGMA application_id").fetchone()
    if stamp == APPLICATION_ID:
        return
    if "schema_version" in tables:
        raise SchemaError(PRE_RESET)
    raise SchemaError("not a flyball store: it holds tables flyball did not make")


OLDER = (
    "this store was made by an older flyball: {missing}; move it aside or delete it:"
    " nothing in it needs keeping (D-064)"
)
"""Why a stamped store whose tables lack columns the baseline now makes is refused."""


@cache
def _expected() -> dict[str, frozenset[str]]:
    """Each table's columns as the migrations shipped here make them, from an empty database."""
    scratch = sqlite3.connect(":memory:")
    try:
        for version in sorted(available()):
            scratch.executescript(available()[version].read_text(encoding="utf-8"))
        return _columns(scratch)
    finally:
        scratch.close()


def _columns(connection: sqlite3.Connection) -> dict[str, frozenset[str]]:
    tables = [
        name
        for (name,) in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    return {
        table: frozenset(row[1] for row in connection.execute(f'PRAGMA table_info("{table}")'))
        for table in tables
    }


def check_columns(connection: sqlite3.Connection) -> None:
    """Refuse a store at this version whose tables lack columns this flyball's baseline makes.

    Until release the baseline is edited in place (D-064), so a store made by the
    baseline as it was carries the same version and stamp and would open, then fail on
    its first read or write of a new column.

    Raises:
        SchemaError: A table, or a column of one, is missing.
    """
    have = _columns(connection)
    missing = [
        f"no table {table}" if table not in have else f"{table} has no {', '.join(sorted(cols))}"
        for table, wanted in sorted(_expected().items())
        if (cols := wanted - have.get(table, frozenset())) or table not in have
    ]
    if missing:
        raise SchemaError(OLDER.format(missing="; ".join(missing)))


def migrate(connection: sqlite3.Connection) -> int:
    """Bring `connection` up to the newest migration. Returns the version now in force.

    Raises:
        SchemaError: The store was made before the baseline, or is not a flyball store
            ([check][flyball.record.migrate.check]); or it is at a version newer than any
            shipped here: a newer flyball wrote it, and this one would misread what it
            cannot know; or its tables lack columns the baseline makes: an older flyball
            made it before the baseline was edited
            ([check_columns][flyball.record.migrate.check_columns]).
    """
    check(connection)
    applied = current(connection)
    newest = max(available(), default=0)
    if applied > newest:
        raise SchemaError(
            f"the store is at schema version {applied}, newer than this flyball's {newest}:"
            " open it with the flyball that wrote it"
        )
    pending = sorted(v for v in available() if v > applied)
    for version in pending:
        sql = available()[version].read_text(encoding="utf-8")
        try:
            with connection:
                # The version write is part of the script, so a failure in
                # either rolls both back rather than leaving the schema ahead
                # of its recorded version.
                connection.executescript(
                    "BEGIN;\n"
                    + sql
                    + "\n;\nDELETE FROM schema_version;\n"
                    + f"INSERT INTO schema_version (version) VALUES ({version:d});\n"
                    + "COMMIT;"
                )
        except sqlite3.Error as e:
            raise SchemaError(f"migration {version:04d} failed: {e}") from e
    check_columns(connection)
    return current(connection)
