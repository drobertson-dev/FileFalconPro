"""
The Duplicates page: find copies of the same photo — exact, resized, re-saved or
cropped — review them side by side, and clear out the extras.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from PyQt6.QtCore import QPoint, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
	QDialog,
	QFileDialog,
	QFrame,
	QGridLayout,
	QLabel,
	QMenu,
	QMessageBox,
	QProgressBar,
	QPushButton,
	QScrollArea,
	QStackedWidget,
	QVBoxLayout,
	QWidget,
)
from send2trash import send2trash

from config import AppPaths, Settings
from gui import icons
from gui.thumbnails import ThumbnailLoader, load_qimage
from gui.widgets import (
	Banner,
	Card,
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
	repolish,
	reveal_in_file_manager,
	section_label,
	short_path,
	vbox,
)
from gui.workers import Task, TaskSlot
from operations.duplicates import (
	DuplicateGroup,
	DuplicateReport,
	GroupMember,
	Similarity,
	find_duplicates,
)
from operations.file_extensions import CATEGORY_BY_KEY
from operations.planner import Operation, PlannedFile, Status
from operations.runner import RunResult, execute_plan
from operations.scanner import FileItem

GROUPS_PER_PAGE = 30
TILES_PER_ROW = 5
THUMB = QSize(176, 132)

_STAGES = {
	"analyse": "Looking at photos",
	"compare": "Comparing identical-looking files",
	"match": "Matching similar and cropped photos",
}


def fit_pixmap(pixmap: QPixmap, box: QSize, ratio: float) -> QPixmap:
	"""Scale ``pixmap`` to fit ``box`` (logical pixels), sharp on Retina screens."""
	scaled = pixmap.scaled(
		box * ratio, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
	)
	scaled.setDevicePixelRatio(ratio)
	return scaled


def _trash_job(
	paths: list[Path], *, progress, cancelled
) -> tuple[list[Path], list[tuple[Path, str]]]:
	done, errors = [], []
	for index, path in enumerate(paths):
		if cancelled():
			break
		progress(index, len(paths))
		try:
			send2trash(path)
			done.append(path)
		except Exception as error:
			errors.append((path, str(error)))
	return done, errors


class Tile(QFrame):
	"""One photo in a group: thumbnail, details, and whether it stays or goes."""

	toggled = pyqtSignal(object)  # GroupMember
	keepOnly = pyqtSignal(object)
	compare = pyqtSignal(object)

	def __init__(self, member: GroupMember) -> None:
		super().__init__()
		self.member = member
		self.setObjectName("Tile")
		self.setCursor(Qt.CursorShape.PointingHandCursor)
		self.setFixedWidth(THUMB.width() + 18)
		info = member.image

		self._thumb = QLabel()
		self._thumb.setObjectName("Thumb")
		self._thumb.setFixedSize(THUMB)
		self._thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
		self._badge = QLabel(self._thumb)
		self._badge.setObjectName("Pill")
		self._badge.move(6, 6)

		name = ElidedLabel(info.path.name, Qt.TextElideMode.ElideMiddle)
		name.setObjectName("TileName")
		dims = f"{info.width}×{info.height} · " if info.width else ""
		meta = label(f"{dims}{human_size(info.size)}", "Muted")
		relation = ElidedLabel(member.detail, Qt.TextElideMode.ElideRight)
		relation.setObjectName("Hint")
		folder = ElidedLabel(short_path(info.path.parent), Qt.TextElideMode.ElideLeft)
		folder.setObjectName("Faint")
		folder.setToolTip(str(info.path))
		self.setToolTip(f"{info.path}\n\nClick to keep or remove · double-click to compare")
		self.setLayout(
			vbox(self._thumb, 4, name, meta, relation, folder, spacing=1, margins=(8, 8, 8, 8))
		)
		self.set_marked(False)

	def set_pixmap(self, pixmap: QPixmap | None, failed: bool = False) -> None:
		if pixmap is None:
			self._thumb.setText("No preview" if failed else "Loading…")
			return
		self._thumb.setPixmap(fit_pixmap(pixmap, THUMB, self.devicePixelRatioF()))

	def set_marked(self, marked: bool) -> None:
		self.setProperty("marked", marked)
		self._badge.setText("Remove" if marked else "Keep")
		self._badge.setProperty("tone", "danger" if marked else "success")
		self._badge.adjustSize()
		repolish(self)
		repolish(self._badge)

	def mouseReleaseEvent(self, event) -> None:  # noqa: N802
		if event.button() == Qt.MouseButton.LeftButton:
			self.toggled.emit(self.member)

	def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
		self.compare.emit(self.member)

	def contextMenuEvent(self, event) -> None:  # noqa: N802
		menu = QMenu(self)
		menu.addAction("Keep only this one", lambda: self.keepOnly.emit(self.member))
		menu.addAction("Compare with the kept photo", lambda: self.compare.emit(self.member))
		menu.addSeparator()
		menu.addAction("Show in Finder", lambda: reveal_in_file_manager(self.member.image.path))
		menu.addAction("Open", lambda: open_file(self.member.image.path))
		menu.exec(event.globalPos())


class GroupCard(Card):
	def __init__(self, number: int, group: DuplicateGroup) -> None:
		super().__init__()
		self.group = group
		self.tiles: list[Tile] = []
		title = label(f"{group.title}")
		title.setObjectName("CardTitle")
		count = label(
			f"{len(group.members)} photos · {human_size(group.reclaimable)} in extra copies",
			"Muted",
		)
		self.keep_all = QPushButton("Keep all")
		self.keep_all.setObjectName("Ghost")
		self.keep_all.setCursor(Qt.CursorShape.PointingHandCursor)
		self.keep_all.setToolTip("These aren’t duplicates — don’t remove any of them")
		grid = QGridLayout()
		grid.setHorizontalSpacing(10)
		grid.setVerticalSpacing(10)
		for index, member in enumerate(group.members):
			tile = Tile(member)
			self.tiles.append(tile)
			grid.addWidget(tile, index // TILES_PER_ROW, index % TILES_PER_ROW)
		grid.setColumnStretch(TILES_PER_ROW, 1)
		number_label = label(f"{number}", "Faint")
		self.setLayout(
			vbox(
				hbox(number_label, title, count, None, self.keep_all, spacing=8),
				grid,
				spacing=10,
				margins=(14, 12, 14, 14),
			)
		)


class CompareDialog(QDialog):
	"""Two photos side by side at a useful size."""

	def __init__(self, parent: QWidget, left: GroupMember, right: GroupMember) -> None:
		super().__init__(parent)
		self.setWindowTitle("Compare")
		self.resize(1100, 640)
		columns = []
		for member, caption in ((left, "Kept"), (right, "This one")):
			picture = QLabel()
			picture.setAlignment(Qt.AlignmentFlag.AlignCenter)
			picture.setMinimumSize(480, 400)
			image = load_qimage(member.image.path, 1000)
			if image is not None:
				picture.setPixmap(
					fit_pixmap(QPixmap.fromImage(image), QSize(500, 460), self.devicePixelRatioF())
				)
			else:
				picture.setText("No preview available")
			info = member.image
			dims = f"{info.width}×{info.height} · " if info.width else ""
			columns.append(
				vbox(
					section_label(caption),
					picture,
					label(info.path.name),
					label(f"{dims}{human_size(info.size)} · {member.detail}", "Muted"),
					ElidedLabel(str(info.path.parent)),
					spacing=6,
				)
			)
		close = QPushButton("Close")
		close.clicked.connect(self.accept)
		self.setLayout(
			vbox(
				hbox(*columns, spacing=24), hbox(None, close), spacing=16, margins=(20, 20, 20, 16)
			)
		)


class DuplicatesPage(QWidget):
	"""Find and clear out duplicate photos."""

	historyChanged = pyqtSignal()

	def __init__(self, settings: Settings, paths: AppPaths, save: Callable[[], None]) -> None:
		super().__init__()
		self.setObjectName("Page")
		self._settings = settings
		self._paths = paths
		self._save = save
		self._scan = TaskSlot()
		self._action = TaskSlot()
		self._report: DuplicateReport | None = None
		self._marked: set[Path] = set()
		self._shown_groups = 0
		self._cards: list[GroupCard] = []
		self._tiles_by_path: dict[Path, list[Tile]] = {}
		self._thumbs = ThumbnailLoader()
		self._thumbs.loaded.connect(self._on_thumbnail)
		self._build()
		self._restore()

	# --- construction --------------------------------------------------------------

	def _build(self) -> None:
		title = label("Duplicates", "PageTitle")
		subtitle = label(
			"Find copies of the same photo — even resized, re-saved or cropped ones.",
			"PageSubtitle",
		)
		self._folder = FolderCard("Look in", "Choose a folder of photos", "Folder to check")
		self._folder.folderChosen.connect(lambda path: self._set_folder(Path(path)))

		self._similarity = Segmented(
			[
				(str(Similarity.EXACT.value), "Exact", None),
				(str(Similarity.CLOSE.value), "Similar", None),
				(str(Similarity.LOOSE.value), "Loose", None),
			]
		)
		self._similarity.changed.connect(self._options_changed)
		self._crops = SwitchRow("Catch cropped copies", checked=True)
		self._crops.toggled.connect(self._options_changed)
		self._similarity_hint = label("", "Hint", wrap=True)
		options = Card()
		options.setFixedWidth(330)
		options.setLayout(
			vbox(
				hbox(section_label("Match"), None, self._similarity, spacing=8),
				self._similarity_hint,
				self._crops,
				spacing=8,
				margins=(14, 10, 14, 10),
			)
		)
		self._find = QPushButton("Find duplicates")
		self._find.setObjectName("Primary")
		self._find.setCursor(Qt.CursorShape.PointingHandCursor)
		icons.bind(self._find, "search", "accent_text", 16)
		self._find.clicked.connect(self.start_scan)
		top = hbox(self._folder, options, vbox(None, self._find, None), spacing=12)
		top.setStretch(0, 1)

		self._banner = Banner()

		# Results
		self._results_title = label("", "CardTitle")
		self._suggest = QPushButton("Suggested selection")
		self._suggest.setObjectName("Ghost")
		self._suggest.setToolTip("Keep the largest photo in each group and mark the rest")
		self._suggest.clicked.connect(self._select_suggested)
		self._clear = QPushButton("Keep everything")
		self._clear.setObjectName("Ghost")
		self._clear.clicked.connect(self._select_none)
		for button in (self._suggest, self._clear):
			button.setCursor(Qt.CursorShape.PointingHandCursor)

		self._groups_layout = QVBoxLayout()
		self._groups_layout.setSpacing(12)
		self._groups_layout.setContentsMargins(0, 0, 8, 0)
		self._more = QPushButton("Show more groups")
		self._more.setCursor(Qt.CursorShape.PointingHandCursor)
		self._more.clicked.connect(self._show_more)
		holder = QWidget()
		holder.setLayout(vbox(self._groups_layout, hbox(None, self._more, None), None, spacing=12))
		self._scroll = QScrollArea()
		self._scroll.setWidgetResizable(True)
		self._scroll.setWidget(holder)
		self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
		self._scroll.verticalScrollBar().valueChanged.connect(self._load_visible_thumbnails)
		results = QWidget()
		results.setLayout(
			vbox(
				hbox(self._results_title, None, self._suggest, self._clear, spacing=4),
				self._scroll,
				spacing=10,
			)
		)

		self._empty = EmptyState()
		self._stack = QStackedWidget()
		self._stack.addWidget(self._empty)
		self._stack.addWidget(results)

		content = vbox(
			vbox(title, subtitle, spacing=2),
			top,
			self._banner,
			self._stack,
			spacing=16,
			margins=(28, 22, 28, 16),
		)
		content.setStretch(3, 1)
		self.setLayout(vbox(content, self._build_action_bar(), spacing=0))

	def _build_action_bar(self) -> QWidget:
		bar = QFrame()
		bar.setObjectName("ActionBar")
		self._summary = ElidedLabel("")
		self._summary_detail = label("", "Muted")
		self._progress = QProgressBar()
		self._progress.setTextVisible(False)
		self._progress.hide()
		self._cancel = QPushButton("Stop")
		self._cancel.setCursor(Qt.CursorShape.PointingHandCursor)
		self._cancel.clicked.connect(self._stop)
		self._cancel.hide()
		self._move = QPushButton("Move to folder…")
		self._move.setCursor(Qt.CursorShape.PointingHandCursor)
		self._move.setToolTip("Move the marked photos into a folder (can be undone from History)")
		self._move.clicked.connect(self._move_marked)
		self._trash = QPushButton("Move to Trash")
		self._trash.setObjectName("Danger")
		self._trash.setCursor(Qt.CursorShape.PointingHandCursor)
		icons.bind(self._trash, "trash", "accent_text", 16)
		self._trash.clicked.connect(self._trash_marked)
		text = vbox(self._summary, self._summary_detail, self._progress, spacing=3)
		layout = hbox(
			text, self._cancel, self._move, self._trash, spacing=10, margins=(28, 12, 28, 12)
		)
		layout.setStretch(0, 1)
		bar.setLayout(layout)
		return bar

	def _restore(self) -> None:
		self._similarity.set_value(str(self._settings.dupes_similarity))
		self._crops.setChecked(self._settings.dupes_detect_crops)
		if self._settings.dupes_folder and Path(self._settings.dupes_folder).is_dir():
			self._folder.set_path(Path(self._settings.dupes_folder))
		self._sync()
		self._show_intro()

	# --- options -------------------------------------------------------------------

	def _similarity_value(self) -> Similarity:
		try:
			return Similarity(int(self._similarity.value()))
		except ValueError:
			return Similarity.CLOSE

	def _options_changed(self, *_) -> None:
		self._settings.dupes_similarity = self._similarity_value().value
		self._settings.dupes_detect_crops = self._crops.isChecked()
		self._save()
		self._sync()

	def _sync(self) -> None:
		similarity = self._similarity_value()
		self._crops.setEnabled(similarity is not Similarity.EXACT)
		self._similarity_hint.setText(
			{
				Similarity.EXACT: "Only byte-for-byte identical files. Fastest.",
				Similarity.CLOSE: "Also resized, re-saved and lightly edited copies.",
				Similarity.LOOSE: "Casts a wider net, e.g. heavier edits. Check results carefully.",
			}[similarity]
		)
		self._update_action()

	def _set_folder(self, path: Path) -> None:
		self._folder.set_path(path.resolve())
		self._settings.dupes_folder = str(self._folder.path)
		self._save()
		self._update_action()

	# --- scanning ------------------------------------------------------------------

	def _show_intro(self) -> None:
		self._empty.show_state(
			"image-stack",
			"Find duplicate photos",
			"Choose a folder and press Find duplicates. Nothing is removed until you review "
			"the results and say so.",
		)
		self._stack.setCurrentWidget(self._empty)

	def start_scan(self) -> None:
		folder = self._folder.path
		if folder is None:
			self._folder.browse()
			folder = self._folder.path
			if folder is None:
				return
		self._banner.hide()
		self._clear_results()
		self._set_busy(True, "Scanning folder…")
		self._empty.show_state("search", "Scanning folder…", short_path(folder), busy=True)
		self._stack.setCurrentWidget(self._empty)
		self._scan.replace(
			Task(
				find_duplicates,
				[folder],
				similarity=self._similarity_value(),
				detect_crops=self._crops.isChecked(),
				cache_path=self._paths.cache_dir / "image_features.sqlite",
			),
			finished=self._on_report,
			progress=self._on_scan_progress,
			failed=self._on_scan_failed,
		)

	def _on_scan_progress(self, value) -> None:
		stage, done, total = value
		text = _STAGES.get(stage, "Working")
		detail = f"{done:,} of {total:,}" if total > 1 else ""
		self._empty.show_state("search", f"{text}…", detail, busy=True)
		self._summary.setText(f"{text}…")
		self._summary_detail.setText(detail)
		if total > 1:
			self._progress.setRange(0, total)
			self._progress.setValue(done)

	def _on_scan_failed(self, error: str) -> None:
		self._set_busy(False)
		self._empty.show_state("alert", "Couldn’t finish the scan", error)

	def _stop(self) -> None:
		if self._scan.running:
			self._scan.cancel()
			self._set_busy(False)
			self._show_intro()
		elif self._action.task is not None:
			self._action.task.cancel()
			self._cancel.setEnabled(False)

	def _set_busy(self, busy: bool, text: str = "") -> None:
		self._folder.setEnabled(not busy)
		self._find.setEnabled(not busy)
		self._similarity.setEnabled(not busy)
		self._crops.setEnabled(not busy and self._similarity_value() is not Similarity.EXACT)
		self._progress.setVisible(busy)
		self._progress.setRange(0, 0)
		self._cancel.setVisible(busy)
		self._cancel.setEnabled(True)
		self._move.setVisible(not busy)
		self._trash.setVisible(not busy)
		if busy:
			self._summary.setText(text)
			self._summary_detail.setText("")
		else:
			self._update_action()

	def _on_report(self, report: DuplicateReport) -> None:
		self._set_busy(False)
		self._report = report
		self._select_suggested(render=False)
		if not report.groups:
			self._empty.show_state(
				"check-circle",
				"No duplicates found",
				f"Checked {plural(report.scanned, 'photo')} — every one is unique.",
			)
			self._stack.setCurrentWidget(self._empty)
		else:
			self._render_groups()
			self._stack.setCurrentIndex(1)
		if report.unreadable:
			self._banner.show_message(
				"warning",
				f"{plural(len(report.unreadable), 'file')} couldn’t be read and were only checked "
				"for exact copies.",
				[("Details", lambda: self._show_unreadable(report))],
			)
		self._update_action()

	def _show_unreadable(self, report: DuplicateReport) -> None:
		box = QMessageBox(self)
		box.setWindowTitle("Unreadable files")
		box.setText("These files couldn’t be opened as images:")
		box.setDetailedText(
			"\n".join(f"{info.path}\n    {info.error}" for info in report.unreadable)
		)
		box.exec()

	# --- results -------------------------------------------------------------------

	def _clear_results(self) -> None:
		self._thumbs.cancel_all()
		for card in self._cards:
			card.deleteLater()
		self._cards.clear()
		self._tiles_by_path.clear()
		self._shown_groups = 0
		self._report = None
		self._marked.clear()
		self._update_action()

	def _render_groups(self) -> None:
		for card in self._cards:
			card.deleteLater()
		self._cards.clear()
		self._tiles_by_path.clear()
		self._shown_groups = 0
		self._show_more()
		self._scroll.verticalScrollBar().setValue(0)

	def _show_more(self) -> None:
		if not self._report:
			return
		groups = self._report.groups
		end = min(len(groups), self._shown_groups + GROUPS_PER_PAGE)
		for number in range(self._shown_groups, end):
			card = GroupCard(number + 1, groups[number])
			card.keep_all.clicked.connect(lambda _=False, g=groups[number]: self._keep_group(g))
			for tile in card.tiles:
				tile.toggled.connect(self._toggle_member)
				tile.keepOnly.connect(self._keep_only)
				tile.compare.connect(self._compare)
				tile.set_marked(tile.member.image.path in self._marked)
				self._tiles_by_path.setdefault(tile.member.image.path, []).append(tile)
			self._groups_layout.addWidget(card)
			self._cards.append(card)
		self._shown_groups = end
		remaining = len(groups) - end
		self._more.setVisible(remaining > 0)
		self._more.setText(f"Show {min(remaining, GROUPS_PER_PAGE)} more of {remaining:,} groups")
		self._update_results_title()
		self._load_visible_thumbnails()

	def _update_results_title(self) -> None:
		if not self._report:
			return
		report = self._report
		self._results_title.setText(
			f"{plural(len(report.groups), 'group')} · {plural(report.extra_copies, 'extra copy', 'extra copies')}"
			f" · {human_size(report.reclaimable)} could be freed"
		)

	def _load_visible_thumbnails(self, *_) -> None:
		viewport = self._scroll.viewport()
		top = self._scroll.verticalScrollBar().value() - 300
		bottom = top + viewport.height() + 900
		for card in self._cards:
			y = card.mapTo(self._scroll.widget(), QPoint(0, 0)).y()
			if y + card.height() < top or y > bottom:
				continue
			for tile in card.tiles:
				path = tile.member.image.path
				pixmap = self._thumbs.get(path)
				tile.set_pixmap(pixmap, self._thumbs.is_failed(path))

	def _on_thumbnail(self, path: Path) -> None:
		pixmap = self._thumbs.get(path)
		for tile in self._tiles_by_path.get(path, []):
			tile.set_pixmap(pixmap, failed=pixmap is None)

	def showEvent(self, event) -> None:  # noqa: N802
		super().showEvent(event)
		self._load_visible_thumbnails()

	def resizeEvent(self, event) -> None:  # noqa: N802
		super().resizeEvent(event)
		self._load_visible_thumbnails()

	# --- marking -------------------------------------------------------------------

	def _apply_marks(self) -> None:
		for path, tiles in self._tiles_by_path.items():
			for tile in tiles:
				tile.set_marked(path in self._marked)
		self._update_action()

	def _select_suggested(self, render: bool = True) -> None:
		self._marked = {
			member.image.path
			for group in (self._report.groups if self._report else [])
			for member in group.members[1:]
		}
		if render:
			self._apply_marks()

	def _select_none(self) -> None:
		self._marked.clear()
		self._apply_marks()

	def _toggle_member(self, member: GroupMember) -> None:
		self._marked ^= {member.image.path}
		self._apply_marks()

	def _keep_only(self, member: GroupMember) -> None:
		group = next(g for g in self._report.groups if member in g.members)
		for other in group.members:
			if other is member:
				self._marked.discard(other.image.path)
			else:
				self._marked.add(other.image.path)
		self._apply_marks()

	def _keep_group(self, group: DuplicateGroup) -> None:
		self._marked -= {member.image.path for member in group.members}
		self._apply_marks()

	def _compare(self, member: GroupMember) -> None:
		group = next(g for g in self._report.groups if member in g.members)
		kept = next((m for m in group.members if m.image.path not in self._marked), group.keeper)
		if kept is member:
			kept = next((m for m in group.members if m is not member), group.keeper)
		CompareDialog(self, kept, member).exec()

	def _marked_members(self) -> list[GroupMember]:
		if not self._report:
			return []
		return [
			member
			for group in self._report.groups
			for member in group.members
			if member.image.path in self._marked
		]

	def _groups_fully_marked(self) -> list[DuplicateGroup]:
		if not self._report:
			return []
		return [
			group
			for group in self._report.groups
			if all(member.image.path in self._marked for member in group.members)
		]

	def _update_action(self) -> None:
		if self._scan.running or self._action.running:
			return
		marked = self._marked_members()
		size = sum(member.image.size for member in marked)
		self._trash.setEnabled(bool(marked))
		self._move.setEnabled(bool(marked))
		self._trash.setText(f"Move {len(marked):,} to Trash" if marked else "Move to Trash")
		self._find.setEnabled(True)
		if self._report is None:
			self._summary.setText(
				"Choose a folder to check" if self._folder.path is None else "Ready when you are"
			)
			self._summary_detail.setText("")
		elif not self._report.groups:
			self._summary.setText("No duplicates left")
			self._summary_detail.setText("")
		elif marked:
			self._summary.setText(f"{plural(len(marked), 'photo')} marked · {human_size(size)}")
			full = self._groups_fully_marked()
			self._summary_detail.setText(
				f"Careful: in {plural(len(full), 'group')} every photo is marked"
				if full
				else "Click a photo to keep or remove it · double-click to compare"
			)
		else:
			self._summary.setText("Nothing marked")
			self._summary_detail.setText("Click photos to mark them for removal")

	# --- acting on marked photos ---------------------------------------------------

	def _confirm_full_groups(self) -> bool:
		full = self._groups_fully_marked()
		if not full:
			return True
		answer = QMessageBox.warning(
			self,
			"Remove every copy?",
			f"In {plural(len(full), 'group')}, every photo is marked, so no copy would be "
			"left. Remove them all anyway?",
			QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
			QMessageBox.StandardButton.Cancel,
		)
		return answer == QMessageBox.StandardButton.Yes

	def _trash_marked(self) -> None:
		marked = self._marked_members()
		if not marked or not self._confirm_full_groups():
			return
		size = human_size(sum(member.image.size for member in marked))
		box = QMessageBox(self)
		box.setIcon(QMessageBox.Icon.Question)
		box.setWindowTitle("Move to Trash")
		box.setText(f"Move {plural(len(marked), 'photo')} ({size}) to the Trash?")
		box.setInformativeText("You can get them back from the Trash in Finder with “Put Back”.")
		confirm = box.addButton("Move to Trash", QMessageBox.ButtonRole.AcceptRole)
		box.addButton(QMessageBox.StandardButton.Cancel)
		box.setDefaultButton(confirm)
		box.exec()
		if box.clickedButton() is not confirm:
			return
		paths = [member.image.path for member in marked]
		self._set_busy(True, "Moving to Trash…")
		self._action.replace(
			Task(_trash_job, paths, report_cancelled=True),
			finished=self._on_trashed,
			progress=lambda value: self._progress_update(*value),
			failed=self._on_action_failed,
		)

	def _progress_update(self, done: int, total: int) -> None:
		self._progress.setRange(0, max(total, 1))
		self._progress.setValue(done)
		self._summary_detail.setText(f"{done:,} of {total:,}")

	def _on_trashed(self, result) -> None:
		done, errors = result
		self._set_busy(False)
		self._remove_from_results(done)
		text = f"Moved {plural(len(done), 'photo')} to the Trash."
		if errors:
			text += f" {plural(len(errors), 'photo')} couldn’t be moved."
		self._banner.show_message(
			"warning" if errors else "success",
			text,
			[("Details", lambda: self._show_errors(errors))] if errors else [],
		)

	def _move_marked(self) -> None:
		marked = self._marked_members()
		if not marked or not self._confirm_full_groups():
			return
		start = str(self._folder.path.parent if self._folder.path else Path.home())
		chosen = QFileDialog.getExistingDirectory(self, "Move marked photos to…", start)
		if not chosen:
			return
		dest = Path(chosen).resolve()
		photos = CATEGORY_BY_KEY["photos"]
		files = [
			PlannedFile(
				FileItem(
					path=member.image.path,
					rel=Path(member.image.path.name),
					size=member.image.size,
					mtime=member.image.mtime,
					birthtime=None,
					category=photos,
				),
				dest / member.image.path.name,
				Status.CHECK_FIRST,
			)
			for member in marked
			if member.image.path.parent != dest
		]
		if not files:
			return
		source_root = Path(os.path.commonpath([str(planned.item.path.parent) for planned in files]))
		self._set_busy(True, "Moving photos…")
		self._action.replace(
			Task(
				execute_plan,
				files,
				operation=Operation.MOVE,
				source_root=source_root,
				dest_root=dest,
				history_dir=self._paths.history_dir,
				remove_empty_dirs=False,
				report_cancelled=True,
			),
			finished=self._on_moved,
			progress=lambda value: self._progress_update(value[0], value[1]),
			failed=self._on_action_failed,
		)

	def _on_moved(self, result: RunResult) -> None:
		self._set_busy(False)
		moved = [m.image.path for m in self._marked_members() if not m.image.path.exists()]
		self._remove_from_results(moved)
		record = result.record
		text = f"Moved {plural(record.transferred, 'photo')} to “{Path(record.dest).name}”."
		if result.skipped:
			text += f" {plural(len(result.skipped), 'identical copy', 'identical copies')} already there were left in place."
		actions = [("Show in Finder", lambda: reveal_in_file_manager(Path(record.dest)))]
		if result.errors:
			text += f" {plural(len(result.errors), 'photo')} couldn’t be moved."
			actions.append(("Details", lambda: self._show_errors(result.errors)))
		self._banner.show_message("warning" if result.errors else "success", text, actions)
		self.historyChanged.emit()

	def _on_action_failed(self, error: str) -> None:
		self._set_busy(False)
		self._banner.show_message("danger", f"Something went wrong: {error}")

	def _show_errors(self, errors: list[tuple[Path, str]]) -> None:
		box = QMessageBox(self)
		box.setIcon(QMessageBox.Icon.Warning)
		box.setWindowTitle("Photos that couldn’t be processed")
		box.setText(f"{plural(len(errors), 'photo')} couldn’t be processed.")
		box.setDetailedText("\n".join(f"{path}\n    {reason}" for path, reason in errors))
		box.exec()

	def _remove_from_results(self, paths: list[Path]) -> None:
		"""Drop removed photos from the results; groups left with one photo disappear."""
		if not self._report:
			return
		gone = set(paths)
		self._marked -= gone
		for path in gone:
			self._thumbs.forget(path)
		groups = []
		for group in self._report.groups:
			group.members = [member for member in group.members if member.image.path not in gone]
			if len(group.members) > 1:
				groups.append(group)
		self._report.groups = groups
		shown = max(self._shown_groups, GROUPS_PER_PAGE)
		scroll = self._scroll.verticalScrollBar().value()
		self._render_groups()
		while self._shown_groups < min(shown, len(groups)):
			self._show_more()
		self._scroll.verticalScrollBar().setValue(scroll)
		if not groups:
			self._empty.show_state("check-circle", "All tidy", "No duplicate groups left.")
			self._stack.setCurrentWidget(self._empty)
		self._update_action()

	# --- lifecycle -----------------------------------------------------------------

	@property
	def busy(self) -> bool:
		return self._action.running

	def shutdown(self) -> None:
		self._scan.cancel()
		self._thumbs.cancel_all()
