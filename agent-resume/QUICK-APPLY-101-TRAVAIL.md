# Quick Apply #101 — Envoi CDP vers claude.ai : bilan validé (04/11/2026)

## Résultat
Le canal Quick Apply → claude.ai (Chrome dédié, CDP pipe, sans port TCP) **envoie réellement les prompts** dans le chat visé, avec une **preuve d'envoi stricte et vérifiable** dans chaque reçu.

Confirmation opérateur (04/11) : les messages de test a7, a20 et a21 sont **visibles** dans le chat `ba05f0cb-d00b-4658-804c-59d009a942c6`.

## Historique des dispatchs
- a1–a19 : échecs successifs documentés (doublon `_cdp_resume` sans fallback `Target.createTarget`, `wait_composer` manquant, regex JS non échappées, `inserted` non défini).
- a7, a20 : **ont abouti** dans le chat mais le reçu de l'époque ne le prouvait pas (preuve « composeur vidé » seul, baseline du compteur mesurée après le clic). Visible a posteriori.
- a21 (premier dispatch après le correctif de preuve stricte) : reçu probant —
  `confirm: {user_messages: 4, user_messages_before: 2, composer_left: 0, url_match: true, url: https://claude.ai/chat/ba05f0cb-…}`.

## Défauts corrigés (commit 1970688, fusion a803df3 sur dev)
1. Aucune vérification de cible entre attach et clic → **garde anti-redirection** : `location.pathname === '/chat/<uuid>'` exigé avant insertion, sinon `status: 'cible'` + URL réelle dans le reçu.
2. `_confirm` faible (baseline après clic, « composeur vidé » seul — confirmé aussi par une redirection) → **preuve stricte** : composeur vidé ET compteur `user-message` > baseline mesurée AVANT le clic ET URL finale = chat visé (fenêtre 4 s), sinon `incertain`.
3. Clic JS nu `.click()` → **clic événementiel** `pointerdown → mousedown → pointerup → mouseup → click`, bouton `disabled` re-vérifié au moment du clic.

## SHAs
- Branche de sujet `codex/101-envoi-cdp` (base `827f93b`) : `9166a7c` (démarrage à froid + envoi), `1970688` (preuve stricte).
- Fusions sur `origin/dev` : `b53c032`, `a803df3` (tests OK, skipped=6).
- Release installé `5bce9265` resynchronisé ; daemon `claude-desktopd` relancé dessus.

## Passation / reste à faire
- Confirmation visuelle opérateur : **faite** (04/11).
- Fix #2 du diagnostic UnboundLocal (`reprise`, `except Exception` ligne ~514 + test de régression) — plan Codex confirmé (thread `01a1a33f-cd3e-75f3-b116-55610bf25093`), implémentation non committée.
- #105 : nettoyage du SingletonLock orphelin au lancement du Chrome dédié.
- `daemon_stop` du release installé cassé (`TypeError Client.__init__ missing 'values'`) — arrêter via SIGTERM sur le pidfile `~/Library/Application Support/Reprise/quick-state/quick-apply.pid`.
- Piège récurrent : ne jamais garder un Chrome opérateur ouvert sur `~/.local/share/claude-cdp/chrome` pendant un dispatch (conflit SingletonLock, échec a2).
