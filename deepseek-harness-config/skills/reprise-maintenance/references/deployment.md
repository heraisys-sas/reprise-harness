# Installation et mise à jour

## Préparer une cible vérifiable

Identifier le checkout et le commit autorisés, les commentaires récents de
l'issue et les composants demandés. Vérifier la CI du **commit exact**, pas
seulement celle d'une branche. Utiliser son artefact ou construire par
`agent-resume/release_build.py build --runtime-archive ARCHIVE_EPINGLEE`.
Le verrou est `agent-resume/python-runtime.lock.json` ; ne pas substituer le
Python ou Node d'un harness au runtime autonome.

Vérifier SHA256 de l'archive, `build-evidence.json`, inventaire du bundle et
empreinte des sources. `release_build.py check --app BUNDLE` nécessite le
checkout correspondant aux entrées du build ; une simple égalité de VERSION
ne prouve pas que deux paquets ont le même code. Le build n'installe rien.
Un paquet source doit être distinct de l'app destination.

Choisir explicitement les chemins suivants, sans les inventer depuis un ancien
checkpoint : `REPRISE_BUNDLE` (candidat vérifié), `REPRISE_TARGET_APP`,
`REPRISE_STATE`, et si nécessaire `REPRISE_PROFILE`.

```sh
REPRISE_PYTHON="$REPRISE_BUNDLE/Contents/Resources/runtime/bin/python3"
REPRISE_HELPERS="$REPRISE_BUNDLE/Contents/Resources/helpers"
"$REPRISE_PYTHON" -E -s -B "$REPRISE_HELPERS/managed_update.py" inspect \
  --app "$REPRISE_TARGET_APP" --state "$REPRISE_STATE"
```

Le runtime embarqué et `-B` évitent une dépendance au PATH utilisateur et les
`__pycache__` supplémentaires qui invalideraient l'inventaire du bundle.

## Plan, application et preuve

Exemple pour **l'app seule**, après choix des variables et du fichier de plan :

```sh
"$REPRISE_PYTHON" -E -s -B "$REPRISE_HELPERS/managed_update.py" plan \
  --bundle "$REPRISE_BUNDLE" --app "$REPRISE_TARGET_APP" \
  --state "$REPRISE_STATE" --components app > "$REPRISE_PLAN"
# Examiner le JSON complet et ses effets avant l'application autorisée.
"$REPRISE_PYTHON" -E -s -B "$REPRISE_HELPERS/managed_update.py" apply \
  --plan "$REPRISE_PLAN"
```

La sortie CLI du plan contient une enveloppe `plan` : conserver le fichier tel
quel, ne pas le confondre avec l'objet Python interne. Fermer normalement
Reprise avant son remplacement, sans arrêt forcé. Ajouter `--relaunch-app`
seulement pour une relance demandée.

Pour sélectionner `scheduler,harness,claude`, lire d'abord les adaptateurs et le
plan. Une intégration Harness/Claude existante doit être gérée ; une première
installation passe par l'onboarding de chaque composant. Le profil Harness se
sélectionne avec `--profile`. Le remplacement du scheduler **sans**
`--activate-scheduler` le laisse arrêté ; avec ce flag, les travaux déjà
autorisés peuvent démarrer au tick. Choisir selon l'état et l'autorisation
actuels, jamais par copie d'une ancienne commande.

Avec le reçu retourné, sans réappliquer inutilement :

```sh
"$REPRISE_PYTHON" -E -s -B "$REPRISE_HELPERS/managed_update.py" verify \
  --receipt "$REPRISE_RECEIPT"
```

Attendre un tick post-installation si le scheduler est activé, puis vérifier
les nouveaux octets des logs. Une configuration écrite ou un plist valide ne
prouve ni chargement ni tick. Les clients ouverts peuvent conserver leur ancien
MCP ; ne pas les relancer pendant un tour actif.

Le CLI Codex natif et le remplacement de claude-desktopd restent séparés du
plan général. Un `daemon.binding=mismatch` interdit de conclure au succès.
Pour un rebind de daemon, appliquer les contrôles de
[migration.md](migration.md), pas un kill depuis un fichier PID.

## Échec, restauration et nettoyage

Conserver le plan, le reçu et les sauvegardes. Une opération pending se reprend
avec son plan original si ses préconditions restent valides ; un conflit exige
réconciliation. Ne pas modifier le reçu pour forcer le passage. Il n'existe pas
de rollback universel à exécuter aveuglément : restaurer seulement une sauvegarde
attribuée, compatible et non modifiée, via un parcours documenté et autorisé.
Ne pas restaurer une base par-dessus des résultats arrivés depuis la sauvegarde.

Après `verify` concluant, observation des logs et nettoyage autorisé :

```sh
"$REPRISE_PYTHON" -E -s -B "$REPRISE_HELPERS/managed_update.py" cleanup \
  --receipt "$REPRISE_RECEIPT"
```

Ce nettoyage supprime les sauvegardes du reçu, pas les anciens clones/releases,
et supprime le retour arrière correspondant. Une sauvegarde dérivée ou un
reçu d'une version ensuite remplacée peut être refusé : ne pas contourner ce
refus en effaçant manuellement des dossiers non réconciliés.

Contrats du checkout : `agent-resume/BUILD-AND-CI.md`, `MANAGED-UPDATE.md`,
`INSTALLATION-CONTRACT.md`, `SCHEDULER-SERVICE.md`. Les mentions historiques
« planifié » ou les anciens nombres de tests ne sont pas un inventaire courant.
