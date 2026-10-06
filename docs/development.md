# Development

## Setup

You need [uv](https://docs.astral.sh/uv/). It installs the right Python and every
dependency into `.venv`:

```bash
git clone https://github.com/drobertson-dev/FileFalconPro.git
cd FileFalconPro
uv sync
```

## Everyday commands

| Command | What it does |
| --- | --- |
| `make run` | Start the app from source |
| `make test` | Run the test suite (`uv run pytest`) |
| `make lint` | Check linting and formatting with ruff |
| `make format` | Fix lint issues and reformat |
| `make check` | Self-test the image libraries (`main.py --check`) |
| `make app` | Build `dist/File Falcon Pro.app` |
| `make install` | Build and copy the app into `/Applications` |
| `make dmg` | Build a disk image for sharing |
| `make screenshots` | Regenerate `docs/images` from a generated demo library |
| `make icon` | Regenerate `assets/icon.png` and `assets/icon.icns` |
| `make clean` | Remove build output and caches |

## Code layout

See [How it works](how-it-works.md) for an overview. The short version: `operations/` holds
all the logic and never imports Qt, so it can be tested quickly and in isolation; `gui/`
is the PyQt6 interface on top.

## Style

- Formatting and linting are done by [ruff](https://docs.astral.sh/ruff/); run
  `make format` before committing.
- The project uses **tabs** for indentation and a 100-character line length (configured in
  `pyproject.toml`).
- Keep `operations/` free of Qt imports, and keep slow work off the UI thread by running it
  through `gui.workers.Task`.

## Tests

```bash
make test
```

- `tests/test_organize.py` covers scanning, capture dates, planning (layouts, filters,
  clashes, in-place sorting), running, cancelling and undoing.
- `tests/test_duplicates.py` builds a synthetic photo library and checks exact, similar,
  rotated and cropped matches, cache consistency and that unrelated photos never match.

Tests never touch the real Trash: they swap in a fake `send2trash`. Do the same in any
script that exercises undo or the Duplicates page.

## Building the macOS app

```bash
make app        # → dist/File Falcon Pro.app
make install    # → /Applications/File Falcon Pro.app
make dmg        # → dist/FileFalconPro-<version>-macOS-<arch>.dmg
```

These run `scripts/build_macos.sh`, which uses [PyInstaller](https://pyinstaller.org) with
the recipe in `packaging/FileFalconPro.spec`. The result is a self-contained app bundle
(about 220 MB) with Python, Qt, OpenCV and the image libraries inside. It is built for the
architecture of the Mac you build on (Apple silicon or Intel).

After building, verify the bundle's libraries work:

```bash
"dist/File Falcon Pro.app/Contents/MacOS/File Falcon Pro" --check
```

### Signing

PyInstaller signs the app ad hoc, which is enough to run it on the Mac that built it. To
share it with other people without Gatekeeper warnings, sign it with a Developer ID
certificate and notarize it:

```bash
codesign --deep --force --options runtime --sign "Developer ID Application: …" "dist/File Falcon Pro.app"
xcrun notarytool submit dist/FileFalconPro-*.dmg --keychain-profile <profile> --wait
xcrun stapler staple dist/FileFalconPro-*.dmg
```

## Releasing

1. Bump `APP_VERSION` in `config.py` and `version` in `pyproject.toml`.
2. Add the changes to `CHANGELOG.md`.
3. Commit, tag (`git tag v2.1.0`) and push with tags.
4. `make dmg` and attach the disk image to a GitHub release.

## Continuous integration

`.github/workflows/ci.yml` runs ruff and the tests on Ubuntu and macOS for every push and
pull request, then builds the macOS app, runs its `--check` self-test and uploads the disk
image as a build artifact.
