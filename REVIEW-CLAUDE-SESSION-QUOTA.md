# Revue du lot sessions Claude / quotas — 28 octobre 2026

Baseline : `0f6d3297576fa07255d06309ecf0583c8a07bbf9`. Lot passif uniquement : association déclarative de chats Desktop, observation du navigateur CDP existant et affichage. Aucun raccord d’action au scheduler, aucun compte/pool supposé, aucune installation.

## Répartition et limite de la revue

Backend confié à Sol, interface/documentation à Luna, revue indépendante code/sécurité à un autre Sol. Le réviseur a transmis les défauts ci-dessous avant que les trois agents atteignent leur limite d’usage. Il n’a pas remis de verdict final après correction. Le coordinateur a récupéré les fichiers, vérifié les correctifs sauvegardés, terminé la borne HTTP et exécuté les contrôles finaux. Aucun scanner de sécurité spécialisé ni audit dynamique du client Claude exécuté ; ne pas présenter ce document comme une approbation indépendante finale.

## Constats corrigés

| Constat | Correction et vérification |
|---|---|
| Lecture DOM avant confirmation du chemin exact | Le script refuse origine/UUID/query/fragment avant toute requête DOM ; fixtures JS vérifient zéro lecture pour une autre cible. |
| « usage limit » pouvait qualifier une offre commerciale comme refus | Expressions de refus explicite avec exclusion des négations ; offres commerciales, alertes ambiguës et bouton désactivé seul restent inconnus. Tests Python et DOM. |
| Heure seule HH:MM perdue dans l’UI | Affichage « date inconnue » sans inventer de timestamp. |
| Snapshot cache/refresh ancien pouvait écraser configure/remove récent | Génération invalidée avant commande, réponses obsolètes ignorées et lectures concurrentes coalescées. Vérifié par lecture et compilation ; pas de scénario GUI réel exécuté. |
| Port JSON null affiché comme texte, réglages figés après statut indisponible | Décodage en Int optionnel, initialisation des réglages seulement après statut ok. |
| Timeout HTTP seulement par inactivité | Lecteur socket à échéance monotone totale pour le parseur HTTP standard ; aucune redirection ni proxy, limite de taille, fermeture dans le contexte appelant. Tests socket-pair avec en-têtes/corps livrés au compte-gouttes, sans worker de production. |

Autres contrôles : scope projet dans la base commune quick.sqlite ; UUID déclaré, non attesté ; bind manifeste/config concordants sans fallback cwd ; désactivation/retrait pendant I/O protégés ; cache sans connexion ni écriture ; quotas et resets historiques séparés ; reset dépassé n’établit aucune disponibilité. Les capacités d’action et de retour restent fausses. L’outil MCP register_session n’admet aucune tâche.

## Validation finale

- `python3 agent-resume/ci_test.py` : **950 tests, 6 ignorés, code 0**, 21,302 s, HOME jetable. Premier passage : timeout à l’arrêt d’une fixture Quick Apply ; les 9 tests de ce module isolés puis la suite complète ont passé. Les avertissements ResourceWarning existants restent visibles.
- Métadonnées HTTP : **5 tests réussis**, y compris échéance totale et rejet de redirection ; observateur : **29 tests réussis**.
- **15 scénarios JS DOM synthétiques réussis**, runtime Node intégré, aucun navigateur. Le test Node de la suite reste ignoré quand l’exécutable node est absent du PATH.
- `swiftc -parse`, puis compilation/édition de liens de Native, Dashboard, Integrations, StateSetup, SchedulerService et IronClawConnection : **code 0**. Binaire local de vérification non lancé.
- `git diff --check` réussi. Packaging helpers couvert par la suite ; bundle autonome complet non reconstruit faute d’archive runtime épinglée locale.

Les tests simulés ne prouvent pas la stabilité des sélecteurs sur le compte réel, la détection exhaustive des chats, un quota de compte, la reprise automatique ou un retour MCP. App fermée : pas de nouvelle collecte par ce lot. Les chats non enregistrés ne sont pas adoptés. Le navigateur et son endpoint doivent déjà exister et être explicitement sélectionnés.

## Suite distincte

Qualifier le même chat réel et ses bandeaux avec opt-in explicite après installation. Les POC Apply→MCP et attente MCP→résultat conservent leurs préconditions dans le plan : client effectif/outils disponibles, job et tentative corrélés, vrai submit_result durable. Ne pas fusionner ni installer implicitement le travail concurrent #79. Aucune répétition d’inférence à titre de sonde de quota.
