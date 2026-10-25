# Lot #77 — updater local Reprise

Baseline de découverte : dev `e159fe219dc61b12fb51e523e6888e02badc4867`.
Base effective de la branche créée par GitHub :
`9f3fc07febd18e88246c5dbb5d991a32cf614953` (distribution du template ASI1
arrivée entre le clone et `gh issue develop`). Les tests et le build du lot
ont porté sur cette base effective, vérifiée avant publication.
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

Validation finale : 20 tests ciblés OK ; 719 tests complets OK, 4 ignorés via
`ci_test.py` sur HOME jetable. Build autonome et smoke du CLI embarqué sur
HOME jetable OK ; intégrité du bundle inchangée après exécution de l’updater
avec `-E -s -B`. Empreinte des sources du build :
`66ad21a747474b337ed7d4d93550ddfc5a626c5b148456b0d7d317456d88b315`.
`git diff --check` OK. ResourceWarnings et dépréciations Trousseau existants.

Limites conservées : intégration Codex produit #30 et arrêt authentifié de
daemon #25/#27 restent distincts ; reformatage YAML #76 non contourné ;
chargement par les clients ouverts inconnu ; signature et notarisation non
validées. Un premier usage réel est décrit ci-dessous.

La mise à jour opérateur réelle effectuée avant ce code a installé 0.1.13,
avec scheduler, DSH, connecteur Claude, claude-desktopd et MCP/skill Codex
vérifiés séparément. Aucun déploiement 0.1.14 ni fusion n’est inclus ici.

## Premier usage réel et correction

L’updater a ensuite été utilisé pour aligner app/scheduler/DSH/Claude sur le
paquet 0.1.13 de dev `9f3fc07`. Cet essai a exposé deux défauts avant nettoyage :
l’override explicite du registre par défaut transformait Quick Apply sibling
en child ; la vérification globale acceptait un `binding=mismatch` observé.
Correction : préserver le layout standalone, refuser tout changement implicite
des registres Claude et bloquer validation/cleanup sur mismatch. Trois tests
supplémentaires verrouillent ces cas. Le rattachement a été rétabli via le plan
natif, avec preuve du chemin original depuis la sauvegarde et confirmation
qu’aucun registre n’avait été créé au mauvais emplacement. Le reçu conserve
la réconciliation ; aucun changement SQLite ni rejeu de tâche.

Après correction : app intègre, scheduler sur la bonne release et tick sans
erreur, DSH/Claude MCP 0.1.13, daemon Claude `binding=matched`. Le template ASI1
est distribué sans activer de rôle. Ce premier usage n’établit ni chargement
des clients ouverts, ni reprise consécutive à un nouveau quota réel. Le proxy
ASI1 #72/#74 reste un lot actif distinct, non déployé par cet updater.
