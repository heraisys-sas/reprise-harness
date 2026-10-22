# Planificateur utilisateur depuis Reprise

Incrément du 22 octobre 2026, suivi #6/#7. Dans **Intégrations → Planificateur utilisateur Reprise**, inspecter, préparer installation/réparation/mise à jour ou retrait, lire le plan, puis autoriser explicitement son application. L’activation est un choix distinct : elle peut lancer au prochain tick les travaux précédemment autorisés lorsque le mode automatique est actif. Lors d’un remplacement, le service chargé est déchargé ; sans activation cochée, il reste arrêté. Une pause existante n’est jamais modifiée par l’installation ; sa réactivation reste une action séparée dans Reprises.

Le helper `scheduler_service.py` propose `scheduler-service-inspect`, `scheduler-service-plan --action install|remove [--activate] [--interval N]`, `scheduler-service-apply` (plan JSON sur stdin) et `scheduler-service-verify`, via `resume`. Les commandes historiques de développement `install`/`uninstall` sont conservées ; le nouveau parcours graphique utilise exclusivement les opérations gérées.

## Configuration et preuves

Le LaunchAgent `~/Library/LaunchAgents/local.agent-resume.plist` référence le CPython embarqué avec `-E -s -B`, le helper `resume tick` et le registre préparé explicitement. Runtime/helpers sont copiés via le mécanisme commun `stage_release`, dans `state.parent/integration-releases/EMPREINTE`, indépendamment du clone et du bundle mobile. Le plan expose les arguments exacts, l’intervalle, l’environnement PATH, le registre, les empreintes et les effets. Aucun nouveau registre métier n’est créé.

L’inspection distingue fichier présent, chargement observé par `launchctl print`, intégrité du release configuré, release attendu/configuré/effectif, pause, dernier tick, dernière erreur du planificateur, dernière sortie launchd et travaux en attente. Un service chargé n’est pas une preuve de tick réussi ni de reprise autorisée. Un release effectif non identifiable reste inconnu. Les métadonnées SQLite sont lues dans une copie temporaire DB/WAL dont la stabilité des fichiers sources est contrôlée ; aucune ouverture SQLite du registre source, aucun nouveau sidecar ni répertoire utilisateur pendant l’inspection.

## Conflits et récupération

Le plan est recalculé avant application. L’ownership enregistré refuse une personnalisation ultérieure. Un ancien plist de forme reconnue peut être adopté explicitement seulement s’il cible le même registre et un release Reprise intègre avec son interpréteur attendu ; aucune adoption générale d’un LaunchAgent inconnu. Les formes historique à trois arguments et isolée à six arguments sont reconnues. Le retrait exige l’ownership enregistré.

Les opérations prennent le verrou `state/worker.lock` non bloquant et un verrou de gestion. Un worker actif refuse l’opération ; aucun arrêt de codexd, Claude Desktop ou des chats. Le verrou est gardé pendant publication et activation ; un tick immédiatement concurrent peut attendre le prochain intervalle. Le contrôle launchd refuse aussi un tick déclaré en cours. Ces verrous sont coopératifs et ne protègent pas contre un écrivain qui les ignore.

Plist sauvegardé, journal et manifeste sont conservés dans `Application Support/Reprise/scheduler-service`. Chaque étape est publiée atomiquement. Après une panne, **Inspecter** retrouve le plan interrompu ; le reprendre applique les seules étapes restantes après contrôle des empreintes. Toute modification concurrente suspend l’opération. Une installation identique est sans effet. Un release géré abîmé peut être déplacé vers le chemin `.damaged-EMPREINTE` explicitement annoncé dans le plan, puis reconstruit depuis le payload intègre ; la copie abîmée reste disponible pour analyse. Ni restauration automatique ni suppression des données après une panne. Le retrait conserve registres, résultats, releases et sauvegardes.

## Validation et limites

Onze tests sur copies temporaires avec launchctl simulé couvrent inspection DB/WAL sans écriture source, pause conservée, installation sans activation, activation explicite, installation identique sans changement, conflits, échec/reprise, retrait sélectif, mise à jour, runtime manquant, adoption bornée, état absent et worker occupé. Aucun test ne lance un tick, une inférence, Vault/Trousseau ou un service utilisateur réel.

Le build et les contrôles Swift ne valident pas le parcours visuel. L’activation launchd, un tick de registre d’essai sans travail exécutable, le compte vierge, les autorisations macOS et la reprise fonctionnelle supervisée restent à confirmer séparément. Une présence de `last_tick` ne prouve pas la réussite de toute la boucle ; examiner aussi les erreurs et les résultats durables. L’installation autonome distribuée reste soumise aux limites du contrat d’installation.

Vérifications du 22/10/2026 : suite complète **397 tests OK, 4 ignorés**, puis **11 tests ciblés OK** après le dernier garde de manifeste ; compilation `native-install` sans `--open`, parse Swift et contrôle des espaces réussis. Les avertissements de ressources préexistants restent présents. Aucun redémarrage de l’application ni installation réelle du nouveau service pendant ces vérifications.
