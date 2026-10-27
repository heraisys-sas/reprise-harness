#!/bin/bash
# Lance un Chrome dédié, avec le port de diagnostic, pour piloter claude.ai.
#
# Pourquoi un profil séparé : on ne touche pas à votre Chrome habituel, et la
# session claude.ai reste persistante entre deux exécutions (login une fois).
set -euo pipefail

PORT="${CDP_PORT:-9333}"
PROFILE="$HOME/.local/share/claude-cdp/chrome"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

if curl -sf "http://127.0.0.1:$PORT/json/version" >/dev/null 2>&1; then
  echo "✓ CDP déjà actif sur 127.0.0.1:$PORT"
  exit 0
fi

[ -x "$CHROME" ] || { echo "✗ Google Chrome introuvable"; exit 1; }
mkdir -p "$PROFILE"

echo "→ lancement de Chrome (profil dédié, port $PORT)"
nohup "$CHROME" \
  --remote-debugging-port="$PORT" \
  --user-data-dir="$PROFILE" \
  --no-first-run --no-default-browser-check \
  about:blank >/tmp/claude-cdp-chrome.log 2>&1 &

for _ in $(seq 1 30); do
  if curl -sf "http://127.0.0.1:$PORT/json/version" >/dev/null 2>&1; then
    echo "✓ CDP actif sur 127.0.0.1:$PORT"
    echo "  profil : $PROFILE"
    exit 0
  fi
  sleep 0.5
done

echo "✗ le port ne répond pas — voir /tmp/claude-cdp-chrome.log"
exit 1
