"""
Running and undoing organise plans.

Every run writes a journal (JSON lines) next to the app's settings *as it goes*, so even
a run that was cancelled or crashed part-way can be undone from the History page.
"""

from __future__ import annotations

import errno
import filecmp
import json
import os
import shutil
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from send2trash import send2trash

from operations.planner import Operation, PlannedFile, Status, numbered_name

PARTIAL_SUFFIX = ".ffp-partial"

# (files_done, files_total, bytes_done, bytes_total, current_name)
RunProgressFn = Callable[[int, int, int, int, str], None]
CancelFn = Callable[[], bool]


@dataclass
class RunRecord:
	"""Summary of one organise run, as shown on the History page."""

	id: str
	journal: Path
	operation: Operation
	source: str
	dest: str
	started: datetime
	finished: datetime | None = None
	transferred: int = 0
	skipped: int = 0
	failed: int = 0
	bytes: int = 0
	cancelled: bool = False
	undone_at: datetime | None = None

	@property
	def interrupted(self) -> bool:
		return self.finished is None


@dataclass
class RunResult:
	record: RunRecord
	skipped: list[tuple[Path, str]] = field(default_factory=list)
	errors: list[tuple[Path, str]] = field(default_factory=list)


@dataclass
class UndoResult:
	restored: int = 0
	problems: list[tuple[Path, str]] = field(default_factory=list)


class Journal:
	"""Append-only JSON-lines log of everything a run changed on disk."""

	def __init__(self, path: Path) -> None:
		self.path = path
		self._handle = None

	def __enter__(self) -> Journal:
		self.path.parent.mkdir(parents=True, exist_ok=True)
		self._handle = self.path.open("a", encoding="utf-8")
		return self

	def __exit__(self, *exc) -> None:
		if self._handle:
			self._handle.close()
			self._handle = None

	def write(self, kind: str, **data) -> None:
		assert self._handle is not None, "Journal is not open"
		self._handle.write(json.dumps({"type": kind, **data}, ensure_ascii=False) + "\n")
		self._handle.flush()

	@staticmethod
	def read(path: Path) -> list[dict]:
		entries = []
		with path.open(encoding="utf-8") as handle:
			for line in handle:
				line = line.strip()
				if not line:
					continue
				try:
					entries.append(json.loads(line))
				except json.JSONDecodeError:
					break  # truncated last line after a crash
		return entries


def _new_run_id() -> str:
	return f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"


def _partial_path(dest: Path) -> Path:
	return dest.with_name(f".{dest.name}{PARTIAL_SUFFIX}")


def _copy_atomically(src: Path, dest: Path) -> None:
	"""Copy via a hidden temp file so an interrupted copy never leaves a broken file."""
	partial = _partial_path(dest)
	try:
		shutil.copy2(src, partial)
		os.replace(partial, dest)
	except BaseException:
		partial.unlink(missing_ok=True)
		raise


def move_file(src: Path, dest: Path) -> None:
	"""Move a file, falling back to copy-and-delete across drives."""
	try:
		os.rename(src, dest)
	except OSError as error:
		if error.errno != errno.EXDEV:
			raise
		_copy_atomically(src, dest)
		src.unlink()


def _free_path(path: Path) -> Path:
	"""Return ``path`` or the first ``name (n).ext`` sibling that does not exist yet."""
	candidate, counter = path, 1
	while candidate.exists() or _partial_path(candidate).exists():
		candidate = path.with_name(numbered_name(path.name, counter))
		counter += 1
	return candidate


def _make_dirs(folder: Path, journal: Journal) -> None:
	"""Create ``folder`` and its parents, journaling each one that is new."""
	missing = []
	current = folder
	while not current.exists():
		missing.append(current)
		current = current.parent
	for path in reversed(missing):
		path.mkdir(exist_ok=True)
		journal.write("mkdir", path=str(path))


def _remove_empty_dirs(folders: Iterable[Path], stop_at: Path, journal: Journal) -> None:
	"""Remove folders that a move left empty, walking up towards ``stop_at``."""
	for folder in sorted(set(folders), key=lambda path: len(path.parts), reverse=True):
		current = folder
		while current != stop_at and stop_at in current.parents:
			if not _remove_if_empty(current):
				break
			journal.write("rmdir", path=str(current))
			current = current.parent


def _remove_if_empty(folder: Path) -> bool:
	"""Remove ``folder`` if it holds nothing but Finder's .DS_Store."""
	try:
		names = os.listdir(folder)
	except OSError:
		return False
	if any(name != ".DS_Store" for name in names):
		return False
	try:
		if names:
			(folder / ".DS_Store").unlink()
		folder.rmdir()
	except OSError:
		return False
	return True


def execute_plan(
	files: list[PlannedFile],
	*,
	operation: Operation,
	source_root: Path,
	dest_root: Path,
	history_dir: Path,
	remove_empty_dirs: bool = True,
	progress: RunProgressFn | None = None,
	cancelled: CancelFn | None = None,
) -> RunResult:
	"""Copy or move every actionable file in ``files``.

	Existing files are never overwritten: identical files are skipped, anything else
	gets a numbered name.
	"""
	todo = [planned for planned in files if planned.actionable]
	run_id = _new_run_id()
	record = RunRecord(
		id=run_id,
		journal=history_dir / f"{run_id}.jsonl",
		operation=operation,
		source=str(source_root),
		dest=str(dest_root),
		started=datetime.now(),
	)
	result = RunResult(record=record)
	total_bytes = sum(planned.item.size for planned in todo)
	done_bytes = 0
	vacated: list[Path] = []

	with Journal(record.journal) as journal:
		journal.write(
			"run",
			id=run_id,
			operation=operation.value,
			source=record.source,
			dest=record.dest,
			started=record.started.isoformat(),
		)
		for index, planned in enumerate(todo):
			if cancelled and cancelled():
				record.cancelled = True
				break
			src = planned.item.path
			if progress:
				progress(index, len(todo), done_bytes, total_bytes, src.name)
			try:
				if not src.exists():
					raise FileNotFoundError("The file is no longer in the source folder")
				dest = planned.dest
				if dest.exists() or planned.status is Status.CHECK_FIRST:
					if dest.exists() and filecmp.cmp(src, dest, shallow=False):
						result.skipped.append((src, "An identical file is already there"))
						record.skipped += 1
						done_bytes += planned.item.size
						continue
					dest = _free_path(dest)
				_make_dirs(dest.parent, journal)
				if operation is Operation.MOVE:
					move_file(src, dest)
					vacated.append(src.parent)
				else:
					_copy_atomically(src, dest)
				journal.write("file", src=str(src), dst=str(dest), size=planned.item.size)
				record.transferred += 1
				record.bytes += planned.item.size
			except OSError as error:
				result.errors.append((src, error.strerror or str(error)))
				record.failed += 1
			done_bytes += planned.item.size

		if operation is Operation.MOVE and remove_empty_dirs:
			_remove_empty_dirs(vacated, source_root, journal)

		record.finished = datetime.now()
		journal.write(
			"end",
			finished=record.finished.isoformat(),
			transferred=record.transferred,
			skipped=record.skipped,
			failed=record.failed,
			bytes=record.bytes,
			cancelled=record.cancelled,
		)
	if progress:
		progress(len(todo), len(todo), total_bytes, total_bytes, "")
	return result


def undo_run(
	journal_path: Path,
	*,
	progress: Callable[[int, int], None] | None = None,
	cancelled: CancelFn | None = None,
) -> UndoResult:
	"""Reverse a run: moved files go back, copies go to the Trash.

	Files that changed since the run are left alone and reported as problems.
	"""
	entries = Journal.read(journal_path)
	if not entries or entries[0].get("type") != "run":
		raise ValueError("This history entry is unreadable")
	if any(entry["type"] == "undo" for entry in entries):
		raise ValueError("This run has already been undone")

	operation = Operation(entries[0]["operation"])
	transfers = [entry for entry in entries if entry["type"] == "file"]
	created_dirs = [Path(entry["path"]) for entry in entries if entry["type"] == "mkdir"]
	result = UndoResult()

	for index, entry in enumerate(reversed(transfers)):
		if cancelled and cancelled():
			break
		if progress:
			progress(index, len(transfers))
		src, dst = Path(entry["src"]), Path(entry["dst"])
		try:
			if operation is Operation.MOVE:
				if dst.exists() and not src.exists():
					src.parent.mkdir(parents=True, exist_ok=True)
					move_file(dst, src)
					result.restored += 1
				elif src.exists() and not dst.exists():
					result.restored += 1  # already back where it was
				elif src.exists():
					result.problems.append(
						(src, "A file with this name is back in the original spot")
					)
				else:
					result.problems.append((dst, "The moved file can no longer be found"))
			else:
				if not dst.exists():
					result.restored += 1  # copy already gone
				elif dst.stat().st_size != entry.get("size"):
					result.problems.append((dst, "The copy was changed since, so it was kept"))
				else:
					send2trash(dst)
					result.restored += 1
		except OSError as error:
			result.problems.append((dst, error.strerror or str(error)))

	for folder in sorted(created_dirs, key=lambda path: len(path.parts), reverse=True):
		_remove_if_empty(folder)

	if progress:
		progress(len(transfers), len(transfers))
	with Journal(journal_path) as journal:
		journal.write(
			"undo",
			at=datetime.now().isoformat(),
			restored=result.restored,
			problems=len(result.problems),
		)
	return result


def load_record(journal_path: Path) -> RunRecord | None:
	"""Summarise a journal for the History page."""
	try:
		entries = Journal.read(journal_path)
	except OSError:
		return None
	if not entries or entries[0].get("type") != "run":
		return None
	head = entries[0]
	record = RunRecord(
		id=head["id"],
		journal=journal_path,
		operation=Operation(head["operation"]),
		source=head["source"],
		dest=head["dest"],
		started=datetime.fromisoformat(head["started"]),
	)
	for entry in entries[1:]:
		match entry["type"]:
			case "file":
				record.transferred += 1
				record.bytes += entry.get("size", 0)
			case "end":
				record.finished = datetime.fromisoformat(entry["finished"])
				record.skipped = entry.get("skipped", 0)
				record.failed = entry.get("failed", 0)
				record.cancelled = entry.get("cancelled", False)
			case "undo":
				record.undone_at = datetime.fromisoformat(entry["at"])
	return record


def list_history(history_dir: Path) -> list[RunRecord]:
	"""All recorded runs, newest first."""
	if not history_dir.exists():
		return []
	records = [load_record(path) for path in history_dir.glob("*.jsonl")]
	return sorted(
		(record for record in records if record is not None),
		key=lambda record: record.started,
		reverse=True,
	)
