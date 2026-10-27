# Exemples — pilotage de claude.ai par CDP

Ces fichiers accompagnent #83 et #84. Ce sont des **exemples expérimentaux non
livrés** : rien ici n'est installé, exporté ou intégré au daemon Reprise. Ils
documentent un banc d'essai; ils ne garantissent pas que les mesures puissent
être reproduites dans un autre compte ou état de session.

## Contenu

| Fichier | Rôle |
|---|---|
| `cdp.mjs` | pilote Chrome DevTools Protocol (Node, zéro dépendance) : `status`, `inspect`, `navigate`, `apply`, `send-staged`, `retry`, `quota`, `selftest` |
| `launch.sh` / `stop.sh` | cycle de vie d'un Chrome dédié (profil persistant, port 9333) |
| `apply.py` | façade Python du pilote : quota, envoi, attente de réouverture |
| `wkwebview/` | prototype WKWebView **écarté** (voir plus bas), gardé comme preuve |

Le transport Python séparé est dans `agent-resume/claude_desktop_apply.py` (#84),
mais il reste expérimental et n'est pas raccordé au daemon.

Le rapport d'envoi du pilote Node vient de l'auteur dans le commentaire
6437860868 de l'issue #83; il n'a pas été reproduit pendant ce portage. `apply`
vise un identifiant de chat fourni, mais le pilote ne prouve pas après navigation
que la page a ouvert cette cible. Un clic sur « Envoyer » ne prouve pas
l'admission du tour, et aucun retour de réponse ni `submit_result` MCP n'est
fourni. Continuer le même chat sur claude.ai ne prouve pas que les outils MCP
stdio locaux de Claude Desktop sont accessibles au tour web.

## Mise en route

```sh
./launch.sh                      # Chrome dédié + port 9333
# se connecter une fois à claude.ai dans cette fenêtre (profil persistant)
node cdp.mjs status
node cdp.mjs inspect             # composeur, boutons visibles
node cdp.mjs selftest            # vérifie frappe + clic sur une page de test locale
```

Node ≥ 22 est requis pour `WebSocket` natif. Aucun `npm install`. Le selftest
figure dans la recette, mais n'a pas été exécuté pendant le portage.

## Avertissements

- **Le port n'écoute que sur `127.0.0.1`, mais tout processus local peut piloter
  ce profil** tant qu'il est ouvert. `./stop.sh` pour fermer.
- **Ne pas tester d'envoi quand le quota est bloqué** : cela ne prouve rien et
  consomme la fenêtre de reprise.
- Aucun retry quota n'est lancé par défaut ni intégré à Reprise. Dans le banc
  d'essai, `apply.py --wait-quota` est une option explicite qui poll et retente
  l'envoi du composeur; ne pas laisser cette option tourner sans décision
  volontaire. Elle ne réarme pas MCP.
- Dans `status`, `log.available: true` signifie qu'un événement du journal a été
  trouvé; `null` signifie que la source ne permet pas de conclure. Ce champ ne
  dit pas que le quota est libre.
- Ne pas chercher à franchir les contrôles anti-bot (headless, hCaptcha) : c'est
  documenté comme impasse, pas comme obstacle à contourner.

## `wkwebview/` — prototype archivé, connexion bloquée

`ClaudeApply.swift` charge claude.ai dans un WKWebView pour éviter Chrome.
Dans le run documenté, WebKit rend la page mais la **connexion reste bloquée** :
hCaptcha boucle (`challenge → checkbox-invisible → challenge`) et ITP refuse les
cookies tiers requis par le captcha. Le prototype n'a atteint ni l'insertion ni
l'envoi; le conserver comme piste active « à prouver » serait trompeur.

Conservé comme archive de cette impasse. Ne pas compiler ici : le build Swift de
Reprise est interne (`BUILD-AND-CI.md`).
