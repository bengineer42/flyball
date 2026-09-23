# Contributing to flyball

Thanks for looking. flyball is a control library and daemon for lab rigs: it drives real
hardware, so correctness matters more here than throughput of changes.

## Licence

flyball is [MIT licensed](LICENSE). Anything you contribute is contributed under the same
licence.

## Sign your commits (DCO)

Every commit in a pull request must carry a `Signed-off-by` line:

```
Signed-off-by: Your Name <your.email@example.com>
```

`git commit -s` adds it for you once `user.name` and `user.email` are set. To stop having to
remember it, opt in to the repository hook once per clone:

```bash
git config core.hooksPath .githooks
```

That replaces your hooks directory wholesale, so move any hooks of your own into `.githooks`
first.

That line is your agreement to the [Developer Certificate of Origin](https://developercertificate.org/)
version 1.1 — in short, that you wrote the contribution or otherwise have the right to submit
it under the project's licence, and that you understand it will be public and kept
indefinitely. There is no separate agreement to sign and no account to create for it.

The name and address need to be **stable and reachable**, so that a question about a
contribution years from now reaches someone. They do not need to be a legal name: the DCO
itself asks for neither, and a handle you actually use is fine.

If you forget, `git commit --amend -s` fixes the last commit and
`git rebase --signoff <base>` fixes a branch. A pull request is checked automatically by
[`.github/workflows/dco.yml`](.github/workflows/dco.yml).

The check is on the sign-off matching **the commit's own author**, so a patch you received
from someone else and are passing on under DCO clause (c) will not pass as-is. Add your own
sign-off *and* keep theirs, and say in the pull request where the patch came from.

## Before you open a pull request

Base your work on `dev`, not `main`.

Set up once — a fresh clone has neither environment, and every command below assumes them:

```bash
cd engine && uv sync --all-extras
cd ../ui && npm install
```

Then run the checks for whatever you touched, each from the repository root. Nothing here
needs hardware.

```bash
(cd engine && make check)
(cd extensions/linux && uv run pytest -q)
(cd ui && npm run typecheck && npm run build && npm test)
```

`make check` is lint, import contracts, pyright and pytest. Extensions each have their own
suite (`bluesky`, `chips`, `linux`, `modbus`, `pymeasure`, `qcodes`, `visa`) — run the one you
changed.

Two things that are not your fault if you see them: `extensions/linux`, `extensions/modbus`
and `extensions/visa` each have one schema test failing on a clean checkout, and the Go
suite's `TestEmbeddedProgramSchemaIsCurrent` needs an interpreter it does not set up. All are
known and none are caused by anything you did.

CI checks the sign-off and builds the book. **It does not run the tests** — that run is on
you.

## What makes a change easy to accept

- **One thing per pull request.** A driver, a fix, a document.
- **Match the surrounding code.** Comment density, naming and idiom vary by layer; follow the
  file you are in rather than a global style.
- **Update the documentation in the same change.** `book/src/` is organised by reader; a new
  driver gets its own `##` in `book/src/2-config/devices/drivers.md`, a new rig-file key goes
  in `book/src/7-reference/rig-file.md`. The book must still build strict:

  ```bash
  cd engine && uv run --group docs mkdocs build -f ../book/mkdocs.yml --strict
  ```

- **Tests that fail before your change and pass after.** If a driver cannot be tested without
  the physical part, say so in the module docstring and test what can be tested against a
  fake link.

## Contributing a driver

Drivers live in `extensions/`, register through the `flyball.configs` entry point, and can
ship as their own pip package rather than in this tree — you do not need to contribute one
here to use it. `book/src/3-extending/` explains the device model.

**If you adapt code from another project, say so in the module docstring and carry its
licence and copyright notice.** Most sensor drivers in this space are MIT or BSD-3 and are
fine to adapt; they are not fine to adapt silently.

## Reporting a bug

Say what rig you were running, what you expected, what happened, and include the rig file
with any secrets removed. A failing test is the best bug report.

## Security

Do not open a public issue for a vulnerability. See [SECURITY.md](SECURITY.md).
