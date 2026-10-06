"""
Regenerate the screenshots in docs/images from a made-up photo library.

Everything happens in a temporary folder that stands in for the home directory, so the
pictures show tidy ``~/Pictures/...`` paths and no real files are touched.

    uv run python scripts/screenshots.py
"""

import os
import random
import shutil
import struct
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "images"
WIDTH, HEIGHT = 1320, 860

PALETTES = [
	# sky top, sky horizon, sun, far ridge, near ridge, ground
	("#1d3b6e", "#f6a46b", "#ffe6a8", "#7a5b8c", "#3d2f55", "#1d1830"),  # sunset
	("#2f74c0", "#bfe0f5", "#fffbe8", "#7fa3b8", "#4f7a5a", "#2f5236"),  # midday
	("#0b1026", "#2b3a6b", "#f2f0e6", "#2a3557", "#1a2140", "#0d1124"),  # night
	("#5b4a8a", "#f3b8c4", "#fff1d6", "#9b7fa8", "#5f4f78", "#2f2742"),  # dawn
	("#3a8fd6", "#f1e3c4", "#fffaf0", "#c9925e", "#a2643a", "#6b3e22"),  # desert
	("#27566b", "#cfe7e4", "#fbfbf3", "#6f9aa0", "#3f6b63", "#21423b"),  # lake
]


def _hex(colour: str) -> np.ndarray:
	return np.array([int(colour[i : i + 2], 16) for i in (1, 3, 5)], dtype=np.float32)


def landscape(seed: int, size=(2400, 1600)) -> Image.Image:
	"""A painterly mountain landscape: gradient sky, sun, layered ridges and trees."""
	rnd = random.Random(seed)
	rng = np.random.default_rng(seed)
	sky_top, horizon, sun, far, near, ground = PALETTES[seed % len(PALETTES)]
	w, h = size
	t = np.linspace(0, 1, h)[:, None, None]
	pixels = (_hex(sky_top) * (1 - t) + _hex(horizon) * t) * np.ones((1, w, 1))
	image = Image.fromarray(pixels.astype(np.uint8))

	glow = Image.new("L", size, 0)
	sx, sy, sr = rnd.randint(w // 5, 4 * w // 5), rnd.randint(h // 6, h // 2), rnd.randint(60, 120)
	ImageDraw.Draw(glow).ellipse([sx - sr * 3, sy - sr * 3, sx + sr * 3, sy + sr * 3], fill=90)
	glow = glow.filter(ImageFilter.GaussianBlur(sr))
	image = Image.composite(Image.new("RGB", size, sun), image, glow)
	ImageDraw.Draw(image).ellipse([sx - sr, sy - sr, sx + sr, sy + sr], fill=sun)

	if seed % len(PALETTES) == 2:  # stars at night
		draw = ImageDraw.Draw(image)
		for _ in range(400):
			x, y = rnd.randint(0, w), rnd.randint(0, h // 2)
			draw.point((x, y), fill=(255, 255, 240))

	layers = [(far, 0.42, 0.18), (near, 0.58, 0.14), (ground, 0.78, 0.08)]
	xs = np.arange(w)
	for index, (colour, base, height) in enumerate(layers):
		ridge = np.zeros(w)
		for octave in range(6):
			freq = (octave + 1) ** 1.7 / w * rnd.uniform(1.5, 3.5)
			ridge += np.sin(xs * freq * 2 * np.pi + rnd.uniform(0, 6)) / (octave + 1) ** 1.2
		ridge = base * h - (ridge - ridge.min()) / np.ptp(ridge) * height * h
		mask = Image.new("L", size, 0)
		ImageDraw.Draw(mask).polygon([(0, h), *zip(xs[::8], ridge[::8]), (w, h)], fill=255)
		layer = Image.new("RGB", size, colour)
		image = Image.composite(layer, image, mask)
		if index == 2:  # pine trees along the front ridge
			draw = ImageDraw.Draw(image)
			for _ in range(140):
				x = rnd.randint(0, w - 1)
				y = ridge[x] + rnd.randint(-10, 60)
				th, tw = rnd.randint(40, 140), rnd.randint(14, 40)
				draw.polygon([(x, y - th), (x - tw, y), (x + tw, y)], fill=colour)
				draw.line([x, y, x, y + th * 0.2], fill=colour, width=4)
	grain = rng.normal(0, 5, (h, w, 3))
	pixels = np.clip(np.asarray(image, dtype=np.float32) + grain, 0, 255).astype(np.uint8)
	return Image.fromarray(pixels).filter(ImageFilter.GaussianBlur(0.6))


def exif_for(when: datetime) -> Image.Exif:
	exif = Image.Exif()
	exif.get_ifd(0x8769)[36867] = when.strftime("%Y:%m:%d %H:%M:%S")
	return exif


def quicktime(path: Path, when: datetime, size: int) -> None:
	epoch = datetime(1904, 1, 1, tzinfo=timezone.utc)
	seconds = int((when.replace(tzinfo=timezone.utc) - epoch).total_seconds())

	def atom(kind: bytes, payload: bytes) -> bytes:
		return struct.pack(">I4s", 8 + len(payload), kind) + payload

	mvhd = atom(b"mvhd", bytes(4) + struct.pack(">II", seconds, seconds) + bytes(88))
	path.write_bytes(
		atom(b"ftyp", b"qt  " + bytes(4)) + atom(b"mdat", os.urandom(size)) + atom(b"moov", mvhd)
	)


def touch(path: Path, when: datetime) -> None:
	os.utime(path, (when.timestamp(), when.timestamp()))


def build_library(home: Path) -> dict[str, Path]:
	pictures = home / "Pictures"
	inbox = pictures / "Camera Import"
	sorted_ = pictures / "Sorted"
	phone = pictures / "Phone Backup"
	for folder in (inbox / "DCIM" / "100APPLE", inbox / "WhatsApp", sorted_, phone):
		folder.mkdir(parents=True, exist_ok=True)

	rnd = random.Random(3)
	start = datetime(2024, 3, 2, 9, 30)
	for n in range(48):
		when = start + timedelta(days=rnd.randint(0, 420), minutes=rnd.randint(0, 600))
		folder = inbox / rnd.choice(["", "DCIM/100APPLE", "DCIM/100APPLE", "WhatsApp"])
		path = folder / f"IMG_{2040 + n}.JPG"
		landscape(n, (1200, 800)).save(path, quality=80, exif=exif_for(when))
		touch(path, datetime(2025, 8, 1, 12))
	# Same name, different photo: will be renamed on the way in
	landscape(99, (1200, 800)).save(inbox / "WhatsApp" / "IMG_2041.JPG", exif=exif_for(start))
	for n in range(8):
		when = start + timedelta(days=rnd.randint(0, 420))
		path = inbox / rnd.choice(["", "DCIM/100APPLE"]) / f"IMG_{3100 + n}.MOV"
		quicktime(path, when, rnd.randint(200_000, 900_000))
	for name, days in [
		("Invoice 2024-03.pdf", 20), ("Invoice 2024-04.pdf", 50), ("Tax return 2024.pdf", 300),
		("Trip itinerary.docx", 120), ("Packing list.txt", 118), ("Holiday budget.xlsx", 100),
	]:  # fmt: skip
		path = inbox / name
		path.write_bytes(os.urandom(rnd.randint(40_000, 300_000)))
		touch(path, start + timedelta(days=days))

	# Duplicates: a phone backup with copies, resized, edited and cropped versions
	for folder in ("Camera Roll", "Old Laptop", "WhatsApp", "Edits"):
		(phone / folder).mkdir(exist_ok=True)
	for n, seed in enumerate((7, 13, 21, 30, 32, 43)):
		original = landscape(seed)
		name = f"IMG_{5120 + n}.JPG"
		original.save(phone / "Camera Roll" / name, quality=90)
		if n in (0, 3, 4):
			shutil.copy2(phone / "Camera Roll" / name, phone / "Old Laptop" / name)
		if n in (0, 1, 5):
			original.resize((1200, 800)).save(phone / "WhatsApp" / f"IMG-WA{n:04d}.jpg", quality=60)
		if n in (0, 2):
			original.crop((500, 250, 1900, 1180)).save(phone / "Edits" / f"{name[:-4]} crop.jpg")
		if n == 1:
			ImageEnhance.Color(original).enhance(1.5).save(
				phone / "Edits" / f"{name[:-4]} vivid.jpg"
			)
	for seed in range(60, 72):
		landscape(seed).save(phone / "Camera Roll" / f"IMG_{5200 + seed}.JPG", quality=88)
	return {"inbox": inbox, "sorted": sorted_, "phone": phone}


def main() -> None:
	# resolve(): macOS temp folders live behind a /var -> /private/var symlink
	temp = Path(tempfile.mkdtemp(prefix="filefalcon-shots-")).resolve()
	home = temp / "home"
	os.environ["HOME"] = str(home)  # so paths display as ~/Pictures/...
	os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
	os.environ.setdefault("QT_SCALE_FACTOR", "2")
	sys.path.insert(0, str(ROOT))

	from PyQt6.QtCore import QEventLoop, QTimer
	from PyQt6.QtWidgets import QApplication, QMessageBox

	from config import APP_NAME, APP_VERSION, AppPaths, Settings
	from gui import theme
	from gui.main_window import MainWindow

	folders = build_library(home)

	def accept(box: QMessageBox) -> int:  # auto-confirm dialogs
		for button in box.buttons():
			if box.buttonRole(button) == QMessageBox.ButtonRole.AcceptRole:
				button.click()
				break
		return 0

	QMessageBox.exec = accept

	# Never touch the real Trash: "trashed" demo files are simply deleted.
	import gui.duplicates_page
	import operations.runner

	operations.runner.send2trash = gui.duplicates_page.send2trash = os.remove

	app = QApplication([])
	app.setApplicationName(APP_NAME)
	app.setApplicationVersion(APP_VERSION)
	app.setStyle("Fusion")
	OUT.mkdir(parents=True, exist_ok=True)

	def wait(ms: int) -> None:
		loop = QEventLoop()
		QTimer.singleShot(ms, loop.quit)
		loop.exec()

	def save(window, name: str) -> None:
		image = window.grab().toImage()
		path = OUT / f"{name}.png"
		image.save(str(path))
		with Image.open(path) as shot:
			shot = shot.convert("RGB").resize(
				(1600, round(1600 * shot.height / shot.width)), Image.LANCZOS
			)
			shot.save(path, optimize=True)
		print("wrote", path.relative_to(ROOT))

	def window(dark: bool) -> MainWindow:
		theme.apply_theme(app, dark)
		paths = AppPaths(temp / ("data-dark" if dark else "data"), temp / "cache")
		paths.ensure()
		settings = Settings(
			source=str(folders["inbox"]),
			dest=str(folders["sorted"]),
			categories=["photos", "videos", "pdfs", "documents"],
			dupes_folder=str(folders["phone"]),
		)
		win = MainWindow(settings, paths)
		win.resize(WIDTH, HEIGHT)
		win.show()
		wait(3500)
		return win

	# Light theme: Organize, then two runs for History, then Duplicates
	win = window(dark=False)
	save(win, "organize")
	organize = win.organize
	organize._go.click()  # copy photos, videos, PDFs and documents
	wait(3000)
	for key, chip in organize._chips.items():
		chip.setChecked(key == "spreadsheets")
	organize._operation.set_value("move")
	organize._options_changed()
	wait(2500)
	organize._go.click()  # move the spreadsheet, then undo that
	wait(2500)
	win.history.refresh()
	cards = list(win.history._cards.values())
	cards[0]._undo.click()
	wait(2500)
	win.history._banner.hide()
	win._nav.button(2).click()
	wait(500)
	save(win, "history")
	win._nav.button(1).click()
	win.duplicates.start_scan()
	wait(8000)
	save(win, "duplicates")
	win.close()

	# Dark theme versions of the two main pages
	shutil.rmtree(folders["sorted"])
	folders["sorted"].mkdir()
	win = window(dark=True)
	save(win, "organize-dark")
	win._nav.button(1).click()
	win.duplicates.start_scan()
	wait(6000)
	save(win, "duplicates-dark")
	win.close()
	shutil.rmtree(temp, ignore_errors=True)


if __name__ == "__main__":
	main()
