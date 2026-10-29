# Ancien exemple CDP — neutralisé

Le pilote de ce dossier est désactivé. `apply.py`, `cdp.mjs`, `launch.sh` et
`stop.sh` refusent les commandes d’envoi, de navigation, de retry, de statut
CDP, d’attente de quota et de cycle de vie du navigateur. Ils ne lancent aucun
sous-processus, n’ouvrent aucun socket et ne pilotent aucune fenêtre.

Pour le parcours produit, utilisez uniquement la CLI Reprise
`agent-resume/resume desktop-apply` et sa documentation
[`CLAUDE-DESKTOP-APPLY.md`](../../agent-resume/CLAUDE-DESKTOP-APPLY.md). Le
code actif de cet exemple n’est pas une alternative à cette CLI.

L’historique du prototype provient de la source PR86
`4bd6b9e74d572468cded63b309a03d8011b648c7`. Il est conservé dans l’historique
Git; les fichiers présents ici sont des refus inertes et ne contiennent plus
le pilote.

`apply.py:quota_blocked()` reste un adaptateur de lecture locale vers
`agent-resume/claude_quota_log.py`. Il ne vérifie pas le compte du profil
Chrome, ne déduit pas sa disponibilité et ne lance aucune action navigateur.
Les tests n’utilisent que des journaux synthétiques.

Le prototype WKWebView demeure une archive de recherche séparée : la recette
documentée n’a pas dépassé le blocage de connexion. Elle n’est pas une voie
produit ni un pilote actif.
