"""
File Falcon Pro - sort, tidy and de-duplicate your files.

Organises photos, videos and documents into a clean folder structure by type and date,
and finds duplicate photos, including resized and cropped copies.
"""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PyQt6.QtCore import QStandardPaths
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from config import APP_NAME, APP_VERSION, AppPaths, Settings
from gui import theme
from gui.main_window import MainWindow
from gui.widgets import logo_pixmap


def setup_logging(log_file: Path) -> None:
	"""Configure application logging."""
	logging.basicConfig(
		level=logging.INFO,
		format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
		handlers=[
			logging.StreamHandler(),
			RotatingFileHandler(log_file, maxBytes=1_000_000, backupCount=2, encoding="utf-8"),
		],
	)


def app_paths() -> AppPaths:
	location = QStandardPaths.StandardLocation
	return AppPaths(
		data_dir=Path(QStandardPaths.writableLocation(location.AppDataLocation)),
		cache_dir=Path(QStandardPaths.writableLocation(location.CacheLocation)),
	)


def self_check() -> int:
	"""Verify the image libraries work (useful after packaging): ``main.py --check``."""
	import tempfile

	import cv2
	import PIL
	from PIL import Image

	from operations.duplicates import find_duplicates

	with tempfile.TemporaryDirectory() as folder:
		original = Image.effect_mandelbrot((640, 480), (-2.0, -1.2, 1.0, 1.2), 100).convert("RGB")
		original.save(Path(folder, "a.jpg"))
		original.save(Path(folder, "b.heic"))
		original.crop((100, 80, 540, 400)).save(Path(folder, "c.png"))
		report = find_duplicates([Path(folder)])
	found = sorted(member.image.path.name for group in report.groups for member in group.members)
	print(f"{APP_NAME} {APP_VERSION}")
	print(
		f"  Python {sys.version.split()[0]} · OpenCV {cv2.__version__} · Pillow {PIL.__version__}"
	)
	print(f"  Duplicate finder grouped: {', '.join(found) or 'nothing'}")
	ok = found == ["a.jpg", "b.heic", "c.png"]
	print("  OK" if ok else "  FAILED")
	return 0 if ok else 1


def main() -> int:
	"""Application entry point.

	Returns:
	    Application exit code
	"""
	if "--check" in sys.argv:
		return self_check()

	app = QApplication(sys.argv)
	app.setApplicationName(APP_NAME)
	app.setApplicationVersion(APP_VERSION)
	app.setStyle("Fusion")

	paths = app_paths()
	paths.ensure()
	setup_logging(paths.log_file)
	logging.getLogger("main").info("Starting File Falcon Pro (data in %s)", paths.data_dir)

	theme.apply_theme(app)
	if not getattr(sys, "frozen", False):  # a packaged app brings its own icon
		app.setWindowIcon(QIcon(logo_pixmap(256)))

	settings = Settings.load(paths.settings_file)
	window = MainWindow(settings, paths)
	window.show()
	return app.exec()


if __name__ == "__main__":
	sys.exit(main())
