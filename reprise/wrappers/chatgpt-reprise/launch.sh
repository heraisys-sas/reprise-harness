#!/bin/bash
# Optional launcher for the official ChatGPT Desktop; not the Codex engine.
set -euo pipefail

APP="/Applications/ChatGPT.app"
EXEC="$APP/Contents/MacOS/ChatGPT"
PORT="${CHATGPT_CDP_PORT:-9333}"
LOG="${HOME}/Library/Logs/chatgpt-reprise-launcher.log"

if [[ ! "$PORT" =~ ^[0-9]+$ ]] || (( 10#$PORT < 1 || 10#$PORT > 65535 )); then
  echo "CHATGPT_CDP_PORT must be an integer from 1 to 65535" >&2
  exit 64
fi
if [[ ! -d "$APP" || ! -x "$EXEC" ]]; then
  echo "Official ChatGPT.app missing or not executable: $APP" >&2
  exit 69
fi

mkdir -p "$(dirname "$LOG")"
exec >>"$LOG" 2>&1
printf 'ChatGPT Reprise launcher start port=%s\n' "$PORT"

cdp_target() {
  /usr/bin/curl -fsS --connect-timeout 1 --max-time 2 \
    "http://127.0.0.1:$PORT/json/list" 2>/dev/null | /usr/bin/grep -Fq 'app://-/index.html'
}
port_listening() {
  /usr/sbin/lsof -nP -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null | /usr/bin/grep -q LISTEN
}
main_pids() {
  # Match only the official main executable, never a generic app name.
  /usr/bin/pgrep -f '^/Applications/ChatGPT[.]app/Contents/MacOS/ChatGPT([[:space:]]|$)' || true
}
signal_main() {
  local signal="$1" pid command
  for pid in $(main_pids); do
    command="$(/bin/ps -p "$pid" -o command= 2>/dev/null || true)"
    if [[ "$command" == "$EXEC" || "$command" == "$EXEC "* ]]; then
      kill "-$signal" "$pid" 2>/dev/null || true
    fi
  done
}

# Do not attach to a foreign debugging server, and do not stop its process.
if port_listening; then
  if ! cdp_target; then
    echo "Foreign or unverified listener on port $PORT; refusing"
    exit 69
  fi
  echo "ChatGPT CDP target already listening; no restart"
  /usr/bin/open -a "$APP"
  exit 0
fi

if [[ -n "$(main_pids)" ]]; then
  echo "ChatGPT running without CDP; requesting graceful quit"
  /usr/bin/osascript -e 'tell application id "com.openai.codex" to quit' || true
  for i in {1..16}; do
    [[ -z "$(main_pids)" ]] && break
    /bin/sleep 0.5
  done
  if [[ -n "$(main_pids)" ]]; then
    echo "Graceful quit timed out; TERM only on verified ChatGPT main process"
    signal_main TERM
    for i in {1..10}; do
      [[ -z "$(main_pids)" ]] && break
      /bin/sleep 0.5
    done
  fi
  if [[ -n "$(main_pids)" ]]; then
    echo "TERM timed out; KILL last resort on verified ChatGPT main process"
    signal_main KILL
    /bin/sleep 1
  fi
  if [[ -n "$(main_pids)" ]]; then
    echo "ChatGPT main process did not exit; refusing concurrent launch"
    exit 70
  fi
fi

# Bind DevTools to loopback. Same-user local processes can still access it.
/usr/bin/open -na "$APP" --args \
  "--remote-debugging-address=127.0.0.1" "--remote-debugging-port=$PORT"
for i in {1..60}; do
  if cdp_target; then
    echo "ChatGPT target available on port $PORT"
    exit 0
  fi
  if port_listening; then
    echo "Port $PORT occupied by unknown listener; refusing"
    exit 69
  fi
  /bin/sleep 0.5
done
echo "ChatGPT debug target did not appear within 30s"
exit 75
