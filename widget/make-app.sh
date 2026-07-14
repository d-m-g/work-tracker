#!/bin/sh
#
# Build the widget and wrap it in a WorkWidget.app you can double-click, keep in
# the Dock, or add to Login Items.
#
# The bundle is only a convenience. `swift run` produces exactly the same widget:
# the binary sets its own activation policy at launch, so it needs no Info.plist
# to stay out of the Dock.
#
# Nothing here is committed -- the app is built, not stored, like web/ui's dist/.

set -eu

cd "$(dirname "$0")"

APP="WorkWidget.app"
ROOT="$(cd .. && pwd)"

echo "building..."
swift build -c release

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"

cp .build/release/WorkWidget "$APP/Contents/MacOS/WorkWidget"

# LSUIElement is redundant with the activation policy the binary sets for itself,
# but it means the app never flashes a Dock icon on the way up.
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>              <string>WorkWidget</string>
    <key>CFBundleDisplayName</key>       <string>Work Widget</string>
    <key>CFBundleIdentifier</key>        <string>com.work-tracker.widget</string>
    <key>CFBundleExecutable</key>        <string>WorkWidget</string>
    <key>CFBundlePackageType</key>       <string>APPL</string>
    <key>CFBundleShortVersionString</key><string>1.0.0</string>
    <key>LSMinimumSystemVersion</key>    <string>13.0</string>
    <key>LSUIElement</key>               <true/>
</dict>
</plist>
PLIST

# Inside a bundle the executable sits in Contents/MacOS, so walking up from it no
# longer lands on the repository. Record where the tracker lives, so the app can
# be moved to /Applications and still find it.
defaults write com.work-tracker.widget WorkTrackerHome "$ROOT"

echo "built $(pwd)/$APP"
echo "tracker: $ROOT"
echo
echo "open $APP   -- or add it to System Settings > General > Login Items"
