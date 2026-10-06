"""
Render the app icon from the same drawing code the app uses for its logo.

Writes ``assets/icon.png`` (1024 px, for docs) and, on macOS, ``assets/icon.icns``.

    uv run python scripts/make_icon.py
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QGuiApplication, QImage, QPainter

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gui.widgets import paint_logo  # noqa: E402

ICONSET_SIZES = (16, 32, 128, 256, 512)


def render(size: int) -> QImage:
	"""The logo on a transparent canvas with the margins macOS icons expect."""
	image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
	image.fill(Qt.GlobalColor.transparent)
	painter = QPainter(image)
	painter.setRenderHint(QPainter.RenderHint.Antialiasing)
	tile = size * 824 / 1024
	inset = (size - tile) / 2
	# Soft shadow under the tile
	for step in range(12, 0, -1):
		painter.setPen(Qt.PenStyle.NoPen)
		painter.setBrush(QColor(20, 20, 60, 4))
		grow = step * size / 1024
		painter.drawRoundedRect(
			QRectF(inset - grow, inset - grow + size * 10 / 1024, tile + 2 * grow, tile + 2 * grow),
			tile * 0.26,
			tile * 0.26,
		)
	paint_logo(painter, QRectF(inset, inset, tile, tile))
	painter.end()
	return image


def main() -> None:
	app = QGuiApplication(sys.argv)  # noqa: F841 - needed for painting
	assets = ROOT / "assets"
	assets.mkdir(exist_ok=True)
	render(1024).save(str(assets / "icon.png"))
	print("wrote", assets / "icon.png")

	if sys.platform != "darwin" or not shutil.which("iconutil"):
		return
	with tempfile.TemporaryDirectory() as temp:
		iconset = Path(temp) / "icon.iconset"
		iconset.mkdir()
		for size in ICONSET_SIZES:
			render(size).save(str(iconset / f"icon_{size}x{size}.png"))
			render(size * 2).save(str(iconset / f"icon_{size}x{size}@2x.png"))
		subprocess.run(
			["iconutil", "-c", "icns", str(iconset), "-o", str(assets / "icon.icns")], check=True
		)
	print("wrote", assets / "icon.icns")


if __name__ == "__main__":
	main()
