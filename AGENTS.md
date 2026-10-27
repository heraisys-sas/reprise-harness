# Contexte de travail — projet Reprise & Harness

Instructions de projet pour les agents qui travaillent sur ce dépôt. Complète (sans écraser) les préférences globales de Harness (`~/.dsh/AGENTS.md`). Répondre en français, identifiants techniques en anglais.

## Ce projet est
Une application macOS de **supervision** (SwiftUI/AppKit + helpers Python) qui suit les sessions d'agents, détecte les anomalies post-update, lit les journaux locaux de Claude Desktop et sert de canal de délégation vers des experts externes (Codex, Claude). `agent-resume/` = l'app native + les modules Python ; `deepseek-harness-config/` = config Harness ; `routing/`, `harness-tools/` = routage et outils.

## Workflow Git
- **Branche de travail : `dev`** — pas de commit direct sur `main`.
- **MR `dev` → `main`** (une MR ouverte : #1). Chaque lot de travail = commit sur `dev`, puis fusion via MR.
- **Prise en charge d’une issue par Codex** : ajouter le label `agent:codex` et utiliser `gh issue develop` pour créer une branche liée à l’issue, basée sur `dev` et nommée `codex/<numero>-<sujet>` ; exemple : `gh issue develop <numero> --base dev --name codex/<numero>-<sujet>`. Vérifier le lien avec `gh issue develop <numero> --list`. Cette branche temporaire doit être intégrée à `dev` avant le parcours habituel vers `main`. Préserver le travail concurrent ; ne pas changer la branche d’un workspace utilisé par un autre agent. Le label désigne l’agent responsable ; le lien GitHub désigne la branche, sans déclencher automatiquement un chat.
- Tests : `python3 -m unittest discover -s agent-resume/tests -q` (~257 OK).
- Compile Swift (sans relancer l'app) : `sh native-install` ; `--open` pour lancer l'app.
- Check syntaxe Swift : `swiftc -parse -module-cache-path /private/tmp/agent-resume-swift-cache Native.swift Dashboard.swift` (dans `agent-resume/`).
- Relancer Reprise : `./restart-chain --runtime-archive FILE` construit un bundle autonome vérifié puis lance l'application ; `./restart-chain --no-build` lance seulement le dernier build vérifié. `--server` (service Quick Apply) et `--development` sont des chemins historiques explicites. Le script ne redémarre jamais `codexd` (chats propriétaires préservés) ; voir `agent-resume/BUILD-AND-CI.md`.

## Prise en charge des issues (agents)
Tous les agents partagent le compte GitHub `heraisys-sas` : l'assigné ne distingue personne. Le propriétaire d'une issue se lit donc dans trois signaux, du plus important au moins important.
1. **Label `agent:<nom>`** (un seul par issue) : `agent:codex`, `agent:claude`, etc. Créer le label s'il n'existe pas (couleur `1D76DB`, description « Travail réservé à <nom> ; ne déclenche pas automatiquement une session »). Un filtre `label:agent:<nom>` donne la file de travail de l'agent.
2. **Branche liée** : créer la branche avec `gh issue develop <n> --base dev` avant de commiter, ce qui la rattache à la colonne « Development » de l'issue. Si la branche existe déjà, la nommer dans le commentaire de réservation. Pas de MR avant le go de l'utilisateur (les MR vont uniquement vers `main`).
3. **Commentaire de réservation lisible par machine**, premier commentaire du travail :
   `<!-- agent-claim: <nom> <AAAA-MM-JJTHH:MMZ> branch=<branche> -->` suivi d'une phrase (branche, commit, état des tests).
- Avant de prendre une issue, lire son dernier marqueur `agent-claim` : une réservation de moins de 24 h, ou avec une branche qui a reçu un commit depuis, appartient à son auteur ; sinon elle est périmée et peut être reprise.
- Pour abandonner : retirer le label et poster `<!-- agent-release: <nom> <date> -->` avec le motif et l'état (branche, ce qui reste). Pour terminer : la fermeture de l'issue vaut libération.
- Ne jamais fermer ni retirer le label d'une issue réservée par un autre agent sans son marqueur de libération ou une réservation périmée.

## Architecture — canaux de délégation et leurs limites
| Canal | Sens | Limite |
|---|---|---|
| **Codex** (`reprise_delegate`) | aller-retour, vérifiable | **seul opérationnel** ; aucun plafond local quotidien Codex ; contrôles fournisseur existants (`quota_blocked`) conservés |
| **Quick Entry / Quick Apply** | Reprise → Claude Desktop | **unidirectionnel** : soumet un tour, ne relit PAS la réponse (`response: None`) |
| **MCP `get_task`/`submit_result`** | aller-retour via SQLite | 1 fichier joint, 1 réponse/tâche ; Claude doit invoquer l'outil (pas push) |

`sampling/createMessage` est refusé par le client MCP Desktop (`-32601`). Pas d'API d'inférence publique. Voir `agent-resume/CLAUDE-MCP-PROBE.md`, `ARCHITECTURE-QUESTION-PENDING.md`, `reprise-delegation.md`.

## Contraintes d'environnement (à connaître avant de coder)
- **Sandbox** : écriture limitée au workspace. Bloqué : `~/.claude-desktop` (socket orphinel impossible à `rm`), `~/.dsh`, `Documents/ChatGPT/productivité`. Un agent qui l'ignore boucle — c'est arrivé.
- **`ps` / `osascript` bloqués** — pas d'inspection de processus fine. Vérifier l'état par **connexion au socket** (`connect()`), pas par existence du fichier socket.
- **Clone du pont MCP** (`Documents/ChatGPT/productivité` = même dossier que `~/Library/Mobile Documents/…/productivité`, inode `.git` identique) = `project_root` du pont `reprise_delegate`. **Synchronisé le 20/10** : `18ff0b4` → `2702215` par `fetch` + `merge --ff-only`, après vérification d'un **chevauchement vide** avec les **7 fichiers modifiés localement non committés** (préservés). Les fichiers créés après `18ff0b4` (`ARCHITECTURE-QUESTION-PENDING.md`, `ux_review_probe.py`, `UX-UI-ANALYSIS.md`) sont donc présents → `reprise_delegate` avec `paths` résout (`resolve(strict=True)`). Avant tout futur `merge`, revérifier le chevauchement incoming ↔ 7 fichiers sales.

## Nomenclature (Unix) — respecter
- **Services/daemons** : `codexd` (daemon Codex / `app-server daemon`), `claude-desktopd` (daemon Quick Apply).
- **Onglet** : « Connexions » (onglet unique, Picker interne codex-desktop / claude-desktop).
- **Chemins** : « socket » (jamais « serveur » — le mot « serveur » avait 3 sens, risque d'arrêter le mauvais service).
- **Marque** : « Quick Apply » (pas « Quick Entry ») dans l'UI et les messages.

## Faits techniques établis (ne pas re-découvrir)
- Le serveur Quick Apply **démarre SANS Claude Desktop** (il bind son socket seul) → pas de blocage d'ordre ; les soumissions sont livrées quand Desktop est prêt.
- Un **socket orphelin** (fichier présent, connexion refusée) = `stopped`, pas `running`. Vérifier par `connect()`.
- `native-install` ne relance l'app **qu'avec `--open`** (compiler ne perturbe pas la session).
- Les timestamps de l'UI sont en **heure locale 24h** (helpers `localTime`/`localDateTime` dans `Dashboard.swift`).
- `runtime.terminate()` doit être résilient (un `killpg` refusé ne doit pas écraser le retour de l'appelant) — corrigé, source du bug des quotas manquants.

## Modèles & escalade
- Le modèle par défaut du workspace est configuré dans `~/.dsh` (hors dépôt) — **il évolue, ne pas le figer ici**. L'identifier par `reprise_backends` / la config du profil au moment du travail.
- Un modèle **léger** (type Flash) est adapté aux tâches **cadrées/localisées** : ajout de composants/labels, correctifs ciblés, tests. Il peut **bloquer sur de la refonte structurante** (échec observé : boucle sans résultat sur une fusion d'onglets).
- **Pour les tâches complexes, ESCALADER vers un modèle plus compétent** plutôt que de forcer le modèle léger :
  - via **sous-agent** (`subagent`) avec un modèle plus capable (DeepSeek Pro / raisonnement élevé, ou un modèle Codex type Sol/Astra selon `reprise_backends`) ;
  - ou via **l'outil de délégation Reprise** (`reprise_delegate`) pour un expert externe (revue, expertise, batch).
- **Poursuivre le même chat distant** : un même sujet/projet délégué à un harness distant se poursuit dans la session distante existante (`reprise_followup`, même `thread_id`, même niveau de compétence — Sol, Astra…) tant que ce niveau ne change pas ; l'expert garde son exploration du dépôt et les consignes stables, ce qui économise tours, coût et délai. Nouvelle session distante si le niveau change, le sujet diverge ou l'historique devient encombrant ; lignage durable via `parent_request_key` (parent en état enregistré).
- Ne jamais laisser un sous-agent boucler indéfiniment sur une tâche hors de sa portée : le constater, l'interrompre, et **escalader ou faire soi-même**. Deux échecs sur un même volet = changer d'approche (modèle ou auteur).
- **Synthèses d'état et lots courts → sous-agent Flash** (`deepseek`/`morph-dsv41flash-1017`, rapide et fiable ; la route **refuse** `reasoning_effort` — ne pas le passer). Cas types : « regarde le nouvel état », collecte factuelle bornée, petites actions cadrées. Prompt imposé : lecture seule explicite (pas de `git add/commit/push`, pas de `gh edit/close/merge/comment`, aucune soumission distante), liste fermée de commandes, baseline avec SHAs fournie, sortie = deltas seuls ≤15 lignes avec SHA complet + horodatage, et « non vérifié » si une commande échoue — jamais de devinette.
- **Le coordinateur re-vérifie avant de décider** : au minimum les SHAs cités et tout chiffre surprenant (exemple du 24/10 : baseline « 5 issues ouvertes » fausse — il y en avait 42 ; horodatages de merge divergents entre deux lectures `gh`). Une synthèse fausse devient la vérité du coordinateur, qui peut merger dessus — re-vérification systématique avant tout effet de bord.
- **Pas de repli implicite Reprise → Flash** : un refus de quota remonte au coordinateur. Revues de code/architecture et diagnostics ambigus ne se dégradent pas silencieusement vers Flash (même angle mort sur un second avis identique) — changer d'expert, pas de modèle léger. Restent au coordinateur : merges/push, décisions, arbitrage entre issues ; à l'opérateur : écritures hors sandbox.

## État et suivi
- Issues GitHub #2–#5 : symptômes UI, architecture de retour (avis Astra obtenu le 20/10 — voir plus bas), canal MCP UX review (prêt, non branché), analyse UX/UI.
- Revue UX/UI : les 4 priorités sont traitées (badges + résumé Santé, nomenclature, accessibilité + feedback, fusion onglets). Détails dans `agent-resume/UX-UI-ANALYSIS.md`.
- Conseil architectural **Astra obtenu le 20/10** (`gpt-6-astra` high, thread `01a15938…`) : **A + B** — Quick Apply déclenche, MCP `get_task`/`submit_result` reçoit, SQLite registre ; seul `submit_result` vaut retour. Réponse intégrale + invariant dans `agent-resume/ARCHITECTURE-QUESTION-PENDING.md`. **Suite : implémenter le manifeste/`request_hash` et brancher `reprise_ux_review` (validation opérateur)**. Suivi via `codex_followup` sur ce thread.

## Points de vigilance
- Ne pas supposer un résultat de test non exécuté ; annoncer précisément les contrôles.
- Distinguer ce qui est **corrigé** de ce qui est **à confirmer en conditions réelles** (hors sandbox).
- Ne pas inventer d'identifiant de modèle, de chemin ou de fonctionnalité — vérifier dans le code/config.

## Orientation actualisée — service bidirectionnel
- Contrat de référence : `agent-resume/HARNESS-SUPERVISION-ARCHITECTURE.md`. Même service logique Reprise pour réception, retour et délégation inter-harness ; prototypes encore séparés.
- Priorité : question/réponse Claude Desktop récupérable, compte gratuit compris, sans repli payant. Work/Cowork réel reste une capacité distincte à démontrer.
- Registre durable des résultats non réclamés ; contrôle des dépendances, budgets et cycles ; aucun verrou de retour conservé pendant une inférence.

- Décision quota actualisée : Après refus, conserver la cause et l’échéance puis tenter la reprise à l’échéance sans mesure préalable obligatoire. Une nouvelle session avec checkpoint est permise lorsque pertinente ; suspendre les rotations qui répètent les mêmes échecs sans progrès. Aucun repli payant implicite. Voir le contrat HARNESS-ROLES-CONTRACT.md (rôles, reprise et archivage proposé dans l’interface).

## Discipline d’exécution et arrêt des boucles

- Privilégier les frontends, CLI et API existants. Après un refus sandbox, utiliser une route autorisée ou demander une permission ciblée ; ne pas répéter une commande inchangée.
- Lorsqu'une validation est demandée avec une commande longue, la faire précéder d'un court commentaire qui en explique l'objet et l'effet attendu.
- Regrouper les lectures indépendantes et les modifications d’un même fichier. Si l’outil invalide une lecture après édition, relire une fois puis appliquer les changements ensemble ; deux refus identiques imposent une autre méthode.
- Déléguer un résultat vérifiable : périmètre et baseline exacts, fichiers autorisés, comportement attendu et vérifications. Permettre les lectures nécessaires plutôt qu’un petit nombre arbitraire. L’expert doit demander une précision si le contexte manque.
- Pour un patch : demander un diff produit par Git depuis la baseline et vérifié par `git apply --check` sur une copie propre. Un patch rédigé comme du texte et non vérifié reste une proposition. Après deux échecs d’application (dont `--recount`), ne plus réparer les hunks par génération : demander un livrable corrigé dans le même chat expert ou appliquer des changements ciblés puis vérifier.
- Un timeout ne prouve ni arrêt ni achèvement distant. Lire le reçu et l’état natif du tour avant continuation. Ne jamais modifier directement SQLite (`indeterminate` → `recorded`) pour franchir une protection ; passer par une réconciliation documentée avec preuve d’état et résultat explicites. À défaut, conserver l’incertitude et demander clarification.
- Pour ASI1 : déléguer expertise, revue, architecture, plan technique et opérations système à un expert adapté via Reprise ou sous-agent. Garder localement la coordination et les manipulations déterministes cadrées ; ne pas improviser une réparation structurante après plusieurs échecs. Vérifier les modèles réellement disponibles.
- Vérifier les changements avec les tests ciblés, puis la suite requise avant publication. Après réussite, ne pas relancer la suite complète sans changement pertinent ni nouveau doute.
- Garder un checkpoint bref : objectif courant, preuves obtenues, blocage, prochaine action. Mettre à jour les tâches terminées et lire les messages utilisateur en attente avant un nouveau lot. Ne pas consacrer plusieurs tours à décider de recharger une skill déjà lue ; la relire seulement si ses instructions manquent ou ont changé.

## Délégation systématique lorsque le moteur coordinateur est ASI1

ASI1 coordonne les tâches techniques ; il confie leur analyse et leur résolution à un modèle adapté, y compris pour un lot court. Il peut exécuter les manipulations déterministes déjà cadrées et vérifier les preuves retournées.

1. Découvrir les capacités Reprise et déléguer au niveau approprié ; poursuivre le même chat distant pour le même sujet et niveau. Ne pas multiplier les avis équivalents.
2. Si aucune route Reprise appropriée n’est disponible, utiliser un sous-agent Harness avec le modèle Morph DeepSeek V4.1 Flash réellement disponible, ou un nouveau chat utilisant cette route si le sous-agent ne peut pas l’utiliser. Dans le profil desktop observé le 23/10/2026 : provider `deepseek`, modèle `morph-dsv41flash-1017`, endpoint Morph ; revérifier le catalogue et l’accès au moment du choix. « Configuré localement » ne signifie pas inférence exécutée sur le Mac. **La route Flash refuse `reasoning_effort` (refus observé le 26/10/2026, issue #81) : ne jamais passer ce paramètre au lancement du sous-agent, ni l’évoquer comme consigne dans son prompt.**
3. Réserver la route gratuite aux lots bornés ; sa limite exacte reste non mesurée. L’utilisateur observe une limite par session sur Morph Flash 1017 ; autoriser une session de remplacement avec checkpoint sans extrapoler cette propriété à tous les quotas.
4. Après refus, conserver la cause et l’échéance puis tenter la reprise à l’échéance sans mesure préalable obligatoire. Une nouvelle session avec checkpoint est permise lorsque pertinente ; suspendre les rotations qui répètent les mêmes échecs sans progrès. Aucun repli payant implicite. Voir le contrat HARNESS-ROLES-CONTRACT.md (rôles, reprise et archivage proposé dans l’interface).

Le succès se mesure au lot validé et au coût total, pas au nombre de délégations. Après deux échecs sur le même volet, changer de méthode ou d’expert ; ne pas ramener à ASI1 un problème complexe qu’il ne sait pas résoudre.

## Procédure standard d'une demande de correction (opérateur, 24/10/2026)

Appliquée par défaut à toute demande technique de l'opérateur, avec traçabilité complète dans le dépôt :

1. **Cadrage** : le coordinateur reformule le résultat attendu, fixe la baseline (SHA), les fichiers autorisés et les critères d'acceptation ; il ne fait pas l'exploration lui-même.
2. **Planification déléguée** : premier tour en lecture seule à un expert — sous-agent Flash pour un lot court ou cadré, Reprise (Codex, niveau adapté) pour un lot complexe. Le plan attendu : conception, fichiers à modifier, tests, risques et questions ouvertes.
3. **Discussion jusqu'à accord** : le coordinateur relit le plan, tranche les arbitrages de son ressort, pose ses questions, et poursuit avec le même expert dans le même fil (`send_message` pour un sous-agent, `reprise_followup` sur le même `thread_id` pour Reprise) jusqu'à un accord explicite. Désaccords et décisions sont consignés, pas seulement échangés.
4. **Resoumission par le bon canal d'écriture** : après accord, le même expert implémente — sous-agent (écriture dans la sandbox du workspace, sans commit), ou Reprise pour un diff produit par Git et vérifié (`git apply --check`) avant application locale. Le coordinateur garde Git : réservation d'issue (label `agent:*` + branche liée + `agent-claim`), commit, push vers `dev`.
5. **Vérification et traçabilité** : tests ciblés puis suite requise avant publication ; chaque étape (plan retenu, arbitrages, accord, implémentation, contrôles) est tracée dans le dépôt — commentaires de l'issue, fichier md de travail ou message de commit — jamais uniquement dans la conversation de travail.

## Protocole court du coordinateur

Quand le rôle coordinator est choisi :
1. Reformuler le résultat attendu et réunir seulement les faits nécessaires à la délégation (baseline, capacités, contraintes). Déléguer l’investigation et la conception elles-mêmes ; ne pas terminer l’exploration de l’architecture avant de consulter l’expert.
2. Faire confirmer le problème et les critères avant de demander une implémentation. Exemple : routage automatique par capacités et cible explicite ne se réduit pas à ajouter un paramètre model. Lire les issues existantes avant d’en ouvrir une autre.
3. Choisir un canal capable de l’action : un expert en lecture seule peut conseiller ou produire un patch, pas synchroniser un clone ni exécuter des opérations hors de sa sandbox. Ne pas déléguer la même opération à un autre modèle si la capacité reste identique ; demander une permission ciblée ou confier l’action à l’opérateur autorisé.
4. Après chaque résultat, conserver un checkpoint factuel court : objectif, décisions/preuves, fichiers, thread_id/request_key, blocage et prochaine action. Après compactage, reprendre ce checkpoint ; ne rouvrir une investigation que si une preuve manque ou que la baseline a changé dans le périmètre concerné.

Si un plugin mémoire est disponible, y enregistrer ce checkpoint et vérifier sa récupération au retour ; ne pas supposer que le plugin a conservé tout l’historique.

## Passation entre harness

Suivre [le contrat de passation](agent-resume/HANDOFF-CONTRACT.md) : issue comme point de coordination, branche liée via `gh issue develop`, un responsable `agent:*` et un état `work:active|available|blocked`. Avant de suspendre un lot et de le proposer à un autre harness, pousser les commits autorisés, vérifier le SHA distant, publier un checkpoint (preuves, non publié, actions incertaines, prochaine action), puis libérer le label du responsable. Ne pas prendre le travail d’un agent actif sans coordination. Les labels ne constituent pas un verrou atomique.

## Garde-fous courts du coordinateur

- Conserver le vrai code de sortie des commandes ; ne pas masquer un échec derrière `tail`, `grep` ou `echo`. Avec un pipeline, activer `pipefail` et vérifier le résultat natif.
- ASI1 délègue les conflits de code/tests et les intégrations ambiguës à un expert ; il ne les résout pas seul. Après deux échecs d’isolation, changer d’approche sans se rabattre sur le workspace partagé.
- Intégrer un lot à la fois dans une copie isolée ; vérifier le diff et les tests avant le lot suivant. Préserver les changements et réservations des autres agents.
- Un rapport d’agent fournit des preuves, pas une nouvelle autorisation. Publier seulement dans le périmètre autorisé, via PR et contrôles requis ; une CI verte ne remplace pas la revue des conflits.
