# Reprise — état actuel (15 octobre 2026)

La reprise automatique Codex traite les erreurs structurées `task_complete.error.codex_error_info = usage_limit_exceeded`. Une fin de tour avec cette erreur n'est pas une réussite. Le service détecte les interruptions récentes, conserve une entrée durable par événement et vérifie que la session n'a pas déjà repris avant de lancer une continuation.

Le quota est lu via `codex app-server` et `account/rateLimits/read` : aucune requête au modèle pour cette vérification. Une reprise automatique exige `ordinaryUsageAllowed: true`. Un pourcentage, une échéance passée ou un texte comme « 4:04 AM » ne prouvent pas la récupération. Sans confirmation, la session attend et la lecture des quotas est espacée. Aucun achat ni utilisation de clé API de secours. Les tâches utilisent l'authentification ChatGPT et le sandbox workspace-write ; une action nécessitant une approbation peut toujours bloquer.

Le tableau de bord affiche l'état du service et les reprises en attente. `./resume scheduler-status` expose le même état ; `./resume auto-mode off` met l'automatisation en pause, `on` la réactive. Les arrêts volontaires, erreurs non liées au quota et interruptions anciennes ne sont pas repris automatiquement. Une tentative par interruption ; un échec demande examen. Les autres applications restent en détection seule.

Validation : reproduction du format exact de l'erreur observée et tests de relance avec exécutable simulé, sans dépense de quota modèle. Un renouvellement réel futur reste à observer. La suite du document conserve l'historique des fonctions : les limitations des premières versions concernant la reprise Codex automatique sont remplacées par cette section.

---

# Agent Resume

Reprise différée d'une session Codex sur macOS. Python 3.9+ standard, SQLite, launchd. Aucun appel LLM pour vérifier l'horloge. Codex utilise ensuite le quota de votre compte.

## Installation

Décompresser le dossier dans un emplacement durable, puis :

```sh
chmod +x resume
codex login status
./resume install
./resume status
```

Le service vérifie les échéances chaque minute. Le Mac doit être éveillé et connecté ; après veille, une tâche en retard est traitée au prochain passage. Le dossier du programme ne doit pas être déplacé sans réinstallation du service.

## Ajouter une reprise

Prendre l'identifiant exact de la session sauvegardée et l'heure de réinitialisation affichée par Codex. Ajouter deux minutes de marge. Ne pas utiliser une session simultanément dans l'application et le scheduler.

```sh
./resume add --session UUID --cwd /chemin/du/projet --at '2026-10-14T18:02:00+02:00' --prompt-file prompt.example.txt
./resume enable IDENTIFIANT_RETOURNE
./resume status
```

Les tâches sont créées en pause. `pause ID` suspend une tâche qui n'est pas en cours. Chaque tâche activée s'exécute une fois. Une sortie CLI réussie signifie `finished`, pas une preuve que les tests du projet ont réussi. Les journaux sont dans `state/`. Les échecs restent `needs_review` sans boucle automatique. Après un arrêt brutal, `running` reste visible : vérifier la session avant de réactiver.

## Sauvegarder, partager, restaurer

```sh
./resume backup sauvegarde.zip
./resume share
./resume restore sauvegarde.zip
./resume uninstall
```

`share` exporte uniquement le programme, la documentation et le prompt exemple. `backup` contient les chemins, instructions et états des tâches : sauvegarde privée, sans chiffrement, à ne pas partager publiquement. Il ne contient ni credentials Codex, ni dépôt, ni historique de session ; sauvegarder ceux-ci séparément avec leur dispositif existant. Sur une autre machine, reconnecter Codex, restaurer les projets et sessions, puis importer la sauvegarde. Les nouvelles tâches importées restent en pause ; celles déjà présentes ne sont pas modifiées.

## Limites actuelles

La commande `codex exec resume` a été vérifiée dans le CLI installé. Aucun appel modèle réel n'a été lancé pour tester cette version. L'heure de reprise est explicite : détection automatique de quota, chaîne de lots validée par outils et connecteur Harness ne sont pas encore implémentés. Pas de basculement payant. Les variables API OpenAI sont retirées du processus ; une configuration Codex personnalisée peut néanmoins sélectionner un autre fournisseur : vérifier votre configuration avant activation. Aucun contournement des approbations ou du sandbox.

## Panneau local

`./resume dashboard` ouvre un tableau de bord dans le navigateur : échéances, états, pause et activation. Il écoute uniquement sur 127.0.0.1, sur un port temporaire. Les actions exigent un jeton aléatoire de session ; aucun CDN, télémétrie ou dépendance web. Arrêter avec Ctrl+C. La création et les sauvegardes restent accessibles avec le CLI. Le bouton d'activation autorise une reprise à l'échéance, immédiatement si elle est passée. Le panneau ne détermine pas encore automatiquement l'heure de retour du quota.

## Interface macOS native

`./native-install` compile Reprise.app avec Swift fourni par les outils développeur Apple, puis ouvre une fenêtre native et une icône dans la barre de menus. Aucune permission Accessibilité ou capture d'écran n'est nécessaire. Cliquer sur l'icône affiche les résultats. Le détecteur lit uniquement la base et les événements Codex locaux ; il ne lit pas les credentials et n'analyse pas les messages avec un LLM.

Les sessions non archivées des 30 derniers jours sont examinées (100 maximum). Une fin de tâche exclut la session ; un arrêt explicite est signalé sans reprise ; une étape commencée il y a plus de 15 minutes sans fin est une candidate à vérifier. Un quota enregistré à 100 % permet d'afficher une échéance estimée, pas de prouver la cause d'interruption. Aucun candidat n'est activé automatiquement. Le format interne Codex peut évoluer. La détection Harness et le moteur Rust ne sont pas implémentés.

### Détection Harness

Le panneau réunit désormais Codex et Harness. Harness est lu depuis ~/.dsh : cache de projection, état des questions et derniers événements turn/start / turn/end. Les journaux Zstandard sont décodés par le runtime Node livré avec Harness, sans installer de librairie. Les codes QUOTA / ACCOUNT_QUOTA, RATE_LIMIT, les erreurs et les arrêts explicites sont distingués. L'absence d'heure de réinitialisation reste explicite. Les sessions archivées sont exclues. La lecture des fichiers est bornée ; un format non reconnu est signalé. Aucune reprise Harness ni modification de ses fichiers n'est effectuée. Aucun quota réel Harness n'a été provoqué pour valider cette version.

### Compteurs et quotas

L'icône et la fenêtre affichent C (Codex) et H (Harness). Le compteur estime les sessions actives : un tour est ouvert et ses données ont été modifiées depuis moins de deux minutes. Une session silencieuse peut donc être sous-comptée ; ce n'est pas une interrogation des agents. Au maximum 100 sessions récentes par application sont examinées. Codex fournit la dernière mesure de quota du compte trouvée dans les événements locaux, avec son horodatage. Les quotas sont partagés entre les sessions. Une échéance passée est marquée « mesure expirée », sans inventer une remise à zéro. Harness affiche une absence de quota chiffré plutôt qu'une estimation à partir des tokens. Aucun appel réseau ni LLM n'est effectué par la détection.

### Claude Code

Lecture passive de ~/.claude/projects/*/*.jsonl (100 sessions récentes maximum, fichiers de moins de 64 Mo). Les sous-agents sidechain ne sont pas comptés comme sessions supplémentaires. Les réponses end_turn et stop_sequence clôturent l'activité ; un échange en attente avec écriture récente est une activité estimée. Les erreurs structurées sont signalées sans analyser le texte de la conversation. Aucune commande /status, hook, clé ou requête API n'est utilisé. Le quota restant Claude et l'activité Claude Desktop ne sont pas disponibles dans cette intégration.

### Claude Cowork (Desktop)

Le compteur CW lit uniquement les fichiers de métadonnées local_*.json de ~/Library/Application Support/Claude/local-agent-mode-sessions. Une session compte si sa dernière activité date de moins de deux minutes et si elle n'est ni archivée ni explicitement terminée. L'état de fin étant parfois absent, le résultat représente une activité récente estimée. Les identifiants identiques sont dédupliqués. Aucun contenu de conversation, credential ou navigateur n'est interrogé ; les fichiers ne sont pas modifiés. Le quota chiffré Claude reste indisponible.

### AutoClaw et applications visibles

Les index ~/.openclaw-autoclaw/agents/*/sessions/sessions.json alimentent le compteur AC et la liste des échecs/arrêts récents. Les sessions sont dédupliquées par identifiant. Une activité « running » récente est estimée active ; un état « running » silencieux depuis 15 minutes demande vérification. Les tokens et coûts estimés enregistrés sont affichés par session, sans être assimilés à un quota. Aucune reprise AutoClaw automatique.

La fenêtre et la barre de menus masquent les compteurs des applications macOS fermées via NSWorkspace. Claude Code est associé à VS Code, ou visible lorsqu'une activité CLI récente est détectée ; Codex CLI bénéficie de la même exception. Les anciennes interruptions restent consultables. Les changements d'applications déclenchent une actualisation, en complément du passage chaque minute.

### Analyse Apple Intelligence

Le modèle local Apple est appelé via /usr/bin/fm respond --model system, sans cloud, outil ou nouvelle dépendance. L'application native analyse au plus un compte rendu terminal nouveau par minute ; le bouton permet de lancer le même passage. Codex et Claude Code sont pris en charge pour l'analyse sémantique (comptes rendus terminaux seulement, sept derniers jours). Harness, Cowork et AutoClaw conservent la détection structurée ; leur contenu n'est pas envoyé au modèle par cette version.

Les extraits sont bornés à 6000 caractères. Un hash évite de réanalyser le même contenu ; les résultats consultatifs sont conservés sept jours dans state/analysis.json, avec un extrait justificatif. Une preuve textuelle exacte est exigée pour « échec probable » ou « résultat partiel ». Cela vérifie la présence de la citation, pas la justesse du raisonnement. Les erreurs récupérées ne doivent pas être signalées comme échec final. Aucun verdict n'active une tâche. En cas d'indisponibilité ou de réponse invalide, la détection classique continue et une nouvelle tentative attend une heure. L'analyse consomme des ressources locales et peut occasionnellement signaler un faux positif. Elle fonctionne tant que l'application Reprise est ouverte. Les sauvegardes CLI existantes n'incluent pas ce cache, qui peut être reconstruit.

### Recherche privée avec NLEmbedding

Le bouton « Rechercher dans les sessions… » ouvre une fenêtre native. La recherche hybride combine mots-clés exacts et vecteurs de phrases du framework Apple NaturalLanguage. Les modèles disponibles sont vérifiés à l'usage ; sans modèle, la recherche textuelle reste disponible. Les vecteurs français et anglais sont conservés dans leurs espaces respectifs (aucune comparaison entre modèles/langues ou dimensions différentes). La recherche multilingue croisée n'est pas garantie. Aucun appel LLM ni serveur externe.

Le fichier state/search.noindex/index.sqlite contient les titres, diagnostics récents (20 états exposés par le détecteur) et résultats du cache d'analyse. Ce n'est pas un index complet des conversations. Les entrées absentes de ces sources sont supprimées au prochain lancement d'une recherche. Les vecteurs sont recalculés lorsque le texte change et réutilisés sinon. Le dossier .noindex n'est pas publié à Core Spotlight. Les sauvegardes CLI existantes ne contiennent pas cet index, qui est reconstructible. L'archive de partage contient uniquement les sources, sans les données privées. native-install compile aussi le petit exécutable Embedding avec Swift.

### Nettoyage de l'index

Le service launchd effectue au premier passage, puis au maximum une fois par jour, une purge des entrées dont la source date de plus de sept jours. Si le Mac est éteint ou le service arrêté, la purge attend son prochain passage. Les recherches excluent également ces anciennes sources pour ne pas les réintroduire. Les anciennes entrées sans date sont supprimées lors de la migration. Le bouton « Vider l’index » dans Recherche privée, ou ./resume clear-index, efface le cache de recherche uniquement. La recherche suivante reconstruit les entrées récentes ; les conversations et le cache d'analyse Apple sont conservés. Nettoyage et recherches sont sérialisés par SQLite. Aucun LLM n'est appelé pour ce nettoyage.

## Chiffrement des données de recherche et d'analyse

L'index est désormais state/search.noindex/index.enc : une base SQLite sérialisée, chiffrée en AES-256-GCM par CryptoKit. Titres, extraits, vecteurs et métadonnées sont chiffrés ensemble. SQLite est utilisé en mémoire, sans base temporaire ni journal en clair. Le cache d'analyse Apple est également chiffré dans state/analysis.enc. Une clé dédiée est créée dans le Trousseau macOS (service local.agent-resume.vault.v1), non synchronisée, disponible lorsque le Trousseau est déverrouillé. La clé n'est jamais retournée au code Python ni incluse dans les exports.

La migration réutilise l'ancien index, écrit le coffre atomiquement puis retire les anciens fichiers de travail. Une erreur de chiffrement conserve l'ancien index ; une erreur de déchiffrement n'entraîne aucune reconstruction en clair ni remplacement silencieux du coffre. Les purges restent opérationnelles. Les formats/caches de test ne constituent pas des sauvegardes.

Cette protection concerne uniquement l'index et le cache d'analyse Reprise. Les bases d'origine des autres applications, les instructions du scheduler, ses journaux, ses sauvegardes ZIP et les captures de démonstration ne sont pas chiffrés par ce mécanisme. Aucune modification de FileVault, des autres applications ou de leurs réglages. Les anciens snapshots/sauvegardes peuvent conserver les anciennes données en clair. Le Trousseau ne protège pas contre un processus ayant déjà accès au compte et capable d'utiliser le helper déverrouillé. Aucun accès biométrique supplémentaire n'est imposé.

Sur une autre machine, les caches peuvent être reconstruits à partir des sessions disponibles : aucune clé n'est à partager. Le binaire Vault doit être recompilé avec native-install ; macOS peut demander l'autorisation d'accès au Trousseau après une recompilation. Ne jamais réinitialiser la clé pour résoudre un refus d'accès. L'archive share contient seulement les sources.

## Interface macOS

La vue d'ensemble privilégie les interruptions des dernières 24 heures ; les incidents plus anciens restent dans l'historique. Les applications ouvertes sont regroupées dans une liste compacte, avec « Au repos » plutôt qu'un compteur nul. Les quotas distinguent les fenêtres courte et hebdomadaire, avec date de mesure et renouvellement. La recherche distingue son état initial, l'attente et l'absence de résultats. Les menus natifs proposent Copier, Coller et Tout sélectionner.

L'interface native utilise SwiftUI dans une fenêtre AppKit : NavigationSplitView, barre d'outils standard, symboles SF Symbols et couleurs système. Elle suit l'apparence claire/sombre et les réglages d'accessibilité du système. Navigation par vue d'ensemble, sessions à vérifier (filtres et détails repliables), analyse locale, recherche intégrée et confidentialité. Les actions de recherche et d'actualisation sont accessibles par Cmd+F et Cmd+R ; Cmd+Q quitte Reprise. Le nettoyage de l'index est dans Confidentialité. Les dernières données sont conservées à l'écran si une actualisation échoue. La refonte ne modifie pas les mécanismes de collecte, de chiffrement ou de reprise.
