"""
Capture-date detection for File Falcon Pro.

Photos carry the moment they were taken in EXIF; QuickTime/MP4 videos store it in the
movie header. Both survive copying between drives, unlike filesystem dates, so they are
preferred when sorting by date.
"""

from __future__ import annotations

import struct
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import ExifTags, Image

from operations.file_extensions import CATEGORY_BY_KEY
from operations.scanner import FileItem

try:
	import pillow_heif

	pillow_heif.register_heif_opener()
except ImportError:  # pragma: no cover - HEIC support is optional
	pass

QUICKTIME_EXTENSIONS = frozenset({".3gp", ".cr3", ".m4v", ".mov", ".mp4", ".qt"})

_EXIF_DATE_TAGS = (36867, 36868)  # DateTimeOriginal, DateTimeDigitized
_TIFF_DATE_TAG = 306  # DateTime
_QUICKTIME_EPOCH = datetime(1904, 1, 1, tzinfo=timezone.utc)

DateResult = tuple[datetime | None, str]


def _plausible(value: datetime) -> bool:
	return 1971 <= value.year <= datetime.now().year + 1


def _parse_exif_date(raw: object) -> datetime | None:
	if not isinstance(raw, str):
		return None
	try:
		value = datetime.strptime(raw.strip()[:19], "%Y:%m:%d %H:%M:%S")
	except ValueError:
		return None
	return value if _plausible(value) else None


def photo_taken(path: Path) -> datetime | None:
	"""Return the EXIF capture date of a photo, if it has one."""
	try:
		with Image.open(path) as image:
			exif = image.getexif()
			sub_ifd = exif.get_ifd(ExifTags.IFD.Exif)
			for tag in _EXIF_DATE_TAGS:
				if value := _parse_exif_date(sub_ifd.get(tag)):
					return value
			return _parse_exif_date(exif.get(_TIFF_DATE_TAG))
	except Exception:
		return None


def _read_atom_header(handle, position: int, limit: int) -> tuple[int, bytes, int] | None:
	"""Return (size, kind, header_length) for the atom at ``position``."""
	if position + 8 > limit:
		return None
	handle.seek(position)
	header = handle.read(8)
	if len(header) < 8:
		return None
	size, kind = struct.unpack(">I4s", header)
	header_length = 8
	if size == 1:
		extended = handle.read(8)
		if len(extended) < 8:
			return None
		size = struct.unpack(">Q", extended)[0]
		header_length = 16
	elif size == 0:
		size = limit - position
	if size < header_length:
		return None
	return size, kind, header_length


def _find_atom(handle, kind: bytes, start: int, end: int) -> tuple[int, int] | None:
	"""Return (payload_start, payload_end) of the first ``kind`` atom in [start, end)."""
	position = start
	while (header := _read_atom_header(handle, position, end)) is not None:
		size, found, header_length = header
		if found == kind:
			return position + header_length, position + size
		position += size
	return None


def video_taken(path: Path) -> datetime | None:
	"""Return the creation time stored in a QuickTime/MP4 movie header."""
	try:
		with path.open("rb") as handle:
			handle.seek(0, 2)
			file_size = handle.tell()
			moov = _find_atom(handle, b"moov", 0, file_size)
			if not moov:
				return None
			mvhd = _find_atom(handle, b"mvhd", *moov)
			if not mvhd:
				return None
			handle.seek(mvhd[0])
			version = handle.read(4)[0]
			if version == 1:
				seconds = struct.unpack(">Q", handle.read(8))[0]
			else:
				seconds = struct.unpack(">I", handle.read(4))[0]
	except (OSError, IndexError, struct.error):
		return None
	if seconds == 0:
		return None
	try:
		value = (_QUICKTIME_EPOCH + timedelta(seconds=seconds)).astimezone().replace(tzinfo=None)
	except OverflowError:
		return None
	return value if _plausible(value) else None


def capture_date(item: FileItem) -> DateResult:
	"""Return the best capture date for ``item`` and where it came from."""
	suffix = item.path.suffix.lower()
	if item.category is CATEGORY_BY_KEY["photos"]:
		if suffix in QUICKTIME_EXTENSIONS:
			if taken := video_taken(item.path):
				return taken, "video"
		elif taken := photo_taken(item.path):
			return taken, "photo"
	elif suffix in QUICKTIME_EXTENSIONS and (taken := video_taken(item.path)):
		return taken, "video"
	return None, "file"


def resolve_capture_dates(
	items: Iterable[FileItem],
	*,
	progress: Callable[[int, int], None] | None = None,
	cancelled: Callable[[], bool] | None = None,
	workers: int = 8,
) -> dict[Path, DateResult]:
	"""Read capture dates for many files in parallel.

	Only photos and videos are opened; everything else resolves to its file date
	immediately. Returns a mapping of path -> (date or None, source).
	"""
	pending = [item for item in items if item.date_source is None]
	results: dict[Path, DateResult] = {}
	media_keys = {"photos", "videos"}

	readable = []
	for item in pending:
		if item.category.key in media_keys:
			readable.append(item)
		else:
			results[item.path] = (None, "file")

	total = len(readable)
	done = 0
	with ThreadPoolExecutor(max_workers=workers) as pool:
		chunk = 256
		for start in range(0, total, chunk):
			if cancelled and cancelled():
				break
			batch = readable[start : start + chunk]
			for item, result in zip(batch, pool.map(capture_date, batch)):
				results[item.path] = result
			done += len(batch)
			if progress:
				progress(done, total)
	return results
