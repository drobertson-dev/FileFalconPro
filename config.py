"""
Configuration management for File Falcon Pro.
Handles persistent storage of user preferences and application settings.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from operations.planner import DateLayout, NameMatch, Operation, OrganizeOptions

logger = logging.getLogger(__name__)

APP_NAME = "File Falcon Pro"
APP_VERSION = "2.0.0"
BUNDLE_ID = "io.github.drobertson-dev.FileFalconPro"


@dataclass(frozen=True)
class AppPaths:
	"""Where the app keeps its own files."""

	data_dir: Path
	cache_dir: Path

	@property
	def settings_file(self) -> Path:
		return self.data_dir / "settings.json"

	@property
	def history_dir(self) -> Path:
		return self.data_dir / "history"

	@property
	def log_file(self) -> Path:
		return self.data_dir / "filefalcon.log"

	def ensure(self) -> None:
		for folder in (self.data_dir, self.cache_dir, self.history_dir):
			folder.mkdir(parents=True, exist_ok=True)


@dataclass
class Settings:
	"""User preferences, remembered between launches."""

	source: str = ""
	dest: str = ""
	categories: list[str] = field(default_factory=lambda: ["photos", "videos"])
	by_type: bool = True
	by_date: bool = True
	date_layout: str = DateLayout.YEAR_MONTH.value
	keep_structure: bool = False
	name_filter: str = ""
	name_match: str = NameMatch.CONTAINS.value
	operation: str = Operation.COPY.value
	remove_empty_dirs: bool = True
	dupes_folder: str = ""
	dupes_similarity: int = 2
	dupes_detect_crops: bool = True
	window_geometry: str = ""

	@classmethod
	def load(cls, path: Path) -> Settings:
		"""Load settings, falling back to defaults for anything missing or invalid."""
		try:
			raw = json.loads(path.read_text(encoding="utf-8"))
		except FileNotFoundError:
			return cls()
		except (OSError, json.JSONDecodeError) as error:
			logger.warning("Could not read settings from %s: %s", path, error)
			return cls()
		defaults = cls()
		known = {f.name: f for f in fields(cls)}
		values = {}
		for name, value in raw.items():
			if name in known and type(value) is type(getattr(defaults, name)):
				values[name] = value
		settings = cls(**values)
		try:
			settings.organize_options()
		except ValueError:
			logger.warning("Ignoring invalid organise settings in %s", path)
			return cls(source=settings.source, dest=settings.dest)
		return settings

	def save(self, path: Path) -> None:
		path.parent.mkdir(parents=True, exist_ok=True)
		temp = path.with_suffix(".tmp")
		temp.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
		temp.replace(path)

	def organize_options(self) -> OrganizeOptions:
		return OrganizeOptions(
			categories=frozenset(self.categories),
			by_type=self.by_type,
			by_date=self.by_date,
			date_layout=DateLayout(self.date_layout),
			keep_structure=self.keep_structure,
			name_filter=self.name_filter,
			name_match=NameMatch(self.name_match),
			operation=Operation(self.operation),
		)

	def set_organize_options(self, options: OrganizeOptions) -> None:
		self.categories = sorted(options.categories)
		self.by_type = options.by_type
		self.by_date = options.by_date
		self.date_layout = options.date_layout.value
		self.keep_structure = options.keep_structure
		self.name_filter = options.name_filter
		self.name_match = options.name_match.value
		self.operation = options.operation.value
