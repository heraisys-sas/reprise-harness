# Intégration Meta AI — invocation rapide (copie patchée, pont UI automatisé)

Complément de [METAI-INTEGRATION.md](METAI-INTEGRATION.md) (transport WebDAV) : cette page couvre le **pont UI** — le pilotage de la copie patchée « Meta AI Reprise.app » par socket in-process, qui supprime le dernier geste manuel (le déclenchement du tour). Validé live le **23 novembre 2026** : `tache-20261123-151306-c1d6` — dispatch → prompt UI injecté et soumis automatiquement (3 s) → résultat `161` (23 × 7) → `answered` en ~25 s, **zéro Accessibilité, zéro CDP**.

## Ce que la copie apporte

Meta AI native (`com.meta.endo.vanilla`) n'offre ni CDP (pas d'Electron) ni Accessibilité (décision d'Olivier). La seule surface d'automatisation est **in-process** : une copie re-signée porte nos dylibs. L'original `/Applications/Meta AI.app` n'est jamais modifié.

## Architecture

```
metaaid (LaunchAgent app.reprise.metaaid — mount + serveur WebDAV + watcher + dispatcher UI + lanceur)
  │ dispatch_task → inbox/<id>.json (WebDAV monté)
  │ metaai_ui_dispatch.py : prompt → socket insert + send, marker .ui-state/<id>
  ▼
Meta AI Reprise.app (copie re-signée, session partagée par trousseau)
  ├── dylib « fake Sparkle » (metaai_reprise.m) : socket ~/.reprise/metaai-reprise-trigger.sock (0600)
  │     ping · dump · insert · send · len — composer = MetaAIDesignSystem.MetaAIComposerNSTextView
  ├── shim Security.framework (metaai_sec_shim.m) : ré-export de la vraie Security + émulation
  │     trousseau legacy pour com.meta.prototypes.endo.tokens (session partagée avec l'originale)
  └── Meta AI (UI Swift) → connecteur fichiers → outbox/<id>.txt → watcher → TaskStore
```

## Technique d'injection (et impasses documentées)

- **DYLD_INSERT_LIBRARIES : impasse.** AMFI le strippe pour les apps enregistrées (xattr `com.apple.provenance` non retirable, réappliqué par le système ; binaire FAT x86_64+arm64 sans padding exploitable dans la zone de load commands — un binaire témoin prouve que la dylib elle-même fonctionne hors app).
- **Voie retenue : remplacement du binaire Sparkle** de la copie (`Versions/B/Sparkle`) par notre dylib qui **ré-exporte** le vrai Sparkle (extrait vers `~/.reprise/Sparkle-real`, LC_ID réécrit absolu + re-signé). L'app charge notre code comme sa propre dépendance, et Sparkle continue de fonctionner.
- **Keychain (mur macOS 26)** : `SecItem` exige un entitlement (`-34018` même sans groupe) ; `com.apple.application-identifier` sans provisioning profile est ignoré par securityd ; `keychain-access-groups` avec un préfixe d'équipe étranger = **SIGKILL AMFI à l'exec** (l'entitlement s'embarque uniquement en signant le fichier binaire directement, pas via le bundle). Contournement : le shim ré-exporte la vraie Security, **supprime `kSecAttrAccessGroup`** et émule les generic passwords du service `com.meta.prototypes.endo.tokens` via l'API legacy `SecKeychain*` (sans entitlement, ACL par chemin) — la copie lit ainsi les tokens de l'originale : **session partagée, aucune connexion requise**.
- **Signature** : identité Apple Development de l'opérateur (runtime durci désactivé par défaut au re-sign). Toujours `chmod 755` le binaire après patch (la réécriture par `write_bytes` perd le bit exec).
- **Détection de soumission** : après `send` (`insertNewline:` au délégué du NSTextView), le composer se vide — commande `len` de confirmation.

## Build & déploiement

```bash
/usr/bin/python3 reprise/metaai_reprise_build.py          # idempotent, ne lance jamais l'app
nohup "$HOME/Applications/Meta AI Reprise.app/Contents/MacOS/Meta AI" &
/usr/bin/python3 reprise/metaai_reprise_client.py '{"cmd":"ping"}'
```

Commandes socket (`metaai_reprise_client.py`) : `ping`, `dump` (arbre des fenêtres/vues), `insert` (texte), `send`, `len`.

## Pont automatisé (metaaid)

Connexion `metaai.json` — champs UI : `ui_bridge` (true), `ui_socket`, `ui_app`. Au boot, `metaaid` lance la copie si la socket est muette (`ensure_app`) ; à chaque `dispatch_task`, le dispatcher injecte + soumet le prompt (template `PROMPT_TEMPLATE` dans `metaai_ui_dispatch.py`), pose le marker `.ui-state/<id>` (idempotent au redémarrage) et le watcher consomme l'outbox comme pour le transport WebDAV seul.

## Limites & exploitation

- **Mise à jour de Meta AI** : la copie est gelée — rejouer `metaai_reprise_build.py` (repart de l'original à jour), puis relancer la copie.
- **Partage connecteur** : hérité du compte (partagé une fois sur le volume WebDAV) ; la copie déclenche sa propre demande TCC au premier accès fichiers — l'accepter une fois.
- **Le trousseau émulé est isolé** : items ACL par chemin, créés par la copie ; les secrets ne sont jamais loggés.
- **Single-instance** : fermer l'originale avant d'utiliser la copie (ou l'inverse) — même bundle id, sessions séparées par trousseau mais même app côté serveur.
- Le prompt pont ne contient pas d'accolades : le bug JSON du connecteur (cf. METAI-INTEGRATION.md) s'applique au contenu de l'outbox, pas au composer — mais le template reste stable par prudence.

## Tests

`python3 -m unittest discover -s reprise/tests -q -p "test_metaai_*.py"` — 18 tests incl. dispatcher UI (prompt, marker/idempotence, AppleDouble, ping, `ensure_app`, `watch_loop` avec/sans `ui=`) sous fake socket.
