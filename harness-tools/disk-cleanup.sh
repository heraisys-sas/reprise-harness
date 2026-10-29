#!/bin/bash
# Nettoyage des caches reconstructibles — à exécuter par l'opérateur.
#
# Contexte (mesuré le 29/10/2026) : disque à 100 %, 386 Mi libres sur 228 Go.
# Cibles : caches d'updaters qui se re-téléchargent en boucle + anciennes
# versions d'extension VSCode. Aucun fichier utilisateur, aucune donnée d'app.
#
# Usage :
#   bash harness-tools/disk-cleanup.sh          # simulation (aucune suppression)
#   bash harness-tools/disk-cleanup.sh --apply  # supprime réellement
#
# Nécessite un Terminal natif : la sandbox des agents refuse ces écritures (EPERM).

set -uo pipefail

APPLY=0
[[ "${1:-}" == "--apply" ]] && APPLY=1

TARGETS=(
  "$HOME/Library/Caches/com.microsoft.VSCode.ShipIt"
  "$HOME/Library/Caches/com.anthropic.claudefordesktop.ShipIt"
  "$HOME/Library/Caches/autoclaw-updater"
  "$HOME/Library/Caches/pip"
  "$HOME/Library/Caches/@deepseek-aidsh-desktop-updater"
)
# Anciennes versions de l'extension claude-code : on garde la plus récente.
EXT_DIR="$HOME/.vscode/extensions"
# bash 3.2 (macOS) : pas de mapfile, on collecte à la main.
if [[ -d "$EXT_DIR" ]]; then
  EXT_ALL=$(ls -d "$EXT_DIR"/anthropic.claude-code-* 2>/dev/null | sort -V)
  EXT_COUNT=$(printf '%s\n' "$EXT_ALL" | grep -c 'anthropic.claude-code-' || true)
  if [[ "$EXT_COUNT" -gt 1 ]]; then
    # toutes sauf la dernière (la plus récente)
    OLD_EXT=$(printf '%s\n' "$EXT_ALL" | head -n $((EXT_COUNT - 1)))
    while IFS= read -r candidate; do
      [[ -n "$candidate" ]] && TARGETS+=("$candidate")
    done <<< "$OLD_EXT"
  fi
fi

free_mib() { df -m /System/Volumes/Data | awk 'NR==2 {print $4}'; }

BEFORE=$(free_mib)
echo "Espace libre avant : ${BEFORE} MiB"
echo

MODE=$([[ $APPLY -eq 1 ]] && echo "SUPPRESSION" || echo "SIMULATION")
echo "Mode : $MODE"
echo

REMOVED=0
for path in "${TARGETS[@]}"; do
  if [[ ! -e "$path" ]]; then
    printf '  absent    %s\n' "$path"
    continue
  fi
  size=$(du -sh "$path" 2>/dev/null | cut -f1)
  if [[ $APPLY -eq 1 ]]; then
    if rm -rf "$path" 2>/dev/null && [[ ! -e "$path" ]]; then
      printf '  supprimé  %-9s %s\n' "$size" "$path"
      REMOVED=$((REMOVED + 1))
    else
      printf '  ÉCHEC     %-9s %s\n' "$size" "$path"
    fi
  else
    printf '  à suppr.  %-9s %s\n' "$size" "$path"
  fi
done

echo
if [[ $APPLY -eq 1 ]]; then
  AFTER=$(free_mib)
  echo "Espace libre après : ${AFTER} MiB"
  echo "Gain mesuré        : $((AFTER - BEFORE)) MiB (${REMOVED} cible(s) supprimée(s))"
else
  echo "Simulation terminée — relancer avec --apply pour supprimer."
fi
echo
echo "Rappel : Réglages ▸ Général ▸ Stockage reste le seul canal pour ventiler"
echo "les ~90 Go de zones protégées (Mail, Photos, Messages, CloudStorage)."
