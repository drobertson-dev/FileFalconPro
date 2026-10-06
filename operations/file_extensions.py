"""
File categories for File Falcon Pro.

Every extension belongs to exactly one category, so when sorting by type a file always
has a single home folder. Extensions that are ambiguous (for example ``.ts``, which could
be a TypeScript source file *or* an MPEG-TS video) are deliberately left out and fall
into "Other".
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Category:
	"""A group of related file types, e.g. Photos or Videos."""

	key: str
	label: str
	extensions: frozenset[str]


def _exts(names: str) -> frozenset[str]:
	"""Turn a space-separated list of extensions into a set like {".jpg", ".png"}."""
	return frozenset(f".{name.lower()}" for name in names.split())


RAW_PHOTO_EXTENSIONS = _exts("arw cr2 cr3 crw dng nef nrw orf pef raf raw rw2 sr2 srw x3f")

# Formats Pillow can decode (with pillow-heif for HEIC), used by the duplicate finder.
DECODABLE_IMAGE_EXTENSIONS = _exts("avif bmp gif heic heif jfif jpeg jpg png tif tiff webp")

CATEGORIES: tuple[Category, ...] = (
	Category("photos", "Photos", DECODABLE_IMAGE_EXTENSIONS | RAW_PHOTO_EXTENSIONS | _exts("ico")),
	Category(
		"videos",
		"Videos",
		_exts(
			"3gp amv asf avi divx drc f4v flv m2ts m2v m4v mkv mov mp4 mpe mpeg mpg mpv mts mxf "
			"ogm ogv qt r3d rm rmvb vob webm wmv y4m"
		),
	),
	Category(
		"audio",
		"Audio",
		_exts("aac aif aiff amr ape flac m4a m4b mid midi mp3 oga ogg opus wav wma"),
	),
	Category("documents", "Documents", _exts("doc docx epub markdown md odt pages rtf tex txt")),
	Category("pdfs", "PDFs", _exts("pdf")),
	Category("spreadsheets", "Spreadsheets", _exts("csv numbers ods tsv xls xlsb xlsm xlsx")),
	Category("presentations", "Presentations", _exts("key odp pot potx pps ppsx ppt pptx")),
	Category("design", "Design", _exts("ai eps fig indd psb psd qxp sketch svg xd")),
	Category("archives", "Archives", _exts("7z bz2 dmg gz iso rar tar tgz xz zip")),
	Category(
		"data",
		"Data & Web",
		_exts("accdb db htm html json jsonl mdb rss sql sqlite sqlite3 xhtml xml yaml yml"),
	),
)

OTHER = Category("other", "Other", frozenset())

ALL_CATEGORIES: tuple[Category, ...] = (*CATEGORIES, OTHER)

CATEGORY_BY_KEY: dict[str, Category] = {category.key: category for category in ALL_CATEGORIES}

_CATEGORY_BY_EXTENSION: dict[str, Category] = {
	ext: category for category in CATEGORIES for ext in category.extensions
}


def category_for(path: str | Path) -> Category:
	"""Return the category a file belongs to, based on its extension."""
	return _CATEGORY_BY_EXTENSION.get(Path(path).suffix.lower(), OTHER)
