.PHONY: test

test:
	uv run pytest -q
	uv run ruff check .
	uv run ruff format --check .
	uv run pyright
