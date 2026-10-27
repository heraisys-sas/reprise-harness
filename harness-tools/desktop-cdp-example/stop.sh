#!/bin/bash
# Ferme le Chrome de diagnostic (et seulement lui).
set -euo pipefail
PROFILE="$HOME/.local/share/claude-cdp/chrome"

if pgrep -f "user-data-dir=$PROFILE" >/dev/null; then
  pkill -f "user-data-dir=$PROFILE" || true
  echo "✓ Chrome de diagnostic fermé"
else
  echo "aucune instance de diagnostic en cours"
fi
