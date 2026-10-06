"""
Destination planning for File Falcon Pro.

Turns scanned files plus the user's organise options into a concrete plan: where each
file will end up, and whether it needs renaming or can be skipped. Planning never touches
the files themselves, so it is cheap to redo whenever an option changes.
"""

from __future__ import annotations

import fnmatch
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

from operations.scanner import FileItem


class DateLayout(str, Enum):
	"""How date folders are nested under the destination."""

	YEAR = "year"
	YEAR_MONTH = "year_month"
	YEAR_MONTH_DAY = "year_month_day"
	MONTH_FLAT = "month_flat"

	@property
	def label(self) -> str:
		return {
			DateLayout.YEAR: "Year",
			DateLayout.YEAR_MONTH: "Year › Month",
			DateLayout.YEAR_MONTH_DAY: "Year › Month › Day",
			DateLayout.MONTH_FLAT: "Year-Month",
		}[self]

	def parts(self, when: datetime) -> tuple[str, ...]:
		year, month, day = f"{when:%Y}", f"{when:%Y-%m}", f"{when:%Y-%m-%d}"
		return {
			DateLayout.YEAR: (year,),
			DateLayout.YEAR_MONTH: (year, month),
			DateLayout.YEAR_MONTH_DAY: (year, month, day),
			DateLayout.MONTH_FLAT: (month,),
		}[self]


class NameMatch(str, Enum):
	"""How the filename filter is interpreted."""

	CONTAINS = "contains"
	EXACT = "exact"
	WILDCARD = "wildcard"
	REGEX = "regex"

	@property
	def label(self) -> str:
		return {
			NameMatch.CONTAINS: "contains",
			NameMatch.EXACT: "is exactly",
			NameMatch.WILDCARD: "matches wildcard",
			NameMatch.REGEX: "matches regex",
		}[self]


class Operation(str, Enum):
	COPY = "copy"
	MOVE = "move"


class Status(str, Enum):
	"""What will happen to a planned file."""

	READY = "ready"
	RENAMED = "renamed"  # destination name taken by a different file; gets a " (1)" suffix
	CHECK_FIRST = "check_first"  # same name and size there; compared byte-for-byte on run
	ALREADY_THERE = "already_there"  # same name, size and modified time there; skipped
	IN_PLACE = "in_place"  # file is already exactly where it would go


# Filesystems such as FAT/exFAT only store modified times to the nearest 2 seconds.
MTIME_TOLERANCE = 2.0


@dataclass
class OrganizeOptions:
	"""Everything the user can choose on the Organize page."""

	categories: frozenset[str] = frozenset({"photos", "videos"})
	by_type: bool = True
	by_date: bool = True
	date_layout: DateLayout = DateLayout.YEAR_MONTH
	keep_structure: bool = False
	name_filter: str = ""
	name_match: NameMatch = NameMatch.CONTAINS
	operation: Operation = Operation.COPY


@dataclass(slots=True, eq=False)
class PlannedFile:
	item: FileItem
	dest: Path
	status: Status
	included: bool = True

	@property
	def actionable(self) -> bool:
		"""Whether running the plan will do anything with this file."""
		return self.included and self.status not in (Status.IN_PLACE, Status.ALREADY_THERE)


@dataclass
class Plan:
	files: list[PlannedFile] = field(default_factory=list)
	dest_root: Path | None = None
	filter_error: str | None = None

	@property
	def actionable(self) -> list[PlannedFile]:
		return [planned for planned in self.files if planned.actionable]


def name_matcher(pattern: str, mode: NameMatch):
	"""Build a predicate for the filename filter. Raises re.error for a bad regex."""
	pattern = pattern.strip()
	if not pattern:
		return lambda name: True
	needle = pattern.casefold()
	match mode:
		case NameMatch.CONTAINS:
			return lambda name: needle in name.casefold()
		case NameMatch.EXACT:
			return lambda name: needle in (name.casefold(), Path(name).stem.casefold())
		case NameMatch.WILDCARD:
			return lambda name: fnmatch.fnmatch(name.casefold(), needle)
		case NameMatch.REGEX:
			compiled = re.compile(pattern, re.IGNORECASE)
			return lambda name: compiled.search(name) is not None
	raise ValueError(f"Unknown match mode: {mode}")


def destination_folder(item: FileItem, options: OrganizeOptions, dest_root: Path) -> Path:
	"""Return the folder ``item`` belongs in under ``dest_root``."""
	folder = dest_root
	if options.by_type:
		folder /= item.category.label
	if options.by_date:
		folder = folder.joinpath(*options.date_layout.parts(item.date))
	if options.keep_structure:
		folder /= item.rel.parent
	return folder


def numbered_name(name: str, counter: int) -> str:
	stem, suffix = os.path.splitext(name)
	return f"{stem} ({counter}){suffix}"


class _DestinationIndex:
	"""Tracks names that are taken in each destination folder, case-insensitively.

	macOS and Windows filesystems are case-insensitive by default, so ``IMG.JPG`` and
	``img.jpg`` must be treated as the same name.
	"""

	def __init__(self) -> None:
		self._existing: dict[Path, dict[str, tuple[int, float]]] = {}
		self._claimed: set[tuple[Path, str]] = set()

	def existing(self, folder: Path) -> dict[str, tuple[int, float]]:
		"""Map of casefolded name -> (size, mtime) for files already in ``folder``."""
		if folder not in self._existing:
			names: dict[str, tuple[int, float]] = {}
			try:
				with os.scandir(folder) as entries:
					for entry in entries:
						try:
							stat = entry.stat(follow_symlinks=False)
							names[entry.name.casefold()] = (stat.st_size, stat.st_mtime)
						except OSError:
							names[entry.name.casefold()] = (-1, 0.0)
			except OSError:
				pass
			self._existing[folder] = names
		return self._existing[folder]

	def is_taken(self, folder: Path, name: str) -> bool:
		key = name.casefold()
		return key in self.existing(folder) or (folder, key) in self._claimed

	def is_claimed(self, folder: Path, name: str) -> bool:
		return (folder, name.casefold()) in self._claimed

	def claim(self, folder: Path, name: str) -> None:
		self._claimed.add((folder, name.casefold()))


def build_plan(
	items: Iterable[FileItem],
	options: OrganizeOptions,
	dest_root: Path,
	*,
	excluded: frozenset[Path] = frozenset(),
) -> Plan:
	"""Work out where every matching file goes.

	Args:
	    items: Scanned files.
	    options: The user's organise options.
	    dest_root: Root destination folder.
	    excluded: Source paths the user unticked; they stay in the plan but are skipped.
	"""
	plan = Plan(dest_root=dest_root)
	try:
		matches_name = name_matcher(options.name_filter, options.name_match)
	except re.error as error:
		plan.filter_error = f"Invalid regex: {error}"
		return plan

	selected = [
		item
		for item in items
		if item.category.key in options.categories and matches_name(item.path.name)
	]
	index = _DestinationIndex()

	# Files already sitting where they belong keep their names; claim those first so
	# other files never get renamed around them.
	targets: list[tuple[FileItem, Path]] = []
	for item in selected:
		folder = destination_folder(item, options, dest_root)
		if folder / item.path.name == item.path:
			index.claim(folder, item.path.name)
		targets.append((item, folder))

	for item, folder in targets:
		name = item.path.name
		if folder / name == item.path:
			plan.files.append(PlannedFile(item, item.path, Status.IN_PLACE))
			continue
		if item.path in excluded:
			plan.files.append(PlannedFile(item, folder / name, Status.READY, included=False))
			continue

		existing = index.existing(folder).get(name.casefold())
		if existing and existing[0] == item.size and not index.is_claimed(folder, name):
			index.claim(folder, name)
			same_time = abs(existing[1] - item.mtime) <= MTIME_TOLERANCE
			status = Status.ALREADY_THERE if same_time else Status.CHECK_FIRST
			plan.files.append(PlannedFile(item, folder / name, status))
			continue

		status = Status.READY
		candidate = name
		counter = 1
		while index.is_taken(folder, candidate):
			candidate = numbered_name(name, counter)
			counter += 1
			status = Status.RENAMED
		index.claim(folder, candidate)
		plan.files.append(PlannedFile(item, folder / candidate, status))

	return plan
