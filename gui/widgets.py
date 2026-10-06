"""
Reusable widgets for File Falcon Pro: folder drop targets, file-type chips, switches,
segmented controls, banners and empty states.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable, Iterable
from pathlib import Path

from PyQt6.QtCore import (
	QEasingCurve,
	QPropertyAnimation,
	QRectF,
	QSize,
	Qt,
	QUrl,
	pyqtProperty,
	pyqtSignal,
)
from PyQt6.QtGui import (
	QColor,
	QDesktopServices,
	QFont,
	QFontMetrics,
	QLinearGradient,
	QPainter,
	QPainterPath,
	QPen,
	QPixmap,
)
from PyQt6.QtWidgets import (
	QAbstractButton,
	QButtonGroup,
	QFileDialog,
	QFrame,
	QHBoxLayout,
	QLabel,
	QProgressBar,
	QPushButton,
	QSizePolicy,
	QVBoxLayout,
	QWidget,
)

from gui import icons, theme

# --- formatting helpers ----------------------------------------------------------------


def human_size(size: float) -> str:
	"""Format a byte count the way Finder does (decimal units)."""
	for unit in ("bytes", "KB", "MB", "GB", "TB"):
		if size < 1000 or unit == "TB":
			if unit == "bytes":
				return f"{int(size):,} bytes" if size != 1 else "1 byte"
			return f"{size:.1f} {unit}" if size < 100 else f"{size:.0f} {unit}"
		size /= 1000
	return f"{size:.1f} TB"


def plural(count: int, word: str, plural_word: str | None = None) -> str:
	return f"{count:,} {word if count == 1 else (plural_word or word + 's')}"


def short_path(path: str | Path) -> str:
	"""Abbreviate the home folder to ~."""
	text = str(path)
	home = str(Path.home())
	return "~" + text[len(home) :] if text == home or text.startswith(home + "/") else text


def reveal_in_file_manager(path: Path) -> None:
	"""Show ``path`` selected in Finder/Explorer, or open it if it is a folder."""
	if path.is_dir():
		QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
	elif sys.platform == "darwin":
		subprocess.run(["open", "-R", str(path)], check=False)
	elif sys.platform == "win32":
		subprocess.run(["explorer", "/select,", str(path)], check=False)
	else:
		QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))


def open_file(path: Path) -> None:
	QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


def label(text: str = "", object_name: str | None = None, *, wrap: bool = False) -> QLabel:
	widget = QLabel(text)
	if object_name:
		widget.setObjectName(object_name)
	widget.setWordWrap(wrap)
	return widget


def section_label(text: str) -> QLabel:
	return label(text.upper(), "SectionLabel")


def hbox(*widgets, spacing: int = 8, margins: tuple[int, int, int, int] = (0, 0, 0, 0)):
	layout = QHBoxLayout()
	layout.setSpacing(spacing)
	layout.setContentsMargins(*margins)
	for widget in widgets:
		if widget is None:
			layout.addStretch(1)
		elif isinstance(widget, int):
			layout.addSpacing(widget)
		elif isinstance(widget, QWidget):
			layout.addWidget(widget)
		else:
			layout.addLayout(widget)
	return layout


def vbox(*widgets, spacing: int = 8, margins: tuple[int, int, int, int] = (0, 0, 0, 0)):
	layout = QVBoxLayout()
	layout.setSpacing(spacing)
	layout.setContentsMargins(*margins)
	for widget in widgets:
		if widget is None:
			layout.addStretch(1)
		elif isinstance(widget, int):
			layout.addSpacing(widget)
		elif isinstance(widget, QWidget):
			layout.addWidget(widget)
		else:
			layout.addLayout(widget)
	return layout


def scaled_font(font: QFont, factor: float, weight: QFont.Weight | None = None) -> QFont:
	"""Return a copy of ``font`` scaled by ``factor``, whether sized in points or pixels."""
	font = QFont(font)
	if font.pointSizeF() > 0:
		font.setPointSizeF(font.pointSizeF() * factor)
	elif font.pixelSize() > 0:
		font.setPixelSize(round(font.pixelSize() * factor))
	if weight is not None:
		font.setWeight(weight)
	return font


def repolish(widget: QWidget) -> None:
	"""Re-apply the stylesheet after changing a dynamic property."""
	widget.style().unpolish(widget)
	widget.style().polish(widget)
	widget.update()


# --- labels ----------------------------------------------------------------------------


class ElidedLabel(QLabel):
	"""A single-line label that shortens its text with … instead of growing."""

	def __init__(
		self, text: str = "", mode: Qt.TextElideMode = Qt.TextElideMode.ElideMiddle
	) -> None:
		super().__init__()
		self._full = text
		self._mode = mode
		self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
		self.setText(text)

	def setText(self, text: str) -> None:  # noqa: N802 - Qt naming
		self._full = text
		self._update_elided()

	def full_text(self) -> str:
		return self._full

	def resizeEvent(self, event) -> None:  # noqa: N802
		super().resizeEvent(event)
		self._update_elided()

	def _update_elided(self) -> None:
		metrics = QFontMetrics(self.font())
		super().setText(metrics.elidedText(self._full, self._mode, max(self.width(), 10)))

	def minimumSizeHint(self) -> QSize:  # noqa: N802
		return QSize(10, super().minimumSizeHint().height())


# --- folder card -----------------------------------------------------------------------


class FolderCard(QFrame):
	"""A card showing a chosen folder; click to browse or drop a folder onto it."""

	folderChosen = pyqtSignal(str)

	def __init__(self, caption: str, placeholder: str, dialog_title: str) -> None:
		super().__init__()
		self.setObjectName("FolderCard")
		self.setAcceptDrops(True)
		self.setCursor(Qt.CursorShape.PointingHandCursor)
		self.setMinimumHeight(84)
		self._dialog_title = dialog_title
		self._placeholder = placeholder
		self._path: Path | None = None

		self._icon = QLabel()
		self._icon.setFixedSize(34, 34)
		self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
		icons.bind(self._icon, "folder", "accent", 26)

		self._caption = section_label(caption)
		self._title = ElidedLabel("", Qt.TextElideMode.ElideRight)
		self._detail = ElidedLabel("")
		self._detail.setObjectName("Muted")
		self._button = QPushButton("Choose…")
		self._button.setObjectName("Small")
		self._button.setCursor(Qt.CursorShape.PointingHandCursor)
		self._button.clicked.connect(self.browse)

		text = vbox(self._caption, self._title, self._detail, spacing=2)
		layout = hbox(self._icon, 4, text, self._button, spacing=10, margins=(14, 12, 14, 12))
		layout.setStretch(2, 1)
		self.setLayout(layout)
		self.set_path(None)

	@property
	def path(self) -> Path | None:
		return self._path

	def set_path(self, path: Path | None, detail: str = "") -> None:
		self._path = path
		if path is None:
			self._title.setObjectName("FolderPlaceholder")
			self._title.setText(self._placeholder)
			self._detail.setText("Click to browse, or drop a folder here")
			self._button.setText("Choose…")
		else:
			self._title.setObjectName("FolderPath")
			self._title.setText(path.name or str(path))
			self._detail.setText(detail or short_path(path))
			self._button.setText("Change…")
		self._title.setToolTip(str(path) if path else "")
		self.setProperty("empty", path is None)
		repolish(self)
		repolish(self._title)

	def set_detail(self, detail: str) -> None:
		if self._path is not None:
			self._detail.setText(detail)
			self._detail.setToolTip(str(self._path))

	def browse(self) -> None:
		start = str(self._path) if self._path else str(Path.home())
		chosen = QFileDialog.getExistingDirectory(self.window(), self._dialog_title, start)
		if chosen:
			self.folderChosen.emit(chosen)

	def mouseReleaseEvent(self, event) -> None:  # noqa: N802
		if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
			self.browse()
		super().mouseReleaseEvent(event)

	def _dropped_folder(self, event) -> str | None:
		urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
		for url in urls:
			if url.isLocalFile() and Path(url.toLocalFile()).is_dir():
				return url.toLocalFile()
		return None

	def dragEnterEvent(self, event) -> None:  # noqa: N802
		if self.isEnabled() and self._dropped_folder(event):
			event.acceptProposedAction()
			self.setProperty("dropping", True)
			repolish(self)

	def dragLeaveEvent(self, event) -> None:  # noqa: N802
		self.setProperty("dropping", False)
		repolish(self)

	def dropEvent(self, event) -> None:  # noqa: N802
		self.setProperty("dropping", False)
		repolish(self)
		if folder := self._dropped_folder(event):
			event.acceptProposedAction()
			self.folderChosen.emit(folder)


# --- chips, switches, segmented controls -----------------------------------------------


class Chip(QAbstractButton):
	"""A toggleable file-type chip: icon, name and a file count."""

	def __init__(self, icon_name: str, text: str) -> None:
		super().__init__()
		self.setCheckable(True)
		self.setText(text)
		self.setCursor(Qt.CursorShape.PointingHandCursor)
		self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
		self._icon_name = icon_name
		self._count: int | None = None
		self._hover = False

	def set_count(self, count: int | None) -> None:
		self._count = count
		self.update()

	def sizeHint(self) -> QSize:  # noqa: N802
		return QSize(130, 36)

	def enterEvent(self, event) -> None:  # noqa: N802
		self._hover = True
		self.update()

	def leaveEvent(self, event) -> None:  # noqa: N802
		self._hover = False
		self.update()

	def paintEvent(self, event) -> None:  # noqa: N802
		t = theme.tokens()
		painter = QPainter(self)
		painter.setRenderHint(QPainter.RenderHint.Antialiasing)
		rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
		checked = self.isChecked()
		empty = self._count == 0
		if checked:
			fill, border, text_colour, icon_colour = t.accent_soft, t.accent, t.text, t.accent
		else:
			fill = t.surface
			border = t.border_strong if self._hover else t.border
			text_colour = t.faint if empty else (t.text if self._hover else t.muted)
			icon_colour = t.faint if empty else t.muted
		painter.setPen(QPen(QColor(border), 1))
		painter.setBrush(QColor(fill))
		painter.drawRoundedRect(rect, 9, 9)

		painter.drawPixmap(
			10, (self.height() - 16) // 2, icons.pixmap(self._icon_name, icon_colour, 16)
		)

		font = scaled_font(
			self.font(), 0.96, QFont.Weight.DemiBold if checked else QFont.Weight.Medium
		)
		painter.setFont(font)
		count_text = "" if self._count is None else f"{self._count:,}"
		metrics = QFontMetrics(font)
		count_width = (
			QFontMetrics(scaled_font(self.font(), 0.85)).horizontalAdvance(count_text) + 10
			if count_text
			else 0
		)
		text_rect = QRectF(32, 0, self.width() - 32 - count_width - 4, self.height())
		painter.setPen(QColor(text_colour))
		painter.drawText(
			text_rect,
			Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
			metrics.elidedText(self.text(), Qt.TextElideMode.ElideRight, int(text_rect.width())),
		)
		if count_text:
			painter.setFont(scaled_font(self.font(), 0.85))
			painter.setPen(QColor(t.accent if checked else t.faint))
			painter.drawText(
				QRectF(0, 0, self.width() - 10, self.height()),
				Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
				count_text,
			)
		painter.end()


class Switch(QAbstractButton):
	"""An animated on/off switch."""

	def __init__(self, checked: bool = False) -> None:
		super().__init__()
		self.setCheckable(True)
		self.setCursor(Qt.CursorShape.PointingHandCursor)
		self.setFixedSize(34, 20)
		self._offset = 1.0 if checked else 0.0
		self._animation = QPropertyAnimation(self, b"offset", self)
		self._animation.setDuration(140)
		self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
		self.setChecked(checked)
		self.toggled.connect(self._animate)

	def _get_offset(self) -> float:
		return self._offset

	def _set_offset(self, value: float) -> None:
		self._offset = value
		self.update()

	offset = pyqtProperty(float, fget=_get_offset, fset=_set_offset)

	def _animate(self, checked: bool) -> None:
		self._animation.stop()
		if not self.isVisible():
			self._set_offset(1.0 if checked else 0.0)
			return
		self._animation.setStartValue(self._offset)
		self._animation.setEndValue(1.0 if checked else 0.0)
		self._animation.start()

	def checkStateSet(self) -> None:  # noqa: N802 - called by setChecked, even with signals blocked
		super().checkStateSet()
		if self._animation.state() != QPropertyAnimation.State.Running:
			self._offset = 1.0 if self.isChecked() else 0.0
			self.update()

	def paintEvent(self, event) -> None:  # noqa: N802
		t = theme.tokens()
		painter = QPainter(self)
		painter.setRenderHint(QPainter.RenderHint.Antialiasing)
		off, on = QColor(t.border_strong), QColor(t.accent)
		track = QColor(
			int(off.red() + (on.red() - off.red()) * self._offset),
			int(off.green() + (on.green() - off.green()) * self._offset),
			int(off.blue() + (on.blue() - off.blue()) * self._offset),
		)
		if not self.isEnabled():
			track.setAlphaF(0.45)
		painter.setPen(Qt.PenStyle.NoPen)
		painter.setBrush(track)
		painter.drawRoundedRect(QRectF(self.rect()), self.height() / 2, self.height() / 2)
		knob = self.height() - 4
		x = 2 + (self.width() - knob - 4) * self._offset
		painter.setBrush(QColor("#ffffff"))
		painter.drawEllipse(QRectF(x, 2, knob, knob))
		painter.end()


class SwitchRow(QWidget):
	"""A labelled switch with an optional hint underneath."""

	toggled = pyqtSignal(bool)

	def __init__(self, title: str, hint: str = "", checked: bool = False) -> None:
		super().__init__()
		self.switch = Switch(checked)
		self.switch.toggled.connect(self.toggled)
		self.title = label(title)
		self.hint = label(hint, "Hint", wrap=True)
		self.hint.setVisible(bool(hint))
		self.extra = QHBoxLayout()
		self.extra.setContentsMargins(0, 0, 0, 0)
		self.setLayout(vbox(hbox(self.title, None, self.switch), self.extra, self.hint, spacing=4))

	def mouseReleaseEvent(self, event) -> None:  # noqa: N802
		if self.isEnabled() and self.title.geometry().contains(event.position().toPoint()):
			self.switch.toggle()

	def isChecked(self) -> bool:  # noqa: N802
		return self.switch.isChecked()

	def setChecked(self, checked: bool) -> None:  # noqa: N802
		self.switch.setChecked(checked)

	def set_hint(self, text: str) -> None:
		self.hint.setText(text)
		self.hint.setVisible(bool(text))


class Segmented(QFrame):
	"""A row of mutually exclusive options, like a macOS segmented control."""

	changed = pyqtSignal(str)

	def __init__(self, options: Iterable[tuple[str, str, str | None]]) -> None:
		super().__init__()
		self.setObjectName("Segmented")
		self._group = QButtonGroup(self)
		self._group.setExclusive(True)
		self._buttons: dict[str, QPushButton] = {}
		layout = hbox(spacing=2, margins=(3, 3, 3, 3))
		for value, text, icon_name in options:
			button = QPushButton(text)
			button.setCheckable(True)
			button.setCursor(Qt.CursorShape.PointingHandCursor)
			if icon_name:
				icons.bind(button, icon_name, "text", 15)
			button.setProperty("value", value)
			self._group.addButton(button)
			self._buttons[value] = button
			layout.addWidget(button)
		self.setLayout(layout)
		self._group.buttonClicked.connect(
			lambda button: self.changed.emit(button.property("value"))
		)

	def value(self) -> str:
		checked = self._group.checkedButton()
		return checked.property("value") if checked else ""

	def set_value(self, value: str) -> None:
		if value in self._buttons:
			self._buttons[value].setChecked(True)


# --- banners and empty states ----------------------------------------------------------


class Banner(QFrame):
	"""An inline message with optional action buttons."""

	_ICONS = {"success": "check-circle", "warning": "alert", "danger": "alert", "info": "sparkle"}
	_ROLES = {"success": "success", "warning": "warning", "danger": "danger", "info": "accent"}

	def __init__(self) -> None:
		super().__init__()
		self.setObjectName("Banner")
		self._icon = QLabel()
		self._text = label("", "BannerText", wrap=True)
		self._actions = QHBoxLayout()
		self._actions.setSpacing(6)
		close = QPushButton()
		close.setObjectName("Ghost")
		close.setFixedSize(26, 26)
		close.setCursor(Qt.CursorShape.PointingHandCursor)
		close.setToolTip("Dismiss")
		icons.bind(close, "close", "muted", 14)
		close.clicked.connect(self.hide)
		layout = hbox(
			self._icon, self._text, self._actions, close, spacing=10, margins=(12, 8, 8, 8)
		)
		layout.setStretch(1, 1)
		self.setLayout(layout)
		self.hide()

	def show_message(
		self, tone: str, text: str, actions: Iterable[tuple[str, Callable[[], None]]] = ()
	) -> None:
		self.setProperty("tone", tone)
		repolish(self)
		icons.bind(
			self._icon, self._ICONS.get(tone, "sparkle"), self._ROLES.get(tone, "accent"), 18
		)
		self._text.setText(text)
		while self._actions.count():
			item = self._actions.takeAt(0)
			if item.widget():
				item.widget().deleteLater()
		for caption, callback in actions:
			button = QPushButton(caption)
			button.setObjectName("Small")
			button.setCursor(Qt.CursorShape.PointingHandCursor)
			button.clicked.connect(callback)
			self._actions.addWidget(button)
		self.show()


class EmptyState(QWidget):
	"""Centered icon, title and explanation, with an optional busy indicator."""

	def __init__(self) -> None:
		super().__init__()
		self._icon = QLabel()
		self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
		self._title = label("")
		self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)
		self._title.setObjectName("EmptyTitle")
		self._text = label("", "Muted", wrap=True)
		self._text.setAlignment(Qt.AlignmentFlag.AlignCenter)
		self._text.setFixedWidth(360)  # a fixed width lets Qt size wrapped text correctly
		self._busy = QProgressBar()
		self._busy.setRange(0, 0)
		self._busy.setFixedWidth(180)
		self._busy.setTextVisible(False)
		self._busy.hide()
		self.setLayout(
			vbox(
				None,
				self._icon,
				6,
				self._title,
				hbox(None, self._text, None),
				8,
				hbox(None, self._busy, None),
				None,
				spacing=6,
			)
		)

	def show_state(self, icon_name: str, title: str, text: str = "", busy: bool = False) -> None:
		icons.bind(self._icon, icon_name, "faint", 44)
		self._title.setText(title)
		self._text.setText(text)
		self._text.setMinimumHeight(self._text.heightForWidth(self._text.width()))
		self._text.setVisible(bool(text))
		self._busy.setVisible(busy)


class Card(QFrame):
	def __init__(self) -> None:
		super().__init__()
		self.setObjectName("Card")


def paint_logo(painter: QPainter, rect: QRectF) -> None:
	"""Draw the app's mark: a gradient tile with a white falcon wing."""
	size = rect.width()
	gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
	gradient.setColorAt(0, QColor("#6a74f0"))
	gradient.setColorAt(1, QColor("#3d3fb8"))
	painter.save()
	painter.setRenderHint(QPainter.RenderHint.Antialiasing)
	painter.setPen(Qt.PenStyle.NoPen)
	painter.setBrush(gradient)
	painter.drawRoundedRect(rect, size * 0.26, size * 0.26)
	painter.translate(rect.topLeft())
	s = size / 24
	wing = QPainterPath()
	wing.moveTo(4.5 * s, 15.5 * s)
	wing.cubicTo(9 * s, 15.5 * s, 14 * s, 12.5 * s, 18.5 * s, 5.5 * s)
	wing.cubicTo(19.5 * s, 9.5 * s, 18 * s, 13 * s, 15.5 * s, 15 * s)
	wing.cubicTo(17 * s, 15 * s, 18.3 * s, 14.6 * s, 19.5 * s, 14 * s)
	wing.cubicTo(17.5 * s, 18 * s, 13 * s, 19.5 * s, 4.5 * s, 15.5 * s)
	painter.setBrush(QColor("#ffffff"))
	painter.drawPath(wing)
	painter.restore()


def logo_pixmap(size: int, ratio: float = 2.0) -> QPixmap:
	pixmap = QPixmap(int(size * ratio), int(size * ratio))
	pixmap.fill(Qt.GlobalColor.transparent)
	pixmap.setDevicePixelRatio(ratio)
	painter = QPainter(pixmap)
	paint_logo(painter, QRectF(0, 0, size, size))
	painter.end()
	return pixmap


def make_logo(size: int = 30) -> QLabel:
	"""The app's badge as a fixed-size label."""
	logo = QLabel()
	logo.setPixmap(logo_pixmap(size))
	logo.setFixedSize(size, size)
	return logo
