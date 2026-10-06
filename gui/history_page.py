"""
The History page: every copy or move run, with one-click undo.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
	QLabel,
	QMessageBox,
	QPushButton,
	QScrollArea,
	QStackedWidget,
	QVBoxLayout,
	QWidget,
)

from config import AppPaths
from gui import icons
from gui.widgets import (
	Banner,
	Card,
	ElidedLabel,
	EmptyState,
	hbox,
	human_size,
	label,
	plural,
	repolish,
	reveal_in_file_manager,
	short_path,
	vbox,
)
from gui.workers import Task, TaskSlot
from operations.planner import Operation
from operations.runner import RunRecord, UndoResult, list_history, load_record, undo_run

MAX_SHOWN = 200


def friendly_time(when: datetime) -> str:
	today = datetime.now().date()
	if when.date() == today:
		day = "Today"
	elif when.date() == today - timedelta(days=1):
		day = "Yesterday"
	elif when.year == today.year:
		day = f"{when.day} {when:%b}"
	else:
		day = f"{when.day} {when:%b %Y}"
	return f"{day} at {when:%H:%M}"


class RunCard(Card):
	"""One past run: what happened, where, and an Undo button."""

	undoClicked = pyqtSignal(object)

	def __init__(self, record: RunRecord) -> None:
		super().__init__()
		self.record = record
		moved = record.operation is Operation.MOVE

		badge = QLabel()
		badge.setFixedSize(36, 36)
		badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
		badge.setObjectName("Pill")
		badge.setProperty("tone", "accent")
		badge.setStyleSheet("border-radius: 18px; padding: 0;")
		icons.bind(badge, "move" if moved else "copy", "accent", 18)

		title = label(f"{'Moved' if moved else 'Copied'} {plural(record.transferred, 'file')}")
		font = title.font()
		font.setWeight(font.Weight.DemiBold)
		title.setFont(font)
		self._pill = label("", "Pill")
		self._pill.hide()

		route = ElidedLabel(f"{short_path(record.source)}  →  {short_path(record.dest)}")
		route.setObjectName("Muted")
		route.setToolTip(f"From: {record.source}\nTo: {record.dest}")

		meta = [friendly_time(record.started), human_size(record.bytes)]
		if record.skipped:
			meta.append(f"{record.skipped:,} skipped")
		if record.failed:
			meta.append(f"{record.failed:,} failed")
		details = label(" · ".join(meta), "Faint")

		self._show = QPushButton("Show")
		self._show.setObjectName("Small")
		self._show.setCursor(Qt.CursorShape.PointingHandCursor)
		self._show.clicked.connect(lambda: reveal_in_file_manager(Path(record.dest)))
		self._undo = QPushButton("Undo")
		self._undo.setObjectName("Small")
		self._undo.setCursor(Qt.CursorShape.PointingHandCursor)
		icons.bind(self._undo, "undo", "text", 14)
		self._undo.clicked.connect(lambda: self.undoClicked.emit(self.record))

		text = vbox(hbox(title, self._pill, None, spacing=8), route, details, spacing=3)
		layout = hbox(badge, text, self._show, self._undo, spacing=12, margins=(14, 12, 14, 12))
		layout.setStretch(1, 1)
		self.setLayout(layout)
		self.update_state()

	def update_state(self, busy: str = "") -> None:
		record = self.record
		pill, tone = "", ""
		if busy:
			pill, tone = busy, "accent"
		elif record.undone_at:
			pill, tone = f"Undone {friendly_time(record.undone_at).lower()}", ""
		elif record.interrupted:
			pill, tone = "Interrupted", "warning"
		elif record.cancelled:
			pill, tone = "Stopped early", "warning"
		elif record.failed:
			pill, tone = "Some files failed", "danger"
		self._pill.setText(pill)
		self._pill.setProperty("tone", tone)
		self._pill.setVisible(bool(pill))
		repolish(self._pill)
		self._undo.setEnabled(not busy and record.undone_at is None and record.transferred > 0)
		self._undo.setToolTip(
			"Put moved files back where they were"
			if record.operation is Operation.MOVE
			else "Move the copies to the Trash (the originals are untouched)"
		)


class HistoryPage(QWidget):
	"""Lists past runs and undoes them."""

	filesChanged = pyqtSignal()

	def __init__(self, paths: AppPaths) -> None:
		super().__init__()
		self.setObjectName("Page")
		self._paths = paths
		self._undo = TaskSlot()
		self._cards: dict[Path, RunCard] = {}

		title = label("History", "PageTitle")
		subtitle = label(
			"Every copy and move is recorded, so you can put things back.", "PageSubtitle"
		)
		self._banner = Banner()

		self._list = QVBoxLayout()
		self._list.setSpacing(10)
		self._list.setContentsMargins(0, 0, 6, 0)
		holder = QWidget()
		holder.setLayout(vbox(self._list, None, spacing=0))
		scroll = QScrollArea()
		scroll.setWidgetResizable(True)
		scroll.setWidget(holder)
		scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

		self._empty = EmptyState()
		self._empty.show_state(
			"history",
			"No runs yet",
			"When you copy or move files on the Organize page, each run shows up here.",
		)
		self._stack = QStackedWidget()
		self._stack.addWidget(self._empty)
		self._stack.addWidget(scroll)

		layout = vbox(
			vbox(title, subtitle, spacing=2),
			self._banner,
			self._stack,
			spacing=16,
			margins=(28, 22, 28, 22),
		)
		layout.setStretch(2, 1)
		self.setLayout(layout)
		self.refresh()

	def refresh(self) -> None:
		while self._list.count():
			item = self._list.takeAt(0)
			if item.widget():
				item.widget().deleteLater()
		self._cards.clear()
		records = list_history(self._paths.history_dir)[:MAX_SHOWN]
		for record in records:
			card = RunCard(record)
			card.undoClicked.connect(self.undo)
			self._list.addWidget(card)
			self._cards[record.journal] = card
		self._stack.setCurrentIndex(1 if records else 0)

	def undo_journal(self, journal: Path) -> None:
		record = load_record(journal)
		if record is not None:
			self.undo(record)

	def undo(self, record: RunRecord) -> None:
		if self._undo.running:
			QMessageBox.information(self, "Undo", "Another undo is still in progress.")
			return
		moved = record.operation is Operation.MOVE
		count = plural(record.transferred, "file")
		box = QMessageBox(self)
		box.setIcon(QMessageBox.Icon.Question)
		box.setWindowTitle("Undo")
		if moved:
			box.setText(f"Move {count} back?")
			box.setInformativeText(
				f"They will go back to where they were in “{short_path(record.source)}”."
			)
		else:
			box.setText(f"Remove the {count} that were copied?")
			box.setInformativeText(
				f"The copies in “{short_path(record.dest)}” go to the Trash. "
				"Your originals are not touched. Copies you changed since are kept."
			)
		confirm = box.addButton("Undo", QMessageBox.ButtonRole.AcceptRole)
		box.addButton(QMessageBox.StandardButton.Cancel)
		box.setDefaultButton(confirm)
		box.exec()
		if box.clickedButton() is not confirm:
			return

		card = self._cards.get(record.journal)
		if card:
			card.update_state("Undoing…")

		def on_progress(value) -> None:
			done, total = value
			if card:
				card.update_state(f"Undoing… {done:,} of {total:,}")

		self._undo.replace(
			Task(undo_run, record.journal),
			finished=lambda result: self._on_undone(record, result),
			progress=on_progress,
			failed=self._on_failed,
		)

	def _on_undone(self, record: RunRecord, result: UndoResult) -> None:
		moved = record.operation is Operation.MOVE
		text = (
			f"Moved {plural(result.restored, 'file')} back."
			if moved
			else f"Moved {plural(result.restored, 'copy', 'copies')} to the Trash."
		)
		if result.problems:
			text += f" {plural(len(result.problems), 'file')} needed attention and were left as is."
			details = "\n".join(f"{path}\n    {reason}" for path, reason in result.problems)
			self._banner.show_message(
				"warning", text, [("Details", lambda: self._show_details(details))]
			)
		else:
			self._banner.show_message("success", text)
		self.refresh()
		self.filesChanged.emit()

	def _on_failed(self, error: str) -> None:
		self._banner.show_message("danger", f"Undo failed: {error}")
		self.refresh()

	def _show_details(self, details: str) -> None:
		box = QMessageBox(self)
		box.setIcon(QMessageBox.Icon.Warning)
		box.setWindowTitle("Undo details")
		box.setText("These files were left where they are:")
		box.setDetailedText(details)
		box.exec()

	@property
	def busy(self) -> bool:
		return self._undo.running
