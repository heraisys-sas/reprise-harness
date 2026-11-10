# Lot #130 — Architecture relais : tout passe par claude-desktopd

## Arbitrage final opérateur (10/11/2026 18h)
`claude_desktopd` (claude_desktop_quick.py) = **unique détenteur** de `~/.claude-desktop/quick-entry.sock`.
L'injection ReClaude (desktop_patch.py) devient **client/relais** du daemon : connect au socket, annonce
relais, applique `QTI` natif quand le daemon pousse un payload. L'option `reclaude.sock` (2 sockets) et la
« fusion » détenue par l'injection (net.createServer + unlinkSync, protocol reprise-dispatch-1) sont
**abandonnées**.

Tracé sur l'issue : issuecomment-6773625362 (architecture finale), -6773487674 (socket fichier conservé),
-6773222890 (observations terrain lsof), -6773512615 (reclaude.sock, remplacé). Problème « appli liée » =
Claude.app hardcodé (desktop_mode.py:22) posé sur #125 (issuecomment-6773232485).

## À faire (état au 10/11 18h)
- [ ] Spécifier le protocole relais daemon↔injection (relay/register, push payload sur connexion tenue ouverte
      ou relay/poll) AVANT le code.
- [ ] Réécrire desktop_patch.py : INJECTION = connecteur client + QTI ; supprimer serveur/unlinkSync/protocol.
- [ ] Côté daemon : détection relais, marquage « relais indisponible » → admission_only (déjà prévu),
      reconnexion si le daemon redémarre.
- [ ] Clients inchangés (quick-entry.sock) ; run_quick retrouve resume_validation_supported:true.
- [ ] réaligner docs/contrats (QUICK-ENTRY-CONTRACT.md, CLAUDE-CONNECTOR.md, README, AGENTS, sentinelles)
      sur « un seul socket, détenu par le daemon » ; supprimer le récit de fusion/injection-serveur.
- [ ] Traiter le worktree detached : rattacher à codex/130-desktop-patch-hardening avant commit.
- [ ] Sous-agent correcteur repo-wide (fragments périmés) — lancer après le réalignement doc/code.
- [ ] Daemon orphelin 22853 (release 1bd85615, socket unlinké) : arrêt ou resync — arbitrage opérateur.

## Notes
- Le garde QTi/ProseMirror reste côté injecté ; MV3 (#107) = piste distincte si CDP/QTI échoue encore.
- Suite complète 1665 OK (skipped=5) sur le worktree ; Lot A non committé.
