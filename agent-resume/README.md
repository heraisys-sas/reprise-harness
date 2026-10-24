Correction du lancement Desktop : l’URL Unix utilise explicitement `localhost`. Une autorité vide est classée comme distante par Desktop et peut déclencher son proxy SOCKS interne (`ECONNREFUSED 127.0.0.1:1080`). Aucun réglage de proxy macOS n’est changé. Quittez normalement Desktop puis utilisez **Lancer Desktop** pour appliquer la correction ; le processus courant n’est pas relancé automatiquement.

Le bouton **Arrêter le serveur** dans **Connexion Codex** utilise `codex app-server daemon stop` pour permettre une mise à jour. Desktop doit être fermé ; le verrou du worker et les états des sessions chargées sont contrôlés avant l’arrêt. Un état actif ou inconnu bloque l’opération. Les reprises restent en pause et la validation du mode partagé est effacée après arrêt confirmé. Aucun signal forcé ni suppression de socket. Un autre client pourrait démarrer un travail entre le contrôle et l’arrêt : fermez également vos clients CLI connectés. Commande : `shared-mode stop`.

# Mode partagé intégré (16 octobre 2026)

La page **Connexion Codex** propose trois étapes : **Préparer le serveur**, **Lancer Desktop**, puis **Vérifier le chat** avec son UUID ou son lien `codex://threads/…`. La préparation met les reprises automatiques en pause. Elle réutilise un serveur joignable ou lance le daemon Codex natif ; un socket présent mais invalide n’est jamais remplacé. Une reprise en cours empêche la préparation.

Le lancement refuse de fermer ou de relancer une application Desktop déjà ouverte. Une fois celle-ci quittée normalement, Reprise l’ouvre avec les paramètres de connexion passés à ce seul processus via `open --env`. Le raccordement utilise le support WebSocket sur Unix présent dans le code du Desktop installé (`CODEX_APP_SERVER_WS_URL=ws+unix://localhost/…:/rpc`), et reste expérimental tant qu’il n’a pas été éprouvé dans l’application. Aucun bundle, variable globale ou élément d’ouverture macOS n’est modifié.

La vérification du chat n’effectue que `initialize` et `thread/read`. Un serveur joignable sans ce chat chargé ne valide pas le mode. Après le test, choisir le serveur Codex local dans le panneau, puis réactiver explicitement les reprises dans leur vue. Le fonctionnement des outils du Desktop doit aussi être vérifié lors de ce premier essai.

L’option **Lancer ce mode à l’ouverture de Reprise** est disponible après un test réussi sur cette version de Desktop. Elle ne quitte jamais un Desktop déjà lancé. Au démarrage automatique, les reprises restent en pause jusqu’à confirmation que le chat précédemment validé est chargé dans le serveur ; un contrôle en lecture seule est tenté après le lancement. Si ce contrôle échoue, utilisez **Vérifier le chat** après avoir ouvert le chat dans Desktop. Une automatisation auparavant active est alors rétablie, sauf si vous avez changé explicitement ce choix entre-temps. Un changement de version bloque ce lancement automatique jusqu’à une nouvelle validation. Désactiver l’option puis quitter et ouvrir Desktop normalement permet de revenir à son lancement habituel ; le daemon partagé reste disponible aux clients qui l’utilisent. Les reprises peuvent être laissées en pause pendant ce changement.

Les commandes correspondantes sont `shared-mode prepare`, `shared-mode launch` et `shared-mode check --session UUID`. Aucun de ces boutons n’a été exécuté sur le Desktop utilisateur pendant l’implémentation ; tests déterministes et transport réel isolé, sans inférence. La suite pour les autres harness est consignée dans [ROADMAP.md](ROADMAP.md).

---

# Connexion Codex dans l’interface (16 octobre 2026)

La page **Connexion Codex** présente le transport enregistré, le socket local et le serveur d’expertise dédié. Un chemin personnalisé peut être testé sans appel au modèle, puis enregistré explicitement. L’enregistrement refait le test et ne modifie pas le choix existant en cas d’échec. Revenir à la détection automatique est possible depuis la même page. Un test réussi ne prouve ni la propriété des sessions Desktop ni les autorisations d’un projet. Aucun serveur ne démarre pendant la détection.

Les commandes `connection-status`, `server-check --socket CHEMIN` et `connection-config --socket CHEMIN` exposent ces fonctions. `connection-config --auto` rétablit le comportement automatique. Les réglages de moteur sont inclus dans les sauvegardes version 2 ; à l’import, ceux déjà présents restent inchangés et l’automatisation reste désactivée.

Le code du Desktop installé contient `CODEX_APP_SERVER_USE_LOCAL_DAEMON` et `CODEX_APP_SERVER_WS_URL`. Le mode daemon comporte plusieurs conditions, notamment l’absence de certaines surcharges de configuration. Ce sont des mécanismes internes observés dans le bundle, pas une validation opérationnelle de Desktop sur notre serveur. Aucun paramètre du Desktop, variable globale, service de démarrage ou session existante n’a été modifié.

---

# Interface et diagnostics (16 octobre 2026)

L’accueil privilégie l’état du service, les applications ouvertes et les quotas. Les anciennes tentatives ne sont plus déroulées en tête de page et ne transforment pas le service en « échec ». Une synthèse mène à la vue **Reprises**, avec trois filtres : **À examiner**, **En attente**, **Historique**. Les résultats sont regroupés par cause, avec une action proposée ; les sessions concernées restent repliées. Les journaux ne sont pas supprimés.

L’analyse des sept tentatives locales distingue trois refus de dossier, deux sessions détenues par un autre processus et deux sous-agents nécessitant leur parent. Le détecteur lit désormais la source des sessions Codex : les sous-agents et les sources non reconnues sont exclus de la programmation automatique indépendante. Le contrôle est refait avant exécution pour annuler une ancienne entrée de file devenue inéligible. Les erreurs historiques de sous-agent sont reclassées à l’affichage sans nouvelle tentative. Quand la session source est visible dans la détection, son détail propose d’ouvrir le parent identifié.

---

# Reprise — connexions par moteur (16 octobre 2026)

Reprise distingue maintenant le CLI Codex, un serveur Codex partagé existant et une délégation explicite à une session parente. Le serveur partagé transporte JSON-RPC dans WebSocket sur socket Unix. Aucun serveur de remplacement n’est lancé, aucun verrou n’est supprimé et aucun message n’est envoyé par le simple affichage d’une session.

```sh
./resume engines
./resume server-check --socket /chemin/prive/codex.sock
./resume add --engine codex-server --socket /chemin/prive/codex.sock --session UUID --cwd /projet --at '2026-10-17T10:00:00+02:00' --prompt-file prompt.example.txt
./resume add --engine codex-parent --socket /chemin/prive/codex.sock --parent UUID_PARENT --session UUID_ENFANT --cwd /projet --at '2026-10-17T10:00:00+02:00' --prompt-file prompt.example.txt
./resume enable IDENTIFIANT_RETOURNE
```

Les ajouts restent en pause jusqu’à activation. `server-check` effectue uniquement la connexion et l’initialisation, sans appeler le modèle. La route `codex` choisit un socket existant ou le CLI ; un socket explicitement configuré ne déclenche jamais un repli vers un second processus. `engine-config codex --socket /chemin/prive/codex.sock` permet de fixer le transport des futures tentatives automatiques. Ne configurer que le serveur qui détient effectivement les sessions concernées.

La délégation exige une relation parent–enfant confirmée par le serveur, le même dossier et un parent au repos. Elle dépose une demande identifiée dans sa file native puis demande son démarrage. « Parent sollicité » ne signifie pas « enfant repris » ; ce résultat reste à examiner et n’est jamais déclaré terminé automatiquement. Cette route utilise des méthodes expérimentales de Codex 0.160.0 et doit être choisie explicitement. Les contrôles d’inactivité ne constituent pas un verrou atomique contre un démarrage simultané dans une autre interface.

Harness dispose d’une reprise CLI manuelle via `--engine harness-cli --profile PROFIL_HEADLESS`, uniquement pour un profil existant déclarant `@deepseek-ai/dsh-headless`. Le modèle et la facturation suivent ce profil, sans héritage garanti de la session historique. Aucun profil n’est créé et le profil Desktop reste inchangé. La reprise des sessions Claude Code existantes reste en détection seule. Un adaptateur d’expertise distinct, explicite et désactivé par défaut est disponible : [contrat et activation Claude](CLAUDE-INTEGRATION.md). La reprise automatique sur quota reste limitée à Codex.

Les sauvegardes version 2 conservent le moteur et ses options par tâche ; les sauvegardes version 1 restent importables. Les imports restent en pause, l’automatisation désactivée et les tâches déjà présentes inchangées. Aucun identifiant d’authentification n’est exporté.

Sur ce Mac, aucun socket du propriétaire Desktop n’a été détecté. Le transport a été éprouvé avec un serveur local isolé et sans appel au modèle ; il ne démontre pas encore la reprise réelle d’une session Desktop. Créer un serveur via `codex app-server --listen unix:///chemin/prive/codex.sock` permet de partager les sessions qu’il détient ; cela ne récupère pas celles d’un autre propriétaire. Les possibilités d’inférence Harness et d’authentification officielle sont détaillées dans [CODEX-INTEGRATION.md](CODEX-INTEGRATION.md).

---

# Reprise — état actuel (15 octobre 2026)

Cliquer sur le titre d’un signalement, d’une analyse ou d’un résultat de recherche affiche l’application associée. Pour Codex, un identifiant UUID valide ouvre directement `codex://threads/UUID`. Les autres applications sont affichées sans prétendre sélectionner la session. La flèche des signalements conserve l’accès aux détails. Pour Claude Code, les journaux ne permettent pas d’identifier l’hôte terminal/éditeur : cette limite est affichée, sans choisir une application au hasard. Aucun message ni reprise n’est envoyé par ce clic.

Les statuts de reprise s’appuient sur les événements JSON du CLI : lancement, tour démarré, fin de tour confirmée, refus de confiance, authentification, quota refusé, délai dépassé ou résultat inconnu. Un code de sortie zéro sans `turn.completed` ne constitue pas une fin confirmée. Une fin de tour ne valide pas la tâche métier. Le dernier contrôle du quota est horodaté, sans présenter cette mesure historique comme une disponibilité actuelle. Les erreurs anciennes sont classées depuis leurs journaux locaux, sans relance. Les journaux d’exécution restent privés, limités à 2 Mio, et ne sont pas chiffrés par l’index de recherche. Un dossier refusé reste bloqué explicitement ; aucune confiance globale ni option de contournement n’est ajoutée.

La reprise automatique Codex traite les erreurs structurées `task_complete.error.codex_error_info = usage_limit_exceeded`. Une fin de tour avec cette erreur n'est pas une réussite. Le service détecte les interruptions récentes, conserve une entrée durable par événement et vérifie que la session n'a pas déjà repris avant de lancer une continuation.

Lorsqu’une interruption quota confirmée comporte une échéance structurée, le scheduler tente une seule continuation à cette échéance, sans mesure préalable obligatoire du quota. Les anciennes attentes repoussées par une mesure sont recalées sur cette échéance seulement si leur événement est inchangé et leur tentative non consommée. Sans échéance, une lecture espacée via `account/rateLimits/read`, sans requête au modèle, reste utilisée pour déterminer quand tenter la reprise. Un pourcentage, une échéance passée ou une admission ne prouvent ni récupération ni succès : seule la fin native du tour confirme son achèvement. Une nouvelle interruption quota constitue un nouvel événement avec sa propre échéance ; une tentative incertaine reste à réconcilier, sans rejeu. Aucun achat ni utilisation de clé API de secours. L’authentification ChatGPT, le fournisseur OpenAI, les permissions existantes et les contrôles de session restent requis ; une action nécessitant une approbation peut toujours bloquer.

Le tableau de bord affiche l'état du service et les reprises en attente. `./resume scheduler-status` expose le même état ; `./resume auto-mode off` met l'automatisation en pause, `on` la réactive. Les arrêts volontaires, erreurs non liées au quota et interruptions anciennes ne sont pas repris automatiquement. Une tentative par interruption ; un échec demande examen. Les autres applications restent en détection seule.

Validation : reproduction du format exact de l'erreur observée et tests de relance avec exécutable simulé, sans dépense de quota modèle. Un renouvellement réel futur reste à observer. La suite du document conserve l'historique des fonctions : les limitations des premières versions concernant la reprise Codex automatique sont remplacées par cette section.

---

# Agent Resume

Reprise différée d'une session Codex sur macOS. Python 3.11+ standard, SQLite, launchd. Aucun appel LLM pour vérifier l'horloge. Codex utilise ensuite le quota de votre compte.

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

L'icône et la fenêtre affichent C (Codex) et H (Harness). Le compteur estime les sessions actives : un tour est ouvert et ses données ont été modifiées depuis moins de deux minutes. Une session silencieuse peut donc être sous-comptée ; ce n'est pas une interrogation des agents. Au maximum 100 sessions récentes par application sont examinées. La carte Codex consulte désormais un registre sourcé : **Lire les quotas** interroge explicitement les métadonnées du socket configuré, sans tour de modèle. Les identités compte/pool sont pseudonymes et les données absentes/périmées restent inconnues. Les signaux de journaux restent séparés, avec leur horodatage et sans attribution de compte. Une échéance passée signifie « mesure périmée », jamais épuisement ou reprise garantie ; le reset affiche sa date et son heure locales complètes. Harness affiche une absence de quota chiffré plutôt qu'une estimation à partir des tokens. La détection ordinaire reste passive ; seule la lecture explicite des quotas peut interroger la source native. CLI : `./resume quota-status --engine codex-server --refresh` ; MCP : `reprise_quotas`. [Contrat et limites](QUOTA-KNOWLEDGE.md).

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

### Organisation du code

- `Native.swift` pilote l’application ; `Dashboard.swift` contient ses vues.
- `scheduler.py` décide des reprises ; `run_status.py` exécute le CLI et décrit ses preuves.
- `runtime.py` centralise l’environnement Codex, le décodage JSONL borné et le nettoyage des processus.
- `detect.py`, `local_analysis.py`, `private_search.py` et `vault.py` gardent leurs responsabilités distinctes.

Vérification : `python3 -W error::ResourceWarning -m unittest discover -s agent-resume/tests`. L’installation native compile les deux fichiers de l’interface ; l’export `resume share` inclut tous les modules nécessaires. Aucune bibliothèque supplémentaire.

### Prérequis du prototype macOS

Reprise cible macOS **26.0+** ; construction avec SDK macOS **26+**, Swift et Python développeur. Le bundle ARM64 peut embarquer son propre CPython (voir [RUNTIME-PACKAGING.md](RUNTIME-PACKAGING.md)). La synthèse locale utilise le framework Swift Foundation Models, sans dépendance au SDK Python Apple FM. Apple Intelligence doit être disponible sur l’appareil pour cette fonction facultative ; son absence conserve les vérifications déterministes. Diagnostic sans génération : `Reprise.app/Contents/MacOS/FoundationExplain --status`. Les versions minimales effectivement produites figurent dans `Contents/Resources/native-manifest.json`.

### Préparer les états depuis le bundle

Sans arguments de développement, Reprise présente le parcours de préparation avant sa supervision :

1. Choisir **Importer un état existant** et sélectionner le dossier contenant `tasks.sqlite`, ou choisir une création neuve volontaire.
2. Prévisualiser les sources, destinations et sauvegardes. Le dossier Quick Apply associé, s’il est reconnu, figure dans le plan ; les états UX séparés restent hors de ce parcours.
3. Arrêter les applications/services qui écrivent dans ces états, puis confirmer l’attestation et appliquer. Reprise ne les arrête pas.
4. Vérifier le reçu et les sauvegardes, puis choisir **Continuer**. Une interruption retrouve le plan conservé et les étapes publiées ; un conflit bloque sans écrasement.

Un ancien état principal seul peut être conservé et son dossier Quick préparé séparément par ce même écran. Le mode développement (`native-install --open`) conserve son état historique explicite. Les commandes CLI `state-init`/`state-import` restent disponibles ; `state-status`/`state-plan` sont sans écriture. Interface compilée, parcours visuel et compte vierge non encore validés : voir [INSTALLATION-CONTRACT.md](INSTALLATION-CONTRACT.md).

### Installer les outils dans Harness

Après préparation des états, **Intégrations** permet de sélectionner un profil Harness existant et son projet, prévisualiser l’installation/réparation ou le retrait puis appliquer explicitement le plan. Les skills sont isolées au profil ; observations/reçus sont exposés par défaut, délégation en option. La vérification directe du pont ne prouve pas son chargement dans Harness. Aucun redémarrage ni inférence automatique. Chemins du runtime installés hors du bundle et registre durable conservé : voir [HARNESS-INTEGRATION.md](HARNESS-INTEGRATION.md) pour garanties, refus et niveaux de preuve.
