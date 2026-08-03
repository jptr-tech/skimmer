.PHONY: help test lint format check clean install dev macos

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

test: ## Run tests, lint, and typecheck
	uv run pytest -q
	uv run ruff check .
	uv run ruff format --check .
	uv run pyright

lint: ## Run linter only (fast)
	uv run ruff check .
	uv run ruff format --check .

format: ## Auto-format code
	uv run ruff check --fix .
	uv run ruff format .

check: ## Run linter + typecheck (no tests)
	uv run ruff check .
	uv run ruff format --check .
	uv run pyright

clean: ## Remove build artifacts
	rm -rf dist/ build/ .pytest_cache/
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

install: ## Install dependencies
	uv sync

dev: ## Install with dev dependencies
	uv sync --all-extras

macos: ## Build macOS .app and .dmg
	./build-aux/macos/build-app.sh
	./build-aux/macos/make-dmg.sh