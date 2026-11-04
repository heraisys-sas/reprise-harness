---
name: reprise-maintenance
description: Installer, mettre à jour, migrer et diagnostiquer Reprise.app, ses services locaux et ses intégrations Codex, DeepSeek Harness et Claude, avec plans gérés et preuves de chargement.
---

# Maintenance de Reprise

Utiliser cette compétence pour les opérations Reprise, pas pour installer ou
mettre à jour les harness eux-mêmes. Elle transmet une procédure ; elle ne
donne aucune permission supplémentaire et n'autorise aucune inférence.

## Choisir le parcours

- Installation ou mise à jour : [deployment.md](references/deployment.md).
- Déplacement de dépôt, workspace, connecteur ou registre :
  [migration.md](references/migration.md).
- Service absent, timeout, version incohérente, conflit ou échec partiel :
  [diagnostics.md](references/diagnostics.md).
- Installer cette skill dans un autre harness ou passer le travail :
  [sharing.md](references/sharing.md).

Lire seulement les références utiles. Les liens ci-dessus restent valides quand
le dossier entier est copié hors du dépôt. Pour les contrats complémentaires,
demander ou identifier le checkout autorisé de `reprise-harness`, vérifier son
SHA et lire son `AGENTS.md`. Ne pas choisir arbitrairement le premier clone trouvé.

## Invariants opérationnels

1. Identifier la cible : paquet/source, profil, projet/workspace, registre
   principal, `quick-state`, release sélectionnée et socket. Une association
   dans le registre de projets n'installe ni ne recharge un client.
2. Inventorier avant mutation. Connecter les sockets ; contrôler les arguments
   réellement chargés par launchd, les doublons et les opérations actives.
   Un refus d'inspection signifie **inconnu**, même si un helper affiche stopped.
3. Réutiliser `managed_update.py` et les adaptateurs `inspect/plan/apply/verify`.
   Exécuter uniquement les mutations autorisées. Conserver scopes, permissions,
   pause globale, arrêts volontaires et options déjà choisies.
4. Préserver les sessions actives et les tentatives incertaines. Un timeout ou
   un verrou libre ne prouve pas l'arrêt d'un tour détaché. Aucun SQL manuel,
   changement de clé ou rejeu pour franchir `needs_review`/`indeterminate`.
5. Ne pas interrompre codexd, les Desktop ou toute la chaîne pour actualiser
   un paquet. Un PID seul n'authentifie pas un processus à arrêter.
6. Vérifier séparément : **paquet installé**, **configuration écrite**,
   **intégration chargée dans le client**, **échange réel terminé**. Les sondes
   initialize/tools/list n'exécutent pas d'inférence et ne prouvent pas les deux
   derniers niveaux. Ne pas lancer un chat pour tester une installation.

Le critère de fin dépend du lot demandé : une installation de paquet peut être
validée sans échange d'inférence. Annoncer alors les niveaux vérifiés et laisser
les autres inconnus ; ne pas bloquer cette installation sur une preuve métier
hors périmètre. Un essai réel exige sa propre autorisation et un tour borné.

Après refus quota, conserver cause et échéance puis tenter une continuation à
l'échéance si elle est autorisée, sans pré-mesure obligatoire de quota. Conserver
les gardes de propriété, session active, permissions, arrêt/pause et incertitude.
Une échéance passée ou une admission ne prouve aucun succès. Aucun repli payant.

## Compte rendu et passation

Rapporter le SHA du code, la version et les chemins effectifs du paquet et des
services, les vérifications réellement exécutées, les preuves et leurs limites,
les reçus/opérations en vol, le non publié et la prochaine action bornée.
Publier un checkpoint expurgé sur l'issue ; conserver configurations, journaux,
identifiants privés et chemins personnels dans l'audit local. Après vérification
des nouveaux logs et autorisation de nettoyage, supprimer seulement les
sauvegardes possédées par l'opération ; préserver les releases encore référencées.
