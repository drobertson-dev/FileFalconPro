# PyInstaller recipe for the macOS app bundle.
# Build with:  ./scripts/build_macos.sh   (or: make app)

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent  # noqa: F821 - provided by PyInstaller
sys.path.insert(0, str(ROOT))

from config import APP_NAME, APP_VERSION, BUNDLE_ID  # noqa: E402

FOLDER_ACCESS = "File Falcon Pro sorts and de-duplicates the files in folders you choose."

a = Analysis(  # noqa: F821
	[str(ROOT / "main.py")],
	pathex=[str(ROOT)],
	# send2trash picks its platform backend at runtime, so PyInstaller can't see it.
	hiddenimports=collect_submodules("send2trash", filter=lambda name: ".win" not in name),
	excludes=["tkinter", "pytest", "PyInstaller"],
)
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
	pyz,
	a.scripts,
	[],
	exclude_binaries=True,
	name=APP_NAME,
	console=False,
	argv_emulation=False,
	upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name=APP_NAME, upx=False)  # noqa: F821
app = BUNDLE(  # noqa: F821
	coll,
	name=f"{APP_NAME}.app",
	icon=str(ROOT / "assets" / "icon.icns"),
	bundle_identifier=BUNDLE_ID,
	version=APP_VERSION,
	info_plist={
		"CFBundleDisplayName": APP_NAME,
		"CFBundleName": APP_NAME,
		"CFBundleShortVersionString": APP_VERSION,
		"CFBundleVersion": APP_VERSION,
		"LSApplicationCategoryType": "public.app-category.productivity",
		"LSMinimumSystemVersion": "12.0",
		"NSHighResolutionCapable": True,
		"NSRequiresAquaSystemAppearance": False,  # follow light/dark mode
		"NSHumanReadableCopyright": "MIT License",
		"NSDesktopFolderUsageDescription": FOLDER_ACCESS,
		"NSDocumentsFolderUsageDescription": FOLDER_ACCESS,
		"NSDownloadsFolderUsageDescription": FOLDER_ACCESS,
		"NSRemovableVolumesUsageDescription": FOLDER_ACCESS,
		"NSNetworkVolumesUsageDescription": FOLDER_ACCESS,
	},
)
