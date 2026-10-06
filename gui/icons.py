"""
Line icons for File Falcon Pro.

Icons are tiny SVG snippets on a 24×24 grid, drawn with round 1.8px strokes and tinted
at render time so they follow the current theme.
"""

from __future__ import annotations

import weakref
from functools import lru_cache

from PyQt6 import sip
from PyQt6.QtCore import QByteArray, QRectF, QSize, Qt
from PyQt6.QtGui import QIcon, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer

from gui import theme

_PATHS: dict[str, str] = {
	"folder": '<path d="M3 7.5A2.5 2.5 0 0 1 5.5 5H9l2 2h7.5A2.5 2.5 0 0 1 21 9.5v8a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 17.5z"/>',
	"folder-open": '<path d="M3 17.5V7.5A2.5 2.5 0 0 1 5.5 5H9l2 2h6.5A2.5 2.5 0 0 1 20 9.5V11"/><path d="M3 17.5 5.6 12a2 2 0 0 1 1.8-1.1H21l-2.9 7.6a2 2 0 0 1-1.9 1.5H5a2 2 0 0 1-2-2.5z"/>',
	"organize": '<rect x="3" y="3" width="7.5" height="7.5" rx="2"/><rect x="13.5" y="3" width="7.5" height="7.5" rx="2"/><rect x="3" y="13.5" width="7.5" height="7.5" rx="2"/><path d="M17.25 14v6.5M14 17.25h6.5"/>',
	"duplicates": '<rect x="8.5" y="8.5" width="12.5" height="12.5" rx="2.5"/><path d="M15.5 8.5V6A2.5 2.5 0 0 0 13 3.5H6A2.5 2.5 0 0 0 3.5 6v7A2.5 2.5 0 0 0 6 15.5h2.5"/>',
	"history": '<path d="M3.5 12a8.5 8.5 0 1 0 2.6-6.1L3.5 8.5"/><path d="M3.5 3.5v5h5"/><path d="M12 7.5V12l3 2"/>',
	"photos": '<rect x="3" y="3.5" width="18" height="17" rx="3"/><circle cx="9" cy="9.5" r="1.8"/><path d="m21 15.5-4.6-4.6a1.5 1.5 0 0 0-2.1 0L5 20.2"/>',
	"videos": '<rect x="2.5" y="6" width="13.5" height="12" rx="2.5"/><path d="m16 10.5 4.4-2.6a.7.7 0 0 1 1.1.6v7a.7.7 0 0 1-1.1.6L16 13.5"/>',
	"audio": '<path d="M9 18V5.5l11-2V16"/><circle cx="6.5" cy="18" r="2.5"/><circle cx="17.5" cy="16" r="2.5"/>',
	"documents": '<path d="M14 3H7a2.5 2.5 0 0 0-2.5 2.5v13A2.5 2.5 0 0 0 7 21h10a2.5 2.5 0 0 0 2.5-2.5V8.5z"/><path d="M14 3v5.5h5.5"/><path d="M8.5 13h7M8.5 16.5h5"/>',
	"pdfs": '<path d="M14 3H7a2.5 2.5 0 0 0-2.5 2.5v13A2.5 2.5 0 0 0 7 21h10a2.5 2.5 0 0 0 2.5-2.5V8.5z"/><path d="M14 3v5.5h5.5"/><path d="M9 17v-4.5h1.5a1.4 1.4 0 0 1 0 2.8H9"/>',
	"spreadsheets": '<rect x="3.5" y="3.5" width="17" height="17" rx="2.5"/><path d="M3.5 9.5h17M3.5 15h17M9.5 9.5v11"/>',
	"presentations": '<path d="M2.5 4h19"/><path d="M4 4v9.5A2.5 2.5 0 0 0 6.5 16h11a2.5 2.5 0 0 0 2.5-2.5V4"/><path d="m8.5 21 3.5-5 3.5 5"/>',
	"design": '<path d="M4 20l1-4.5L16 4.5a2.1 2.1 0 0 1 3 3L8 18.5z"/><path d="m14 6.5 3.5 3.5"/>',
	"archives": '<rect x="3" y="4" width="18" height="5" rx="1.5"/><path d="M5 9v9.5A2.5 2.5 0 0 0 7.5 21h9a2.5 2.5 0 0 0 2.5-2.5V9"/><path d="M10 13h4"/>',
	"data": '<ellipse cx="12" cy="5.5" rx="7.5" ry="2.8"/><path d="M4.5 5.5v13c0 1.5 3.4 2.8 7.5 2.8s7.5-1.3 7.5-2.8v-13"/><path d="M4.5 12c0 1.5 3.4 2.8 7.5 2.8s7.5-1.3 7.5-2.8"/>',
	"other": '<rect x="3.5" y="3.5" width="17" height="17" rx="4"/><path d="M8 12h.01M12 12h.01M16 12h.01" stroke-width="2.6"/>',
	"arrow-right": '<path d="M5 12h14M13 6l6 6-6 6"/>',
	"search": '<circle cx="11" cy="11" r="6.5"/><path d="m20.5 20.5-4.8-4.8"/>',
	"undo": '<path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>',
	"check": '<path d="M20 6 9 17l-5-5"/>',
	"check-circle": '<circle cx="12" cy="12" r="9"/><path d="m8 12.5 2.8 2.8L16.5 9.5"/>',
	"alert": '<path d="M10.3 3.9 2.4 17.6A2 2 0 0 0 4.1 20.5h15.8a2 2 0 0 0 1.7-2.9L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9.5v4M12 17h.01"/>',
	"copy": '<rect x="8.5" y="8.5" width="12.5" height="12.5" rx="2.5"/><path d="M15.5 8.5V6A2.5 2.5 0 0 0 13 3.5H6A2.5 2.5 0 0 0 3.5 6v7A2.5 2.5 0 0 0 6 15.5h2.5"/>',
	"move": '<path d="M3 7.5A2.5 2.5 0 0 1 5.5 5H9l2 2h7.5A2.5 2.5 0 0 1 21 9.5v8a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 17.5z"/><path d="M8.5 13.5h7M13 11l2.5 2.5L13 16"/>',
	"close": '<path d="M17 7 7 17M7 7l10 10"/>',
	"refresh": '<path d="M20.5 12a8.5 8.5 0 1 1-2.6-6.1l2.6 2.6"/><path d="M20.5 3.5v5h-5"/>',
	"reveal": '<path d="M14.5 3.5h6v6"/><path d="M10 14 20.5 3.5"/><path d="M18 13.5v5A2.5 2.5 0 0 1 15.5 21h-10A2.5 2.5 0 0 1 3 18.5v-10A2.5 2.5 0 0 1 5.5 6h5"/>',
	"trash": '<path d="M3.5 6.5h17"/><path d="M9 6.5V4.5A1.5 1.5 0 0 1 10.5 3h3A1.5 1.5 0 0 1 15 4.5v2"/><path d="m18.5 6.5-.8 12.1a2.5 2.5 0 0 1-2.5 2.4H8.8a2.5 2.5 0 0 1-2.5-2.4L5.5 6.5"/>',
	"sparkle": '<path d="M12 3v4M12 17v4M3 12h4M17 12h4M5.6 5.6l2.8 2.8M15.6 15.6l2.8 2.8M18.4 5.6l-2.8 2.8M8.4 15.6l-2.8 2.8"/>',
	"image-stack": '<rect x="6.5" y="6.5" width="14.5" height="14.5" rx="2.5"/><path d="M3 16.5V5.5A2.5 2.5 0 0 1 5.5 3h11"/><circle cx="11.5" cy="11.5" r="1.5"/><path d="m21 17-3.5-3.5L10 21"/>',
	"crop": '<path d="M6 2.5V16a2 2 0 0 0 2 2h13.5"/><path d="M2.5 6H16a2 2 0 0 1 2 2v13.5"/>',
}


def _svg(name: str, colour: str) -> bytes:
	body = _PATHS[name]
	return (
		'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
		f'stroke="{colour}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
		f"{body}</svg>"
	).encode()


@lru_cache(maxsize=512)
def pixmap(name: str, colour: str, size: int = 18, ratio: float = 2.0) -> QPixmap:
	renderer = QSvgRenderer(QByteArray(_svg(name, colour)))
	image = QPixmap(int(size * ratio), int(size * ratio))
	image.fill(Qt.GlobalColor.transparent)
	painter = QPainter(image)
	painter.setRenderHint(QPainter.RenderHint.Antialiasing)
	renderer.render(painter, QRectF(0, 0, size * ratio, size * ratio))
	painter.end()
	image.setDevicePixelRatio(ratio)
	return image


def icon(name: str, colour: str | None = None, size: int = 18) -> QIcon:
	"""Return an icon tinted ``colour`` (defaults to the theme's text colour)."""
	return QIcon(pixmap(name, colour or theme.tokens().text, size))


# Widgets whose icons depend on the theme, re-tinted when the theme changes.
_themed: list[tuple[weakref.ref, str, str, int]] = []


def _apply(widget, name: str, role: str, size: int) -> None:
	colour = getattr(theme.tokens(), role)
	if hasattr(widget, "setIcon"):
		widget.setIcon(icon(name, colour, size))
		widget.setIconSize(QSize(size, size))
	else:
		widget.setPixmap(pixmap(name, colour, size))


def bind(widget, name: str, role: str = "text", size: int = 18) -> None:
	"""Give ``widget`` (a button or label) a themed icon that follows theme changes."""
	_themed[:] = [entry for entry in _themed if entry[0]() is not widget]
	_themed.append((weakref.ref(widget), name, role, size))
	_apply(widget, name, role, size)


def refresh() -> None:
	"""Re-tint every bound icon for the current theme."""
	alive = []
	for ref, name, role, size in _themed:
		widget = ref()
		if widget is None or sip.isdeleted(widget):
			continue
		_apply(widget, name, role, size)
		alive.append((ref, name, role, size))
	_themed[:] = alive
