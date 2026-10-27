# Exemples — pilotage de claude.ai par CDP

Ces fichiers accompagnent #83 et #84. Ce sont des **exemples reproductibles**,
pas des composants livrés : rien ici n'est installé, exporté ni supporté. Ils
existent pour qu'un relecteur puisse **refaire les mesures** au lieu de les
croire, et pour que les impasses soient documentées plutôt que retentées.

## Contenu

| Fichier | Rôle |
|---|---|
| `cdp.mjs` | pilote Chrome DevTools Protocol (Node, zéro dépendance) : `status`, `inspect`, `navigate`, `apply`, `send-staged`, `retry`, `quota`, `selftest` |
| `launch.sh` / `stop.sh` | cycle de vie d'un Chrome dédié (profil persistant, port 9333) |
| `apply.py` | façade Python du pilote : quota, envoi, attente de réouverture |
| `wkwebview/` | prototype WKWebView **écarté** (voir plus bas), gardé comme preuve |

Le transport livrable, lui, est dans `agent-resume/claude_desktop_apply.py` (#84).

## Mise en route

```sh
./launch.sh                      # Chrome dédié + port 9333
# se connecter une fois à claude.ai dans cette fenêtre (profil persistant)
node cdp.mjs status
node cdp.mjs inspect             # composeur, boutons visibles
node cdp.mjs selftest            # prouve frappe + clic sur une page de test locale
```

Node ≥ 22 est requis pour `WebSocket` natif. Aucun `npm install`.

## Avertissements

- **Le port n'écoute que sur `127.0.0.1`, mais tout processus local peut piloter
  ce profil** tant qu'il est ouvert. `./stop.sh` pour fermer.
- **Ne pas tester d'envoi quand le quota est bloqué** : cela ne prouve rien et
  consomme la fenêtre de reprise.
- Ne pas chercher à franchir les contrôles anti-bot (headless, hCaptcha) : c'est
  documenté comme impasse, pas comme obstacle à contourner.

## `wkwebview/` — impasse documentée

`ClaudeApply.swift` charge claude.ai dans un WKWebView pour éviter Chrome.
Mesuré : WebKit rend bien la page, mais la **connexion** est bloquée — hCaptcha
boucle (`challenge → checkbox-invisible → challenge`), et ITP refuse les cookies
tiers dont le captcha a besoin. Aucune API Apple ne permet de désactiver ITP.

Conservé pour deux raisons : la mesure est reproductible, et la voie ne doit pas
être retentée sans élément nouveau. Ne pas compiler ici : le build Swift de
Reprise est interne (`BUILD-AND-CI.md`).
