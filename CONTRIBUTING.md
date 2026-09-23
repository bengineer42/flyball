# Contributing to flyball

Thanks for looking. flyball is a control library and daemon for lab rigs: it drives real
hardware, so correctness matters more here than throughput of changes.

## Licence

flyball is [MIT licensed](LICENSE). Anything you contribute is contributed under the same
licence.

## Sign your commits (DCO)

Every commit must carry a `Signed-off-by` line:

```
Signed-off-by: Your Name <your.email@example.com>
```

`git commit -s` adds it for you. Configure `user.name` and `user.email` first and it is
automatic thereafter.

That line is your agreement to the [Developer Certificate of Origin](https://developercertificate.org/)
version 1.1 — in short, that you wrote the contribution or otherwise have the right to submit
it under the project's licence, and that you understand it will be public and kept
indefinitely. There is nothing to sign and no account to create.

If you forget, `git commit --amend -s` fixes the last commit, and
`git rebase --signoff <base>` fixes a branch.

## Before you open a pull request

Run the checks for whatever you touched. Nothing here needs hardware.

```bash
cd engine && make lint imports test && uvx pyright --pythonpath .venv/bin/python src/flyball
cd extensions/linux && uv run pytest -q
cd ui && npm run typecheck && npm run build && npm test
```

Extensions each have their own suite (`bluesky`, `chips`, `linux`, `modbus`, `pymeasure`,
`qcodes`, `visa`) — run the one you changed.

## What makes a change easy to accept

- **One thing per pull request.** A driver, a fix, a document.
- **Match the surrounding code.** Comment density, naming and idiom vary by layer; follow the
  file you are in rather than a global style.
- **Update the documentation in the same change.** `book/src/` is organised by reader; a new
  driver gets its own `##` in `book/src/2-config/drivers.md`, a new rig-file key goes in
  `book/src/7-reference/rig-file.md`. Both books must still build strict:

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
