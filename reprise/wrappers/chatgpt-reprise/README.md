# ChatGPT Reprise.app — lanceur optionnel

Ce bundle est uniquement un **wrapper** de `/Applications/ChatGPT.app` (`com.openai.codex`).
Il ne modifie pas l'application officielle et n'invoque pas le moteur Codex.

## Construction reproductible

Le bundle `.app` est un artefact généré, ignoré par `.gitignore`. Les fichiers
`build_wrapper.py` et `launch.sh` sont versionnés et contrôlés en CI.

```sh
python3 reprise/wrappers/chatgpt-reprise/build_wrapper.py --output "$HOME/Applications"
open "$HOME/Applications/ChatGPT Reprise.app"
```

Le constructeur refuse d'écraser un wrapper existant : le remplacement doit
être explicite et hors exécution. Port 9333 par défaut, variable `CHATGPT_CDP_PORT`.

## Comportement préservé

1. Si le port dispose déjà de la cible `app://-/index.html`, activer ChatGPT.
   Un port occupé par un service étranger provoque un refus, sans interruption.
2. Si ChatGPT tourne sans CDP, demander sa fermeture normale par AppleScript.
   Au besoin, envoyer TERM puis KILL en **dernier recours** au seul processus
   principal authentifié par son chemin exécutable absolu.
3. Sinon, lancer l'app officielle avec `--remote-debugging-port` lié à
   `127.0.0.1`, puis attendre au maximum 30 secondes la cible ChatGPT.

Journal : `~/Library/Logs/chatgpt-reprise-launcher.log`.

**Attention :** la fermeture de ChatGPT peut interrompre une réponse ou un
brouillon en cours. Utiliser le wrapper uniquement avec autorisation.

## Commandes

```sh
node reprise/chatgpt_cdp.mjs discover
node reprise/chatgpt_cdp.mjs send "ping"
```

`send` n'effectue qu'un seul clic et renvoie un reçu JSON séparant tentative,
confirmation d'un tour utilisateur et observation d'une réponse. L'observation
du texte de réponse ne prouve pas la fin du travail métier : `response_complete`
reste `false`. Pas de lien automatique à `submit_result` dans ce prototype.

**Sécurité :** le port CDP localhost reste un accès privilégié pour les autres
processus locaux. Ne jamais l'exposer sur le réseau.

## Architecture commune expérimentale : CDP pipe (issue #161)

Le lanceur TCP ci-dessus reste une solution de compatibilité. Une nouvelle
voie **sans wrapper ni port TCP** réutilise le transport Python de Reprise
`claude_cdp_pipe.PipeTransport` et son lanceur générique :

```sh
python3 -B reprise/chatgpt_desktop_pipe.py inspect
# Probe explicite : démarre/arrête uniquement son processus dédié.
python3 -B reprise/chatgpt_desktop_pipe.py probe --profile dedicated
```

L'action `inspect` est passive ; `probe` crée un profil persistant
`~/Library/Application Support/Reprise/chatgpt-cdp-profiles/dedicated`,
privé (0700), avec lock exclusif (0600). Une instance déjà propriétaire du
profil n'est **jamais adoptée** ; un lock orphelin entraîne un refus explicite.
Le probe cherche une cible unique `app://-/index.html` et examine seulement
l'existence du composeur, sans lire de messages ni envoyer de prompt.

**Compatibilité et coexistence réelles non prouvées :** même avec un profil dédié,
un mécanisme singleton Electron pourrait activer la fenêtre opérateur. Le probe
indique donc `operator_isolation_verified: false`, jusqu'à un test réel. Le
fork Electron de ChatGPT Desktop peut
ne pas honorer `--remote-debugging-pipe`, contrairement à son mode TCP
déjà sondé. Un échec `pipe_unavailable` ne doit pas entraîner de
reprise implicite sur le port ni sur le moteur Codex. Il faut également
démontrer la coexistence indépendante avec l'instance de l'opérateur sur
un Mac réel. Les profils sont conservés après probe ; aucune session utilisateur
n'est effacée automatiquement.
