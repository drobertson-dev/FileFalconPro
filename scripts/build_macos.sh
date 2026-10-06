#!/usr/bin/env bash
# Build "File Falcon Pro.app" into dist/.
#
#   ./scripts/build_macos.sh            build the app
#   ./scripts/build_macos.sh --install  build, then copy it into /Applications
#   ./scripts/build_macos.sh --dmg      build, then wrap it in a disk image for sharing
set -euo pipefail

cd "$(dirname "$0")/.."
APP_NAME="File Falcon Pro"
VERSION="$(uv run python -c 'from config import APP_VERSION; print(APP_VERSION)')"
APP="dist/${APP_NAME}.app"

echo "→ Building ${APP_NAME} ${VERSION}"
uv sync --group build --quiet
uv run --group build pyinstaller packaging/FileFalconPro.spec \
	--noconfirm --clean --log-level WARN --distpath dist --workpath build
echo "✓ Built ${APP} ($(du -sh "${APP}" | cut -f1))"

case "${1:-}" in
	--install)
		rm -rf "/Applications/${APP_NAME}.app"
		ditto "${APP}" "/Applications/${APP_NAME}.app"
		echo "✓ Installed to /Applications/${APP_NAME}.app"
		;;
	--dmg)
		DMG="dist/FileFalconPro-${VERSION}-macOS-$(uname -m).dmg"
		rm -f "${DMG}"
		STAGING="$(mktemp -d)"
		ditto "${APP}" "${STAGING}/${APP_NAME}.app"
		ln -s /Applications "${STAGING}/Applications"
		hdiutil create -volname "${APP_NAME}" -srcfolder "${STAGING}" -ov -format UDZO "${DMG}" >/dev/null
		rm -rf "${STAGING}"
		echo "✓ Disk image: ${DMG}"
		;;
esac
