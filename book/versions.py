"""Build every published version of both books into one site tree.

    python book/versions.py OUT [--dev]

For each tag `vX.Y…` the repository has, the sources at that tag are checked
out into a temporary worktree and both books are built strictly from them:
the main book into `OUT/X.Y/`, the humidity book into `OUT/humidity/X.Y/`.
`--dev` also builds the working tree as `dev`. The newest tag is `latest`;
the root and `humidity/` redirect to it (to `dev` while there is no tag). A
`versions.json` beside each tree is what Material's version selector reads
(the `mike` format, without mike). `robots.txt` and `.nojekyll` are copied
to the root. Nothing built is ever committed: the site is regenerated from
sources on every run.

Runs with the controller's docs environment; the humidity book is built by
the same MkDocs, since mkdocstrings reads sources from the paths each
config names rather than importing them.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOOKS = {"": "book/mkdocs.yml", "humidity": "examples/humidity/book/mkdocs.yml"}
REDIRECT = '<!doctype html><meta http-equiv="refresh" content="0; url={to}/"><a href="{to}/">{to}</a>\n'


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout


def tags() -> list[tuple[str, str]]:
    """(version, tag) newest last, for every tag that looks like v1, v1.2, v1.2.3."""
    found = []
    for tag in git("tag", "-l", "v*").split():
        if re.fullmatch(r"v\d+(\.\d+)*", tag):
            found.append((tuple(int(n) for n in tag[1:].split(".")), tag))
    return [(tag[1:], tag) for _, tag in sorted(found)]


def build(source: Path, out: Path, version: str) -> None:
    for prefix, config in BOOKS.items():
        target = out / prefix / version if prefix else out / version
        subprocess.run(
            [sys.executable, "-m", "mkdocs", "build", "-f", str(source / config), "--strict", "--site-dir", str(target)],
            check=True,
        )


def versions_json(out: Path, versions: list[str], latest: str | None, dev: bool) -> None:
    entries = [{"version": v, "title": v, "aliases": ["latest"] if v == latest else []} for v in reversed(versions)]
    if dev:
        entries.insert(0, {"version": "dev", "title": "dev", "aliases": []})
    default = latest or ("dev" if dev else None)
    for prefix in BOOKS:
        tree = out / prefix if prefix else out
        tree.mkdir(parents=True, exist_ok=True)
        (tree / "versions.json").write_text(json.dumps(entries, indent=2) + "\n")
        if latest:
            # `latest` is a copy, not a symlink: Pages serves files.
            shutil.copytree(tree / latest, tree / "latest", dirs_exist_ok=True)
        if default:
            (tree / "index.html").write_text(REDIRECT.format(to=default))


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("out", type=Path)
    p.add_argument("--dev", action="store_true", help="also build the working tree as `dev`")
    args = p.parse_args(argv)
    out = args.out.resolve()
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    built = []
    for version, tag in tags():
        with tempfile.TemporaryDirectory(prefix="book-") as tmp:
            git("worktree", "add", "--detach", tmp, tag)
            try:
                build(Path(tmp), out, version)
            finally:
                git("worktree", "remove", "--force", tmp)
        built.append(version)
        print(f"built {version} from {tag}")
    if args.dev:
        build(ROOT, out, "dev")
        print("built dev from the working tree")
    if not built and not args.dev:
        print("nothing to build: no v* tag, and --dev not given", file=sys.stderr)
        return 2
    versions_json(out, built, built[-1] if built else None, args.dev)
    shutil.copy(ROOT / "book" / "robots.txt", out / "robots.txt")
    (out / ".nojekyll").write_text("")
    print(f"site at {out}: {', '.join(built + (['dev'] if args.dev else []))}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
