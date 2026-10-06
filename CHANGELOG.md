# Changelog

## 2.0.0 — 2026-10-06

A ground-up rebuild with a new interface.

### Added

- **New interface** with a sidebar and three pages, light and dark themes that follow the
  system, drag-and-drop folder pickers and settings that are remembered between launches.
- **Organize:** sort many file types in one run; group by type, by capture date (EXIF and
  QuickTime metadata) and/or original subfolders; filter names by text, wildcard or regex;
  live preview with per-file include/exclude; background processing with progress and
  cancel; case-insensitive clash handling; rsync-style "already there" detection.
- **Duplicates:** find exact, resized/re-saved and cropped copies of photos (perceptual
  hashing plus ORB keypoint matching); side-by-side review; move extras to the Trash or a
  folder; cached image analysis for fast re-scans; HEIC support.
- **History:** every run is journaled as it happens and can be undone, including
  cancelled or interrupted runs.
- **macOS app:** standalone `File Falcon Pro.app` built with PyInstaller, with an app icon
  and a `--check` self-test.
- Documentation, a test suite and continuous integration.

### Changed

- The old "Basic/Advanced" modes, single category per run and flat output folder are
  replaced by the options above.
- Dependencies are managed with uv; pydantic, rich and python-dotenv are no longer needed.

## 1.x — 2023–2025

The original File Falcon Pro: a PyQt6 utility that copied or moved files of one category
(by extension) from a source folder into a destination folder, with an optional filename
keyword filter.
