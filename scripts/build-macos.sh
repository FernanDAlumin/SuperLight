#!/bin/bash
set -euo pipefail
SL_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SL_BUILD="$SL_ROOT/build/macos"
SL_APP="$SL_ROOT/dist/SuperLight.app"
SL_PYTHON="$(command -v python3)"
"$SL_PYTHON" -c 'import sys; assert sys.version_info >= (3, 9), "Python 3.9+ is required"'
mkdir -p "$SL_BUILD/module-cache" "$SL_APP/Contents/MacOS" "$SL_APP/Contents/Resources/backend"
xcrun swiftc -O -parse-as-library -swift-version 5 -target "$(uname -m)-apple-macosx13.0" \
  -module-cache-path "$SL_BUILD/module-cache" -framework AppKit -framework WebKit -framework ServiceManagement \
  "$SL_ROOT/native/SuperLight.swift" -o "$SL_APP/Contents/MacOS/SuperLight"
xcrun swiftc -O -module-cache-path "$SL_BUILD/module-cache" -framework AppKit \
  "$SL_ROOT/native/GenerateIcon.swift" -o "$SL_BUILD/generate-icon"
"$SL_BUILD/generate-icon" "$SL_BUILD/AppIcon.iconset"
iconutil -c icns "$SL_BUILD/AppIcon.iconset" -o "$SL_APP/Contents/Resources/AppIcon.icns"
"$SL_PYTHON" - "$SL_ROOT" "$SL_APP" "$SL_PYTHON" <<'PY'
import plistlib
import shutil
import sys
from pathlib import Path
root, app, python = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
shutil.copytree(root / 'superlight', app / 'Contents/Resources/backend/superlight',
                dirs_exist_ok=True, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
shutil.copy2(root / 'LICENSE', app / 'Contents/Resources/LICENSE')
(app / 'Contents/Resources/python-path.txt').write_text(python)
info = dict(CFBundleName='SuperLight', CFBundleDisplayName='SuperLight',
            CFBundleIdentifier='org.superlight.app', CFBundleExecutable='SuperLight',
            CFBundlePackageType='APPL', CFBundleShortVersionString='0.2.0', CFBundleVersion='2',
            CFBundleIconFile='AppIcon', LSMinimumSystemVersion='13.0', LSUIElement=True,
            NSHighResolutionCapable=True, NSPrincipalClass='NSApplication',
            NSAppTransportSecurity={'NSAllowsLocalNetworking': True})
(app / 'Contents/Info.plist').write_bytes(plistlib.dumps(info))
PY
codesign --force --deep --sign - "$SL_APP"
printf 'Built %s\nDouble-click it in Finder to start SuperLight.\n' "$SL_APP"
