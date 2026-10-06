"""
Duplicate photo detection for File Falcon Pro.

Three layers, from cheapest to smartest:

1. **Exact copies** – same bytes, found by grouping on file size and then hashing.
2. **Near-duplicates** – the same picture resized, re-saved, recompressed or lightly
   edited, found with perceptual hashes (pHash + dHash) of a small greyscale thumbnail.
3. **Crops** – part of another photo, found by matching ORB keypoints between images and
   checking that they line up under a single scale + shift (RANSAC). Candidate pairs come
   from locality-sensitive hashing of the keypoint descriptors, so we never compare every
   photo with every other one.

Image analysis is cached in SQLite keyed on path, size and modified time, so a second
scan of the same folder is fast.
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
import sqlite3
from collections import defaultdict
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

from operations.file_extensions import DECODABLE_IMAGE_EXTENSIONS
from operations.scanner import scan_folder

try:
	import pillow_heif

	pillow_heif.register_heif_opener()
except ImportError:  # pragma: no cover - HEIC support is optional
	pass

logger = logging.getLogger(__name__)

ANALYSIS_SIZE = 512  # longest side of the image used for keypoints
ORB_FEATURES = 400
FLAT_STD = 6.0  # thumbnails flatter than this (e.g. blank pages) skip similarity matching
CACHE_VERSION = 1

ProgressFn = Callable[[str, int, int], None]  # (stage, done, total)
CancelFn = Callable[[], bool]


class Similarity(int, Enum):
	"""How hard to look. Higher finds more, with more chance of false matches."""

	EXACT = 1
	CLOSE = 2
	LOOSE = 3

	@property
	def phash_limit(self) -> int:
		return {Similarity.EXACT: -1, Similarity.CLOSE: 8, Similarity.LOOSE: 14}[self]

	@property
	def dhash_limit(self) -> int:
		return {Similarity.EXACT: -1, Similarity.CLOSE: 14, Similarity.LOOSE: 20}[self]

	@property
	def min_inliers(self) -> int:
		return {Similarity.EXACT: 0, Similarity.CLOSE: 18, Similarity.LOOSE: 12}[self]


class Relation(str, Enum):
	"""How a photo in a group relates to the one we suggest keeping."""

	KEEP = "keep"
	EXACT = "exact"
	SMALLER = "smaller"
	SIMILAR = "similar"
	CROPPED = "cropped"
	CONTAINS = "contains"  # the suggested keeper is a crop of this one (rare)


# --- image analysis ----------------------------------------------------------------------


@dataclass(slots=True, eq=False)
class ImageInfo:
	path: Path
	size: int
	mtime: float
	width: int = 0
	height: int = 0
	phash: int = 0
	dhash: int = 0
	flat: bool = False
	analysis_size: tuple[int, int] = (0, 0)
	points: np.ndarray | None = None  # N×2 float32 keypoint positions (analysis pixels)
	descriptors: np.ndarray | None = None  # N×32 uint8 ORB descriptors
	digest: bytes | None = None
	error: str | None = None

	@property
	def pixels(self) -> int:
		return self.width * self.height

	@property
	def aspect(self) -> float:
		return self.width / self.height if self.height else 0.0


def _dct_matrix(n: int = 32) -> np.ndarray:
	k = np.arange(n)[:, None]
	i = np.arange(n)[None, :]
	matrix = np.cos(np.pi * (2 * i + 1) * k / (2 * n)) * math.sqrt(2 / n)
	matrix[0] /= math.sqrt(2)
	return matrix


_DCT = _dct_matrix()


def _bits_to_int(bits: np.ndarray) -> int:
	return int.from_bytes(np.packbits(bits.astype(np.uint8).ravel()).tobytes(), "big")


def perceptual_hash(gray: Image.Image) -> tuple[int, int, bool]:
	"""Return (pHash, dHash, is_flat) for a greyscale image."""
	small = np.asarray(gray.resize((32, 32), Image.Resampling.LANCZOS), dtype=np.float64)
	freq = _DCT @ small @ _DCT.T
	low = freq[:8, :8].ravel()
	phash = _bits_to_int(low > np.median(low[1:]))
	tiny = np.asarray(gray.resize((9, 8), Image.Resampling.LANCZOS), dtype=np.int16)
	dhash = _bits_to_int(tiny[:, 1:] > tiny[:, :-1])
	return phash, dhash, float(small.std()) < FLAT_STD


def load_oriented(path: Path, max_side: int) -> Image.Image:
	"""Open an image, upright per its EXIF orientation, roughly ``max_side`` big."""
	image = Image.open(path)
	if image.format == "JPEG":
		image.draft("RGB", (max_side, max_side))  # decode at reduced scale: much faster
	image = ImageOps.exif_transpose(image)
	if image.mode not in ("RGB", "L"):
		image = image.convert("RGBA").convert("RGB") if "A" in image.mode else image.convert("RGB")
	return image


def analyse_image(path: Path, size: int, mtime: float, *, keypoints: bool) -> ImageInfo:
	"""Decode one image and compute everything the matcher needs."""
	info = ImageInfo(path=path, size=size, mtime=mtime)
	try:
		with Image.open(path) as probe:
			width, height = probe.size
			orientation = probe.getexif().get(0x0112, 1)
		if orientation in (5, 6, 7, 8):
			width, height = height, width
		info.width, info.height = width, height

		image = load_oriented(path, ANALYSIS_SIZE)
		gray = image.convert("L")
		info.phash, info.dhash, info.flat = perceptual_hash(gray)
		if keypoints and not info.flat:
			gray.thumbnail((ANALYSIS_SIZE, ANALYSIS_SIZE), Image.Resampling.LANCZOS)
			array = np.asarray(gray, dtype=np.uint8)
			info.analysis_size = (array.shape[1], array.shape[0])
			orb = cv2.ORB_create(nfeatures=ORB_FEATURES, fastThreshold=12, edgeThreshold=15)
			found, descriptors = orb.detectAndCompute(array, None)
			if descriptors is not None and len(found) >= 8:
				info.points = np.array([kp.pt for kp in found], dtype=np.float32)
				info.descriptors = descriptors
	except Exception as error:  # corrupt or unsupported files still take part in exact matching
		info.error = str(error) or type(error).__name__
	return info


def file_digest(path: Path) -> bytes | None:
	digest = hashlib.blake2b(digest_size=20)
	try:
		with path.open("rb") as handle:
			while chunk := handle.read(1 << 20):
				digest.update(chunk)
	except OSError:
		return None
	return digest.digest()


# --- cache -------------------------------------------------------------------------------


class FeatureCache:
	"""SQLite store of analysed images, keyed on path + size + modified time."""

	def __init__(self, path: Path) -> None:
		path.parent.mkdir(parents=True, exist_ok=True)
		self._db = sqlite3.connect(path)
		self._db.execute(
			"""CREATE TABLE IF NOT EXISTS images (
				path TEXT PRIMARY KEY, size INTEGER, mtime REAL, version INTEGER,
				width INTEGER, height INTEGER, phash INTEGER, dhash INTEGER, flat INTEGER,
				aw INTEGER, ah INTEGER, points BLOB, descriptors BLOB, digest BLOB, error TEXT
			)"""
		)

	@staticmethod
	def _signed(value: int) -> int:
		return value - (1 << 64) if value >= 1 << 63 else value

	@staticmethod
	def _unsigned(value: int) -> int:
		return value + (1 << 64) if value < 0 else value

	def get(self, path: Path, size: int, mtime: float, *, keypoints: bool) -> ImageInfo | None:
		row = self._db.execute(
			"SELECT * FROM images WHERE path = ? AND size = ? AND mtime = ? AND version = ?",
			(str(path), size, mtime, CACHE_VERSION),
		).fetchone()
		if row is None:
			return None
		(_, _, _, _, width, height, phash, dhash, flat, aw, ah, points, descriptors, digest,
			error) = row  # fmt: skip
		if keypoints and not flat and not error and aw == 0:
			return None  # cached without keypoints; analyse again
		info = ImageInfo(
			path=path,
			size=size,
			mtime=mtime,
			width=width,
			height=height,
			phash=self._unsigned(phash),
			dhash=self._unsigned(dhash),
			flat=bool(flat),
			analysis_size=(aw, ah),
			digest=digest,
			error=error,
		)
		if points:
			info.points = np.frombuffer(points, dtype=np.float32).reshape(-1, 2)
			info.descriptors = np.frombuffer(descriptors, dtype=np.uint8).reshape(-1, 32)
		return info

	def put(self, info: ImageInfo) -> None:
		has_points = info.points is not None and info.descriptors is not None
		self._db.execute(
			"INSERT OR REPLACE INTO images VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
			(
				str(info.path), info.size, info.mtime, CACHE_VERSION, info.width, info.height,
				self._signed(info.phash), self._signed(info.dhash), int(info.flat),
				info.analysis_size[0], info.analysis_size[1],
				info.points.tobytes() if has_points else None,
				info.descriptors.tobytes() if has_points else None,
				info.digest, info.error,
			),
		)  # fmt: skip

	def set_digest(self, info: ImageInfo) -> None:
		self._db.execute(
			"UPDATE images SET digest = ? WHERE path = ? AND size = ? AND mtime = ?",
			(info.digest, str(info.path), info.size, info.mtime),
		)

	def commit(self) -> None:
		self._db.commit()

	def close(self) -> None:
		self._db.commit()
		self._db.close()


# --- matching ----------------------------------------------------------------------------


@dataclass(frozen=True)
class CropMatch:
	"""Result of lining up two images' keypoints."""

	inliers: int
	coverage_of_a: float  # share of image A's area that B covers
	coverage_of_b: float  # share of image B's area that lies inside A


def near_duplicate_pairs(
	infos: list[ImageInfo], similarity: Similarity, cancelled: CancelFn | None = None
) -> list[tuple[int, int]]:
	"""Pairs whose perceptual hashes are within the similarity limits."""
	candidates = [i for i, info in enumerate(infos) if not info.error and not info.flat]
	if len(candidates) < 2 or similarity is Similarity.EXACT:
		return []
	index = np.array(candidates, dtype=np.int64)
	phashes = np.array([infos[i].phash for i in candidates], dtype=np.uint64)
	dhashes = np.array([infos[i].dhash for i in candidates], dtype=np.uint64)
	aspects = np.array([infos[i].aspect for i in candidates], dtype=np.float64)
	n = len(candidates)
	chunk = max(1, 4_000_000 // n)
	pairs: list[tuple[int, int]] = []
	for start in range(0, n, chunk):
		if cancelled and cancelled():
			break
		stop = min(n, start + chunk)
		p_dist = np.bitwise_count(phashes[start:stop, None] ^ phashes[None, :])
		rows, cols = np.nonzero(p_dist <= similarity.phash_limit)
		rows += start
		keep = cols > rows
		rows, cols = rows[keep], cols[keep]
		if not len(rows):
			continue
		d_dist = np.bitwise_count(dhashes[rows] ^ dhashes[cols])
		ratio = np.maximum(aspects[rows], aspects[cols]) / np.maximum(
			np.minimum(aspects[rows], aspects[cols]), 1e-6
		)
		keep = (d_dist <= similarity.dhash_limit) & (ratio < 1.08)
		pairs.extend(zip(index[rows[keep]].tolist(), index[cols[keep]].tolist()))
	return pairs


def crop_candidates(
	infos: list[ImageInfo],
	*,
	tables: int = 8,
	max_bucket: int = 24,
	min_votes: int = 6,
	per_image: int = 6,
	seed: int = 7,
) -> list[tuple[int, int]]:
	"""Find image pairs that share many similar keypoint descriptors.

	Each descriptor is bucketed by a random subset of its bits (locality-sensitive
	hashing); two images that land in the same buckets again and again probably show
	the same thing.
	"""
	owners, blocks = [], []
	for i, info in enumerate(infos):
		if info.descriptors is not None:
			owners.append(np.full(len(info.descriptors), i, dtype=np.int64))
			blocks.append(info.descriptors)
	if len(blocks) < 2:
		return []
	owner = np.concatenate(owners)
	descriptors = np.concatenate(blocks)
	n_images = len(infos)
	bits = int(np.clip(math.log2(len(descriptors)) + 1, 14, 24))
	rng = np.random.default_rng(seed)
	pair_codes = []

	for _ in range(tables):
		positions = rng.choice(256, bits, replace=False)
		key = np.zeros(len(descriptors), dtype=np.int64)
		for bit, position in enumerate(positions):
			column = descriptors[:, position >> 3]
			key |= ((column >> (position & 7)) & 1).astype(np.int64) << bit
		combo = np.unique((key << 32) | owner)  # one vote per image per bucket
		keys, members = combo >> 32, combo & 0xFFFFFFFF
		edges = np.flatnonzero(np.diff(keys)) + 1
		starts = np.concatenate(([0], edges))
		sizes = np.diff(np.concatenate((starts, [len(keys)])))
		for size in range(2, max_bucket + 1):
			bucket_starts = starts[sizes == size]
			if not len(bucket_starts):
				continue
			grid = members[bucket_starts[:, None] + np.arange(size)[None, :]]
			a, b = np.triu_indices(size, 1)
			first, second = grid[:, a].ravel(), grid[:, b].ravel()
			pair_codes.append(np.minimum(first, second) * n_images + np.maximum(first, second))

	if not pair_codes:
		return []
	codes, votes = np.unique(np.concatenate(pair_codes), return_counts=True)
	strong = votes >= min_votes
	codes, votes = codes[strong], votes[strong]
	order = np.argsort(-votes, kind="stable")
	taken: dict[int, int] = defaultdict(int)
	result = []
	for code in codes[order].tolist():
		a, b = divmod(code, n_images)
		if taken[a] >= per_image and taken[b] >= per_image:
			continue
		taken[a] += 1
		taken[b] += 1
		result.append((a, b))
	return result


def match_keypoints(a: ImageInfo, b: ImageInfo, min_inliers: int) -> CropMatch | None:
	"""Check whether B lines up with (part of) A under a scale + shift."""
	if a.descriptors is None or b.descriptors is None:
		return None
	matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
	knn = matcher.knnMatch(b.descriptors, a.descriptors, k=2)
	good = [
		pair[0]
		for pair in knn
		if len(pair) == 2 and pair[0].distance < 0.8 * pair[1].distance and pair[0].distance < 64
	]
	if len(good) < min_inliers:
		return None
	points_b = b.points[[m.queryIdx for m in good]]
	points_a = a.points[[m.trainIdx for m in good]]
	transform, mask = cv2.estimateAffinePartial2D(
		points_b, points_a, method=cv2.RANSAC, ransacReprojThreshold=4.0, maxIters=2000
	)
	if transform is None or mask is None:
		return None
	inlier_mask = mask.ravel().astype(bool)
	inliers = int(inlier_mask.sum())
	if inliers < min_inliers or inliers < 0.3 * len(good):
		return None
	scale = math.hypot(transform[0, 0], transform[1, 0])
	angle = math.degrees(math.atan2(transform[1, 0], transform[0, 0]))
	if abs(angle) > 4 or not 0.1 < scale < 10:
		return None

	# Matches bunched into one corner (a shared logo, a toolbar) don't make a duplicate:
	# require the matched points to spread over a good part of image B.
	wb, hb = b.analysis_size
	hull = cv2.convexHull(points_b[inlier_mask].reshape(-1, 1, 2))
	if cv2.contourArea(hull) < 0.2 * wb * hb:
		return None

	wa, ha = a.analysis_size
	corners = np.array([[0, 0], [wb, 0], [wb, hb], [0, hb]], dtype=np.float64)
	mapped = corners @ transform[:, :2].T + transform[:, 2]
	x0, y0 = mapped.min(axis=0)
	x1, y1 = mapped.max(axis=0)
	overlap = max(0.0, min(x1, wa) - max(x0, 0)) * max(0.0, min(y1, ha) - max(y0, 0))
	mapped_area = (x1 - x0) * (y1 - y0)
	if overlap <= 0 or mapped_area <= 0:
		return None
	return CropMatch(
		inliers=inliers,
		coverage_of_a=overlap / (wa * ha),
		coverage_of_b=overlap / mapped_area,
	)


# --- grouping ----------------------------------------------------------------------------


class _UnionFind:
	def __init__(self, n: int) -> None:
		self.parent = list(range(n))

	def find(self, x: int) -> int:
		while self.parent[x] != x:
			self.parent[x] = self.parent[self.parent[x]]
			x = self.parent[x]
		return x

	def union(self, a: int, b: int) -> None:
		ra, rb = self.find(a), self.find(b)
		if ra != rb:
			self.parent[rb] = ra


@dataclass(slots=True, eq=False)
class GroupMember:
	image: ImageInfo
	relation: Relation
	detail: str = ""


@dataclass(eq=False)
class DuplicateGroup:
	members: list[GroupMember]  # the suggested keeper comes first

	@property
	def keeper(self) -> GroupMember:
		return self.members[0]

	@property
	def title(self) -> str:
		relations = {member.relation for member in self.members[1:]}
		if relations == {Relation.EXACT}:
			return "Exact copies"
		if Relation.CROPPED in relations or Relation.CONTAINS in relations:
			return "Includes cropped versions"
		return "Near-duplicates"

	@property
	def reclaimable(self) -> int:
		return sum(member.image.size for member in self.members[1:])


@dataclass
class DuplicateReport:
	groups: list[DuplicateGroup] = field(default_factory=list)
	scanned: int = 0
	unreadable: list[ImageInfo] = field(default_factory=list)
	cancelled: bool = False

	@property
	def extra_copies(self) -> int:
		return sum(len(group.members) - 1 for group in self.groups)

	@property
	def reclaimable(self) -> int:
		return sum(group.reclaimable for group in self.groups)


def _keeper_rank(info: ImageInfo) -> tuple:
	"""Bigger picture first, then bigger file, then older, then shorter path."""
	return (-info.pixels, -info.size, info.mtime, len(str(info.path)), str(info.path))


def _describe(keeper: ImageInfo, other: ImageInfo, crop: CropMatch | None, keeper_is_a: bool):
	if keeper.digest and keeper.digest == other.digest:
		return Relation.EXACT, "Identical file"
	if crop is not None:
		mine, theirs = (
			(crop.coverage_of_a, crop.coverage_of_b)
			if keeper_is_a
			else (crop.coverage_of_b, crop.coverage_of_a)
		)
		if mine < 0.9 and theirs >= 0.85:
			return Relation.CROPPED, f"Cropped · {mine:.0%} of the original"
		if theirs < 0.9 and mine >= 0.85:
			return Relation.CONTAINS, "Wider shot · contains the kept one"
	if other.pixels and keeper.pixels and other.pixels < 0.9 * keeper.pixels:
		return Relation.SMALLER, f"Smaller copy · {other.width / keeper.width:.0%} size"
	return Relation.SIMILAR, "Similar · edited or re-saved"


def find_duplicates(
	folders: Iterable[Path],
	*,
	similarity: Similarity = Similarity.CLOSE,
	detect_crops: bool = True,
	cache_path: Path | None = None,
	progress: ProgressFn | None = None,
	cancelled: CancelFn | None = None,
	workers: int | None = None,
) -> DuplicateReport:
	"""Scan ``folders`` for duplicate photos and group them."""
	report = DuplicateReport()
	is_cancelled = cancelled or (lambda: False)
	notify = progress or (lambda stage, done, total: None)
	workers = workers or max(2, min(8, (os.cpu_count() or 4)))
	detect_crops = detect_crops and similarity is not Similarity.EXACT

	# 1. Collect images
	seen: set[Path] = set()
	files = []
	for folder in folders:
		for item in scan_folder(folder, cancelled=is_cancelled):
			if item.path.suffix.lower() in DECODABLE_IMAGE_EXTENSIONS and item.path not in seen:
				seen.add(item.path)
				files.append(item)
	report.scanned = len(files)
	if is_cancelled():
		report.cancelled = True
		return report

	cache = FeatureCache(cache_path) if cache_path else None
	try:
		# 2. Analyse (or load from cache)
		infos: list[ImageInfo] = []
		if similarity is Similarity.EXACT:
			infos = [ImageInfo(path=f.path, size=f.size, mtime=f.mtime) for f in files]
		else:
			todo = []
			for item in files:
				cached = (
					cache.get(item.path, item.size, item.mtime, keypoints=detect_crops)
					if cache
					else None
				)
				if cached is not None:
					infos.append(cached)
				else:
					todo.append(item)
			done = len(infos)
			notify("analyse", done, len(files))
			with ThreadPoolExecutor(max_workers=workers) as pool:
				for start in range(0, len(todo), 64):
					if is_cancelled():
						report.cancelled = True
						return report
					batch = todo[start : start + 64]
					results = pool.map(
						lambda f: analyse_image(f.path, f.size, f.mtime, keypoints=detect_crops),
						batch,
					)
					for info in results:
						infos.append(info)
						if cache:
							cache.put(info)
					done += len(batch)
					notify("analyse", done, len(files))
					if cache:
						cache.commit()

		# 3. Exact copies: same size, then same content hash
		by_size: dict[int, list[ImageInfo]] = defaultdict(list)
		for info in infos:
			by_size[info.size].append(info)
		need_digest = [info for group in by_size.values() if len(group) > 1 for info in group]
		need_digest = [info for info in need_digest if info.digest is None]
		for count, info in enumerate(need_digest, 1):
			if is_cancelled():
				report.cancelled = True
				return report
			info.digest = file_digest(info.path)
			if cache and similarity is not Similarity.EXACT:
				cache.set_digest(info)
			if count % 32 == 0:
				notify("compare", count, len(need_digest))
		if cache:
			cache.commit()

		union = _UnionFind(len(infos))
		by_digest: dict[bytes, int] = {}
		for i, info in enumerate(infos):
			if info.digest is None:
				continue
			if info.digest in by_digest:
				union.union(by_digest[info.digest], i)
			else:
				by_digest[info.digest] = i

		# 4. Near-duplicates from perceptual hashes
		notify("match", 0, 1)
		for a, b in near_duplicate_pairs(infos, similarity, is_cancelled):
			union.union(a, b)

		# 5. Crops from keypoint matching
		crops: dict[tuple[int, int], CropMatch] = {}
		if detect_crops:
			candidates = [
				(a, b) for a, b in crop_candidates(infos) if union.find(a) != union.find(b)
			]

			def verify(pair):
				a, b = pair
				first, second = infos[a], infos[b]
				# Match the smaller picture against the bigger one
				if second.pixels > first.pixels:
					a, b, first, second = b, a, second, first
				return a, b, match_keypoints(first, second, similarity.min_inliers)

			with ThreadPoolExecutor(max_workers=workers) as pool:
				for count, (a, b, match) in enumerate(pool.map(verify, candidates), 1):
					if is_cancelled():
						report.cancelled = True
						return report
					if count % 16 == 0:
						notify("match", count, len(candidates))
					if match is None:
						continue
					if min(match.coverage_of_a, match.coverage_of_b) < 0.15:
						continue  # barely overlapping shots, e.g. two halves of a panorama
					if max(match.coverage_of_a, match.coverage_of_b) < 0.85:
						continue  # neither contains the other: similar scene, not a copy
					crops[(a, b)] = match
					union.union(a, b)

		# 6. Build groups
		members_by_root: dict[int, list[int]] = defaultdict(list)
		for i in range(len(infos)):
			members_by_root[union.find(i)].append(i)
		for indexes in members_by_root.values():
			if len(indexes) < 2:
				continue
			indexes.sort(key=lambda i: _keeper_rank(infos[i]))
			keeper_index = indexes[0]
			keeper = infos[keeper_index]
			members = [GroupMember(keeper, Relation.KEEP, "Best quality · suggested keep")]
			for i in indexes[1:]:
				crop = crops.get((keeper_index, i))
				keeper_is_a = True
				if crop is None and (i, keeper_index) in crops:
					crop, keeper_is_a = crops[(i, keeper_index)], False
				relation, detail = _describe(keeper, infos[i], crop, keeper_is_a)
				members.append(GroupMember(infos[i], relation, detail))
			report.groups.append(DuplicateGroup(members))

		report.groups.sort(key=lambda group: (-group.reclaimable, str(group.keeper.image.path)))
		report.unreadable = [info for info in infos if info.error]
		return report
	finally:
		if cache:
			cache.close()
