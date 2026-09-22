# The checks, in the order they are quickest to fail. `make check` before a commit.
.PHONY: check lint imports types test format

check: lint imports types test

lint:
	uv run ruff check src tests
	uv run ruff format --check src tests

format:
	uv run ruff format src tests
	uv run ruff check --fix src tests

imports:
	uv run lint-imports

types:
	uvx pyright --pythonpath .venv/bin/python src/flyball

test:
	uv run pytest -q -p no:cacheprovider
