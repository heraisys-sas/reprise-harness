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

### Correctifs après revue contradictoire (22/10)

La configuration chargée est observée indépendamment du plist : comparaison exacte du tableau `arguments` de `launchctl print`, avec son ordre, son interpréteur et sans argument supplémentaire. Une réparation décharge le job obsolète même si le plist est déjà à jour ; elle ne recharge que si l’activation a été explicitement choisie. Le refus d’un worker ou tick actif reste inchangé. Un journal repris ne permet pas de sauter ce contrôle.

La clôture revalide le hash du plist et, lorsque l’activation est demandée, les arguments effectivement chargés avant publication du manifeste. Restaurer l’ancien plist après un échec laisse l’opération en attente et conserve le manifeste antérieur ; ce refus était déjà présent dans le code inspecté et possède désormais sa régression explicite. Le simulateur conserve désormais sa propre configuration chargée, indépendante du fichier courant, comme launchd.

Dix-huit tests ciblés passent : les sept nouveaux cas couvrent la restauration d’un ancien plist, le release chargé obsolète, la réparation sans activation, un bootstrap annoncé réussi mais incohérent, une modification du plist pendant activation, les arguments ajoutés/permutés ou mauvais interpréteur, et une preuve de chargement absente/ambiguë/incomplète. Aucun service réel n’a été modifié par ces tests.

Validation du correctif : suite complète de **404 tests réussie, dont 4 ignorés**, compilation `native-install` sans `--open` et contrôle des espaces réussis. Relecture indépendante du diff après correction ; aucun défaut résiduel identifié dans le périmètre des deux signalements. Les avertissements de ressources préexistants restent présents.

### Validation réelle du service

Le 22/10 : plan frais appliqué avec activation explicite, installation historique adoptée et sauvegardée, release configuré/chargé courant et intègre. Un tick automatique a ensuite progressé, dernière sortie launchd 0, aucune erreur enregistrée, aucun travail en attente ; les sept anciennes tâches et leurs marqueurs de consommation sont restés strictement inchangés. Aucun tour d’agent n’a été démarré pour ce contrôle. Cette preuve concerne le service, pas une reprise métier après quota.

Le contrôle graphique a confirmé le bouton d’autorisation du cache, mais a reproduit un crash de récursion dans l’arbre d’accessibilité lors de la lecture de l’onglet Intégrations. Des groupes de présentation ont été simplifiés ; compilation réussie, validation visuelle non acquise car le canal natif de contrôle s’est ensuite fermé (`native pipe closed before response`), y compris après réinitialisation du client. Ne pas annoncer le crash corrigé sur la seule compilation.
