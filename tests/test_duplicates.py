import random
import shutil
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

from operations.duplicates import Relation, Similarity, find_duplicates


def scene(seed: int, size=(1600, 1200)) -> Image.Image:
	"""A busy, photo-like picture: textured background with overlapping shapes."""
	rnd = random.Random(seed)
	rng = np.random.default_rng(seed)
	w, h = size
	# Low-frequency "landscape" gradient plus grain
	base = rng.normal(0, 1, (h // 40 + 1, w // 40 + 1, 3))
	base = np.asarray(
		Image.fromarray(((base - base.min()) / np.ptp(base) * 255).astype(np.uint8)).resize(
			(w, h), Image.Resampling.BICUBIC
		),
		dtype=np.float32,
	)
	image = Image.fromarray(np.clip(base + rng.normal(0, 8, base.shape), 0, 255).astype(np.uint8))
	draw = ImageDraw.Draw(image)
	for _ in range(60):
		x, y = rnd.randint(0, w), rnd.randint(0, h)
		r = rnd.randint(15, 140)
		colour = tuple(rnd.randint(0, 255) for _ in range(3))
		shape = rnd.random()
		if shape < 0.4:
			draw.ellipse([x - r, y - r, x + r, y + r], fill=colour)
		elif shape < 0.8:
			draw.rectangle([x - r, y - r // 2, x + r, y + r // 2], fill=colour)
		else:
			draw.line(
				[x, y, x + rnd.randint(-300, 300), y + rnd.randint(-300, 300)], fill=colour, width=6
			)
	return image.filter(ImageFilter.GaussianBlur(0.8))


@pytest.fixture(scope="module")
def library(tmp_path_factory) -> Path:
	root = tmp_path_factory.mktemp("library")
	(root / "Phone").mkdir()
	(root / "Backup").mkdir()
	(root / "Edits").mkdir()
	original = scene(1)
	original.save(root / "Phone" / "IMG_0001.jpg", quality=92)
	shutil.copy2(root / "Phone" / "IMG_0001.jpg", root / "Backup" / "IMG_0001 copy.jpg")
	original.resize((800, 600), Image.Resampling.LANCZOS).save(
		root / "Edits" / "small.jpg", quality=70
	)
	original.save(root / "Edits" / "recompressed.jpg", quality=45)
	ImageEnhance.Brightness(original).enhance(1.15).save(
		root / "Edits" / "brighter.jpg", quality=88
	)
	original.crop((320, 240, 1280, 960)).save(root / "Edits" / "crop_centre.jpg", quality=90)
	original.crop((0, 0, 1100, 800)).save(root / "Edits" / "crop_corner.png")

	# Stored sideways but marked with EXIF orientation 6, so viewers show it upright
	exif = Image.Exif()
	exif[0x0112] = 6
	original.rotate(90, expand=True).save(root / "Backup" / "rotated.jpg", quality=90, exif=exif)

	for seed in range(100, 110):
		scene(seed).save(root / "Phone" / f"IMG_{seed}.jpg", quality=90)
	Image.new("RGB", (800, 600), "white").save(root / "Phone" / "blank.png")
	Image.new("RGB", (400, 300), "white").save(root / "Phone" / "blank_small.png")
	(root / "Phone" / "broken.jpg").write_bytes(b"not really a jpeg")
	return root


def names(group) -> set[str]:
	return {member.image.path.name for member in group.members}


def test_finds_exact_resized_edited_rotated_and_cropped_copies(library, tmp_path):
	report = find_duplicates([library], cache_path=tmp_path / "cache.sqlite")

	assert len(report.groups) == 1, [names(g) for g in report.groups]
	[group] = report.groups
	assert names(group) == {
		"IMG_0001.jpg",
		"IMG_0001 copy.jpg",
		"small.jpg",
		"recompressed.jpg",
		"brighter.jpg",
		"rotated.jpg",
		"crop_centre.jpg",
		"crop_corner.png",
	}
	relations = {m.image.path.name: m.relation for m in group.members}
	assert group.keeper.image.path.name in {"IMG_0001.jpg", "IMG_0001 copy.jpg"}
	assert Relation.EXACT in relations.values()
	assert relations["small.jpg"] is Relation.SMALLER
	assert relations["crop_centre.jpg"] is Relation.CROPPED
	assert relations["crop_corner.png"] is Relation.CROPPED
	assert {"broken.jpg"} == {info.path.name for info in report.unreadable}


def test_cache_gives_the_same_answer(library, tmp_path):
	cache = tmp_path / "cache.sqlite"
	first = find_duplicates([library], cache_path=cache)
	second = find_duplicates([library], cache_path=cache)
	assert [names(g) for g in first.groups] == [names(g) for g in second.groups]


def test_without_crop_detection_crops_are_left_out(library):
	report = find_duplicates([library], detect_crops=False)

	[group] = report.groups
	assert "crop_centre.jpg" not in names(group)
	assert "crop_corner.png" not in names(group)
	assert {"IMG_0001.jpg", "small.jpg", "rotated.jpg"} <= names(group)


def test_exact_mode_only_groups_identical_files(library):
	report = find_duplicates([library], similarity=Similarity.EXACT)

	assert [names(g) for g in report.groups] == [{"IMG_0001.jpg", "IMG_0001 copy.jpg"}]
	assert report.groups[0].title == "Exact copies"


def test_unrelated_photos_never_match(tmp_path):
	for seed in range(200, 230):
		scene(seed, (1200, 900)).save(tmp_path / f"{seed}.jpg", quality=85)

	for similarity in (Similarity.CLOSE, Similarity.LOOSE):
		report = find_duplicates([tmp_path], similarity=similarity)
		assert report.groups == [], similarity


def test_cancel_stops_early(library):
	report = find_duplicates([library], cancelled=lambda: True)
	assert report.cancelled and not report.groups
