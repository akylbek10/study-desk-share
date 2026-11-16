#!/bin/bash
# Study Desk.app: the program macOS runs when you open the app.
# First open: copies the code to ~/Library/Application Support/Study Desk/app, installs uv, Python and the
# dependencies there, installs the background service (launchd) and opens the setup guide in the browser.
# Later opens: just make sure the service runs and open Study Desk. Your data is never touched.
# For testing, STUDY_HOME / STUDY_PORT / STUDY_LABEL (and AGY_HOME) can point it at a separate copy.
set -u
UV_VERSION="0.13.8"
UV_SHA_ARM="6b71028a6b904f2ae3340ef52e595512de807d06c0a20eaef2f692581b246558"
UV_SHA_X86="a04c2ffeac28b2003a2c13caf538ab425fcd7def3e4a41571f4a8fb370a66af3"

BUNDLE="$(cd "$(dirname "$0")/../.." && pwd)"
SRC="$BUNDLE/Contents/Resources/study-app"
HOME_DIR="${STUDY_HOME:-$HOME/Library/Application Support/Study Desk}"
PORT="${STUDY_PORT:-8890}"
LABEL="${STUDY_LABEL:-com.studydesk.app}"
APPCODE="$HOME_DIR/app"
VENV="$HOME_DIR/venv"
PY="$VENV/bin/python"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
export PATH="$HOME_DIR/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export UV_PYTHON_INSTALL_DIR="$HOME_DIR/python" UV_CACHE_DIR="$HOME_DIR/cache/uv" UV_PROJECT_ENVIRONMENT="$VENV"
export UV_PYTHON_PREFERENCE=only-managed     # its own Python, never whatever else is on the Mac
export PLAYWRIGHT_BROWSERS_PATH="$HOME_DIR/browsers"

mkdir -p "$HOME_DIR/bin" "$HOME_DIR/data" "$HOME/Library/LaunchAgents"
exec >>"$HOME_DIR/data/install.log" 2>&1
echo "=== $(date '+%F %T') Study Desk $(cat "$SRC/VERSION" 2>/dev/null) from $BUNDLE ($(uname -m))"

notify() { osascript -e "display notification \"$1\" with title \"Study Desk\"" >/dev/null 2>&1; }
fail() {
  echo "FAILED: $1"
  osascript -e "display alert \"Study Desk couldn't start\" message \"$1

Details are in: $HOME_DIR/data/install.log\" as critical" >/dev/null 2>&1
  exit 1
}
newer() { [ "$1" != "$2" ] && [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | tail -1)" = "$1" ]; }

[ -f "$SRC/server.py" ] || fail "The app is incomplete. Download it again."

# 1. the code: copy it out of the app (an update from Settings may already have put a newer one there)
RESTART=0
have="$(cat "$APPCODE/VERSION" 2>/dev/null || echo 0)"
mine="$(cat "$SRC/VERSION")"
if [ ! -f "$APPCODE/server.py" ] || newer "$mine" "$have"; then
  echo "copying code $mine (had $have)"
  rm -rf "$APPCODE.new" && cp -R "$SRC" "$APPCODE.new" && rm -rf "$APPCODE" && mv "$APPCODE.new" "$APPCODE" || fail "Couldn't copy the app's files."
  RESTART=1
fi

# 2. uv (it installs Python and the dependencies): Intel or Apple silicon
if [ ! -x "$HOME_DIR/bin/uv" ]; then
  notify "Setting up Study Desk for the first time. This takes a few minutes…"
  if [ "$(uname -m)" = "arm64" ]; then UA=aarch64; SUM=$UV_SHA_ARM; else UA=x86_64; SUM=$UV_SHA_X86; fi
  T="$(mktemp -d)"
  curl -fsSL --retry 3 -o "$T/uv.tgz" "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-$UA-apple-darwin.tar.gz" \
    || fail "Couldn't download uv. Check the internet connection and open Study Desk again."
  [ "$(shasum -a 256 "$T/uv.tgz" | cut -d' ' -f1)" = "$SUM" ] || fail "The uv download was damaged. Open Study Desk again."
  tar xzf "$T/uv.tgz" -C "$T" && cp "$T"/uv-*/uv "$HOME_DIR/bin/uv" && chmod +x "$HOME_DIR/bin/uv" || fail "Couldn't unpack uv."
  rm -rf "$T"
fi

# 3. Python 3.12 and the packages, only when the lock file changed
stamp="$(shasum -a 256 "$APPCODE/uv.lock" | cut -d' ' -f1)"
if [ ! -x "$PY" ] || [ "$(cat "$VENV/.study-desk-lock" 2>/dev/null)" != "$stamp" ]; then
  notify "Installing Python and the parts Study Desk needs…"
  "$HOME_DIR/bin/uv" sync --frozen --project "$APPCODE" --python 3.12 || fail "Couldn't install Python and the packages (see the log)."
  echo "$stamp" > "$VENV/.study-desk-lock"
  RESTART=1
fi

# 4. a browser for IntoStudy and Compass: your Google Chrome, or Playwright's Chromium if there is no Chrome
if [ ! -d "/Applications/Google Chrome.app" ] && [ ! -d "$HOME/Applications/Google Chrome.app" ] && [ ! -d "$PLAYWRIGHT_BROWSERS_PATH" ]; then
  notify "Downloading a browser for IntoStudy and Compass (no Google Chrome found)…"
  "$PY" -m playwright install chromium || fail "Couldn't download the browser. Install Google Chrome and open Study Desk again."
fi

# 5. the background service: starts with your Mac and restarts if it stops
TMP="$(mktemp)"
cat > "$TMP" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>$PY</string><string>-u</string><string>$APPCODE/server.py</string></array>
  <key>WorkingDirectory</key><string>$APPCODE</string>
  <key>EnvironmentVariables</key><dict>
    <key>STUDY_HOME</key><string>$HOME_DIR</string>
    <key>STUDY_PORT</key><string>$PORT</string>
    <key>STUDY_LABEL</key><string>$LABEL</string>
    <key>STUDY_PYTHON</key><string>$PY</string>
    <key>STUDY_APP_BUNDLE</key><string>$BUNDLE</string>
    <key>PLAYWRIGHT_BROWSERS_PATH</key><string>$PLAYWRIGHT_BROWSERS_PATH</string>
    <key>PYTHONDONTWRITEBYTECODE</key><string>1</string>
    <key>PYTHONUNBUFFERED</key><string>1</string>
    <key>PATH</key><string>$PATH</string>
$( [ -n "${AGY_HOME:-}" ] && printf '    <key>AGY_HOME</key><string>%s</string>\n' "$AGY_HOME" )
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>20</integer>
  <key>StandardOutPath</key><string>$HOME_DIR/data/server.log</string>
  <key>StandardErrorPath</key><string>$HOME_DIR/data/server.log</string>
</dict></plist>
EOF
DOMAIN="gui/$(id -u)"
if ! cmp -s "$TMP" "$PLIST"; then
  echo "installing the service $LABEL"
  launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null
  mv "$TMP" "$PLIST"
  launchctl bootstrap "$DOMAIN" "$PLIST" || fail "Couldn't start the Study Desk service."
else
  rm -f "$TMP"
  if ! launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
    launchctl bootstrap "$DOMAIN" "$PLIST" || fail "Couldn't start the Study Desk service."
  elif [ "$RESTART" = 1 ]; then
    launchctl kickstart -k "$DOMAIN/$LABEL"
  fi
fi

# 6. wait for it, then open it (the first time it opens the setup guide by itself)
for i in $(seq 1 90); do
  curl -s -o /dev/null --max-time 2 "http://127.0.0.1:$PORT/api/state" && break
  sleep 1
done
curl -s -o /dev/null --max-time 2 "http://127.0.0.1:$PORT/api/state" || fail "Study Desk didn't start (see $HOME_DIR/data/server.log)."
echo "ready on $PORT"
open "http://127.0.0.1:$PORT/"
