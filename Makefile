.PHONY: run check test lint format app install dmg icon screenshots clean

run:  ## Start the app from source
	uv run python main.py

check:  ## Self-test the image libraries
	uv run python main.py --check

test:  ## Run the test suite
	uv run pytest

lint:  ## Check linting and formatting
	uv run ruff check .
	uv run ruff format --check .

format:  ## Fix lint issues and reformat
	uv run ruff check --fix .
	uv run ruff format .

app:  ## Build dist/File Falcon Pro.app
	./scripts/build_macos.sh

install:  ## Build the app and copy it into /Applications
	./scripts/build_macos.sh --install

dmg:  ## Build a disk image for sharing
	./scripts/build_macos.sh --dmg

icon:  ## Regenerate assets/icon.png and assets/icon.icns
	QT_QPA_PLATFORM=offscreen uv run python scripts/make_icon.py

screenshots:  ## Regenerate docs/images
	uv run python scripts/screenshots.py

clean:  ## Remove build output and caches
	rm -rf build dist .pytest_cache .ruff_cache
	find . -name __pycache__ -type d -prune -not -path "./.venv/*" -exec rm -rf {} +
