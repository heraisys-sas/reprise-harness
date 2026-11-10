# Contexte de travail — projet Reprise & Harness

Instructions de projet pour les agents qui travaillent sur ce dépôt. Complète (sans écraser) les préférences globales de Harness (`~/.dsh/AGENTS.md`). Répondre en français, identifiants techniques en anglais.

Règles de placement et d'hygiène de l'arbre : [USAGE.md](USAGE.md).

## Ce projet est
Une application macOS de **supervision** (SwiftUI/AppKit + helpers Python) qui suit les sessions d'agents, détecte les anomalies post-update, lit les journaux locaux de Claude Desktop et sert de canal de délégation vers des experts externes (Codex, Claude). `reprise/` = l'app native + les modules Python ; `harness/` = config Harness ; `routing/embeddings/`, `tools/` = routage et outils.

## Workflow Git
- **Branche de travail : `dev`** — pas de commit direct sur `main`.
- **MR `dev` → `main`** (une MR ouverte : #1). Chaque lot de travail = commit sur `dev`, puis fusion via MR.
- **Prise en charge d’une issue par un agent** : ajouter le label `agent:<nom>` (ex. `agent:codex`, `agent:claude`) et utiliser `gh issue develop` pour créer une branche liée à l’issue, basée sur `dev` et nommée `codex/<numero>-<sujet>` ; exemple : `gh issue develop <numero> --base dev --name codex/<numero>-<sujet>`. Vérifier le lien avec `gh issue develop <numero> --list`. Cette branche temporaire doit être intégrée à `dev` avant le parcours habituel vers `main`. Préserver le travail concurrent ; ne pas changer la branche d’un workspace utilisé par un autre agent. Le label désigne l’agent responsable ; le lien GitHub désigne la branche, sans déclencher automatiquement un chat.
- **Layout Git par workspace** : chaque agent possède son propre clone dans `<workspace>/source/` (son propre `.git`, branche de travail), jamais un `.git` partagé hors workspace — la sandbox DSH (`workspace-write`) limite l'écriture à la racine du workspace de session, et un `.git` extérieur (ex. `~/Projets/reprise-harness/source/`) impose une approbation par opération Git. La coordination inter-agents passe par `origin` (push/fetch), pas par des branches locales partagées. L'arborescence locale de référence (`~/Projets/reprise-harness/`) :

```
~/Projets/reprise-harness/
├── source/                    ← clone racine (source de vérité pour le déploiement opérateur : release_build, bundle)
├── workspaces/
│   ├── autoclaw/              ← workspace de l'agent autoclaw (pas de clone source/)
│   ├── claude/                ← workspace claude (runtime des connecteurs, pas de clone source/)
│   ├── codex/source/          ← clone de travail de l'agent codex
│   └── harness/source/        ← clone de travail de l'agent harness
└── archives/                  ← collections et migrations archivées
```

Le clone `source/` racine est le seul qui alimente le déploiement opérateur ; les `workspaces/<agent>/source/` sont des clones de travail par agent, coordonnés uniquement via `origin`. Les workspaces sans `source/` (`claude/`, `autoclaw/`) servent de runtime aux connecteurs, pas de clone Git. Convention de nommage : le dossier clone s'appelle toujours `source`, au singulier, quel que soit l'agent.
- **Worktrees temporaires** : pour un merge, une revalidation ou toute manipulation hors branche de workspace, créer un worktree dédié sous `<workspace>/tmp/<sujet>/` (ex. `workspaces/harness/tmp/merge-99`), jamais dans un autre worktree d'agent ni dans `/private/tmp` (purgé au reboot). Le clone `source/` étant dans le workspace, ces worktrees restent dans le périmètre inscriptible. Supprimer le worktree après usage (`git worktree remove`).
- Tests : `python3 -m unittest discover -s reprise/tests -q` (suite fusionnée : 1144 OK, 6 skipped — le chiffre évolue, ne pas le figer).
- CLI : l'entrée utilisateur est `reprise/reprise` ; `reprise/resume` n'est qu'un wrapper déprécié qui l'exécute (#99).
- Compile Swift (sans relancer l'app) : `sh native-install` ; `--open` pour lancer l'app.
- Check syntaxe Swift : `swiftc -parse -module-cache-path /private/tmp/agent-resume-swift-cache Native.swift Dashboard.swift` (dans `reprise/`).
- Relancer Reprise : `./restart-chain --runtime-archive FILE` construit un bundle autonome vérifié puis lance l'application ; `./restart-chain --no-build` lance seulement le dernier build vérifié. `--server` (service Quick Apply) et `--development` sont des chemins historiques explicites. Le script ne redémarre jamais `codexd` (chats propriétaires préservés) ; voir `reprise/BUILD-AND-CI.md`.

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
| **Codex** (`reprise_delegate`) | aller-retour, vérifiable | opérationnel ; aucun plafond local quotidien Codex ; contrôles fournisseur existants (`quota_blocked`) conservés |
| **Claude — dispatch natif QTi** | Reprise → Claude Desktop | push unique : socket `~/.claude-desktop/quick-entry.sock` tenu par ReClaude (fusion 10/11/2026, un seul socket) (`initialize` → `quick_entry/capabilities` → `quick_entry/submit`) ; `dispatch_attempted` est fire-and-forget, preuve d'arrivée ET de traitement requises avant de conclure |
| **Claude — bridge MCP orchestration** | aller-retour durable | backend `claude-desktop` de `reprise_delegate` : admission + collecte `submit_result` ; peut renvoyer `engine_unavailable` à tort (état stale du bridge alors que le socket répond) → diagnostic par `connect()` direct avant toute autre conclusion |
| **MCP `get_task`/`submit_result`** | aller-retour via SQLite | 1 fichier joint, 1 réponse/tâche ; Claude doit invoquer l'outil (pas push) |

Un **seul connecteur actif côté Claude** depuis #124 : le dispatch natif QTi porté par ReClaude, qui tient le socket unique `~/.claude-desktop/quick-entry.sock` (fusion 10/11/2026). Un socket `connect()` refusé ou absent = ReClaude arrêtée — ne jamais « réparer » une délégation en démarrant un daemon, c'est un faux diagnostic. `sampling/createMessage` est refusé par le client MCP Desktop (`-32601`). Pas d'API d'inférence publique. Voir `docs/integrations/claude/CLAUDE-MCP-PROBE.md` et `docs/integrations/harness/reprise-delegation.md`.

## Contraintes d'environnement (à connaître avant de coder)
- **Sandbox** : écriture limitée au workspace. Bloqué : `~/.claude-desktop` (socket orphinel impossible à `rm`), `~/.dsh`, `Documents/ChatGPT/productivité`. Un agent qui l'ignore boucle — c'est arrivé.
- **`ps` / `osascript` bloqués** — pas d'inspection de processus fine. Vérifier l'état par **connexion au socket** (`connect()`), pas par existence du fichier socket.
- **Clone du pont MCP** (`Documents/ChatGPT/productivité` = même dossier que `~/Library/Mobile Documents/…/productivité`, inode `.git` identique) = `project_root` du pont `reprise_delegate`. Son état de synchronisation se lit dans l'historique Git et les issues, pas dans ce fichier : avant tout `merge` dans ce clone, revérifier le chevauchement entre les changements entrants et les fichiers modifiés non committés (préservés), puis vérifier que `reprise_delegate` avec `paths` résout (`resolve(strict=True)`).

## Nomenclature (Unix) — respecter
- **Services/daemons** : `codexd` (daemon Codex / `app-server daemon`), `claude-desktopd` (daemon Quick Apply, **historique/arrêté depuis #124** — le connecteur Claude actif est l'app ReClaude, pas un daemon).
- **Onglet** : « Connexions » (onglet unique, Picker interne codex-desktop / claude-desktop).
- **Chemins** : « socket » (jamais « serveur » — le mot « serveur » avait 3 sens, risque d'arrêter le mauvais service).
- **Marque** : « Quick Apply » (pas « Quick Entry ») dans l'UI et les messages.

## Faits techniques établis (ne pas re-découvrir)
- Le serveur Quick Apply **démarre SANS Claude Desktop** (il bind son socket seul) → pas de blocage d'ordre ; les soumissions sont livrées quand Desktop est prêt.
- Un **socket orphelin** (fichier présent, connexion refusée) = `stopped`, pas `running`. Vérifier par `connect()`.
- `native-install` ne relance l'app **qu'avec `--open`** (compiler ne perturbe pas la session).
- Les timestamps de l'UI sont en **heure locale 24h** (helpers `localTime`/`localDateTime` dans `Dashboard.swift`).
- `runtime.terminate()` doit être résilient (un `killpg` refusé ne doit pas écraser le retour de l'appelant) — corrigé, source du bug des quotas manquants.

## Quick Apply — architecture réelle (mise à jour 10/11, lot #124)
- **Canal actif unique : dispatch natif QTi.** L'app ReClaude (Claude Desktop re-signé, JS injecté dans l'asar) tient elle-même le socket `~/.claude-desktop/quick-entry.sock` (fusion 10/11/2026, un seul socket ; l'ancien nom quick-entry-reprise.sock n'est plus créé) et pousse les prompts vers claude.ai. Méthodes exposées : `initialize`, `quick_entry/capabilities`, `quick_entry/submit` (`request_key` requis, `chatId` optionnel). Toute autre méthode → `-32601` (normal).
- **Le daemon CDP historique `claude-desktopd` (`claude_desktop_quick.py`, socket `quick-entry.sock`) est ARRÊTÉ et non requis.** Un `quick-apply-daemon-status` → `stopped` est l'état normal depuis #124 : ne jamais le démarrer pour « réparer » une délégation — faux diagnostic. Le bridge MCP `claude-desktop` peut renvoyer `engine_unavailable` (« Endpoint Quick Apply injoignable ») alors que le socket répond : diagnostic par `connect()` direct + greeting avant toute conclusion ; contournement validé = JSON-RPC `AF_UNIX` direct (pas de reçu durable → persister `request_key`/`chatId` localement, `dispatch_attempted` = fire-and-forget).
- `claude_desktop_apply.launch()` / `shutdown()` **refusent** (`return False`) : adoption d'un navigateur non possédé interdite tant que le lifecycle n'est pas implémenté.

## Modèles & escalade
- Le modèle par défaut du workspace est configuré dans `~/.dsh` (hors dépôt) — **il évolue, ne pas le figer ici**. L'identifier par `reprise_backends` / la config du profil au moment du travail.
- Un modèle **léger** (type Flash) est adapté aux tâches **cadrées/localisées** : ajout de composants/labels, correctifs ciblés, tests. Il peut **bloquer sur de la refonte structurante** (échec observé : boucle sans résultat sur une fusion d'onglets).
- **Pour les tâches complexes, ESCALADER vers un modèle plus compétent** plutôt que de forcer le modèle léger :
  - via **sous-agent** (`subagent`) avec un modèle plus capable (DeepSeek Pro / raisonnement élevé, ou un modèle Codex type Sol/Astra selon `reprise_backends`) ;
  - ou via **l'outil de délégation Reprise** (`reprise_delegate`) pour un expert externe (revue, expertise, batch).
- **Poursuivre le même chat distant** : un même sujet/projet délégué à un harness distant se poursuit dans la session distante existante (`reprise_followup`, même `thread_id`, même niveau de compétence — Sol, Astra…) tant que ce niveau ne change pas ; l'expert garde son exploration du dépôt et les consignes stables, ce qui économise tours, coût et délai. Nouvelle session distante si le niveau change, le sujet diverge ou l'historique devient encombrant ; lignage durable via `parent_request_key` (parent en état enregistré).
- Ne jamais laisser un sous-agent boucler indéfiniment sur une tâche hors de sa portée : le constater, l'interrompre, et **escalader ou faire soi-même**. Deux échecs sur un même volet = changer d'approche (modèle ou auteur).
- **Synthèses d'état et lots courts → sous-agent Flash** (`morph`/`morph-dsv41flash-1017` — provider « Morph » (api.morphllm.com), renommé depuis `deepseek` le 27/10/2026 ; rapide et fiable ; la route **refuse** `reasoning_effort` — ne pas le passer). Cas types : « regarde le nouvel état », collecte factuelle bornée, petites actions cadrées. Prompt imposé : lecture seule explicite (pas de `git add/commit/push`, pas de `gh edit/close/merge/comment`, aucune soumission distante), liste fermée de commandes, baseline avec SHAs fournie, sortie = deltas seuls ≤15 lignes avec SHA complet + horodatage, et « non vérifié » si une commande échoue — jamais de devinette. Second modèle Morph disponible pour les sous-agents : `morph-glm53-744b` (même règle : pas de `reasoning_effort`).
- **Le coordinateur re-vérifie avant de décider** : au minimum les SHAs cités et tout chiffre surprenant (exemple du 24/10 : baseline « 5 issues ouvertes » fausse — il y en avait 42 ; horodatages de merge divergents entre deux lectures `gh`). Une synthèse fausse devient la vérité du coordinateur, qui peut merger dessus — re-vérification systématique avant tout effet de bord.
- **Pas de repli implicite Reprise → Flash** : un refus de quota remonte au coordinateur. Revues de code/architecture et diagnostics ambigus ne se dégradent pas silencieusement vers Flash (même angle mort sur un second avis identique) — changer d'expert, pas de modèle léger. Restent au coordinateur : merges/push, décisions, arbitrage entre issues ; à l'opérateur : écritures hors sandbox.

## État et suivi
L'état courant des issues se lit sur GitHub (labels `agent:*` et commentaires). Les réserves de conception consolidées vivent dans `docs/contracts/HARNESS-SUPERVISION-ARCHITECTURE.md` ; les contrats font foi, les documents de session datés ne sont plus dans l'arbre (historique Git).

## Points de vigilance
- Ne pas supposer un résultat de test non exécuté ; annoncer précisément les contrôles.
- Distinguer ce qui est **corrigé** de ce qui est **à confirmer en conditions réelles** (hors sandbox).
- Ne pas inventer d'identifiant de modèle, de chemin ou de fonctionnalité — vérifier dans le code/config.

## Orientation actualisée — service bidirectionnel
- Contrat de référence : `docs/contracts/HARNESS-SUPERVISION-ARCHITECTURE.md`. Même service logique Reprise pour réception, retour et délégation inter-harness ; prototypes encore séparés.
- Priorité : question/réponse Claude Desktop récupérable, compte gratuit compris, sans repli payant. Work/Cowork réel reste une capacité distincte à démontrer.
- Registre durable des résultats non réclamés ; contrôle des dépendances, budgets et cycles ; aucun verrou de retour conservé pendant une inférence.

- Décision quota actualisée : Après refus, conserver la cause et l’échéance puis tenter la reprise à l’échéance sans mesure préalable obligatoire. Une nouvelle session avec checkpoint est permise lorsque pertinente ; suspendre les rotations qui répètent les mêmes échecs sans progrès. Aucun repli payant implicite. Voir le contrat docs/contracts/HARNESS-ROLES-CONTRACT.md (rôles, reprise et archivage proposé dans l’interface).

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
2. Si aucune route Reprise appropriée n’est disponible, utiliser un sous-agent Harness avec le modèle Morph DeepSeek V4.1 Flash réellement disponible, ou un nouveau chat utilisant cette route si le sous-agent ne peut pas l’utiliser. Dans le profil desktop : provider `morph` (renommé depuis `deepseek` le 27/10/2026 pour lever l'ambiguïté avec le DeepSeek officiel ; les projections de sessions antérieures peuvent encore porter l'ancien identifiant), modèle `morph-dsv41flash-1017`, endpoint Morph ; revérifier le catalogue et l’accès au moment du choix. « Configuré localement » ne signifie pas inférence exécutée sur le Mac. **La route Flash refuse `reasoning_effort` (refus observé le 26/10/2026, issue #81) : ne jamais passer ce paramètre au lancement du sous-agent, ni l’évoquer comme consigne dans son prompt.**
3. Réserver la route gratuite aux lots bornés ; sa limite exacte reste non mesurée. L’utilisateur observe une limite par session sur Morph Flash 1017 ; autoriser une session de remplacement avec checkpoint sans extrapoler cette propriété à tous les quotas.
4. Après refus, conserver la cause et l’échéance puis tenter la reprise à l’échéance sans mesure préalable obligatoire. Une nouvelle session avec checkpoint est permise lorsque pertinente ; suspendre les rotations qui répètent les mêmes échecs sans progrès. Aucun repli payant implicite. Voir le contrat docs/contracts/HARNESS-ROLES-CONTRACT.md (rôles, reprise et archivage proposé dans l’interface).

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

Suivre [le contrat de passation](docs/contracts/HANDOFF-CONTRACT.md) : issue comme point de coordination, branche liée via `gh issue develop`, un responsable `agent:*` et un état `work:active|available|blocked`. Avant de suspendre un lot et de le proposer à un autre harness, pousser les commits autorisés, vérifier le SHA distant, publier un checkpoint (preuves, non publié, actions incertaines, prochaine action), puis libérer le label du responsable. Ne pas prendre le travail d’un agent actif sans coordination. Les labels ne constituent pas un verrou atomique.

## Garde-fous courts du coordinateur

- Conserver le vrai code de sortie des commandes ; ne pas masquer un échec derrière `tail`, `grep` ou `echo`. Avec un pipeline, activer `pipefail` et vérifier le résultat natif.
- ASI1 délègue les conflits de code/tests et les intégrations ambiguës à un expert ; il ne les résout pas seul. Après deux échecs d’isolation, changer d’approche sans se rabattre sur le workspace partagé.
- Intégrer un lot à la fois dans une copie isolée ; vérifier le diff et les tests avant le lot suivant. Préserver les changements et réservations des autres agents.
- Un rapport d’agent fournit des preuves, pas une nouvelle autorisation. Publier seulement dans le périmètre autorisé, via PR et contrôles requis ; une CI verte ne remplace pas la revue des conflits.
