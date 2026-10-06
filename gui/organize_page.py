"""
The Organize page: pick a source and destination, choose what to sort and how, check the
live preview, then copy or move with one click.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QItemSelectionModel, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import (
	QAbstractItemView,
	QComboBox,
	QFrame,
	QGridLayout,
	QHeaderView,
	QLineEdit,
	QMenu,
	QMessageBox,
	QProgressBar,
	QPushButton,
	QScrollArea,
	QStackedWidget,
	QTableView,
	QWidget,
)

from config import AppPaths, Settings
from gui import icons
from gui.preview_model import SETTLED, Column, PlanFilterModel, PlanModel
from gui.widgets import (
	Banner,
	Card,
	Chip,
	ElidedLabel,
	EmptyState,
	FolderCard,
	Segmented,
	SwitchRow,
	hbox,
	human_size,
	label,
	open_file,
	plural,
	reveal_in_file_manager,
	section_label,
	short_path,
	vbox,
)
from gui.workers import Task, TaskSlot
from operations.dates import resolve_capture_dates
from operations.file_extensions import ALL_CATEGORIES
from operations.planner import (
	DateLayout,
	NameMatch,
	Operation,
	OrganizeOptions,
	Plan,
	Status,
	build_plan,
	name_matcher,
)
from operations.runner import RunResult, execute_plan
from operations.scanner import FileItem, scan_folder


def _plan_job(
	items: list[FileItem],
	options: OrganizeOptions,
	source: Path,
	dest: Path,
	excluded: frozenset[Path],
	*,
	progress,
	cancelled,
) -> Plan:
	if dest != source and dest.is_relative_to(source):
		items = [item for item in items if not item.path.is_relative_to(dest)]
	return build_plan(items, options, dest, excluded=excluded)


class OrganizePage(QWidget):
	"""Sort files from one folder into a tidy structure in another."""

	historyChanged = pyqtSignal()
	undoRequested = pyqtSignal(Path)

	def __init__(self, settings: Settings, paths: AppPaths, save: Callable[[], None]) -> None:
		super().__init__()
		self.setObjectName("Page")
		self._settings = settings
		self._paths = paths
		self._save = save

		self._items: list[FileItem] | None = None
		self._plan: Plan | None = None
		self._excluded: set[Path] = set()
		self._scan = TaskSlot()
		self._dates = TaskSlot()
		self._planner = TaskSlot()
		self._runner = TaskSlot()
		self._date_progress = ""

		self._refresh_timer = QTimer(self)
		self._refresh_timer.setSingleShot(True)
		self._refresh_timer.setInterval(120)
		self._refresh_timer.timeout.connect(self._refresh)

		self._build()
		self._restore()

	# --- construction --------------------------------------------------------------

	def _build(self) -> None:
		title = label("Organize", "PageTitle")
		subtitle = label(
			"Sort files from one folder into a tidy structure — by type, by date, or both.",
			"PageSubtitle",
		)

		self._source_card = FolderCard("From", "Choose a folder to sort", "Folder to sort")
		self._dest_card = FolderCard("To", "Choose where sorted files go", "Destination folder")
		self._source_card.folderChosen.connect(lambda path: self.set_source(Path(path)))
		self._dest_card.folderChosen.connect(lambda path: self.set_dest(Path(path)))
		arrow = label()
		icons.bind(arrow, "arrow-right", "faint", 20)
		folders = hbox(self._source_card, arrow, self._dest_card, spacing=12)
		folders.setStretch(0, 1)
		folders.setStretch(2, 1)

		self._banner = Banner()

		self._options_panel = self._build_options()
		preview = self._build_preview()
		body = hbox(self._options_panel, preview, spacing=16)
		body.setStretch(1, 1)

		content = vbox(
			vbox(title, subtitle, spacing=2),
			folders,
			self._banner,
			body,
			spacing=16,
			margins=(28, 22, 28, 16),
		)
		content.setStretch(3, 1)
		self.setLayout(vbox(content, self._build_action_bar(), spacing=0))

	def _build_options(self) -> QWidget:
		panel = QWidget()

		# File types
		self._chips: dict[str, Chip] = {}
		grid = QGridLayout()
		grid.setSpacing(6)
		for index, category in enumerate(ALL_CATEGORIES):
			chip = Chip(category.key, category.label)
			chip.toggled.connect(self._options_changed)
			self._chips[category.key] = chip
			grid.addWidget(chip, index // 2, index % 2)
		select_all = QPushButton("All")
		select_none = QPushButton("None")
		for button in (select_all, select_none):
			button.setObjectName("Ghost")
			button.setCursor(Qt.CursorShape.PointingHandCursor)
		select_all.clicked.connect(lambda: self._select_categories(all_=True))
		select_none.clicked.connect(lambda: self._select_categories(all_=False))
		types_section = vbox(
			hbox(section_label("File types"), None, select_all, select_none, spacing=0),
			grid,
			spacing=8,
		)

		# Folder structure
		self._by_type = SwitchRow("Group by type", "Photos, Videos, Documents…")
		self._by_date = SwitchRow("Group by date")
		self._date_layout = QComboBox()
		for layout in DateLayout:
			self._date_layout.addItem(layout.label, layout.value)
		self._by_date.extra.addWidget(self._date_layout)
		self._by_date.set_hint("Uses the date a photo or video was taken, when it has one.")
		self._keep_structure = SwitchRow("Keep original subfolders")
		self._example = ElidedLabel("", Qt.TextElideMode.ElideLeft)
		self._example.setObjectName("PathExample")
		for widget in (self._by_type, self._by_date, self._keep_structure):
			widget.toggled.connect(self._options_changed)
		self._date_layout.currentIndexChanged.connect(self._options_changed)
		structure_section = vbox(
			section_label("Folder structure"),
			self._by_type,
			self._by_date,
			self._keep_structure,
			self._example,
			spacing=10,
		)

		# Name filter
		self._name_match = QComboBox()
		for match in NameMatch:
			self._name_match.addItem(match.label, match.value)
		self._name_filter = QLineEdit()
		self._name_filter.setPlaceholderText("Any name")
		self._name_filter.setClearButtonEnabled(True)
		self._name_error = label("", "Error", wrap=True)
		self._name_error.hide()
		self._name_match.currentIndexChanged.connect(self._options_changed)
		self._name_filter.textChanged.connect(self._options_changed)
		name_section = vbox(
			section_label("Only names that…"),
			hbox(self._name_match, self._name_filter, spacing=6),
			self._name_error,
			spacing=8,
		)

		# Action
		self._operation = Segmented(
			[(Operation.COPY.value, "Copy", "copy"), (Operation.MOVE.value, "Move", "move")]
		)
		self._operation.changed.connect(self._options_changed)
		self._operation_hint = label("", "Hint", wrap=True)
		self._remove_empty = SwitchRow("Tidy up emptied folders", checked=True)
		self._remove_empty.toggled.connect(self._options_changed)
		action_section = vbox(
			section_label("Action"),
			self._operation,
			self._operation_hint,
			self._remove_empty,
			spacing=8,
		)

		card = Card()
		card.setLayout(
			vbox(
				types_section,
				self._divider(),
				structure_section,
				self._divider(),
				name_section,
				self._divider(),
				action_section,
				None,
				spacing=16,
				margins=(16, 14, 16, 16),
			)
		)
		scroll = QScrollArea()
		scroll.setWidgetResizable(True)
		scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
		scroll.setFrameShape(QFrame.Shape.NoFrame)
		scroll.setWidget(card)
		scroll.setFixedWidth(356)
		panel.setLayout(vbox(scroll))
		return panel

	@staticmethod
	def _divider() -> QFrame:
		line = QFrame()
		line.setFrameShape(QFrame.Shape.HLine)
		line.setObjectName("Divider")
		line.setStyleSheet("QFrame#Divider { border: none; border-top: 1px solid palette(mid); }")
		line.setFixedHeight(1)
		return line

	def _build_preview(self) -> QWidget:
		heading = label("Preview", "CardTitle")
		self._preview_note = label("", "Muted")

		self._search = QLineEdit()
		self._search.setObjectName("Search")
		self._search.setPlaceholderText("Search files")
		self._search.setClearButtonEnabled(True)
		self._search.setFixedWidth(220)
		self._search.addAction(
			icons.icon("search", None, 14), QLineEdit.ActionPosition.LeadingPosition
		)
		self._rescan = QPushButton()
		self._rescan.setObjectName("Ghost")
		self._rescan.setToolTip("Scan the folder again")
		self._rescan.setCursor(Qt.CursorShape.PointingHandCursor)
		icons.bind(self._rescan, "refresh", "muted", 16)
		self._rescan.clicked.connect(self.rescan)

		self._model = PlanModel()
		self._model.inclusionChanged.connect(self._on_inclusion_changed)
		self._proxy = PlanFilterModel()
		self._proxy.setSourceModel(self._model)
		self._search.textChanged.connect(self._proxy.set_search)

		table = QTableView()
		table.setModel(self._proxy)
		table.setSortingEnabled(True)
		table.sortByColumn(Column.NAME, Qt.SortOrder.AscendingOrder)
		table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
		table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
		table.setAlternatingRowColors(True)
		table.setShowGrid(False)
		table.setWordWrap(False)
		table.setTextElideMode(Qt.TextElideMode.ElideMiddle)
		table.verticalHeader().hide()
		table.verticalHeader().setDefaultSectionSize(30)
		table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
		table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
		header = table.horizontalHeader()
		header.setHighlightSections(False)
		header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
		header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
		header.setSectionResizeMode(Column.DESTINATION, QHeaderView.ResizeMode.Stretch)
		for column, width in (
			(Column.NAME, 180),
			(Column.SIZE, 72),
			(Column.DATE, 102),
			(Column.STATUS, 112),
		):
			table.setColumnWidth(column, width)
		table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
		table.customContextMenuRequested.connect(self._show_table_menu)
		table.doubleClicked.connect(lambda index: open_file(self._planned_at(index).item.path))
		toggle = QAction(table)
		toggle.setShortcut(QKeySequence(Qt.Key.Key_Space))
		toggle.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
		toggle.triggered.connect(self._toggle_selected)
		table.addAction(toggle)
		self._table = table

		self._empty = EmptyState()
		self._stack = QStackedWidget()
		self._stack.addWidget(self._empty)
		self._stack.addWidget(table)

		card = Card()
		layout = vbox(
			hbox(
				heading,
				self._preview_note,
				None,
				self._search,
				self._rescan,
				spacing=10,
				margins=(16, 12, 12, 8),
			),  # fmt: skip
			self._stack,
			spacing=0,
			margins=(0, 0, 0, 6),
		)
		layout.setStretch(1, 1)
		card.setLayout(layout)
		return card

	def _build_action_bar(self) -> QWidget:
		bar = QFrame()
		bar.setObjectName("ActionBar")
		self._summary = ElidedLabel("")
		self._summary_detail = label("", "Muted")
		self._progress = QProgressBar()
		self._progress.setTextVisible(False)
		self._progress.hide()
		self._cancel = QPushButton("Cancel")
		self._cancel.setCursor(Qt.CursorShape.PointingHandCursor)
		self._cancel.clicked.connect(self._cancel_run)
		self._cancel.hide()
		self._go = QPushButton("Copy files")
		self._go.setObjectName("Primary")
		self._go.setCursor(Qt.CursorShape.PointingHandCursor)
		self._go.setMinimumWidth(170)
		self._go.clicked.connect(self._start_run)
		text = vbox(self._summary, self._summary_detail, self._progress, spacing=3)
		layout = hbox(text, self._cancel, self._go, spacing=10, margins=(28, 12, 28, 12))
		layout.setStretch(0, 1)
		bar.setLayout(layout)
		return bar

	# --- settings ------------------------------------------------------------------

	def _restore(self) -> None:
		settings = self._settings
		options = settings.organize_options()
		for widget in self._option_widgets():
			widget.blockSignals(True)
		for key, chip in self._chips.items():
			chip.setChecked(key in options.categories)
		self._by_type.setChecked(options.by_type)
		self._by_date.setChecked(options.by_date)
		self._date_layout.setCurrentIndex(self._date_layout.findData(options.date_layout.value))
		self._keep_structure.setChecked(options.keep_structure)
		self._name_match.setCurrentIndex(self._name_match.findData(options.name_match.value))
		self._name_filter.setText(options.name_filter)
		self._operation.set_value(options.operation.value)
		self._remove_empty.setChecked(settings.remove_empty_dirs)
		for widget in self._option_widgets():
			widget.blockSignals(False)

		if settings.dest and Path(settings.dest).is_dir():
			self._dest_card.set_path(Path(settings.dest).resolve())
		if settings.source and Path(settings.source).is_dir():
			self.set_source(Path(settings.source), save=False)
		self._sync_option_widgets()
		self._refresh()

	def _option_widgets(self) -> list[QWidget]:
		return [
			*self._chips.values(),
			self._by_type.switch,
			self._by_date.switch,
			self._date_layout,
			self._keep_structure.switch,
			self._name_match,
			self._name_filter,
			self._operation,
			self._remove_empty.switch,
		]

	def options(self) -> OrganizeOptions:
		return OrganizeOptions(
			categories=frozenset(key for key, chip in self._chips.items() if chip.isChecked()),
			by_type=self._by_type.isChecked(),
			by_date=self._by_date.isChecked(),
			date_layout=DateLayout(self._date_layout.currentData()),
			keep_structure=self._keep_structure.isChecked() and not self._in_place,
			name_filter=self._name_filter.text(),
			name_match=NameMatch(self._name_match.currentData()),
			operation=Operation(self._operation.value() or Operation.COPY.value),
		)

	@property
	def _in_place(self) -> bool:
		return self._source_card.path is not None and self._source_card.path == self._dest_card.path

	def _options_changed(self, *_) -> None:
		options = self.options()
		self._settings.set_organize_options(options)
		self._settings.remove_empty_dirs = self._remove_empty.isChecked()
		self._save()
		self._sync_option_widgets()
		self._refresh_timer.start()

	def _sync_option_widgets(self) -> None:
		options = self.options()
		self._date_layout.setEnabled(options.by_date)
		self._keep_structure.setEnabled(not self._in_place)
		self._keep_structure.set_hint(
			"Not available when sorting a folder into itself." if self._in_place else ""
		)
		moving = options.operation is Operation.MOVE
		self._remove_empty.setVisible(moving)
		self._operation_hint.setText(
			"Files leave the source folder. You can undo this from History."
			if moving
			else "Originals stay where they are; copies go to the destination."
		)
		self._update_example()
		self._update_action()

	def _select_categories(self, *, all_: bool) -> None:
		"""Tick every type (except the catch-all "Other") or none."""
		for key, chip in self._chips.items():
			chip.blockSignals(True)
			chip.setChecked(all_ and key != "other")
			chip.blockSignals(False)
		self._options_changed()

	# --- folders -------------------------------------------------------------------

	def set_source(self, path: Path, *, save: bool = True) -> None:
		path = path.resolve()
		if path != self._source_card.path:
			self._excluded.clear()
		self._source_card.set_path(path, short_path(path))
		self._update_dest_detail()
		if save:
			self._settings.source = str(path)
			self._save()
		self._sync_option_widgets()
		self.rescan()

	def set_dest(self, path: Path) -> None:
		self._dest_card.set_path(path.resolve())
		self._update_dest_detail()
		self._settings.dest = str(self._dest_card.path)
		self._save()
		self._sync_option_widgets()
		self._refresh_timer.start()

	def _update_dest_detail(self) -> None:
		dest, source = self._dest_card.path, self._source_card.path
		if dest is None:
			return
		detail = short_path(dest)
		if source is not None and dest == source:
			detail = "Same as source — files are sorted in place"
		elif source is not None and dest.is_relative_to(source):
			detail = f"{short_path(dest)} · inside the source, left out of the scan"
		self._dest_card.set_detail(detail)

	def rescan(self) -> None:
		source = self._source_card.path
		if source is None:
			return
		if not source.is_dir():
			self._banner.show_message("warning", f"“{short_path(source)}” can no longer be found.")
			return
		self._items = None
		self._plan = None
		self._dates.cancel()
		self._planner.cancel()
		self._model.set_plan(None)
		self._update_counts()
		self._show_empty("search", "Scanning folder…", short_path(source), busy=True)
		self._scan.replace(
			Task(scan_folder, source),
			finished=self._on_scanned,
			progress=lambda count: self._show_empty(
				"search", "Scanning folder…", f"{count:,} files found so far", busy=True
			),
			failed=lambda error: self._show_empty("alert", "Couldn’t scan this folder", error),
		)
		self._update_action()

	def _on_scanned(self, items: list[FileItem]) -> None:
		self._items = items
		total = sum(item.size for item in items)
		self._source_card.set_detail(
			f"{plural(len(items), 'file')} · {human_size(total)} · {short_path(self._source_card.path)}"
		)
		self._update_counts()
		self._refresh()

	def _category_counts(self) -> Counter[str]:
		return Counter(item.category.key for item in self._items or ())

	def _update_counts(self) -> None:
		counts = self._category_counts() if self._items is not None else None
		for key, chip in self._chips.items():
			chip.set_count(None if counts is None else counts.get(key, 0))

	# --- planning ------------------------------------------------------------------

	def _refresh(self) -> None:
		"""Recompute the plan for the current folders and options."""
		source, dest = self._source_card.path, self._dest_card.path
		options = self.options()
		self._update_example()
		if source is None:
			self._show_empty(
				"folder-open",
				"Choose a folder to sort",
				"Drop a folder onto “From”, or click it to browse. Nothing is changed until "
				"you press the button at the bottom.",
			)
			self._update_action()
			return
		if self._items is None:
			return  # still scanning; _on_scanned calls back here

		try:
			name_matcher(options.name_filter, options.name_match)
			self._name_error.hide()
		except re.error as error:
			self._name_error.setText(f"That regex isn’t valid: {error}")
			self._name_error.show()

		if dest is None:
			self._model.set_plan(None)
			self._plan = None
			self._show_empty(
				"folder", "Choose where sorted files go", "Pick a destination folder on the right."
			)
			self._update_action()
			return

		if options.by_date:
			self._resolve_dates(options)
		self._planner.replace(
			Task(
				_plan_job,
				self._items,
				options,
				source,
				dest,
				frozenset(self._excluded),
			),
			finished=self._on_planned,
			failed=lambda error: self._show_empty("alert", "Couldn’t build the preview", error),
		)
		self._update_action()

	def _resolve_dates(self, options: OrganizeOptions) -> None:
		if self._dates.running:
			return
		needing = [
			item
			for item in self._items or ()
			if item.category.key in options.categories and item.date_source is None
		]
		if not needing:
			return

		def on_progress(value) -> None:
			done, total = value
			self._date_progress = f"Reading dates… {done:,} of {total:,}"
			self._update_note()

		def on_finished(results) -> None:
			for item in needing:
				if item.path in results:
					item.taken, item.date_source = results[item.path]
			self._date_progress = ""
			self._refresh()

		def on_failed(error: str) -> None:
			for item in needing:
				item.date_source = item.date_source or "file"
			self._date_progress = ""
			self._refresh()

		self._date_progress = "Reading dates…"
		self._dates.replace(
			Task(resolve_capture_dates, needing),
			finished=on_finished,
			progress=on_progress,
			failed=on_failed,
		)
		self._update_action()

	def _on_planned(self, plan: Plan) -> None:
		self._plan = plan
		scroll = self._table.verticalScrollBar().value()
		self._model.set_plan(plan)
		self._table.verticalScrollBar().setValue(scroll)
		if plan.filter_error:
			self._show_empty("search", "Fix the name filter", plan.filter_error)
		elif not plan.files:
			self._show_empty(
				"search",
				"Nothing to sort",
				"No files in this folder match the selected file types and name filter.",
			)
		else:
			self._stack.setCurrentWidget(self._table)
		self._update_example()
		self._update_action()

	def _on_inclusion_changed(self) -> None:
		shown = {planned.item.path for planned in self._model.files()}
		self._excluded = (self._excluded - shown) | {
			planned.item.path
			for planned in self._model.files()
			if not planned.included and planned.status not in SETTLED
		}
		self._update_action()

	def _show_empty(self, icon_name: str, title: str, text: str = "", busy: bool = False) -> None:
		self._empty.show_state(icon_name, title, text, busy)
		self._stack.setCurrentWidget(self._empty)

	def _update_example(self) -> None:
		options = self.options()
		sample = next(
			(item for item in self._items or () if item.category.key in options.categories),
			None,
		)
		if sample is not None:
			category, when = sample.category.label, sample.date
			subfolders, name = sample.rel.parent.parts, sample.path.name
		else:
			category, when, subfolders, name = (
				"Photos",
				datetime.now(),
				("Holiday",),
				"IMG_1234.JPG",
			)
		parts = [self._dest_card.path.name if self._dest_card.path else "Destination"]
		if options.by_type:
			parts.append(category)
		if options.by_date:
			parts.extend(options.date_layout.parts(when))
		if options.keep_structure:
			parts.extend(subfolders)
		parts.append(name)
		self._example.setText(" / ".join(parts))
		self._example.setToolTip("Example: " + " / ".join(parts))

	def _update_note(self) -> None:
		if self._date_progress:
			self._preview_note.setText(self._date_progress)
		elif self._plan and self._plan.files:
			shown = self._proxy.rowCount()
			total = len(self._plan.files)
			self._preview_note.setText(
				plural(total, "file") if shown == total else f"{shown:,} of {total:,} files"
			)
		else:
			self._preview_note.setText("")

	def _update_action(self) -> None:
		self._update_note()
		if self._runner.running:
			return
		options = self.options()
		verb = "Move" if options.operation is Operation.MOVE else "Copy"
		actionable = self._plan.actionable if self._plan else []
		count = len(actionable)
		size = sum(planned.item.size for planned in actionable)
		busy = self._scan.running or self._dates.running or self._planner.running
		self._go.setText(f"{verb} {plural(count, 'file')}" if count else f"{verb} files")
		icons.bind(self._go, options.operation.value, "accent_text", 16)
		self._go.setEnabled(count > 0 and not busy)

		if self._source_card.path is None or self._dest_card.path is None:
			self._summary.setText("Pick a source and destination to get started")
			self._summary_detail.setText("")
		elif self._items is None or busy:
			self._summary.setText("Getting things ready…")
			self._summary_detail.setText("")
		else:
			files = self._plan.files if self._plan else []
			everything = Counter(planned.status for planned in files)
			done_already = everything[Status.ALREADY_THERE] + everything[Status.IN_PLACE]
			unticked = sum(
				1 for planned in files if not planned.included and planned.status not in SETTLED
			)
			details = []
			if count:
				to = short_path(self._dest_card.path)
				self._summary.setText(f"{plural(count, 'file')} · {human_size(size)} → {to}")
				statuses = Counter(planned.status for planned in actionable)
				if renamed := statuses[Status.RENAMED]:
					details.append(f"{renamed:,} renamed to avoid a clash")
				if check := statuses[Status.CHECK_FIRST]:
					details.append(f"{check:,} compared first, in case they’re already there")
			elif done_already and not unticked:
				self._summary.setText("Everything here is already sorted")
			else:
				self._summary.setText("Nothing to do yet")
				details.append("Select some file types, or tick files in the preview")
			if done_already:
				details.append(f"{done_already:,} already in place")
			if unticked:
				details.append(f"{unticked:,} unticked")
			self._summary_detail.setText(" · ".join(details))

	# --- table interactions --------------------------------------------------------

	def _planned_at(self, proxy_index):
		return self._model.planned(self._proxy.mapToSource(proxy_index).row())

	def _selected_rows(self) -> list[int]:
		rows = self._table.selectionModel().selectedRows()
		return [self._proxy.mapToSource(index).row() for index in rows]

	def _toggle_selected(self) -> None:
		rows = self._selected_rows()
		if not rows or self._runner.running:
			return
		any_included = any(self._model.planned(row).included for row in rows)
		self._model.set_included(rows, not any_included)

	def _show_table_menu(self, position) -> None:
		index = self._table.indexAt(position)
		if index.isValid() and not self._table.selectionModel().isRowSelected(index.row()):
			self._table.selectionModel().select(
				index,
				QItemSelectionModel.SelectionFlag.ClearAndSelect
				| QItemSelectionModel.SelectionFlag.Rows,
			)
		rows = self._selected_rows()
		menu = QMenu(self)
		locked = self._runner.running
		if rows:
			include = menu.addAction("Include")
			include.triggered.connect(lambda: self._model.set_included(rows, True))
			exclude = menu.addAction("Leave out")
			exclude.triggered.connect(lambda: self._model.set_included(rows, False))
			include.setEnabled(not locked)
			exclude.setEnabled(not locked)
			menu.addSeparator()
			first = self._model.planned(rows[0]).item.path
			menu.addAction("Show in Finder", lambda: reveal_in_file_manager(first))
			menu.addAction("Open", lambda: open_file(first))
			menu.addSeparator()
		visible = [
			self._proxy.mapToSource(self._proxy.index(row, 0)).row()
			for row in range(self._proxy.rowCount())
		]
		include_all = menu.addAction("Include all shown")
		include_all.triggered.connect(lambda: self._model.set_included(visible, True))
		exclude_all = menu.addAction("Leave out all shown")
		exclude_all.triggered.connect(lambda: self._model.set_included(visible, False))
		include_all.setEnabled(bool(visible) and not locked)
		exclude_all.setEnabled(bool(visible) and not locked)
		menu.exec(self._table.viewport().mapToGlobal(position))

	# --- running -------------------------------------------------------------------

	def _start_run(self) -> None:
		if not self._plan or self._runner.running:
			return
		options = self.options()
		todo = self._plan.actionable
		if not todo:
			return
		source, dest = self._source_card.path, self._dest_card.path
		size = human_size(sum(planned.item.size for planned in todo))
		if options.operation is Operation.MOVE:
			box = QMessageBox(self)
			box.setIcon(QMessageBox.Icon.Question)
			box.setWindowTitle("Move files")
			box.setText(f"Move {plural(len(todo), 'file')} ({size})?")
			box.setInformativeText(
				f"They will leave “{short_path(source)}” and go into “{short_path(dest)}”. "
				"You can undo this later from History."
			)
			move = box.addButton("Move", QMessageBox.ButtonRole.AcceptRole)
			box.addButton(QMessageBox.StandardButton.Cancel)
			box.setDefaultButton(move)
			box.exec()
			if box.clickedButton() is not move:
				return

		self._set_running(True, options.operation)
		self._runner.replace(
			Task(
				execute_plan,
				list(todo),
				operation=options.operation,
				source_root=source,
				dest_root=dest,
				history_dir=self._paths.history_dir,
				remove_empty_dirs=self._remove_empty.isChecked(),
				report_cancelled=True,
			),
			finished=self._on_run_finished,
			progress=self._on_run_progress,
			failed=self._on_run_failed,
		)

	def _set_running(self, running: bool, operation: Operation | None = None) -> None:
		self._options_panel.setEnabled(not running)
		self._source_card.setEnabled(not running)
		self._dest_card.setEnabled(not running)
		self._rescan.setEnabled(not running)
		self._model.locked = running
		self._progress.setVisible(running)
		self._cancel.setVisible(running)
		self._cancel.setEnabled(True)
		self._go.setVisible(not running)
		if running:
			self._banner.hide()
			self._progress.setRange(0, 0)
			verb = "Moving" if operation is Operation.MOVE else "Copying"
			self._summary.setText(f"{verb} files…")
			self._summary_detail.setText("")

	def _on_run_progress(self, value) -> None:
		done, total, bytes_done, bytes_total, name = value
		self._progress.setRange(0, 1000)
		self._progress.setValue(int(1000 * bytes_done / bytes_total) if bytes_total else 0)
		verb = "Moving" if self.options().operation is Operation.MOVE else "Copying"
		self._summary.setText(f"{verb} {done + 1:,} of {total:,}…")
		self._summary_detail.setText(
			f"{human_size(bytes_done)} of {human_size(bytes_total)} · {name}"
		)

	def _cancel_run(self) -> None:
		if self._runner.task is not None:
			self._runner.task.cancel()
			self._cancel.setEnabled(False)
			self._summary.setText("Stopping after the current file…")

	def _on_run_failed(self, error: str) -> None:
		self._set_running(False)
		self._banner.show_message("danger", f"Something went wrong: {error}")
		self.historyChanged.emit()
		self.rescan()

	def _on_run_finished(self, result: RunResult) -> None:
		self._set_running(False)
		record = result.record
		verb = "Moved" if record.operation is Operation.MOVE else "Copied"
		parts = [f"{verb} {plural(record.transferred, 'file')} ({human_size(record.bytes)})"]
		if record.cancelled:
			parts[0] = f"Stopped. {parts[0]} before cancelling"
		if record.skipped:
			parts.append(f"{record.skipped:,} identical already there, so skipped")
		if result.errors:
			parts.append(f"{plural(len(result.errors), 'file')} couldn’t be {verb.lower()}")
		tone = "danger" if result.errors else ("warning" if record.cancelled else "success")
		actions: list[tuple[str, Callable[[], None]]] = []
		if result.errors:
			actions.append(("Details", lambda: self._show_errors(result)))
		dest = Path(record.dest)
		actions.append(("Show in Finder", lambda: reveal_in_file_manager(dest)))
		if record.transferred:
			actions.append(("Undo", lambda: self.undoRequested.emit(record.journal)))
		self._banner.show_message(tone, ". ".join(parts) + ".", actions)
		self.historyChanged.emit()
		self.rescan()

	def _show_errors(self, result: RunResult) -> None:
		box = QMessageBox(self)
		box.setIcon(QMessageBox.Icon.Warning)
		box.setWindowTitle("Files that couldn’t be processed")
		box.setText(f"{plural(len(result.errors), 'file')} couldn’t be processed.")
		box.setDetailedText("\n".join(f"{path}\n    {reason}" for path, reason in result.errors))
		box.exec()

	def shutdown(self) -> bool:
		"""Stop background work. Returns False if a run is still going."""
		self._scan.cancel()
		self._dates.cancel()
		self._planner.cancel()
		return not self._runner.running

	def cancel_run(self) -> None:
		self._cancel_run()
