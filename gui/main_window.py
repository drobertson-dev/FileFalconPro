"""
Main window for File Falcon Pro: a sidebar on the left and one page per task.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QByteArray, Qt, QThreadPool, QTimer
from PyQt6.QtGui import QGuiApplication, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
	QApplication,
	QButtonGroup,
	QFrame,
	QHBoxLayout,
	QMainWindow,
	QMessageBox,
	QPushButton,
	QStackedWidget,
	QWidget,
)

from config import AppPaths, Settings
from gui import icons, theme
from gui.history_page import HistoryPage
from gui.organize_page import OrganizePage
from gui.widgets import hbox, label, make_logo, vbox


class MainWindow(QMainWindow):
	"""Main window for the File Falcon Pro application."""

	def __init__(self, settings: Settings, paths: AppPaths) -> None:
		super().__init__()
		self.settings = settings
		self.paths = paths
		self.setWindowTitle("File Falcon Pro")
		self.setMinimumSize(1040, 680)
		self.resize(1320, 860)

		self._save_timer = QTimer(self)
		self._save_timer.setSingleShot(True)
		self._save_timer.setInterval(400)
		self._save_timer.timeout.connect(self._save_now)

		self.organize = OrganizePage(settings, paths, self._save_timer.start)
		self.history = HistoryPage(paths)
		self.duplicates = self._make_duplicates_page()

		self.organize.historyChanged.connect(self.history.refresh)
		self.duplicates.historyChanged.connect(self.history.refresh)
		self.organize.undoRequested.connect(self.history.undo_journal)
		self.history.filesChanged.connect(self.organize.rescan)

		self._pages = QStackedWidget()
		self._nav = QButtonGroup(self)
		self._nav.setExclusive(True)
		sidebar = self._build_sidebar(
			[
				("Organize", "organize", self.organize, "Ctrl+1"),
				("Duplicates", "duplicates", self.duplicates, "Ctrl+2"),
				("History", "history", self.history, "Ctrl+3"),
			]
		)

		central = QWidget()
		layout = QHBoxLayout(central)
		layout.setContentsMargins(0, 0, 0, 0)
		layout.setSpacing(0)
		layout.addWidget(sidebar)
		layout.addWidget(self._pages, 1)
		self.setCentralWidget(central)

		if settings.window_geometry:
			self.restoreGeometry(QByteArray.fromBase64(settings.window_geometry.encode()))

		QGuiApplication.styleHints().colorSchemeChanged.connect(self._on_color_scheme_changed)

	def _make_duplicates_page(self) -> QWidget:
		from gui.duplicates_page import DuplicatesPage

		return DuplicatesPage(self.settings, self.paths, lambda: self._save_timer.start())

	def _build_sidebar(self, pages: list[tuple[str, str, QWidget, str]]) -> QFrame:
		sidebar = QFrame()
		sidebar.setObjectName("Sidebar")
		sidebar.setFixedWidth(212)

		brand = hbox(
			make_logo(30),
			vbox(
				label("File Falcon", "AppName"),
				label("Sort · Tidy · De-dupe", "AppTagline"),
				spacing=0,
			),
			None,
			spacing=10,
		)
		nav = vbox(spacing=4)
		for index, (title, icon_name, page, shortcut) in enumerate(pages):
			self._pages.addWidget(page)
			button = QPushButton(f"  {title}")
			button.setObjectName("NavButton")
			button.setCheckable(True)
			button.setCursor(Qt.CursorShape.PointingHandCursor)
			button.setToolTip(
				f"{title} ({QKeySequence(shortcut).toString(QKeySequence.SequenceFormat.NativeText)})"
			)
			icons.bind(button, icon_name, "muted", 18)
			button.toggled.connect(
				lambda checked, b=button, n=icon_name: icons.bind(
					b, n, "accent" if checked else "muted", 18
				)
			)
			button.clicked.connect(lambda _=False, i=index: self._pages.setCurrentIndex(i))
			QShortcut(QKeySequence(shortcut), self, activated=button.click)
			self._nav.addButton(button, index)
			nav.addWidget(button)
		self._nav.button(0).setChecked(True)

		footer = label(f"Version {QApplication.applicationVersion()}", "SidebarFooter")
		sidebar.setLayout(vbox(brand, 18, nav, None, footer, spacing=0, margins=(14, 18, 14, 14)))
		return sidebar

	def _on_color_scheme_changed(self, *_) -> None:
		theme.apply_theme(QApplication.instance())
		icons.refresh()
		for widget in QApplication.allWidgets():
			widget.update()

	def _save_now(self) -> None:
		self.settings.save(self.paths.settings_file)

	def closeEvent(self, event) -> None:  # noqa: N802
		busy = not self.organize.shutdown() or self.history.busy or self.duplicates.busy
		if busy:
			answer = QMessageBox.question(
				self,
				"Quit File Falcon Pro?",
				"Files are still being processed. Stop after the current file and quit?",
			)
			if answer != QMessageBox.StandardButton.Yes:
				event.ignore()
				return
			self.organize.cancel_run()
		self.duplicates.shutdown()
		QThreadPool.globalInstance().waitForDone(10_000)
		self.settings.window_geometry = bytes(self.saveGeometry().toBase64()).decode()
		self._save_now()
		super().closeEvent(event)

	def open_source(self, folder: Path) -> None:
		self.organize.set_source(folder)
