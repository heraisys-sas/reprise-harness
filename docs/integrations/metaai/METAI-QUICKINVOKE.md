# Intégration Meta AI — invocation rapide (copie patchée, pont UI automatisé)

Complément de [METAI-INTEGRATION.md](METAI-INTEGRATION.md) (transport WebDAV) : cette page couvre le **pont UI** — le pilotage de la copie patchée « ReMetaAI.app » par socket in-process, qui supprime le dernier geste manuel (le déclenchement du tour). Validé live le **23 novembre 2026** : `tache-20261123-151306-c1d6` — dispatch → prompt UI injecté et soumis automatiquement (3 s) → résultat `161` (23 × 7) → `answered` en ~25 s, **zéro Accessibilité, zéro CDP**.

## Ce que la copie apporte

Meta AI native (`com.meta.endo.vanilla`) n'offre ni CDP (pas d'Electron) ni Accessibilité (décision d'Olivier). La seule surface d'automatisation est **in-process** : une copie re-signée porte nos dylibs. L'original `/Applications/Meta AI.app` n'est jamais modifié.

## Architecture

```
metaaid (LaunchAgent app.reprise.metaaid — mount + serveur WebDAV + watcher + dispatcher UI + lanceur)
  │ dispatch_task → inbox/<id>.json (WebDAV monté)
  │ metaai_ui_dispatch.py : prompt → socket insert + send, marker .ui-state/<id>
  ▼
ReMetaAI.app (copie re-signée, session partagée par trousseau)
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
nohup "$HOME/Applications/ReMetaAI.app/Contents/MacOS/Meta AI" &
/usr/bin/python3 reprise/metaai_reprise_client.py '{"cmd":"ping"}'
```

Commandes socket (`metaai_reprise_client.py`) : `ping`, `dump` (arbre des fenêtres/vues), `insert` (texte), `send`, `len`.

## Pont automatisé (metaaid)

Connexion `metaai.json` — champs UI : `ui_bridge` (true), `ui_socket`, `ui_app`. Au boot, `metaaid` lance la copie si la socket est muette (`ensure_app`) ; à chaque `dispatch_task`, le dispatcher injecte + soumet le prompt (template `PROMPT_TEMPLATE` dans `metaai_ui_dispatch.py`), pose le marker `.ui-state/<id>` (idempotent au redémarrage) et le watcher consomme l'outbox comme pour le transport WebDAV seul.

## Limites & exploitation

- **Identité de la copie (ReMetaAI)** : bundle renommé `ReMetaAI.app`, identité distincte `com.reprise.remetaai` (posée par `patch_info_plist`). Raison : l'originale de Meta omet `CFBundleExecutable` — LaunchServices retombe sur le nom du bundle et une copie renommée sans la clé ne s'ouvre plus (« executable is missing ») ; et partager le bundle id de l'originale confondait les deux apps côté macOS (TCC et items de connexion résolus vers l'originale, single-instance par bundle id : tout lancement de l'une tuait l'autre). L'exécutable interne reste `Contents/MacOS/Meta AI` (les chemins des dylibs y sont réécrits, ne pas le renommer).
- **Mise à jour de Meta AI** : la copie est gelée — rejouer `metaai_reprise_build.py` (repart de l'original à jour et re-pose l'identité ReMetaAI), puis relancer la copie. Ne pas recréer la copie à la main (copier-coller Finder) : sans les clés du Info.plist elle retombe sur les défauts de l'originale.
- **Partage connecteur** : lié à l'identité de bundle — chaque nouvelle identité (renommage) repart avec un TCC vierge ; accepter une fois la demande d'accès au dossier partagé. La session chat elle-même est partagée via le trousseau (tokens de l'originale), indépendante de l'identité.
- **Lanceur metaaid (`ensure_app`)** : relance la copie si la socket est muette, au boot et au dispatch — pas en veille périodique ; une copie morte en silence reste morte jusqu'au prochain événement (amélioration à prévoir : ping périodique).
- **Le trousseau émulé est isolé** : items ACL par chemin, créés par la copie ; les secrets ne sont jamais loggés.
- **Originale et copie peuvent coexister** (identités distinctes) mais utilisent la même session serveur — éviter de chatter des deux côtés en même temps.
- Le prompt pont ne contient pas d'accolades : le bug JSON du connecteur (cf. METAI-INTEGRATION.md) s'applique au contenu de l'outbox, pas au composer — mais le template reste stable par prudence.
- **Session & login initial** : la session chat de la copie vient des tokens de l'originale, lus dans le trousseau via le shim (`SecKeychain*` legacy sur le service `com.meta.prototypes.endo.tokens`) — indépendant de l'identité de bundle : le renommage ne l'affecte pas. Preuve live du 24/11/2026 sous `com.reprise.remetaai` : `SecItemCopyMatching[emu] native_credential_record_v1 → 0` au boot, historique du chat présent sans connexion. Si la session se perd un jour : reconnecter d'abord l'originale (elle écrit les tokens du service émulé), puis relancer la copie — la copie peut aussi se connecter elle-même (ses items restent ACL par chemin, isolés). Le mur initial (entitlements SecItem sur macOS 26) est contourné par le shim, cf. § Keychain ci-dessus.
- **Lecture des réponses du chat** : le dump ne couvre que l'AppKit (composer, sidebar) — le rendu SwiftUI de la conversation est illisible via la dylib ; les réponses structurées passent par la outbox WebDAV.

## Tests

`python3 -m unittest discover -s reprise/tests -q -p "test_metaai_*.py"` — 18 tests incl. dispatcher UI (prompt, marker/idempotence, AppleDouble, ping, `ensure_app`, `watch_loop` avec/sans `ui=`) sous fake socket ; plus 21 tests CLI (`test_metaai_reprise_cli.py`) sous 3.9 et 3.13.

## Redéploiement depuis la GUI (0.1.23)

Après une mise à jour de `/Applications/Meta AI.app`, la copie devient périmée. Deux voies équivalentes :

- **GUI Reprise** → Vue Intégrations → carte « Copie ReMetaAI » : « Relire l'état » → « Préparer le redéploiement… » → acquittement (Meta AI quitée) → « Appliquer le redéploiement » → « Vérifier ».
- **CLI** (JSON stdout) : `python3 reprise/reprise metaai-integration-inspect|plan|apply|verify` — plan/apply/verify passent le plan JSON par stdin, mêmes conventions que `codex-integration-*`.

Garde-fous : refus si Meta AI (originale) est en cours d'exécution ; plan périmé refusé si l'empreinte de l'arbre de l'originale a changé ; outils de build vérifiés (`clang`, `codesign`, `rsync`, `install_name_tool`, `security`) ; jamais de lancement de l'app ni de modification de l'originale. Le `apply` resynchronise la copie (`rsync -a --delete`), ré-extrait le Sparkle si son empreinte change, réapplique swap + shim + re-signature, puis consigne `~/.reprise/metaai-reprise-state.json` (empreinte, version, `last_plan_id`).
