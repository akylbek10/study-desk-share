#!/bin/zsh
# Double-click to start Study Desk, then it opens in your browser.
cd "${0:A:h}"
if ! curl -s -o /dev/null http://127.0.0.1:8890/api/state; then
  python3 server.py &
  sleep 1
fi
open http://127.0.0.1:8890
wait
