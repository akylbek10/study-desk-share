#!/bin/zsh
# Build "Study Desk.app" and a zip for a GitHub release:   macapp/build.sh   ->  dist/Study-Desk-<version>.zip
# The app holds only code: personal parts (Telegram/Hermes, Supabase, the phone version) and any data stay out.
set -e
cd "${0:A:h}/.."                                   # study-app
VERSION=$(cat VERSION)
OUT=dist/"Study Desk.app"
rm -rf dist && mkdir -p "$OUT/Contents/MacOS" "$OUT/Contents/Resources/study-app"
sed "s/__VERSION__/$VERSION/g" macapp/Info.plist > "$OUT/Contents/Info.plist"
cp macapp/launcher.sh "$OUT/Contents/MacOS/Study Desk" && chmod +x "$OUT/Contents/MacOS/Study Desk"
# the code (cloud.py stays: server.py imports it, and it does nothing without Supabase keys)
for f in server.py paths.py cloud.py sync.py compass.py onedrive.py outlook.py extract_text.py setup_topics.py pyproject.toml uv.lock VERSION; do
  cp "$f" "$OUT/Contents/Resources/study-app/"
done
rsync -a --exclude ".DS_Store" static compass-extension "$OUT/Contents/Resources/study-app/"
# icon
T=$(mktemp -d); mkdir "$T/AppIcon.iconset"
swift macapp/make_icon.swift "$T/icon.png"
for s in 16 32 128 256 512; do
  sips -z $s $s "$T/icon.png" --out "$T/AppIcon.iconset/icon_${s}x${s}.png" >/dev/null
  sips -z $((s*2)) $((s*2)) "$T/icon.png" --out "$T/AppIcon.iconset/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$T/AppIcon.iconset" -o "$OUT/Contents/Resources/AppIcon.icns"
rm -rf "$T"
# nothing personal may be inside
if find "$OUT" \( -name '.env' -o -name 'topics.json' -o -name 'data' -o -name '*.pyc' \) | grep -q .; then
  echo "Refusing: personal or generated files ended up in the app"; exit 1
fi
(cd dist && ditto -c -k --keepParent "Study Desk.app" "Study-Desk-$VERSION.zip")
echo "Built dist/Study-Desk-$VERSION.zip ($(du -h "dist/Study-Desk-$VERSION.zip" | cut -f1))"
