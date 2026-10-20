#!/bin/sh
# Diagnostique et soumet une tâche à un agent IronClaw/OpenClaw distant via SSH.
# Usage:  sh probe.sh [message]
# Variables d'env: IRONCLAW_HOST, IRONCLAW_PORT, IRONCLAW_USER, IRONCLAW_KEY, IRONCLAW_UI_PORT
# Jamais de clé privée affichée en entier; seules empreintes et droits.
set -eu

HOST="${IRONCLAW_HOST:-baremetal06.agents.near.ai}"
PORT="${IRONCLAW_PORT:-29403}"
USER="${IRONCLAW_USER:-agent}"
KEY="${IRONCLAW_KEY:-$HOME/Ironclaw/agent-private-key.pem}"
UI="${IRONCLAW_UI_PORT:-3000}"
MSG="${1:-Réponds juste PING}"

SSH="ssh -o BatchMode=yes -o ConnectTimeout=12 -o StrictHostKeyChecking=accept-new -i \"$KEY\" -p \"$PORT\""

echo "== 1. Clé (empreinte, sans contenu) =="
if [ -f "$KEY" ]; then ssh-keygen -lf "$KEY" 2>&1 | sed 's/^/   /'; else echo "   CLÉ ABSENTE: $KEY"; exit 1; fi

echo "== 2. Shell distant =="
$SSH "$USER@$HOST" 'echo SHELL_OK; hostname; uname -s' 2>&1 | sed 's/^/   /' || { echo "   SHELL ÉCHEC"; exit 1; }

echo "== 3. UI distante =="
$SSH "$USER@$HOST" "curl -s -o /dev/null -w '%{http_code}\n' --max-time 5 http://127.0.0.1:$UI/ 2>/dev/null || echo 000" 2>&1 | sed 's/^/   /'

echo "== 4. Statut runtime =="
$SSH "$USER@$HOST" 'ironclaw status 2>&1 | head -12' 2>&1 | sed 's/^/   /' || true

echo "== 5. Soumission tâche (latence) =="
START=$(date +%s)
$SSH "$USER@$HOST" "ironclaw run -m \"$MSG\" 2>&1 | tail -3" 2>&1 | sed 's/^/   /'
END=$(date +%s)
echo "   durée: $((END-START))s"
