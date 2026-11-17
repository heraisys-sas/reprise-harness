# APP-CLONES — inspect / plan / apply / verify des clones d'app

Procédure gérée pour `ReChatGPT.app` (ChatGPT Desktop) et `ReClaude.app`
(Claude Desktop patché #124). Contrat :
[APP-CLONES-CONTRACT](../contracts/APP-CLONES-CONTRACT.md). Entrée CLI :

```sh
python3 reprise/app_clones.py inspect
python3 reprise/app_clones.py plan
python3 reprise/app_clones.py apply [rechatgpt reclaude]
python3 reprise/app_clones.py verify
```

## Pré-conditions (apply)

- L'originale `/Applications/<App>.app` existe et est lisible ; elle n'est
  **jamais** mutée (invariant du contrat).
- L'app clone cible ne tourne pas — le PID est authentifié par chemin
  d'exécutable (`pgrep -f` + vérification `ps`) ; refus `target_running:<pid>`
  sinon (leçon #198/#94 : jamais un arrêt sur un nom de processus seul).
- Disque : ≥ 2 Gio libres (le clonefile APFS ne consomme presque rien au
  départ ; la re-signature duplique quelques Mo).

## apply — ce que fait chaque étape

| Étape | Effet |
| --- | --- |
| `clone` | `cp -c -R` (clonefile) de l'originale vers `~/Applications/<Clone>.app` ; repli copie profonde si le volume refuse |
| `identity` | `CFBundleName`/`CFBundleDisplayName`/`CFBundleIdentifier` → identité reprise (`com.reprise.rechatgpt` / `com.anthropic.claudefordesktop.reprise`) |
| `sign` | `codesign --force --deep --sign -` (ad-hoc, valide localement) |
| `update-off` | `defaults write <bundle-id> SUEnableAutomaticChecks/SUAutomaticallyUpdate false` — sans cela Sparkle/Squirrel remplacerait le clone par le bundle officiel |
| `refresh` | supprime le clone courant avant un re-clone (refus si l'app tourne) |
| `freeze-record` | version gelée + upstream + date écrits dans `app-clones.json` |

ReClaude spécifique : le **patch Reprise** (asar/slot/socket, pipeline #124
via `desktop_patch.py`) reste la référence ; `app_clones apply` finalise
identité/gel/signature sur un clone déjà patché ou à créer, et ne contourne
jamais le pipeline.

## verify

Post-conditions par clone : identité conforme, signature valide, auto-update
désactivé, version gelée cohérente avec le manifeste. Toute dérive remonte
dans `issues` (`identity`, `sign`, `update-off`, `refresh`,
`manifest_version_mismatch`).

## Après un update de l'originale

1. `inspect` → `upstream_newer: true` ;
2. `apply` (refresh) : quit du clone, re-clone, identité, re-sign, SU off ;
3. ReClaude : re-run du pipeline #124 **avant** la finalisation ;
4. recalage éventuel des sélecteurs de capture (leçon #226) : vérifier sur
   une délégation t1 avant toute délégation réelle.

## Interface graphique

La vue **Intégrations → Clones d'app** lit le manifeste
`~/Library/Application Support/Reprise/app-clones.json` (lecture seule) :
identité, versions, dérives. Les actions restent CLI gérées dans cette
vague ; le branchement natif `managed_update` (composant `app-clones`) est
planifié avec la release v0.1.20.
