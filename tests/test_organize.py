import os
import struct
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from PIL import Image

from operations import runner
from operations.dates import photo_taken, resolve_capture_dates, video_taken
from operations.planner import (
	DateLayout,
	NameMatch,
	Operation,
	OrganizeOptions,
	Status,
	build_plan,
)
from operations.runner import execute_plan, list_history, undo_run
from operations.scanner import scan_folder

JAN_2021 = datetime(2021, 1, 15, 12, 0).timestamp()


def make(path: Path, content: bytes = b"data", mtime: float = JAN_2021) -> Path:
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_bytes(content)
	os.utime(path, (mtime, mtime))
	return path


def plan_for(source: Path, dest: Path, **options):
	items = scan_folder(source, exclude=dest)
	for item in items:
		item.birthtime = None  # make file dates deterministic
	return build_plan(items, OrganizeOptions(**options), dest)


def rel_dests(plan, dest: Path) -> dict[str, str]:
	return {p.item.rel.as_posix(): p.dest.relative_to(dest).as_posix() for p in plan.files}


@pytest.fixture
def fake_trash(monkeypatch):
	trashed = []

	def trash(path):
		trashed.append(Path(path))
		Path(path).unlink()

	monkeypatch.setattr(runner, "send2trash", trash)
	return trashed


# --- scanning -------------------------------------------------------------------------


def test_scan_skips_hidden_packages_symlinks_and_destination(tmp_path):
	src = tmp_path / "src"
	make(src / "a.jpg")
	make(src / ".hidden.jpg")
	make(src / ".git" / "obj.jpg")
	make(src / "Lib.photoslibrary" / "originals" / "x.jpg")
	make(src / "sorted" / "already.jpg")
	(src / "link.jpg").symlink_to(src / "a.jpg")

	items = scan_folder(src, exclude=src / "sorted")

	assert [item.rel.as_posix() for item in items] == ["a.jpg"]


# --- planning -------------------------------------------------------------------------


def test_plan_by_type_and_date(tmp_path):
	src, dest = tmp_path / "src", tmp_path / "dest"
	make(src / "trip" / "IMG_1.JPG")
	make(src / "clip.mov")
	make(src / "notes.txt")

	plan = plan_for(src, dest, categories=frozenset({"photos", "videos"}))

	assert rel_dests(plan, dest) == {
		"clip.mov": "Videos/2021/2021-01/clip.mov",
		"trip/IMG_1.JPG": "Photos/2021/2021-01/IMG_1.JPG",
	}


def test_plan_all_layout_options_combine(tmp_path):
	src, dest = tmp_path / "src", tmp_path / "dest"
	make(src / "trip" / "day1" / "a.jpg")

	plan = plan_for(
		src,
		dest,
		categories=frozenset({"photos"}),
		date_layout=DateLayout.YEAR_MONTH_DAY,
		keep_structure=True,
	)

	assert rel_dests(plan, dest) == {
		"trip/day1/a.jpg": "Photos/2021/2021-01/2021-01-15/trip/day1/a.jpg",
	}


def test_plan_flat_when_no_layout_options(tmp_path):
	src, dest = tmp_path / "src", tmp_path / "dest"
	make(src / "x" / "a.pdf")

	plan = plan_for(src, dest, categories=frozenset({"pdfs"}), by_type=False, by_date=False)

	assert rel_dests(plan, dest) == {"x/a.pdf": "a.pdf"}


@pytest.mark.parametrize(
	("pattern", "mode", "expected"),
	[
		("invoice", NameMatch.CONTAINS, {"Invoice-2021.pdf", "old invoice.pdf"}),
		("invoice-2021", NameMatch.EXACT, {"Invoice-2021.pdf"}),
		("invoice*.pdf", NameMatch.WILDCARD, {"Invoice-2021.pdf"}),
		(r"\d{4}", NameMatch.REGEX, {"Invoice-2021.pdf"}),
	],
)
def test_name_filter(tmp_path, pattern, mode, expected):
	src, dest = tmp_path / "src", tmp_path / "dest"
	for name in ("Invoice-2021.pdf", "old invoice.pdf", "receipt.pdf"):
		make(src / name)

	plan = plan_for(src, dest, categories=frozenset({"pdfs"}), name_filter=pattern, name_match=mode)

	assert {p.item.path.name for p in plan.files} == expected


def test_invalid_regex_reports_error(tmp_path):
	make(tmp_path / "src" / "a.pdf")
	plan = plan_for(
		tmp_path / "src", tmp_path / "d", categories=frozenset({"pdfs"}), name_filter="(",
		name_match=NameMatch.REGEX,
	)  # fmt: skip
	assert plan.filter_error and not plan.files


def test_plan_renames_clashes_case_insensitively(tmp_path):
	src, dest = tmp_path / "src", tmp_path / "dest"
	make(src / "a" / "IMG.jpg", b"one")
	make(src / "b" / "img.JPG", b"two")
	make(src / "c" / "img.jpg", b"three")

	plan = plan_for(src, dest, categories=frozenset({"photos"}), by_type=False, by_date=False)

	assert sorted(p.dest.name for p in plan.files) == ["IMG.jpg", "img (1).JPG", "img (2).jpg"]
	assert [p.status for p in plan.files] == [Status.READY, Status.RENAMED, Status.RENAMED]


def test_plan_detects_existing_files_at_destination(tmp_path):
	src, dest = tmp_path / "src", tmp_path / "dest"
	make(src / "same.jpg", b"1234")
	make(src / "maybe.jpg", b"1234")
	make(src / "different.jpg", b"1234")
	make(dest / "same.jpg", b"1234")  # same name, size and time: copied here before
	make(dest / "maybe.jpg", b"1234", mtime=JAN_2021 + 3600)
	make(dest / "different.jpg", b"123456789")

	plan = plan_for(src, dest, categories=frozenset({"photos"}), by_type=False, by_date=False)
	by_name = {p.item.path.name: p for p in plan.files}

	assert by_name["same.jpg"].status is Status.ALREADY_THERE
	assert not by_name["same.jpg"].actionable
	assert by_name["maybe.jpg"].status is Status.CHECK_FIRST
	assert by_name["maybe.jpg"].actionable
	assert by_name["different.jpg"].status is Status.RENAMED
	assert by_name["different.jpg"].dest.name == "different (1).jpg"


def test_plan_in_place_leaves_sorted_files_alone(tmp_path):
	root = tmp_path / "root"
	make(root / "Photos" / "2021" / "2021-01" / "a.jpg")
	make(root / "a.jpg", b"another a")
	make(root / "b.jpg")

	items = scan_folder(root)
	for item in items:
		item.birthtime = None
	plan = build_plan(items, OrganizeOptions(categories=frozenset({"photos"})), root)
	by_rel = {p.item.rel.as_posix(): p for p in plan.files}

	assert by_rel["Photos/2021/2021-01/a.jpg"].status is Status.IN_PLACE
	assert by_rel["a.jpg"].dest.name == "a (1).jpg"
	assert by_rel["b.jpg"].status is Status.READY


def test_excluded_files_are_not_actionable(tmp_path):
	src, dest = tmp_path / "src", tmp_path / "dest"
	keep = make(src / "keep.jpg")
	make(src / "go.jpg")

	items = scan_folder(src)
	plan = build_plan(
		items, OrganizeOptions(categories=frozenset({"photos"})), dest, excluded=frozenset({keep})
	)

	assert [p.item.path.name for p in plan.actionable] == ["go.jpg"]


# --- running and undoing --------------------------------------------------------------


def test_copy_run_and_undo(tmp_path, fake_trash):
	src, dest, history = tmp_path / "src", tmp_path / "dest", tmp_path / "history"
	make(src / "a.jpg", b"aaa")
	make(src / "sub" / "b.mov", b"bbb")
	plan = plan_for(src, dest, categories=frozenset({"photos", "videos"}))

	result = execute_plan(
		plan.files, operation=Operation.COPY, source_root=src, dest_root=dest, history_dir=history
	)

	assert result.record.transferred == 2 and not result.errors
	assert (dest / "Photos/2021/2021-01/a.jpg").read_bytes() == b"aaa"
	assert (src / "a.jpg").exists(), "copy must keep the original"
	assert not list(dest.rglob("*.ffp-partial"))

	undo = undo_run(result.record.journal)

	assert undo.restored == 2 and not undo.problems
	assert len(fake_trash) == 2
	assert not dest.exists() or not any(dest.iterdir()), "created folders are cleaned up"
	assert (src / "a.jpg").exists()


def test_move_run_and_undo_restores_everything(tmp_path):
	src, dest, history = tmp_path / "src", tmp_path / "dest", tmp_path / "history"
	make(src / "trip" / "a.jpg", b"aaa")
	make(src / "trip" / ".DS_Store", b"finder junk")
	make(src / "b.pdf", b"bbb")
	plan = plan_for(src, dest, categories=frozenset({"photos", "pdfs"}))

	result = execute_plan(
		plan.files, operation=Operation.MOVE, source_root=src, dest_root=dest, history_dir=history
	)

	assert result.record.transferred == 2
	assert not (src / "trip").exists(), "emptied source folder is removed"
	assert (dest / "PDFs/2021/2021-01/b.pdf").read_bytes() == b"bbb"

	undo = undo_run(result.record.journal)

	assert undo.restored == 2 and not undo.problems
	assert (src / "trip" / "a.jpg").read_bytes() == b"aaa"
	assert (src / "b.pdf").read_bytes() == b"bbb"
	assert not dest.exists() or not any(dest.rglob("*"))

	with pytest.raises(ValueError, match="already been undone"):
		undo_run(result.record.journal)


def test_run_skips_identical_and_renames_lookalikes(tmp_path):
	src, dest, history = tmp_path / "src", tmp_path / "dest", tmp_path / "history"
	make(src / "same.jpg", b"1234")
	make(src / "lookalike.jpg", b"abcd")
	make(dest / "same.jpg", b"1234", mtime=JAN_2021 + 60)  # touched since, same content
	make(dest / "lookalike.jpg", b"wxyz", mtime=JAN_2021 + 60)  # same size, other content
	plan = plan_for(src, dest, categories=frozenset({"photos"}), by_type=False, by_date=False)

	result = execute_plan(
		plan.files, operation=Operation.COPY, source_root=src, dest_root=dest, history_dir=history
	)

	assert [path.name for path, _ in result.skipped] == ["same.jpg"]
	assert (dest / "lookalike (1).jpg").read_bytes() == b"abcd"
	assert (dest / "lookalike.jpg").read_bytes() == b"wxyz"


def test_second_copy_run_has_nothing_to_do(tmp_path):
	src, dest, history = tmp_path / "src", tmp_path / "dest", tmp_path / "history"
	make(src / "a.jpg", b"aaa")
	make(src / "b.mov", b"bbb")
	plan = plan_for(src, dest, categories=frozenset({"photos", "videos"}))
	execute_plan(
		plan.files, operation=Operation.COPY, source_root=src, dest_root=dest, history_dir=history
	)

	again = plan_for(src, dest, categories=frozenset({"photos", "videos"}))

	assert [p.status for p in again.files] == [Status.ALREADY_THERE] * 2
	assert not again.actionable


def test_cancelled_run_is_recorded_and_undoable(tmp_path):
	src, dest, history = tmp_path / "src", tmp_path / "dest", tmp_path / "history"
	for n in range(5):
		make(src / f"{n}.jpg", str(n).encode())
	plan = plan_for(src, dest, categories=frozenset({"photos"}), by_type=False, by_date=False)
	calls = iter(range(100))

	result = execute_plan(
		plan.files,
		operation=Operation.MOVE,
		source_root=src,
		dest_root=dest,
		history_dir=history,
		cancelled=lambda: next(calls) >= 2,
	)

	assert result.record.cancelled and result.record.transferred == 2
	[record] = list_history(history)
	assert record.transferred == 2 and record.cancelled and record.undone_at is None
	undo_run(record.journal)
	assert len(list(src.glob("*.jpg"))) == 5
	assert list_history(history)[0].undone_at is not None


def test_undo_leaves_changed_copies_alone(tmp_path, fake_trash):
	src, dest, history = tmp_path / "src", tmp_path / "dest", tmp_path / "history"
	make(src / "a.txt", b"original")
	plan = plan_for(src, dest, categories=frozenset({"documents"}), by_type=False, by_date=False)
	result = execute_plan(
		plan.files, operation=Operation.COPY, source_root=src, dest_root=dest, history_dir=history
	)
	(dest / "a.txt").write_bytes(b"edited after copying")

	undo = undo_run(result.record.journal)

	assert undo.restored == 0 and len(undo.problems) == 1
	assert (dest / "a.txt").exists() and not fake_trash


# --- capture dates --------------------------------------------------------------------


def _atom(kind: bytes, payload: bytes) -> bytes:
	return struct.pack(">I4s", 8 + len(payload), kind) + payload


def test_video_taken_reads_movie_header(tmp_path):
	when = datetime(2019, 7, 4, 18, 30, tzinfo=timezone.utc)
	seconds = int((when - datetime(1904, 1, 1, tzinfo=timezone.utc)).total_seconds())
	mvhd = _atom(b"mvhd", bytes([0, 0, 0, 0]) + struct.pack(">II", seconds, seconds) + bytes(88))
	movie = _atom(b"ftyp", b"qt  " + bytes(4)) + _atom(b"mdat", bytes(64)) + _atom(b"moov", mvhd)
	path = make(tmp_path / "clip.mov", movie)

	assert video_taken(path) == when.astimezone().replace(tzinfo=None)


def test_video_taken_handles_garbage(tmp_path):
	assert video_taken(make(tmp_path / "bad.mp4", b"\x00\x00\x00\x01junk")) is None
	assert video_taken(make(tmp_path / "zero.mov", _atom(b"moov", b""))) is None


def test_photo_taken_reads_exif(tmp_path):
	exif = Image.Exif()
	exif.get_ifd(0x8769)[36867] = "2018:12:25 08:15:00"
	path = tmp_path / "xmas.jpg"
	Image.new("RGB", (8, 8)).save(path, exif=exif)

	assert photo_taken(path) == datetime(2018, 12, 25, 8, 15)


def test_resolve_dates_falls_back_to_file_date(tmp_path):
	exif = Image.Exif()
	exif.get_ifd(0x8769)[36867] = "2018:12:25 08:15:00"
	Image.new("RGB", (8, 8)).save(tmp_path / "xmas.jpg", exif=exif)
	Image.new("RGB", (8, 8)).save(tmp_path / "plain.png")
	make(tmp_path / "doc.pdf")

	items = {item.path.name: item for item in scan_folder(tmp_path)}
	results = resolve_capture_dates(items.values())

	assert results[items["xmas.jpg"].path] == (datetime(2018, 12, 25, 8, 15), "photo")
	assert results[items["plain.png"].path] == (None, "file")
	assert results[items["doc.pdf"].path] == (None, "file")
	assert items["doc.pdf"].date - datetime.fromtimestamp(JAN_2021) < timedelta(days=1)
