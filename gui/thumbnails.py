"""
Asynchronous thumbnail loading for the Duplicates page.
"""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

from PIL import Image
from PyQt6.QtCore import QObject, QThreadPool, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap

from gui.workers import Task
from operations.duplicates import load_oriented


def load_qimage(path: Path, max_side: int) -> QImage | None:
	"""Decode an image (upright, HEIC included) into a QImage no bigger than ``max_side``."""
	try:
		image = load_oriented(path, max_side)
		image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
		image = image.convert("RGB")
	except Exception:
		return None
	data = image.tobytes()
	qimage = QImage(data, image.width, image.height, image.width * 3, QImage.Format.Format_RGB888)
	return qimage.copy()  # detach from the Python buffer


def _job(path: Path, max_side: int, *, progress, cancelled) -> tuple[Path, QImage | None]:
	return path, None if cancelled() else load_qimage(path, max_side)


class ThumbnailLoader(QObject):
	"""Loads thumbnails on a small private thread pool and caches the most recent ones."""

	loaded = pyqtSignal(object)  # Path

	def __init__(self, max_side: int = 360, capacity: int = 600) -> None:
		super().__init__()
		self._max_side = max_side
		self._capacity = capacity
		self._cache: OrderedDict[Path, QPixmap | None] = OrderedDict()
		self._pending: dict[Path, Task] = {}
		self._pool = QThreadPool(self)
		self._pool.setMaxThreadCount(4)

	def get(self, path: Path) -> QPixmap | None:
		"""Return the cached thumbnail, or start loading it and return None for now."""
		if path in self._cache:
			self._cache.move_to_end(path)
			return self._cache[path]
		if path not in self._pending:
			task = Task(_job, path, self._max_side)
			task.signals.finished.connect(self._on_loaded)
			self._pending[path] = task
			task.start(self._pool)
		return None

	def is_failed(self, path: Path) -> bool:
		return path in self._cache and self._cache[path] is None

	def _on_loaded(self, result) -> None:
		path, image = result
		self._pending.pop(path, None)
		self._cache[path] = QPixmap.fromImage(image) if image is not None else None
		while len(self._cache) > self._capacity:
			self._cache.popitem(last=False)
		self.loaded.emit(path)

	def cancel_all(self) -> None:
		for task in self._pending.values():
			task.cancel()  # queued jobs then return immediately
		self._pending.clear()

	def forget(self, path: Path) -> None:
		self._cache.pop(path, None)
