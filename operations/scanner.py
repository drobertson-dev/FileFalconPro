"""
Folder scanning for File Falcon Pro.

Walks a source folder and collects the files that could be organised, skipping hidden
files, symlinks and macOS packages (an app bundle or a Photos library must never be
picked apart).
"""

from __future__ import annotations

import os
import stat as stat_mode
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from operations.file_extensions import Category, category_for

# Directories that look like folders but are really single documents on macOS.
PACKAGE_SUFFIXES = frozenset(
	{
		".app", ".aplibrary", ".band", ".bundle", ".dtbase2", ".fcpbundle", ".framework",
		".imovielibrary", ".kext", ".logicx", ".lrdata", ".lrlibrary", ".musiclibrary",
		".photolibrary", ".photoslibrary", ".plugin", ".pkg", ".rtfd", ".scriptbundle",
		".sparsebundle", ".theater", ".tvlibrary", ".xcodeproj", ".xcworkspace",
	}
)  # fmt: skip

ProgressFn = Callable[[int], None]
CancelFn = Callable[[], bool]


@dataclass(slots=True, eq=False)
class FileItem:
	"""A file found while scanning a source folder."""

	path: Path
	rel: Path
	size: int
	mtime: float
	birthtime: float | None
	category: Category
	taken: datetime | None = None
	date_source: str | None = None

	@property
	def file_date(self) -> datetime:
		"""Earliest filesystem timestamp; copies often reset one of the two."""
		stamps = [self.mtime]
		if self.birthtime:
			stamps.append(self.birthtime)
		return datetime.fromtimestamp(min(stamps))

	@property
	def date(self) -> datetime:
		"""Best known date: capture date when resolved, file date otherwise."""
		return self.taken or self.file_date


def is_hidden(name: str) -> bool:
	return name.startswith(".")


def scan_folder(
	root: Path,
	*,
	exclude: Path | None = None,
	include_hidden: bool = False,
	progress: ProgressFn | None = None,
	cancelled: CancelFn | None = None,
) -> list[FileItem]:
	"""Collect every regular file below ``root``.

	Args:
	    root: Folder to scan.
	    exclude: Folder to skip entirely, e.g. a destination nested inside the source.
	    include_hidden: Whether to include dot-files and dot-folders.
	    progress: Called periodically with the number of files found so far.
	    cancelled: Polled periodically; scanning stops early when it returns True.
	"""
	root = root.resolve()
	exclude = exclude.resolve() if exclude else None
	items: list[FileItem] = []

	for dirpath, dirnames, filenames in os.walk(root):
		if cancelled and cancelled():
			break
		current = Path(dirpath)
		dirnames[:] = sorted(
			name
			for name in dirnames
			if (include_hidden or not is_hidden(name))
			and Path(name).suffix.lower() not in PACKAGE_SUFFIXES
			and (exclude is None or current / name != exclude)
		)
		for name in sorted(filenames):
			if not include_hidden and is_hidden(name):
				continue
			path = current / name
			try:
				stat = path.lstat()
			except OSError:
				continue
			if not stat_mode.S_ISREG(stat.st_mode):
				continue
			items.append(
				FileItem(
					path=path,
					rel=path.relative_to(root),
					size=stat.st_size,
					mtime=stat.st_mtime,
					birthtime=getattr(stat, "st_birthtime", None),
					category=category_for(name),
				)
			)
			if progress and len(items) % 250 == 0:
				progress(len(items))

	if progress:
		progress(len(items))
	return items
