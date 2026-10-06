# How it works

File Falcon Pro is split into two layers:

```
main.py              entry point: app paths, logging, theme, main window
config.py            app constants, AppPaths, persisted Settings
operations/          plain Python — no Qt — and fully unit-tested
  file_extensions.py   file types and their extensions
  scanner.py           walk a folder → FileItem list
  dates.py             capture dates from EXIF / QuickTime headers
  planner.py           FileItems + options → Plan (where every file goes)
  runner.py            execute a Plan with a journal; undo a journal; history
  duplicates.py        exact / perceptual / crop duplicate detection
gui/                 the PyQt6 interface
  main_window.py       sidebar + pages, settings persistence, theme switching
  organize_page.py     Organize page
  duplicates_page.py   Duplicates page
  history_page.py      History page
  preview_model.py     table model behind the Organize preview
  workers.py           background tasks (Task / TaskSlot)
  thumbnails.py        async thumbnail loading
  widgets.py           folder cards, chips, switches, banners, empty states
  theme.py, icons.py   light/dark themes and line icons
```

Everything slow — scanning, reading dates, planning, copying, duplicate detection,
thumbnails — runs on a thread pool. A `TaskSlot` holds at most one task of each kind and
only delivers results from the newest one, so a stale scan can never overwrite fresh state.

## Organize

```
scan_folder ──► resolve_capture_dates ──► build_plan ──► execute_plan ──► journal
 (stat only)     (photos/videos only)      (no disk writes)  (copy/move)     └► undo_run
```

### Scanning

`scan_folder` walks the source with `os.walk`, recording size, modified time, birth time and
category for every regular file. It skips hidden files and folders, symbolic links, an
excluded subfolder (the destination, when it sits inside the source) and macOS packages
such as `.photoslibrary` and `.app` bundles, which look like folders but must never be taken
apart.

### Capture dates

When *Group by date* is on, photos and videos in the selected categories are opened in
parallel to find when they were taken:

- **Photos:** EXIF `DateTimeOriginal`, then `DateTimeDigitized`, then the TIFF `DateTime`
  tag (via Pillow, with pillow-heif for HEIC).
- **Videos:** the creation time in the QuickTime/MP4 movie header (`moov/mvhd`), read
  directly without any external tools. It is stored in UTC and converted to local time.

Implausible values (before 1971 or in the future) are ignored. Without a capture date, the
earlier of the file's birth and modified times is used — copies often reset one of the two.

### Planning

`build_plan` turns the scanned files and the options into a `Plan`: one `PlannedFile` per
matching file with a destination and a status. It never writes to disk, so it is simply
re-run whenever an option changes.

- The destination folder is `dest / [type] / [date parts] / [original subfolders]`.
- Names are compared **case-insensitively**, both against files already in each destination
  folder (read once per folder with `os.scandir`) and against names claimed earlier in the
  same plan. Clashes get ` (1)`, ` (2)`… suffixes.
- Files already exactly where they would go are claimed first, so nothing is renamed
  around them (this is what makes sorting a folder into itself safe to repeat).
- A file whose name **and size and modified time** (within 2 s, for FAT/exFAT drives)
  match an existing file is *Already there* and is skipped — the same quick check rsync
  uses. If only name and size match, it is *Check first*: compared byte for byte at run
  time.

### Running and the journal

`execute_plan` processes the plan file by file:

- It re-checks the destination just before each file. Identical files are skipped;
  anything else that has appeared since planning gets a fresh numbered name. Nothing is
  ever overwritten.
- **Copies** are written to a hidden `.<name>.ffp-partial` file and renamed into place, so
  an interrupted copy never leaves a truncated file behind.
- **Moves** use `rename` within a drive and copy-then-delete across drives.
- Every folder created and every file transferred is appended to a JSON-lines **journal**
  in `~/Library/Application Support/File Falcon Pro/history/` and flushed immediately.

`undo_run` reads a journal backwards. Moved files go back (folders are recreated as
needed); copies go to the Trash, but only if their size still matches what was copied. A
file is never moved back on top of something new. Folders the run created are removed if
they are empty again, and an `undo` record is appended so a run can't be undone twice.

## Duplicates

`find_duplicates` works in layers, from cheapest to smartest, and joins every match it
finds with a union-find structure into groups.

### 1. Analysis (cached)

Each image is decoded once, upright according to its EXIF orientation. JPEGs are decoded
at reduced scale (`Image.draft`), which is several times faster than a full decode. From
each image we keep:

- **pHash** — the 8×8 lowest frequencies of a 32×32 greyscale DCT, thresholded at their
  median (64 bits).
- **dHash** — whether each pixel of a 9×8 greyscale thumbnail is brighter than its right
  neighbour (64 bits).
- **ORB keypoints** — up to 400 corner features and their 256-bit descriptors, computed
  on a version of the image at most 512 px wide.
- Dimensions, and whether the image is nearly flat (a blank page, a black frame), in which
  case it only takes part in exact matching.

Results are stored in a SQLite cache keyed on path, size and modified time, so unchanged
photos aren't analysed again.

### 2. Exact copies

Files are grouped by size; only files that share a size are hashed (BLAKE2b). Equal
hashes mean identical files.

### 3. Near-duplicates

All pHashes are compared with each other using vectorised XOR + popcount in NumPy, in
chunks to bound memory. A pair matches when:

| Level | pHash distance | dHash distance |
| --- | --- | --- |
| Similar | ≤ 8 | ≤ 14 |
| Loose | ≤ 14 | ≤ 20 |

…and the two images' aspect ratios differ by less than 8%. Perceptual hashes survive
resizing, recompression and moderate colour or brightness edits, but not cropping.

### 4. Crops

Comparing keypoints between every pair of photos would be far too slow, so candidates come
from **locality-sensitive hashing**: each descriptor is bucketed by a random subset of its
bits, in 8 independent tables. Two photos that keep landing in the same buckets (at least
6 votes) share many similar features. Each photo keeps its 6 strongest candidates.

Each candidate pair is then verified:

1. Match descriptors from the smaller image to the larger with a ratio test (0.8).
2. Fit a similarity transform (scale + rotation + shift) with RANSAC (4 px tolerance) and
   require enough inliers (18 for *Similar*, 12 for *Loose*).
3. Reject rotations over 4° and absurd scales.
4. Require the matched points to cover at least 20% of the smaller image, so a shared logo
   or watermark in one corner doesn't count.
5. Project the smaller image's outline into the larger one and measure the overlap. If one
   image lies (≥ 85%) inside the other, it's a crop; pairs that only partly overlap — such
   as two halves of a panorama — are ignored.

### 5. Groups

Within each group the photo with the most pixels (then the biggest file, then the oldest)
is suggested to keep. Every other photo is labelled relative to it: identical, smaller copy,
cropped (with the share of the original it shows), or similar.

### Accuracy

The test suite (`tests/test_duplicates.py`) builds a synthetic library with an original,
an exact copy, a resized copy, a recompressed copy, a brightened copy, an EXIF-rotated copy
and two crops, plus unrelated photos, blank images and a corrupt file, and checks that
exactly the right group comes out at each level. On a 1,000-photo stress test the detector
found 40% crops, crop + resize + recompress combinations and desaturated crops, with no
false matches — including different photos sharing a corner watermark.
