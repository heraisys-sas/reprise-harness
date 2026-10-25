# Lot #77 — updater local Reprise

Baseline : dev `e159fe219dc61b12fb51e523e6888e02badc4867`.
Branche : `codex/77-managed-updater`, créée par `gh issue develop 77 --base dev`.

Plan : consultation déléguée en lecture seule, puis accord du coordinateur sur
un orchestrateur local des adaptateurs existants. Le sous-agent ne disposait
pas d’outils de lecture ; son conseil n’est pas une revue vérifiée du code.
L’inspection, l’implémentation et la revue des invariants sont locales.

Décisions : bundle explicitement choisi et intégralement vérifié ; aucune
inférence/téléchargement ; app fermée par l’opérateur avant remplacement ;
activation scheduler explicite sans changer la pause SQLite ; projets,
permissions et options DSH conservés ; reçu durable et verrou commun ; reprise
après publication interrompue et refus de dérive ; cleanup après vérification,
logs bornés, uniquement sauvegardes possédées non modifiées.

Fichiers : `agent-resume/managed_update.py`, `MANAGED-UPDATE.md`, tests dédiés,
payload `package_helpers.py` et version préparatoire `0.1.14`.

Validation finale : 17 tests ciblés OK ; 716 tests complets OK, 4 ignorés via
`ci_test.py` sur HOME jetable. Build autonome et smoke du CLI embarqué sur
HOME jetable OK ; intégrité du bundle inchangée après exécution de l’updater
avec `-E -s -B`. Empreinte des sources du build :
`8533ea3c680c9868d952f583095c21f55967efdfd34501b983246c10aea1dd8c`.
`git diff --check` OK. ResourceWarnings et dépréciations Trousseau existants.

Limites conservées : intégration Codex produit #30 et arrêt authentifié de
daemon #25/#27 restent distincts ; reformatage YAML #76 non contourné ;
chargement par les clients ouverts inconnu ; pas de validation du nouvel
updater sur les services réels, de signature ou notarisation.

La mise à jour opérateur réelle effectuée avant ce code a installé 0.1.13,
avec scheduler, DSH, connecteur Claude, claude-desktopd et MCP/skill Codex
vérifiés séparément. Aucun déploiement 0.1.14 ni fusion n’est inclus ici.
