# Checkpoint — reprise Codex, premier lot local

Horodatage : 2026-10-24T17:35:37.101290+00:00
Issue de rattachement annoncée : #7 (lien GitHub et labels non vérifiés).
Branche locale : `codex/7-live-recovery-20261024`.
Baseline : `8227b753d337c915cf5d28484bef6d9c1ea7e6b1` ; copie initialement propre.

## Résultat borné

Le scheduler repoussait les tâches `ready` selon une mesure de quota puis
exigeait une nouvelle mesure positive au preflight. L’adaptateur `codex_rpc`
exigeait encore `ordinaryUsageAllowed: true` avant de charger la session.
Ces gardes contredisaient la décision de tenter à l’échéance annoncée.
Trois régressions de scheduler ont échoué sur l’ancienne implémentation.

Correctif local : une échéance connue permet une seule tentative sans mesure
préalable. La réconciliation récupère les attentes différées par l’ancienne
garde seulement pour un événement inchangé, non consommé et sans tentative.
Sans échéance, la mesure reste utilisée. Les protections de session propriétaire,
de compte ChatGPT/fournisseur OpenAI, de permissions, d’arrêt volontaire,
de pause et de tentative consommée sont conservées. Aucune réconciliation
forcée de résultat incertain. La route explicite de délégation `codex-parent`
reste hors de ce correctif de continuation du même thread.

Fichiers : `scheduler.py`, `codex_rpc.py`, leurs deux fichiers de tests,
`README.md` et le présent checkpoint. Modifications non committées, non publiées.
Aucun push, merge, changement de branche d’un autre clone ou redéploiement.
Aucun appel à un modèle, aucune copie de credentials.

## Preuves locales

- `python3 -m unittest discover -s agent-resume/tests -p test_resume.py -q` :
  21 tests, OK.
- `python3 -m unittest discover -s agent-resume/tests -p test_codex_rpc.py -q` :
  23 tests, OK, dont 1 ignoré (smoke installé opt-in).
- Fixture propriétaire : un seul `turn/start`, fin native simulée confirmée,
  tentative consommée une fois, aucune lecture préalable de quota.
- Nouvelle interruption : nouvel événement et nouvelle échéance ; ancienne
  tentative `needs_review` inchangée.
- `python3 -m unittest discover -s agent-resume/tests -q` : 655 tests en
  18,899 s, 1 échec, 1 erreur, 6 ignorés. Erreur socket `PermissionError`
  dans `test_state_install.StateInstallTests.test_import_excludes_runtime_socket_and_locks` ;
  échec daemon sorti au premier lancement dans
  `test_claude_desktop_quick.EndpointTests.test_first_launch_from_bundle_without_preexisting_quick_state`.
  La cause de sortie du second daemon n’est pas établie ici. ResourceWarnings
  également présents. Ne pas annoncer la suite verte.
- Dans `agent-resume/` :
  `sh native-install --runtime-archive /private/tmp/reprise-cpython-3.13.16-arm64.tar.gz` :
  code 0, avertissements de dépréciation Trousseau dans `Vault.swift`.
- Paquet local : `agent-resume/Reprise.app`, version `0.1.8`, non lancé,
  non installé dans les releases partagées. Runtime autonome vérifié par
  `package_runtime.metadata` : Python 3.13.16 arm64, release 20261020,
  archive SHA-256 `235f3d5b182a171f6564ee98ef2812cfb2da8a73c4a37fca1937b25702f94880`.
  Les empreintes des helpers `scheduler.py` et `codex_rpc.py` empaquetés
  correspondent aux sources corrigées.
- `git diff --check` : OK.

## Inventaire observé, sans mutation des services

- `launchctl print gui/501/local.agent-resume` réussit : scheduler chargé,
  au repos entre ticks ; les arguments et le registre chargés correspondent
  au plist (`loaded_matches=True`). Intervalle sur disque : 60 s.
- Release chargée/configurée :
  `~/Library/Application Support/Reprise/integration-releases/1cb2c33f8d5737648c6898ab8868f223c0acbc364c754ed00f0cdc3afd3a0171`.
  Helpers : `Contents/Resources/helpers/resume`, version `0.1.7`.
  Runtime sélectionné : `Contents/Resources/runtime/bin/python3`, présent.
- Registre sélectionné :
  `~/Library/Application Support/Reprise/state/tasks.sqlite`.
  Inspection par copie privée stable via `readonly_registry`.
  `auto_enabled=false`, conservé. Tick observé : `2026-10-24T17:32:46.168336+00:00`.
  Tâches : 6 `cancelled`, 4 `needs_review`, aucune en attente/exécution.
  Détails historiques : 2 `blocked_owner`, 3 `blocked_parent`, 4 `cancelled`.
  Ces observations ne prouvent pas la cause de chaque ancienne interruption.
- Un enregistrement Reprise natif et un scheduler `local.agent-resume`
  observés dans le domaine launchd utilisateur ; pas de second scheduler Reprise
  dans cet inventaire. Chemin exact/version du processus UI et éventuels processus
  hors launchd non vérifiés ; ne pas déduire la release UI de celle du scheduler.
- Accès au chemin résolu du socket Codex refusé par la sandbox
  (`/private/tmp/codex-daemon-501`, `Operation not permitted`). Connexion réelle
  non vérifiée ; aucun contournement, aucune relance de codexd/Desktop.
- GitHub non consulté : réseau non autorisé pour ce lot. Labels, commentaires
  récents #7/#14/#15/#16/#59, lien de branche et état PR #61 non vérifiés.
  `HARNESS-ROLES-CONTRACT.md` absent de cette baseline ; fusion non présumée.
  Aucun checkpoint ni changement de responsabilité publié sur l’issue.

## Prochaine action bornée pour le parent

Vérifier les commentaires/labels/lien de branche et la version effective du
contrat avant publication. Examiner les deux contrôles de suite complète avec
les permissions prévues au followup. Coordonner avec le chat Claude la release
partagée et tout remplacement du scheduler. Préparer ensuite un plan géré
et vérifier release, registre chargé et tick. La pause observée exige une
vérification de l’intention courante avant réactivation. Identifier un chat
interrompu admissible, réconcilier toute opération incertaine, puis vérifier un
seul tour réel sur le même thread/projet (preuve native de début/fin).
Aucune preuve de reprise réelle ni de déploiement n’est livrée dans ce lot.

## Validation et déploiement coordonnés par le parent

La revue indépendante via Reprise n'a établi aucun défaut du premier diff.
La combinaison des lots Codex et Claude passe 661 tests (4 ignorés) hors sandbox.
Le déploiement réel révèle ensuite un blocage supplémentaire : les événements
Codex récents de métadonnées rendaient tous les journaux « unknown_format »,
puis une erreur globale bloquait la détection. Les événements observés sont
reconnus ; une preuve de couverture par session isole désormais les journaux
incertains, sans reprise ni annulation par absence pour ceux-ci. Une erreur
SQLite globale reste bloquante. Scan réel après correction : 100 journaux
reconnus, aucune erreur ; tests ciblés détection 20 et scheduler 27, tous OK.

Le test natif de continuation sur un chat isolé, via l'adaptateur empaqueté
et le forfait ChatGPT existant, confirme turn.started, turn.completed et la
réponse exacte attendue. Ce test ne simule pas un refus fournisseur réel.
Les anciens événements needs_review ne sont pas remis en file. Le registre
partagé, les sauvegardes et les reçus détaillés restent sur le poste ; aucun
contenu de conversation n'est nécessaire à la publication. Version : 0.1.11.
