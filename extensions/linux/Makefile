.PHONY: check lint types test format

check: lint types test

lint:
	uv run ruff check src tests
	uv run ruff format --check src tests

format:
	uv run ruff format src tests
	uv run ruff check --fix src tests

types:
	uvx pyright --pythonpath .venv/bin/python src/flyball_linux

test:
	uv run pytest -q -p no:cacheprovider
