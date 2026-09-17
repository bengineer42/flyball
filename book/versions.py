"""Build the two published versions of both books into one site tree.

    python book/versions.py OUT

`latest` is the `main` branch, `dev` is the `dev` branch: each is checked out
into a temporary worktree from `origin/<branch>` (the working tree itself
when that is what is checked out) and both books are built strictly from it
-- the main book into `OUT/<version>/`, the humidity book into
`OUT/humidity/<version>/`. A `versions.json` beside each tree is what
Material's version selector reads; the root and `humidity/` redirect to
`latest`. `robots.txt` and `.nojekyll` are copied to the root. Nothing built
is ever committed: the whole site is regenerated from the two branches on
every run, whichever of them triggered it.

Runs with the controller's docs environment; the humidity book is built by
the same MkDocs, since mkdocstrings reads sources from the paths each
config names rather than importing them.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOOKS = {"": "book/mkdocs.yml", "humidity": "examples/humidity/book/mkdocs.yml"}
VERSIONS = {"latest": "main", "dev": "dev"}  # version name -> branch
REDIRECT = '<!doctype html><meta http-equiv="refresh" content="0; url={to}/"><a href="{to}/">{to}</a>\n'


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()


def ref_for(branch: str) -> str | None:
    """`origin/<branch>` if fetched, else the local branch, else None."""
    for ref in (f"origin/{branch}", branch):
        if subprocess.run(["git", "rev-parse", "--verify", "-q", ref], cwd=ROOT, capture_output=True).returncode == 0:
            return ref
    return None


def build(source: Path, out: Path, version: str) -> None:
    for prefix, config in BOOKS.items():
        target = out / prefix / version if prefix else out / version
        subprocess.run(
            [sys.executable, "-m", "mkdocs", "build", "-f", str(source / config), "--strict", "--site-dir", str(target)],
            check=True,
        )


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("out", type=Path)
    args = p.parse_args(argv)
    out = args.out.resolve()
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    built = []
    for version, branch in VERSIONS.items():
        ref = ref_for(branch)
        if ref is None:
            print(f"no branch {branch}: skipping {version}", file=sys.stderr)
            continue
        with tempfile.TemporaryDirectory(prefix=f"book-{version}-") as tmp:
            git("worktree", "add", "--detach", tmp, ref)
            try:
                build(Path(tmp), out, version)
            finally:
                git("worktree", "remove", "--force", tmp)
        built.append(version)
        print(f"built {version} from {ref} ({git('rev-parse', '--short', ref)})")
    if not built:
        return 2
    default = "latest" if "latest" in built else built[0]
    entries = [{"version": v, "title": v, "aliases": []} for v in built]
    for prefix in BOOKS:
        tree = out / prefix if prefix else out
        (tree / "versions.json").write_text(json.dumps(entries, indent=2) + "\n")
        (tree / "index.html").write_text(REDIRECT.format(to=default))
    shutil.copy(ROOT / "book" / "robots.txt", out / "robots.txt")
    (out / ".nojekyll").write_text("")
    print(f"site at {out}: {', '.join(built)}; root -> {default}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
