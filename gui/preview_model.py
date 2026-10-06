"""
Table model for the Organize preview: one row per file, showing where it will go.
"""

from __future__ import annotations

from enum import IntEnum
from pathlib import Path

from PyQt6.QtCore import (
	QAbstractTableModel,
	QModelIndex,
	QSortFilterProxyModel,
	Qt,
	pyqtSignal,
)
from PyQt6.QtGui import QColor

from gui import icons, theme
from gui.widgets import human_size
from operations.planner import Plan, PlannedFile, Status

SORT_ROLE = Qt.ItemDataRole.UserRole + 1

# Files in these states need nothing doing, so they get no checkbox.
SETTLED = (Status.IN_PLACE, Status.ALREADY_THERE)


class Column(IntEnum):
	NAME = 0
	SIZE = 1
	DATE = 2
	DESTINATION = 3
	STATUS = 4


HEADERS = {
	Column.NAME: "Name",
	Column.SIZE: "Size",
	Column.DATE: "Date",
	Column.DESTINATION: "Goes to",
	Column.STATUS: "Status",
}

_DATE_SOURCES = {
	"photo": "taken (from photo metadata)",
	"video": "recorded (from video metadata)",
	"file": "file date",
	None: "file date",
}


def status_text(planned: PlannedFile) -> str:
	if not planned.included and planned.status not in SETTLED:
		return "Skipped"
	return {
		Status.READY: "Ready",
		Status.RENAMED: "Renamed",
		Status.CHECK_FIRST: "Check first",
		Status.ALREADY_THERE: "Already there",
		Status.IN_PLACE: "Already sorted",
	}[planned.status]


def status_tooltip(planned: PlannedFile) -> str:
	if not planned.included and planned.status not in SETTLED:
		return "You unticked this file, so it will be left alone."
	match planned.status:
		case Status.RENAMED:
			return (
				f"A different file called “{planned.item.path.name}” is already there, "
				f"so this one will be saved as “{planned.dest.name}”."
			)
		case Status.CHECK_FIRST:
			return (
				"A file with the same name and size is already there. It will be compared "
				"byte-for-byte and skipped if identical, or saved with a new name if not."
			)
		case Status.ALREADY_THERE:
			return (
				"The same file (name, size and modified time) is already there, so it will "
				"be left alone."
			)
		case Status.IN_PLACE:
			return "This file is already where it belongs."
	return ""


class PlanModel(QAbstractTableModel):
	"""Rows are ``PlannedFile`` objects; the Name column carries the include checkbox."""

	inclusionChanged = pyqtSignal()

	def __init__(self) -> None:
		super().__init__()
		self._files: list[PlannedFile] = []
		self._dest_root: Path | None = None
		self.locked = False  # no ticking/unticking while a run is in progress

	def set_plan(self, plan: Plan | None) -> None:
		self.beginResetModel()
		self._files = list(plan.files) if plan else []
		self._dest_root = plan.dest_root if plan else None
		self.endResetModel()

	def planned(self, row: int) -> PlannedFile:
		return self._files[row]

	def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
		return 0 if parent.isValid() else len(self._files)

	def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
		return 0 if parent.isValid() else len(Column)

	def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
		if orientation == Qt.Orientation.Horizontal:
			if role == Qt.ItemDataRole.DisplayRole:
				return HEADERS[Column(section)]
			if role == Qt.ItemDataRole.TextAlignmentRole and section == Column.SIZE:
				return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
		return None

	def flags(self, index: QModelIndex) -> Qt.ItemFlag:
		flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
		if (
			index.column() == Column.NAME
			and not self.locked
			and self._files[index.row()].status not in SETTLED
		):
			flags |= Qt.ItemFlag.ItemIsUserCheckable
		return flags

	def _relative_dest(self, planned: PlannedFile) -> str:
		"""Destination folder relative to the root, plus the new name if it changes."""
		folder = planned.dest.parent
		if self._dest_root is not None and folder.is_relative_to(self._dest_root):
			text = folder.relative_to(self._dest_root).as_posix()
			text = "" if text == "." else text + "/"
		else:
			text = str(folder) + "/"
		if planned.dest.name != planned.item.path.name:
			text += planned.dest.name
		return text or "(top level)"

	def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
		if not index.isValid():
			return None
		planned = self._files[index.row()]
		item = planned.item
		column = Column(index.column())
		t = theme.tokens()
		inactive = not planned.actionable

		if role == Qt.ItemDataRole.DisplayRole:
			match column:
				case Column.NAME:
					return item.path.name
				case Column.SIZE:
					return human_size(item.size)
				case Column.DATE:
					return f"{item.date.day} {item.date:%b %Y}"
				case Column.DESTINATION:
					return self._relative_dest(planned)
				case Column.STATUS:
					return status_text(planned)

		if role == SORT_ROLE:
			match column:
				case Column.NAME:
					return item.path.name.casefold()
				case Column.SIZE:
					return item.size
				case Column.DATE:
					return item.date.timestamp()
				case Column.DESTINATION:
					return self._relative_dest(planned).casefold()
				case Column.STATUS:
					return status_text(planned)

		if role == Qt.ItemDataRole.CheckStateRole and column == Column.NAME:
			if planned.status in SETTLED:
				return None
			return Qt.CheckState.Checked if planned.included else Qt.CheckState.Unchecked

		if role == Qt.ItemDataRole.DecorationRole and column == Column.NAME:
			return icons.pixmap(item.category.key, t.faint if inactive else t.muted, 15)

		if role == Qt.ItemDataRole.ForegroundRole:
			if inactive:
				return QColor(t.faint)
			if column == Column.STATUS:
				return QColor(
					{
						Status.READY: t.success,
						Status.RENAMED: t.warning,
						Status.CHECK_FIRST: t.muted,
					}.get(planned.status, t.faint)
				)
			if column in (Column.SIZE, Column.DATE, Column.DESTINATION):
				return QColor(t.muted)

		if role == Qt.ItemDataRole.TextAlignmentRole and column == Column.SIZE:
			return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter

		if role == Qt.ItemDataRole.ToolTipRole:
			match column:
				case Column.NAME:
					return str(item.path)
				case Column.DATE:
					return f"{item.date.day} {item.date:%B %Y, %H:%M} · {_DATE_SOURCES[item.date_source]}"
				case Column.DESTINATION:
					return str(planned.dest)
				case Column.STATUS:
					return status_tooltip(planned) or None
				case Column.SIZE:
					return f"{item.size:,} bytes"
		return None

	def setData(self, index: QModelIndex, value, role: int = Qt.ItemDataRole.EditRole) -> bool:  # noqa: N802
		if role != Qt.ItemDataRole.CheckStateRole or index.column() != Column.NAME:
			return False
		self.set_included([index.row()], Qt.CheckState(value) == Qt.CheckState.Checked)
		return True

	def set_included(self, rows: list[int], included: bool) -> None:
		if self.locked:
			return
		changed = False
		for row in rows:
			planned = self._files[row]
			if planned.status in SETTLED or planned.included == included:
				continue
			planned.included = included
			changed = True
			self.dataChanged.emit(self.index(row, 0), self.index(row, len(Column) - 1))
		if changed:
			self.inclusionChanged.emit()

	def files(self) -> list[PlannedFile]:
		return self._files


class PlanFilterModel(QSortFilterProxyModel):
	"""Search across name and destination, and sort by raw values."""

	def __init__(self) -> None:
		super().__init__()
		self.setSortRole(SORT_ROLE)
		self.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
		self._needle = ""

	def set_search(self, text: str) -> None:
		self._needle = text.strip().casefold()
		self.invalidateFilter()

	def filterAcceptsRow(self, row: int, parent: QModelIndex) -> bool:  # noqa: N802
		if not self._needle:
			return True
		model: PlanModel = self.sourceModel()
		planned = model.planned(row)
		return (
			self._needle in planned.item.path.name.casefold()
			or self._needle in str(planned.dest).casefold()
			or self._needle in str(planned.item.rel).casefold()
		)
