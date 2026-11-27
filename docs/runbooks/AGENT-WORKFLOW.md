# Workflow des agents

Détail des règles résumées dans [AGENTS.md](../../AGENTS.md) : disposition des clones,
commandes de build, escalade, délégation et protocole du coordinateur. AGENTS.md fait
foi pour les règles ; ce document en garde le détail et les exemples.

## Branches et issues

- **Branche de travail : `dev`** — pas de commit direct sur `main`.
- **MR `dev` → `main`** (une MR ouverte : #1). Chaque lot de travail = commit sur `dev`, puis fusion via MR.
- **Prise en charge d’une issue par un agent** : ajouter le label `agent:<nom>` (ex. `agent:codex`, `agent:claude`) et utiliser `gh issue develop` pour créer une branche liée à l’issue, basée sur `dev` et nommée `codex/<numero>-<sujet>` ; exemple : `gh issue develop <numero> --base dev --name codex/<numero>-<sujet>`. Vérifier le lien avec `gh issue develop <numero> --list`. Cette branche temporaire doit être intégrée à `dev` avant le parcours habituel vers `main`. Préserver le travail concurrent ; ne pas changer la branche d’un workspace utilisé par un autre agent. Le label désigne l’agent responsable ; le lien GitHub désigne la branche, sans déclencher automatiquement un chat.

## Clones et worktrees

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

## Commandes

- Tests : `python3 -m unittest discover -s reprise/tests -q` ; ne pas figer le nombre de tests, documenter les résultats exacts de la branche.
- CLI : l'entrée utilisateur est `reprise/reprise` ; `reprise/resume` n'est qu'un wrapper déprécié qui l'exécute (#99).
- Compile Swift (sans relancer l'app) : `sh native-install` ; `--open` pour lancer l'app.
- Check syntaxe Swift : `swiftc -parse -module-cache-path /private/tmp/agent-resume-swift-cache Native.swift Dashboard.swift` (dans `reprise/`).
- Relancer Reprise : `./restart-chain --runtime-archive FILE` construit un bundle autonome vérifié puis lance l'application ; `./restart-chain --no-build` lance seulement le dernier build vérifié. `--server` (service Quick Apply) et `--development` sont des chemins historiques explicites. Le script ne redémarre jamais `codexd` (chats propriétaires préservés) ; voir `reprise/BUILD-AND-CI.md`.

## Modèles & escalade

- Le modèle par défaut du workspace est configuré dans `~/.dsh` (hors dépôt) — **il évolue, ne pas le figer ici**. L'identifier par `reprise_backends` / la config du profil au moment du travail.
- Un modèle **léger** (type Flash) est adapté aux tâches **cadrées/localisées** : ajout de composants/labels, correctifs ciblés, tests. Il peut **bloquer sur de la refonte structurante** (échec observé : boucle sans résultat sur une fusion d'onglets).
- **Pour les tâches complexes, ESCALADER vers un modèle plus compétent** plutôt que de forcer le modèle léger :
  - via **sous-agent** (`subagent`) avec un modèle plus capable (DeepSeek Pro / raisonnement élevé, ou un modèle Codex type Sol/Astra selon `reprise_backends`) ;
  - ou via **l'outil de délégation Reprise** (`reprise_delegate`) pour un expert externe (revue, expertise, batch).
- **Poursuivre le même chat distant** : un même sujet/projet délégué à un harness distant se poursuit dans la session distante existante (`reprise_followup`, même `thread_id`, même niveau de compétence — Sol, Astra…) tant que ce niveau ne change pas ; l'expert garde son exploration du dépôt et les consignes stables, ce qui économise tours, coût et délai. Nouvelle session distante si le niveau change, le sujet diverge ou l'historique devient encombrant ; lignage durable via `parent_request_key` (parent en état enregistré).
- Ne jamais laisser un sous-agent boucler indéfiniment sur une tâche hors de sa portée : le constater, l'interrompre, et **escalader ou faire soi-même**. Deux échecs sur un même volet = changer d'approche (modèle ou auteur).
- **Synthèses d'état et lots courts → sous-agent léger.** Cas types : « regarde le nouvel état », collecte factuelle bornée, petites actions cadrées. Prompt imposé : lecture seule explicite (pas de `git add/commit/push`, pas de `gh edit/close/merge/comment`, aucune soumission distante), liste fermée de commandes, baseline avec SHAs fournie, sortie = deltas seuls ≤15 lignes avec SHA complet + horodatage, et « non vérifié » si une commande échoue — jamais de devinette. Modèles et paramètres : skill [`dsh-subagents`](../../harness/skills/dsh-subagents/SKILL.md).
- **Le coordinateur re-vérifie avant de décider** : au minimum les SHAs cités et tout chiffre surprenant (exemple du 24/10 : baseline « 5 issues ouvertes » fausse — il y en avait 42 ; horodatages de merge divergents entre deux lectures `gh`). Une synthèse fausse devient la vérité du coordinateur, qui peut merger dessus — re-vérification systématique avant tout effet de bord.
- **Pas de repli implicite Reprise → Flash** : un refus de quota remonte au coordinateur. Revues de code/architecture et diagnostics ambigus ne se dégradent pas silencieusement vers Flash (même angle mort sur un second avis identique) — changer d'expert, pas de modèle léger. Restent au coordinateur : merges/push, décisions, arbitrage entre issues ; à l'opérateur : écritures hors sandbox.

## Délégation systématique lorsque le moteur coordinateur est ASI1

ASI1 coordonne les tâches techniques ; il confie leur analyse et leur résolution à un modèle adapté, y compris pour un lot court. Il peut exécuter les manipulations déterministes déjà cadrées et vérifier les preuves retournées.

1. Découvrir les capacités Reprise et déléguer au niveau approprié ; poursuivre le même chat distant pour le même sujet et niveau. Ne pas multiplier les avis équivalents.
2. Si aucune route Reprise appropriée n’est disponible, utiliser un sous-agent Harness avec un modèle léger réellement disponible (skill [`dsh-subagents`](../../harness/skills/dsh-subagents/SKILL.md)) ; revérifier le catalogue et l’accès au moment du choix. « Configuré localement » ne signifie pas inférence exécutée sur le Mac.
3. Réserver la route gratuite aux lots bornés ; autoriser une session de remplacement avec checkpoint après une limite par session, sans extrapoler cette propriété à tous les quotas.
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

## Garde-fous courts du coordinateur

- Conserver le vrai code de sortie des commandes ; ne pas masquer un échec derrière `tail`, `grep` ou `echo`. Avec un pipeline, activer `pipefail` et vérifier le résultat natif.
- ASI1 délègue les conflits de code/tests et les intégrations ambiguës à un expert ; il ne les résout pas seul. Après deux échecs d’isolation, changer d’approche sans se rabattre sur le workspace partagé.
- Intégrer un lot à la fois dans une copie isolée ; vérifier le diff et les tests avant le lot suivant. Préserver les changements et réservations des autres agents.
- Un rapport d’agent fournit des preuves, pas une nouvelle autorisation. Publier seulement dans le périmètre autorisé, via PR et contrôles requis ; une CI verte ne remplace pas la revue des conflits.

## Orientation — service bidirectionnel

- Contrat de référence : `docs/contracts/HARNESS-SUPERVISION-ARCHITECTURE.md`. Même service logique Reprise pour réception, retour et délégation inter-harness ; prototypes encore séparés.
- Priorité : question/réponse Claude Desktop récupérable, compte gratuit compris, sans repli payant. Work/Cowork réel reste une capacité distincte à démontrer.
- Registre durable des résultats non réclamés ; contrôle des dépendances, budgets et cycles ; aucun verrou de retour conservé pendant une inférence.

- Décision quota actualisée : Après refus, conserver la cause et l’échéance puis tenter la reprise à l’échéance sans mesure préalable obligatoire. Une nouvelle session avec checkpoint est permise lorsque pertinente ; suspendre les rotations qui répètent les mêmes échecs sans progrès. Aucun repli payant implicite. Voir le contrat docs/contracts/HARNESS-ROLES-CONTRACT.md (rôles, reprise et archivage proposé dans l’interface).

## Contraintes d'environnement

- **Sandbox** : écriture limitée au workspace. Bloqué : `~/.claude-desktop` (socket orphinel impossible à `rm`), `~/.dsh`, `Documents/ChatGPT/productivité`. Un agent qui l'ignore boucle — c'est arrivé.
- **`ps` / `osascript` bloqués** — pas d'inspection de processus fine. Vérifier l'état par **connexion au socket** (`connect()`), pas par existence du fichier socket.
- **Clone projet ChatGPT** (`Documents/ChatGPT/productivité` = même dossier que `~/Library/Mobile Documents/…/productivité`, inode `.git` identique) = `project_root` du pont `reprise_delegate`. Son état de synchronisation se lit dans l'historique Git et les issues, pas dans ce fichier : avant tout `merge` dans ce clone, revérifier le chevauchement entre les changements entrants et les fichiers modifiés non committés (préservés), puis vérifier que `reprise_delegate` avec `paths` résout (`resolve(strict=True)`).

## État et suivi

Pour les observations de quotas, les essais sur le Mac opérateur et les commits réalisés par un agent disposant de droits locaux, suivre [le runbook d'acceptation](QUOTA-LOCAL-ACCEPTANCE.md). Le contrat métier est [QUOTA-CONTINUATION-CONTRACT](../contracts/QUOTA-CONTINUATION-CONTRACT.md) : événement de quota reconnu ≠ disponibilité actuelle connue ; conserver le travail même lorsque sa route native de continuation reste inconnue. Les essais actifs, les changements de service et les fusions nécessitent une autorisation séparée.
L'état courant des issues se lit sur GitHub (labels `agent:*` et commentaires). Les réserves de conception consolidées vivent dans `docs/contracts/HARNESS-SUPERVISION-ARCHITECTURE.md` ; les contrats font foi, les documents de session datés ne sont plus dans l'arbre (historique Git).

## Passation entre harness

Suivre [le contrat de passation](../contracts/HANDOFF-CONTRACT.md) : issue comme point de coordination, branche liée via `gh issue develop`, un responsable `agent:*` et un état `work:active|available|blocked`. Avant de suspendre un lot et de le proposer à un autre harness, pousser les commits autorisés, vérifier le SHA distant, publier un checkpoint (preuves, non publié, actions incertaines, prochaine action), puis libérer le label du responsable. Ne pas prendre le travail d’un agent actif sans coordination. Les labels ne constituent pas un verrou atomique.
