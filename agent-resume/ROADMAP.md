# Reprise — suites convenues

- Étendre aux autres harness le dispositif de connexion et de lancement contrôlé en cours pour Codex : DeepSeek Harness, Claude Code/Cowork et AutoClaw selon leurs capacités réelles. Prioriser les interfaces publiques et les mécanismes natifs ; distinguer un serveur propriétaire existant d’un serveur dédié. Vérifier les sessions, permissions et statuts avant toute activation au démarrage. Cette note ne déclenche aucune installation ni activation pour ces applications.

- Claude Code : adaptateur d’expertise explicite implémenté et testé en simulation, avec le même contrat de tâche et des sessions liées au projet/modèle. Activation réelle encore conditionnée à la connexion native et à la confirmation que les crédits payants sont désactivés ; voir CLAUDE-INTEGRATION.md. Prochaine validation : un appel court puis une reprise sur abonnement. Comparer la capacité utile et les revues indépendantes de deux abonnements modestes à un abonnement plus élevé ; vérifier les prix, limites et conditions actuels avant tout achat. AutoClaw vient ensuite, après validation du transport, des permissions et de son repli embarqué.

- **Priorité produit réaffirmée le 20/10 :** reprise multi-harness après quota, checkpoints et compactage adapté, détection des boucles et budgets parent/sous-agents. Inventaire local Claude et lots de validation : [plan de reprise et de surveillance](RECOVERY-AND-LOOP-PLAN.md). Observation passive d’abord ; contrôle actif selon capacités natives vérifiées.

- **Architecture commune à chaque harness** : [contrat de supervision et reprise](HARNESS-SUPERVISION-ARCHITECTURE.md). Même parcours pour quotas, checkpoints, boucles, consommation et contexte ; registre de capacités vérifiées et repli manuel explicite. Supervision progressive Codex + DeepSeek Harness puis autres ; priorité fonctionnelle actualisée ci-dessous : aller-retour Claude Desktop via le service commun.

- **Work/Cowork déléguable** : permettre à un harness sans ce mode de soumettre un job outillé à un environnement Work réel via outil ou connecteur Reprise. Contrat asynchrone durable, budgets, checkpoints, livrables et reprise communs ; choix du transport après comparaison fiabilité/coût. Le lancement Cowork reste à démontrer, distinct du déclenchement Chat Quick Apply. Voir le contrat multi-harness.

- **Registre des travaux non réclamés** : distinguer demandes sans exécutant, tentatives incertaines, résultats non récupérés/non intégrés et travaux interrompus. Retrouver les résultats après redémarrage du demandeur et préparer le rattachement autorisé à une session de remplacement, sans double exécution ni relance en boucle. Contrat détaillé dans l’architecture multi-harness.


**Priorité actualisée :** unifier réception (`get_task`), retour (`submit_result`) et délégation (`reprise_delegate`/suivi) dans le même service Reprise pour tous les harness. Premier parcours : question à Claude Desktop, compte gratuit compris, réponse durable récupérable ; aucun repli payant. Dépendances enfants, limites de délégation et absence de verrou bloquant les retours exigées. Cette priorité précède le pilote Work réel, qui reste distinct.

## Ordre de travail quotas actualisé

Le plafond local quotidien des sollicitations Codex est supprimé. Priorité à une observation fiable et sourcée des quotas par compte/pool, avec portée, fraîcheur et inconnus explicites. Le comportement de reprise dépendant des quotas sera conçu ensuite ; ne pas étendre l’automatisation actuelle avant cette validation. Les contrôles fournisseur et protections de concurrence existants sont conservés.

## Lot planifié — remplacer les plafonds arbitraires restants

Décision utilisateur du 20/10. Statut au 21/10 : **premier lot implémenté en simulation**, voir [contrat livré et limites](DELEGATION-PERSISTENCE.md). Identité/récupération, barrière incertaine, lignée et suppression des plafonds locaux sont présentes ; intégration autonome Claude et validation réelle restent à confirmer. Suppression Codex antérieure (`4e3f39b`). Ce lot ne modifie pas la priorité de l’aller-retour Claude Desktop.

1. **Protections ciblées** : identité durable de demande/tentative, déduplication persistante, récupération après accusé perdu, absence de redistribution d’une tentative incertaine, limites de concurrence et détection des cycles de délégation. Surveiller les répétitions avec preuves et conserver une intervention graduée ; ne pas remplacer le plafond quotidien par un arrêt aveugle après un nombre fixe d’itérations.
2. **Supprimer les plafonds quotidiens locaux restants** : admission non-Codex de `codex_expert_mcp.py` (Claude Code/Desktop) et soumissions de `claude_desktop_quick.py`. Mettre à jour installateurs, options `--daily-limit`, tests et documentation ; traiter les compteurs hérités sans blocage résiduel. Aucun nouveau canal ne doit hériter de ces plafonds arbitraires. Les limites réelles du fournisseur et budgets explicitement demandés par l’utilisateur restent distincts.
3. **Fiabiliser la connaissance des quotas** : preuve par compte/profil/pool, portée, fraîcheur, échéance connue ou inconnue, changement de compte et indisponibilité de mesure ; aucune conversion arbitraire d’un compteur d’appels en quota.
4. **Ensuite seulement**, définir le comportement de reprise selon ces quotas ; ne pas généraliser entre-temps la reprise automatique actuelle.

Acceptation : appels légitimes au-delà des anciens plafonds sans `budget_blocked` local ; anciens compteurs/options sans blocage fantôme ; doublon après redémarrage sans nouvelle exécution ; envoi incertain sans renvoi automatique ; cycle de délégation signalé ; répétition légitime non assimilée automatiquement à une boucle ; refus fournisseur conservé et quota inconnu affiché explicitement. Tests simulés d’abord, aucun appel payant requis pour ces contrôles.

## Connaissance des quotas — incrément du 21/10

Registre sourcé par route/compte opaque/pool livré : [contrat, inventaire et limites](QUOTA-KNOWLEDGE.md). CLI `quota-status`, MCP `reprise_quotas`, lecture explicite dans Reprise. Source Codex validée sur le socket existant ; quotas Claude/DSH inconnus. Compteur de cinq heures signalé : mesures non attribuées séparées, reset complet et périmé distinct d’épuisé. Aucun changement de politique de reprise/routage ni de réserve 5 %. Suite : valider les autres sources et la restitution visuelle, puis seulement concevoir la politique multi-compte.

## Installation autonome et intégrations — planifié le 21/10

[Issue de suivi #6](https://github.com/heraisys-sas/reprise-harness/issues/6) · [Contrat d’installation et critères](INSTALLATION-CONTRACT.md). Faire de Reprise le point d’installation et de maintenance de ses outils, sans dépendance au clone Git ni aux outils de développement chez l’utilisateur. Premier parcours complet sur Harness avant généralisation de l’écran « Intégrations ».

Ordre : **D1** paquet autonome, helpers/runtime et données séparées → **D2** installation/maintenance Harness depuis Reprise → **D3** validation sur compte macOS vierge, conflits et mises à jour interrompues → **D4** autres clients, signature/notarisation et distribution. Packaging Python à confirmer par prototype ; aucune installation ou activation déclenchée par cette planification. Priorité aller-retour Claude maintenue ; suivi des quotas indépendant.

Premier incrément D1 (21/10) : helpers/assets embarqués et relogeables, manifeste, résolution des binaires et état séparé ; parcours CLI déplacé testé. Runtime Python externe, import historique, services et compte vierge restent à livrer. **D1 non terminé** ; garanties/limites dans le contrat d’installation.
