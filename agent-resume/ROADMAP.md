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

- Issue #6, D1 : commandes `state-init` / `state-import` hors ligne ajoutées, sauvegarde SQLite WAL vérifiée, publication séparée et identité Quick Apply conservée ; tests sur copies temporaires. Runtime Python embarqué et compte macOS vierge restent à traiter (voir INSTALLATION-CONTRACT.md).

Exigence D2/D3 du 21/10 : installer également dans Codex et chaque client supporté les consignes d’utilisation économique des outils Reprise (MCP + skill/instructions selon mécanisme vérifié). Découverte des capacités, tâches bornées, contexte minimal, récupération avant renvoi et escalade justifiée ; aucune dépendance aux outils Desktop pour la délégation commune. Critères dans le [contrat d’installation](INSTALLATION-CONTRACT.md#usage-inter-harness-depuis-codex-et-les-autres-clients). Routage automatique selon quotas différé jusqu’à connaissance fiable.

- Issue #6, D1 : runtime CPython ARM64 embarqué et épinglé, extraction sûre, notices tierces, sélection native sans Python système. Test réel sur bundle déplacé et HOME temporaire ; voir RUNTIME-PACKAGING.md. Compte vierge, autres architectures et distribution restent à valider.

- Issue #6 : prérequis Foundation Models explicites, cible macOS 26.0 contrôlée sur tous les binaires et plist, manifeste natif, sonde de disponibilité sans génération et motifs affichables. Validation macOS 26 réel encore requise.

- Issue #6, D1 : préparation graphique avant supervision, prévisualisation sans registre, import main/Quick sauvegardé, journal de reprise par étape et refus sans écrasement. Mode développement conservé ; parcours visuel et compte vierge restent à valider. Prochain incrément fonctionnel : D2, installation MCP et consignes économiques ciblées Harness/Codex.

- Issue #6, D2 : premier parcours Harness géré depuis Intégrations (inspect/plan/apply/verify/remove), provider skill propre au profil, consignes économiques/clarifications, releases indépendants du bundle et état conservé. Profil synthétique et sonde MCP directe validés ; chargement/client/outils/échange et compte vierge restent à confirmer. Codex client reste un adaptateur suivant. Voir HARNESS-INTEGRATION.md.

## Priorité corrective — reprise Codex après quota (22/10)

La reprise fonctionnelle passe avant les extensions d’installation. Corriger le chargement d’une session propriétaire `notLoaded` avant la vérification `idle`, conserver les refus sur session active/erreur/identité divergente et interdire le repli CLI en présence d’un lien de socket cassé. `restart-chain` doit préserver codexd et les sockets/processus non identifiés ; aucun arrêt systématique du moteur partagé.

Diagnostic local : automatisation en pause et anciennes tentatives en examen ; ne pas réenfiler automatiquement ces dernières. Réactivation et validation de bout en bout doivent distinguer détection, quota actuel confirmé, chargement, démarrage et fin du tour. Ne pas présenter un test de chargement sans inférence comme une reprise métier réussie.

Correctif [issue #7](https://github.com/heraisys-sas/reprise-harness/issues/7) : implémenté et vérifié par **386 tests OK, 4 ignorés** ; chargement réel `notLoaded` → `idle` confirmé sans lancement de tour. Le parcours simulé complet consomme une interruption une seule fois. Avertissements de ressources préexistants conservés. Réactivation explicitement autorisée par l’utilisateur ; anciennes tentatives en examen préservées. Le succès métier d’une reprise réelle reste distinct de ces preuves.

Suite installation #6/#7 implémentée : parcours Intégrations pour inspecter, planifier, installer/réparer, activer explicitement et retirer le planificateur, avec diagnostics de chargement, pause, tick et releases. Validation : 397 tests OK, 4 ignorés, compilation sans lancement ; parcours visuel, activation réelle et compte vierge restent à confirmer. Voir [SCHEDULER-SERVICE.md](SCHEDULER-SERVICE.md). Contrat dans [INSTALLATION-CONTRACT.md](INSTALLATION-CONTRACT.md#planificateur-de-reprise-installable-et-vérifiable). La remise en service ponctuelle du poste ne vaut pas parcours produit livré.

Anciennes interruptions #7 revalidées sur les événements natifs : quatre demandes devenues obsolètes annulées ; trois sous-agents conservés en examen avec état `blocked_parent`. Sauvegardes cohérentes des deux registres ; aucune relance ni succès métier déduit.

Accès privé : diagnostic et pistes pour les dialogues du Trousseau dans [KEYCHAIN-AND-SSH.md](KEYCHAIN-AND-SSH.md). Sens SSH demandé : Reprise vers le conteneur distant IronClaw, autonomie après redémarrage ; clé dédiée existante sans passphrase vérifiée, authentification SSH et HTTP distant confirmés ; transfert de ports refusé par la couche SSH. Résoudre la politique de forwarding avant installation d’un LaunchAgent ; prévoir installation/diagnostic/retrait depuis Reprise. Aucun adaptateur de tâches IronClaw encore implémenté, aucune clé modifiée.

Priorité Trousseau : accès automatiques non interactifs implémentés ; refus sans copie en clair et analyse suspendue avant inférence si clé inaccessible. Autorisation manuelle depuis Confidentialité, signature du helper configurable par certificat. Suite de 412 tests réussie (4 ignorés) ; certificat et parcours visuel restent à vérifier.

Validation réelle planificateur : activation du release corrigé, tick automatique progressant avec exit0 et erreur nulle, sept anciennes interruptions inchangées. Contrôle graphique incomplet : récursion d’accessibilité reproduite puis canal de contrôle natif indisponible ; simplification de présentation compilée à confirmer visuellement.

IronClaw SSH direct : première connexion gérée ajoutée à Connexions (configuration privée de chemins, sonde API authentifiée sans inférence), neuf tests ciblés et GET réel HTTP200. Soumission et résultat durables restent non activés : prouver idempotence, ACK et récupération corrélée après interruption avant activation. Aucun tunnel requis, aucune clé copiée, aucune extension de droits distante.

Lot durable IronClaw expérimental : admission et identifiants d’action persistés avant SSH, barrières contre doublons et envois incertains, commandes explicites submit/poll/result/requests, réponses corrélées au run, reçus locaux conservés et acquittables même sans configuration. Aucun branchement automatique au scheduler/MCP ni écoute SSE automatique. Suite 443 tests réussie (4 ignorés), puis 28 ciblés ; POST réel et facturation bornée à démontrer avant qualification opérationnelle. Crash AX Intégrations désormais suivi dans [issue #8](https://github.com/heraisys-sas/reprise-harness/issues/8), contrôle graphique toujours indisponible.

Version 0.1.2 — reprise Codex : autoriser systemError uniquement avant chargement, puis imposer idle ; un blocage prouvé avant turn/start libère la réservation et attend 300 s, avec revalidation de la session. Un envoi incertain reste distinct et ne se rejoue pas aveuglément. Le message demande de vérifier le travail et les sous-agents avant continuation. Validation réelle de systemError encore requise.

Consolidation 0.1.3 : lot DSH 200cff7 comparé au lot durable local, packaging des modules MCP/UX corrigé, installation UX revalidée avant publication et sans faux « chargé ». Réconciliation ACK et followup IronClaw exposés explicitement. Prototype MCP CLI IronClaw désactivé en attendant raccordement au registre Tasks/Endpoint unique (#17) ; aucune suppression des capacités SSH durables. Le correctif Codex 0.1.2 reste intégralement conservé.

Instructions d’orchestration déployables : profil optionnel pour modèles spécialisés en orchestration (ex. ASI1), `REPRISE-AGENTS.template.md`, distribué avec la skill et le payload, suivi par le manifeste Harness ; extension aux futurs adaptateurs selon leur mécanisme natif d’instructions. Aucune promesse de rechargement automatique du contexte actif ni de route Morph gratuite universelle.

Courtier d’opérations Git/GitHub (#29, lié #24) : contrat LOCAL-EXECUTION-CONTRACT.md, avis Astra et revue indépendante, premier évaluateur pur git.fast_forward sans IO ni exécution. Étapes suivantes : collecteur fiable/isolation, autorisation opérateur, exécuteur/récupération, façade MCP/CLI et recettes distantes distinctes.

Façade Reprise cliente Codex CLI/Desktop (#30) : exposer aussi la délégation vers Claude/autres harness, leurs clarifications, suivis et résultats durables, comme pour Harness ; pas seulement les recettes Git. Installation gérée du client MCP, CODEX_HOME/host/projet vérifiés, chargement et round-trip réels distincts, retour Claude Desktop MCP corrélé requis. Aucune réactivation implicite des outils de redélégation dans les experts isolés.

### #35 — limite de tokens de sortie Harness

Continuation native dans la même session après `max-tokens`, distincte des quotas
fournisseur : plugin optionnel installé depuis Intégrations, pause globale,
réservation durable par événement et protection contre continuations consécutives.
Lecture de toutes les trames Zstandard des journaux. Vérification réelle de son
chargement dans un client Desktop après rechargement coordonné à distinguer des
tests simulés. Contrat : [HARNESS-INTEGRATION.md](HARNESS-INTEGRATION.md).

### #36 — Claude Desktop gratuit : reset affiché et Continuer

Helper natif Accessibilité et registre durable des pauses gratuites, date/fuseau
vérifiés, clic Continuer après revalidation sur le même dernier message. Option
installable depuis Intégrations, activée par défaut, subordonnée à la pause
globale. Aucune API privée ni facturation de repli. Contrat et limites dans
[CLAUDE-INTEGRATION.md](CLAUDE-INTEGRATION.md). L’essai après échéance réelle et la
permission Accessibilité restent à confirmer séparément des tests simulés.


### #31 — connecteur Claude unifié et déploiement autonome

Façade MCP unique pour tâches entrantes/résultats et délégation sortante, registre
partagé avec claude-desktopd, isolation projet, retours paginés, cycle Desktop
immédiat bloqué. Installation/inspection/retrait/vérification depuis Intégrations,
runtime embarqué, sauvegardes et conflits préservés. Contrat :
[CLAUDE-CONNECTOR.md](CLAUDE-CONNECTOR.md). Chargement dans Claude à confirmer après
rechargement consenti ; ce lot ne prétend pas livrer le pilote natif Quick Apply.

### Passation explicite entre harness

Convention immédiate GitHub `agent:*` + `work:active|available|blocked`, branche liée à l’issue et checkpoint de reprise : [contrat](HANDOFF-CONTRACT.md). À implémenter dans Reprise : réservation atomique avec génération, renouvellement, réconciliation des actions en vol et projection des labels ; distribution de la règle avec chaque intégration. Ne pas assimiler expiration, quota, pause volontaire et achèvement.

### #62 — déléguer une implémentation native à Codex

[Contrat](CODEX-EXECUTION-CONTRACT.md) : grant explicite par projet, mode implementation distinct des expertises, session durable et followup de même mode, installation DSH gérée et contrôles de fichiers adaptés. Le canal ne crée pas de permission implicite GitHub/système et n’est pas le courtier de commandes #29. Vérifier séparément présence/classification Desktop et approbations interactives #24.
