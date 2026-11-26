# Contrat services Reprise « juge de paix » et « gestion du dépôt »

- **Date** : 27 novembre 2026
- **Auteur** : ASI1 (agent AutoClaw) — conception ; aucun code dans ce contrat
- **Statut** : conception soumise à révision (questions opérateur en §10) ; révisable par l'opérateur
- **Version** : 1.2 — décisions opérateur §10 (26/11 : les six défauts sont adoptés, rétention des preuves fixée à 10 jours) ; v1.1 = intégration dans l'interface graphique (§8 : activité en cours, santé du dépôt, journal) et maquette associée ; v1.0 = conception des deux services
- **Base** : dev `a81c53b` ; branche `autoclaw/services-juge-depot-20261127`
- **Précédent directeur** : issue #270 (service gh niveau Reprise) — « soit il centralise tout, soit il délivre un service à tous les harness dans leur workspace » ; décision tenue le 26/11 : **les deux combinés** (cœur central dans `reprise/`, livraison par shims `bin/` par harness). Le présent contrat applique ce schéma à deux nouveaux services.

## 0. Objet

L'app Reprise (l'orchestrateur Mac) fournit à **tous les harness** (AutoClaw, Codex, Claude Desktop, ChatGPT, Meta AI ; IronClaw et équivalents distants déclarés non admissibles au 27/11) deux services locaux communs, à la place des pratiques ad hoc actuelles :

1. **Service juge de paix** — exécution centralisée du vérificateur indépendant `reprise/ci_test.py` (`--parallel 4` sous le runtime 3.13 épinglé) comme **preuve de vérification commune** de l'étage E6 : droits, files d'attente, verrous, traces, refus nommés.
2. **Service dépôt** — gestion du dépôt Git et du clone canonique `source/` : création/réservation/nettoyage des worktrees par harness, conventions de branches et de push (refspec explicite), alignement dev↔origin (fetch, merge-base, diff trois-points avant intégration), politique de fusion « Intégration X » et garde du FF de dev sur go opérateur explicite.

L'état, les opérations en cours et les actions de ces deux services sont exposés à l'opérateur dans l'interface graphique de l'app (§8) — un service invisible est un service inutilisé.

**Hors périmètre** : le contenu des tests et leur écriture (SOD, contrat E4) ; les verdicts E6 (le juge produit des faits, jamais un verdict ; E6 seul écrivain de `delegation_verdicts` — spec WORKFLOW-REPRISE v1.2) ; les worktrees étrangers `codex/*` et les zones des autres agents (jamais touchés, cf. §5.3) ; la CI distante (les shards CI restent la référence avant fusion, USAGE.md) ; l'interface MCP/`reprise_delegate` (extension ultérieure, §9) ; tout élément des interdits E4 (VERSION/CHANGELOG, configuration CI, `ci_test.py`).

## 1. Principes (hérités de reprise_gh.py et des gardes E4)

1. **Fail-closed** : acteur inconnu = refus nommé ; politique explicite par fichier JSON (`juge-policy.json`, `depot-policy.json` dans `~/Library/Application Support/Reprise/`), jamais d'auto-élévation (précédent : `reprise_gh init` sans auto-élévation, test hermétique 4b210cc).
2. **Preuve par reçu** : toute exécution produit un reçu JSON digesté (sha256), recomputable, stocké **hors arbre Git** ; un log ou un reçu ne se commit jamais (USAGE.md).
3. **Refus nommés** : codes `MAJUSCULES_SNAKE` en JSON, aucune prose libre (format des gardes E4) ; un refus est une donnée, jamais un message d'excuse.
4. **Anti-TOCTOU** : tout consentement (go) est lié au couple (sha du contenu, digest de la preuve) ; un écart ⇒ refus, jamais de push silencieux (spec WORKFLOW-REPRISE v1.2).
5. **SOD** : auteur ≠ vérificateur ≠ intégrateur ; les services sont des outils **neutres** qui ne portent aucun rôle ; chaque reçu enregistre son demandeur, ce qui permet de vérifier a posteriori que la preuve d'acceptation n'a pas été demandée par l'auteur du lot.
6. **Zéro dépendance** : stdlib uniquement, compatible Python 3.9 (contrainte daemon metaaid) ; les sous-processus de test tournent sous l'interpréteur épinglé que `ci_test.py` résout lui-même (3.13, `pinned_interpreter()` : archive `~/Library/Caches/Reprise/python` vérifiée par sha256, cache local par empreinte, verrou `.extract.lock`).
7. **Coordination par origin** : les clones de travail restent privés ; origin fait foi ; le clone racine `source/` reste le seul clone de déploiement opérateur (AGENTS.md) — les services n'y écrivent que par les gardes de §6-§7.
8. **État hors arbre** : tout l'état des deux services vit dans le state dir Reprise (précédent reprise_gh) ; l'arbre Git ne contient jamais de reçus, logs ou inventaires de poste.
9. **Interface = surface de contrôle** : chaque service expose son état, son activité et ses actions dans l'app Reprise (§8) ; l'interface lit les reçus, le registre, le flux d'activité et les audits des services — jamais une source de vérité parallèle (les vues ne doublent pas plus l'état que les rôles ne doublent les routes) ; les actes sensibles y passent par confirmation unique avec go tracé : mêmes gardes en CLI et en GUI.
10. **Grand public d'abord** : libellés sans jargon, preuves cliquables derrière chaque badge, refus nommés traduits en phrase d'action ; rien n'est déduit d'un état non prouvé (motif maquette #118).
11. **Diagnostic ≠ verrou** : les indicateurs de suivi (santé du dépôt §8.5) éclairent l'opérateur sans jamais bloquer un service — seule une garde explicite (§2.5, §4.2, §7) refuse.

## 2. Service juge de paix

### 2.1 Objet et non-objets

- Centraliser l'exécution de `reprise/ci_test.py --parallel N` (N = 4 par défaut, N ∈ {1, 2, 4, 8}).
- Produire pour chaque exécution une **évidence archivée** : log complet + reçu digesté, liés à l'état exact de l'arbre testé.
- Sérialiser et tracer : verrou par arbre, file d'attente, concurrence bornée, audit JSONL.

**Ce que le juge n'est pas** : il ne juge pas (aucune interprétation du résultat) ; il ne modifie jamais le code ni les tests (SOD) ; il n'écrit jamais dans `delegation_verdicts` ; il ne remplace ni la CI avant fusion (USAGE.md) ni la relecture de l'auteur ; il n'est pas une étape de routage (aucun modèle facturé — les rôles restent des chaînes de routes, le juge est un outil mécanique local).

### 2.2 Interface

- Module : `reprise/juge_paix.py` (stdlib, 3.9) ; CLI : `python3 -m reprise.juge_paix` ; shim livré par harness : `bin/reprise-juge --actor <acteur> …` (précédent `bin/reprise-gh`).
- Sous-commandes :
  - `run --worktree <chemin> [--parallel 4]` — met en file, attend, retourne le reçu JSON sur stdout ;
  - `status --run-id <id>` / `list [--acteur X] [--dernier N]` — lecture des reçus ;
  - `init --acteurs a,b,c` — écrit la politique initiale ; un acteur non listé exige `--consent` tracé (précédent reprise_gh, jamais d'auto-élévation).
- `--actor` obligatoire et présent dans la politique, sinon `JUGE_ACTEUR_INCONNU`.

### 2.3 Évidence et reçus

Chaque exécution produit, dans `juge/runs/<run_id>/` :

- **run_id** = digest déterministe de `(arbre, suite, paramètres)` :
  - `sha_arbre` = `git -C <worktree> rev-parse HEAD` ;
  - `digest_modifs` = sha256 de `git status --porcelain` (hors `.runtime-py/` et fichiers ignorés) — un arbre sale est admis mais marqué `arbre_sale=true` ;
  - `digest_suite` = sha256 du contenu de `reprise/tests/` + `reprise/ci_test.py` du worktree ;
  - `parallel` = N.
- **Reçu** `receipt.json` : run_id, demandeur, realpath du worktree, sha_arbre, arbre_sale, digest_suite, parallel, interpréteur résolu (chemin + sha256), début/fin/durée, code de sortie global et par shard, état (`fini | expire | refuse`), sha256 du log, digest du reçu.
- **Log archivé** `test-parallel.log` : copie du log de `<worktree>/reprise/.runtime-py/test-parallel.log` réalisée **avant libération du verrou**, car ci_test écrase ce fichier à chaque exécution (l.141-144) — la copie rend l'évidence durable alors que le log du worktree reste volatile.
- **Pas de `--reuse-log`, pas de `--force`** : le juge relance toujours une suite fraîche (une preuve est toujours neuve) ; la réutilisation de log reste un outil ad hoc de développement, hors preuve.
- **Rejeu idempotent** : une demande identique (même run_id, preuve `fini` existante) retourne le reçu existant sans relancer (garde E4-0).

**Éligibilité E6** : un reçu sert de preuve d'acceptation E6 seulement si `arbre_sale=false`, `etat=fini` et code de sortie 0. Un reçu d'arbre sale sert uniquement de retour de développement (boucle rouge/vert), jamais d'acceptation.

### 2.4 Files d'attente, verrous, budgets

- **Verrou par worktree** : une seule exécution à la fois sur un même arbre (les exécutions concurrentes s'écraseraient mutuellement `test-parallel.log` et partageraient le cache d'interpréteur) ; les demandes suivantes entrent en file (état `attente`, position, TTL 600 s).
- **Concurrence machine** : 2 exécutions simultanées par défaut (chaque run lance N shards, soit 4 suites complètes en parallèle) ; au-delà, `JUGE_FILE_PLEINE` (le demandeur réessaie plus tard) — pas de file sans fin.
- **Équité par acteur** : max 1 exécution active par acteur ; sélection FIFO pondérée (tour de rôle entre acteurs, pas de monopolisation).
- **Budget temps** : 1800 s par défaut, plafond 3600 s ; dépassement ⇒ état `expire` : interruption propre (kill du groupe de processus), reçu conservé, **non éligible comme preuve** (incertain — un délai dépassé n'est ni un échec ni une réussite de tests) ; aucune relance automatique.
- **Environnement contrôlé** : le juge fixe lui-même `REPRISE_TEST_PYTHON`/`REPRISE_TEST_RUNTIME_ARCHIVE` (override explicite ci_test) et le HOME jetable ; un héritage d'environnement entrant est ignoré et journalisé `JUGE_ENV_REFUSE` (la preuve doit être reproductible).

### 2.5 Politique et refus nommés

`juge-policy.json` :

```json
{"actif": true,
 "acteurs": {"autoclaw": {}, "codex": {}, "claude": {}, "chatgpt": {}, "metaai": {}, "harness": {}},
 "concurrence_machine": 2, "file_max": 8, "delai_secondes": 1800, "retention_jours": 10}
```

Refus : `JUGE_DESACTIVE`, `JUGE_ACTEUR_INCONNU`, `JUGE_POLITIQUE_ABSENTE` (fail-closed), `JUGE_ARBRE_INVALIDE` (pas un worktree Git ou pas de `reprise/ci_test.py`), `JUGE_FILE_PLEINE`, `JUGE_DELAI_ATTENTE` (TTL de file épuisé), `JUGE_DELAI_DEPASSE` (budget de run), `JUGE_ENV_REFUSE`, `JUGE_LOG_ABSENT` (log introuvable à l'archivage ⇒ reçu `fini` refusé, état incertain).

### 2.6 Audit

`juge-audit.jsonl` (append-only, state dir) : `{ts, acteur, run_id, worktree, sha_arbre, arbre_sale, code, digest_log, duree_s, refus?}` — sans secret, sans dump d'environnement. Rétention : reçus et logs conservés `retention_jours` (10 par défaut — décision opérateur 26/11), audits intacts.

### 2.7 Cas limites

- **Flake baseline** : l'échec de `test_apple_pilot_ui` hors CI (sandbox swiftc) est un bruit documenté (USAGE.md). Le juge n'édulcore jamais un résultat : le reçu porte le code de sortie réel et le log complet ; le champ `notes` rappelle seulement le flake documenté. L'interprétation reste un travail E6, jamais du juge.
- **Suite modifiée dans le worktree** : le juge n'interdit pas de modifier les tests du worktree (l'auteur les écrit — la SOD les sépare), mais toute preuve cite `digest_suite`, ce qui rend toute modification vérifiable a posteriori ; `ci_test.py` lui-même reste sous les interdits E4.
- **HOME jetable** : inchangé — ci_test s'exécute déjà avec un HOME temporaire ; les tests trousseau mockent `_run_credential_fill` (leçon ci_test 26/11).

## 3. Service dépôt

### 3.1 Objet et interface

Module `reprise/depot_service.py` (stdlib, 3.9), CLI `python3 -m reprise.depot_service`, shim `bin/reprise-depot --actor <acteur> …`.

Le service opère sur le **clone qui l'héberge** (clone racine `source/` ou clone de travail d'un agent, chaque clone gardant son propre `.git` — AGENTS.md) ; les zones ci-dessous s'interprètent relativement à la racine du projet ; la coordination inter-clones passe exclusivement par `origin`.

Sous-commandes :

- `worktree-claim --sujet <slug> [--base <ref>] [--branche <nom>]` — création + réservation (§5) ;
- `worktree-enregistrer --chemin <path>` — déclaration d'un worktree auto-géré (§5.2) ;
- `worktree-liste [--tout]` / `worktree-libere --chemin <path>` / `worktree-gc [--applique]` ;
- `branche-cree <nom>` — création de branche validée (namespace + base, §4) ;
- `push --branche <nom>` — push à refspec explicite obligatoire (§4.2) ;
- `aligne --branche dev` — diagnostic fetch + merge-base + diff trois-points, lecture seule (§6) ;
- `integre --branches <b1,b2,…> --go <trace>` — intégration « Intégration X » sur dev avec go tracé (§7) ;
- `init --acteurs …` — politique initiale (sans auto-élévation).

### 3.2 Registre et zones

- **Registre** : `depot/worktrees.json` (state dir) : `[{chemin, acteur, branche, sha_base, objet, cree_le, statut: actif|libere, reserve_par, reserve_le}]`.
- **Zones** (dérivées du layout AGENTS.md, configurables dans la politique) : `workspaces/autoclaw/**` → autoclaw, `workspaces/codex/**` → codex, `workspaces/harness/**` → harness, `workspaces/claude/**` → claude ; tout worktree hors de ces zones (ex. `tmp/rel-*` à la racine du clone) est classé `operateur` (hors zone agents).
- **Premier scan de réconciliation en lecture seule** : les worktrees existants sont enregistrés avec leur propriétaire déduit de la zone ; rien n'est modifié ni supprimé ; les anomalies (hors zone, détachés) sont listées dans le rapport.

## 4. Conventions de branches et de push

### 4.1 Branches

- **Namespace par acteur** (politique `depot-policy.json`) : `autoclaw` → `autoclaw/*`, Codex → `codex/*`, etc. Les deux formats cohabitent : `<agent>/<n>-<sujet>` pour une branche d'issue (AGENTS.md, `gh issue develop`), `<agent>/<sujet>-<AAAAMMJJ>` pour un lot sans issue. Sujet non vide, sans espace, ASCII.
- Un acteur ne peut créer/pousser que dans son namespace : `DEPOT_NS_INTERDIT`. La suppression d'une branche `codex/*` par un non-codex : `DEPOT_BRANCHE_ETRANGERE`.
- `dev` et `main` ne sont pas des branches de travail : `branche-cree dev|main` ⇒ `DEPOT_DEV_RESERVE` (main : MR #1 seule voie, AGENTS.md).

### 4.2 Push : refspec explicite obligatoire

- **Piège éliminé par construction** : une branche créée depuis `origin/dev` porte `branch.merge=refs/heads/dev` — un `git push` nu pousserait vers `dev`. `depot push` exécute toujours `git push -u origin <br>:refs/heads/<br>` ; le service n'émet aucun autre formulaire.
- **Garde de preuve** : pousser une branche destinée à l'intégration exige un **reçu juge frais** sur le tip poussé (arbre propre, code 0, digest du tip) : sinon `DEPOT_PUSH_SANS_PREUVE`. Politique par acteur : `exigee|avertie` (défaut : exigée).
- **Cibles protégées** : `push --branche dev|main` ⇒ `DEPOT_DEV_RESERVE` (l'intégration à dev passe par §7 ; main par MR #1). Force-push ⇒ `DEPOT_FORCE_INTERDIT` (jamais, aucune exception côté service).
- Hygiène d'avant-push inchangée (USAGE.md) : jamais de reçu, log, checkpoint de session, inventaire de poste dans l'arbre ; `ci_test.py` et configuration CI hors périmètre des agents.

## 5. Décision tranchée : comment les harness obtiennent leurs worktrees

**Réponse : service réservateur par défaut + auto-gestion bornée déclarée** — le même « les deux combinés » que #270.

1. **Voie normative = le service.** Un harness demande son worktree : `worktree-claim` crée `workspaces/<acteur>/tmp/<sujet>-<AAAAMMJJ>/` (convention AGENTS.md : jamais dans un autre worktree d'agent, jamais `/private/tmp`), crée la branche demandée depuis `--base` (défaut `origin/dev`, après fetch), l'enregistre au registre et pose la réservation (acteur, chemin, branche, sha_base, objet, horodatage). La réservation est un verrou d'intention : un autre acteur voit `reservé_par=<acteur>`. Collisions : chemin existant ⇒ `DEPOT_EMPLACEMENT_PRIS` ; branche existante ⇒ `DEPOT_BRANCHE_EXISTANTE`.
2. **Auto-gestion tolérée mais déclarée.** Un acteur peut créer un worktree lui-même **uniquement dans sa zone**, à la condition de le déclarer : `worktree-enregistrer` vérifie le préfixe de zone (`DEPOT_ZONE_INTERDITE` sinon) et l'ajoute au registre. Un worktree auto-géré **non déclaré** reste toléré (pas de police rétroactive) mais il est signalé `non_enregistre` par `worktree-liste --tout` et **ne peut pas** servir de base à une intégration (§7) ni produire une preuve E6 liée à un propriétaire.
3. **Étrangers intouchables (cas codex/*)** — l'intangibilité devient machine-lisible au lieu d'être sociale. Un worktree est **étranger** au demandeur si (a) son propriétaire de registre est un autre acteur, (b) il se trouve sous la zone d'un autre acteur, ou (c) sa branche porte un namespace étranger. Toute opération mutante sur un worktree étranger est refusée : `DEPOT_ARBRE_ETRANGER` (remove, checkout, prune, reset, rebase) ; les lectures (`worktree-liste --tout`, `aligne`) les listent avec la mention `etranger`.
4. **Nettoyage.** `worktree-libere` (propriétaire ou opérateur) : refuse si modifications non commitées (`DEPOT_SALE_REFUSE`) ; `--sec` sauvegarde d'abord un patch de récupération dans `workspaces/<acteur>/tmp/sauvegardes/` (leçon purge 24/11 : patch-recovery avant toute suppression) ; refuse si la branche n'est ni poussée ni fusionnée (`DEPOT_BRANCHE_NON_POUSSSEE`, idem `--sec`) ; puis `git worktree remove` + statut `libere` au registre. `worktree-gc` **propose** les réservations périmées (TTL 14 jours) à l'arbre propre ; `--applique` n'agit que sur les worktrees du demandeur lui-même — jamais les étrangers, jamais les hors zone opérateur.

## 6. Alignement dev ↔ origin (prérequis d'intégration)

`aligne --branche dev` (lecture seule) : `git fetch origin`, puis rapport JSON + lisible :

- sha local, sha origin, merge-base, avance/recul de chaque côté ;
- **diff trois-points** `git diff $(git merge-base dev origin/dev)..origin/dev --stat` + liste des commits ;
- classification nommée : `ALIGNE` (identiques), `RETARD` (local strictement derrière — FF possible, informatif), `DIVERGENCE` (avance des deux côtés — décision d'intégration requise), `AVANCE_LOCAL` (dev local avancé sans pousser — jamais poussé automatiquement, cas ffff1e6), `APPORT_VIDE` (diff trois-points vide = commits déjà appliqués, cas 144).
- Le rapport est horodaté avec les sha origin fetchés et persisté (`depot/aligne-dev.json`) ; `integre` exige un rapport **frais** et re-vérifie `origin/dev` juste avant push (§7).
- Leçons encodées : vérifier le merge-base **avant** de conclure à une divergence ; un dev local qui a avancé sans pousser n'est pas une divergence ; l'apport vide se déclare, le commit redondant s'abandonne.

## 7. Intégration « Intégration X » et garde FF dev

Norme en vigueur depuis le 27/11 (directives opérateur) : les intégrations à `dev` se font par **commits de fusion à 2 parents « Intégration <branche> (<résumé>) »**, plus de FF linéaire systématique. L'intégration reste un **acte opérateur** (go explicite, E4 Q11) — le service prépare, vérifie et exécute sur go tracé, jamais de sa propre initiative.

`integre --branches <b1[,b2,…]> --go "<qui, quand, canal>"` :

1. **Contrôles d'entrée** : politique active ; go tracé obligatoire (`DEPOT_GO_ABSENT`) ; rapport `aligne` frais (`DEPOT_ALIGNE_REQUIS`) ; pour chaque branche : reçu juge frais sur le tip final, arbre propre, code 0, **lié au sha du tip** (sinon `DEPOT_PREUVE_PERIMEE`) ; namespace conforme.
2. **Worktree d'intégration** : `worktree-claim` éphémère `workspaces/autoclaw/tmp/integre-<AAAAMMJJ>`, jamais le checkout racine `source/` ni le checkout d'un autre acteur (pratique 25-27/11 : intégration par refspec depuis un worktree dédié, sans toucher `source/`).
3. **Fusion** : `git merge --no-ff <br>` avec message canonique « Intégration <branche> (<résumé>) » — **un commit d'intégration par branche** (traçabilité par lot, précédent a8d2d99/a306600/a81c53b) ; résumé = objet du registre ou fourni par le go. Conflit ⇒ `git merge --abort`, état `DEPOT_CONFLIT`, retour au demandeur : le service ne résout jamais un conflit (SOD : la résolution appartient à l'auteur ou à l'opérateur, jamais à l'intégrateur mécanique).
4. **Vérification sur la pointe intégrée** : juge de paix sur la pointe fusionnée **avant push** (pratique du 27/11 : « juge de paix 4 shards VERT sur la pointe intégrée AVANT push »). Rouge ⇒ pas de push, rapport au demandeur, retour en E5 (le rouge naît en E6, jamais patché par l'intégrateur).
5. **Push de dev** : uniquement si le go reste **lié au couple (sha de la pointe intégrée, digest du reçu juge)** : si la pointe a bougé depuis le go (nouvelle intégration arrivée entre-temps) ⇒ `DEPOT_CONSENT_EXPIRE`, re-boucle depuis 1 (anti-TOCTOU, WORKFLOW-REPRISE v1.2). Push avec refspec explicite `dev:refs/heads/dev` ; course origin (non-fast-forward inattendu après re-fetch) ⇒ `DEPOT_COURSE_ORIGIN`, jamais de force.
6. **Clôture** : reçu d'intégration (sha avant/après, **diffstat réel — leçon 743fadc : toujours vérifier `--stat` vs message** avant intégration), registre mis à jour, worktree d'intégration libéré, suggestion de commentaire d'issue via reprise_gh (séparé).

**Exception FF** : un FF de dev (au lieu d'un commit de fusion) reste possible uniquement si (a) le go opérateur le demande explicitement, (b) `aligne` montre `RETARD` pur (dev local ancêtre strict d'origin/dev), (c) la pointe de branche est fille directe de dev. Sinon `DEPOT_FF_REFUSE` (non linéaire). Le service ne fait jamais de FF sans go tracé.

## 8. Intégration dans l'interface graphique (app Reprise)

**Principe d'abord** : un service invisible est un service inutilisé. Le juge de paix et le service dépôt exposent dans l'app leur état réel, leurs opérations en cours et leurs actions cadrées ; l'interface lit les reçus, le registre, le flux d'activité et les audits des services (aucune source de vérité parallèle) ; les actes sensibles passent par une confirmation unique tracée — mêmes gardes en CLI et en GUI ; libellés grand public, preuves cliquables, refus traduits en phrases d'action.

### 8.1 Placement

- Écran « Services » en **cinquième entrée du switcher** de l'onglet Connexions (après « Pilote Apple » ; précédent maquette #118 : switcher générique, une seule fenêtre, sidebar inchangée).
- Bande d'activité en cours en tête d'écran, deux cartes principales (juge de paix, dépôt), carte de santé du dépôt, journal commun en bas ; tout est consultable sur place, jamais perdu dans un écran séparé (détails ▸).

### 8.2 Carte « Juge de paix »

- **En-tête** : état du service (`✓ actif` / `✗ désactivé` avec le refus nommé traduit), interpréteur épinglé (`3.13 vérifié` + digest court de l'archive), dernière vérification.
- **File d'attente en direct** : « N en cours · M en attente » + chips par acteur — les données viennent du verrou et de la file du service, jamais d'un estimé.
- **Derniers reçus** : badge d'état (vert `✓ vérifié` = fini, code 0, arbre propre ; rouge `✗ échec` ; gris `incertain` = expire/refus), acteur, branche + sha court, durée, shards, horodatage heure locale 24 h ; clic ⇒ détail du reçu (run_id, digest du reçu, digest du log, bouton « Ouvrir le log »).
- **Actions** : « Vérifier maintenant » (sélecteur de worktree parmi les arbres du registre du service dépôt ; désactivé avec raison nommée si la file est pleine ou le service désactivé) ; « Journal du juge » ; `Avancé ▸` (politique : acteurs, concurrence, budget, rétention ; **coupe-circuit** : désactivation immédiate, opérateur seul, tracée).
- **Refus affichés** : carte de refus fail-closed (motif `JUGE_*` + phrase d'action), même langage que la carte refus de #118.

### 8.3 Carte « Dépôt »

- **En-tête** : état dev↔origin (`✓ aligné` / `⏱ retard` / `⚠ divergence` / `⚠ avance locale` / `apport vide` + dernière synchronisation) ; bouton « Synchroniser » (`aligne`, lecture seule).
- **Worktrees** : liste (chemin court, acteur, branche, âge, statut de réservation) ; les **étrangers** grisés avec cadenas « étranger — intouchable » (aucune action, lecture seule) ; les hors zone taggés « hors zone » (propriété opérateur).
- **Branches prêtes à intégrer** : pour chaque branche poussée non intégrée, badge de preuve (`✓ preuve prête` si un reçu juge frais couvre le tip / `⚠ preuve à rafraîchir` sinon) ; le bouton d'intégration reste désactivé tant que la preuve manque — raison nommée affichée.
- **Action « Intégrer sur dev »** : panneau de **confirmation unique** (motif #118 lot D) : branches, résumés « Intégration X », pointe avant, digest de la preuve juge, bouton unique « Confirmer l'intégration » qui **trace le go de l'opérateur** (qui, quand) ; après exécution, ligne `.applied` (sha après, statut du push). Si la pointe a bougé entre l'affichage et le clic, l'état frais est ré-affiché avant toute écriture (anti-TOCTOU visible).
- **Garde FF** : l'option « fusion rapide (ff) » n'apparaît que si les trois conditions du §7 sont réunies ; sinon elle est absente, et son absence est expliquée dans les détails ▸.
- `Avancé ▸` : politique (namespaces, zones, TTL), proposition de nettoyage (`worktree-gc`) avec bouton « Nettoyer » par élément — jamais les étrangers, jamais les hors zone.

### 8.4 Activité en cours (tests et dépôt, en direct)

- Une bande **« Activité en cours »** en tête d'écran : toute opération en cours des deux services, **synthétique et esthétique** — chaque opération est une carte compacte avec progression, durée écoulée et dernier événement ; jamais un mur de log (le log complet reste une action, pas un état par défaut).
- **Vérification en cours** : progression par shard (chips `1 ✓ · 2 ✓ · 3 ▸ · 4 ○`), durée écoulée, **erreurs déjà visibles comptées et consultables sans attendre la fin** (les 3 dernières lignes d'échec en monospace, clic ⇒ log complet à la position) — un échec n'attend pas la fin du run pour être vu.
- **Opération dépôt en cours** (aligne, fusion « Intégration X », push, claim, release, gc) : séquence d'étapes horodatées (`aligne ✓ → preuve ✓ → fusion ▸ → juge ○ → push ○`) ; un conflit ou un refus s'affiche dès qu'il survient, en carte de refus.
- **Flux d'activité** : les services publient un `activity.json` atomique dans le state dir (file d'attente, opérations actives, derniers événements, erreurs courantes) que l'interface lit en continu — pas de copie d'état, pas d'estimé.
- Interruption : aucune opération en cours n'est annulable par un bouton muet ; seul le coupe-circuit opérateur (tracé) peut arrêter un run (état `expire`) — la vue le rappelle.

### 8.5 Santé du dépôt (structure seule)

- Une carte **« Santé du dépôt »** avec un indicateur global (`✓ bonne tenue` / `⚠ à surveiller` / `✗ à corriger`) décomposé en contrôles **déterministes, structurels, sans jamais lire le code** : seuls Git et l'inventaire de l'arbre sont consultés :
  1. **dev ↔ origin** : aligné, retard simple, divergence, avance locale, apport vide (§6) ;
  2. **worktrees** : arbres propres, TTL respectés, aucun hors zone récent, étrangers intacts ;
  3. **branches** : aucune branche fusionnée non supprimée sur origin, aucune branche de sujet sans activité ni push depuis plus de N jours ;
  4. **preuves** : chaque branche poussée destinée à l'intégration porte une preuve juge à jour ;
  5. **hygiène de l'arbre** : aucun fichier interdit dans l'arbre (reçus, logs, checkpoints de session — USAGE.md) ;
  6. **intégrations** : commits « Intégration X » bien formés (message canonique, 2 parents), historique sain ;
  7. **résidus** : branches distantes sans worktree ni issue liée.
- Chaque contrôle affiche : badge, une ligne de preuve et une phrase « pour améliorer » ; **un score global sans détail est interdit**.
- La santé est un **diagnostic, jamais un verrou** (principe 11) : aucun refus de service ne découle du score ; calcul sur demande (« Recalculer ») avec cache horodaté.

### 8.6 Journal commun

Section bas d'écran : audits juge + dépôt fusionnés en ordre chronologique (`juge-audit.jsonl` + `depot-audit.jsonl`), filtre par acteur et par type ; chaque ligne ouvre son reçu ou son log digesté. C'est la surface des « traces » exigée par ce contrat : rien de l'activité des services n'est muet.

### 8.7 Règles d'implémentation de l'interface

1. **Une seule source de vérité** : les vues SwiftUI appellent les modules des services (JSON sur stdout, `activity.json`, mêmes politiques) — jamais une copie d'état dans l'app.
2. **Paramètres d'état réel, jamais de prose** (motif #118) : toute proposition d'action s'affiche en lignes structurées (acteur, arbre, preuve, budget), pas en texte rédigé.
3. **Confirmation unique par acte sensible** (intégrer sur dev, ff, gc, coupe-circuit) : admission ≠ exécution, le clic trace le go ; un écran affiché sans clic ne prouve rien.
4. **Refus traduits** : chaque refus `JUGE_*`/`DEPOT_*` a sa phrase grand public (« La file est pleine — réessayez dans quelques minutes ») ; le code nommé reste visible dans les détails ▸.
5. **Preuves cliquables** : un badge sans preuve est interdit ; chaque badge ouvre sa preuve (reçu, log digesté).
6. **Esthétique synthétique** : activité et santé tiennent sur un écran sans défilement — chips, steppers, badges ; le log complet reste une action, jamais l'état par défaut.
7. **Port SwiftUI borné** : la maquette HTML (précédent #118) valide la structure avant tout .swift ; le port n'entreprend que la structure validée, chaque preuve consultable reste un écran existant.
8. Maquette livrée avec ce contrat : `docs/design/interface-structure/maquette-services-juge-depot.html` (données fictives, langage visuel #118).

## 9. Interfaces entre services

- Le service dépôt **consomme** les reçus du juge (gardes §4.2 et §7) : les deux partagent le state dir et le format de reçu (digest sha256).
- Le juge **ne connaît pas** le dépôt au-delà de l'arbre qu'il teste (pas de fetch, pas de push, pas de réseau).
- **E6 / `delegation_verdicts`** : le reçu juge est la preuve que l'acteur E6 cite (`digest_preuve`) ; le juge n'écrit jamais la table (E6 seul écrivain, erratum Q12). La SOD se vérifie a posteriori depuis les reçus : `demandeur` de la preuve d'acceptation ≠ auteur du lot.
- **Routage** : aucun des deux services n'est une route du routeur interne (aucun modèle facturé, aucune admissibilité LLM) — ce sont des outils mécaniques locaux, comme reprise_gh.
- **Extension ultérieure** (hors présent contrat) : outils MCP `reprise_juge`/`reprise_depot` exposés via `reprise_delegate`, et installation des shims par l'installeur (metaai-install).

## 10. Questions opérateur — TRANCHÉES le 26/11/2026 (opérateur : « défauts adoptés, rétention de preuves 10 jours »)

1. **Garde de preuve avant push** : `exigee` — un reçu juge frais avant tout push de branche (défaut confirmé).
2. **TTL worktrees** : 14 jours avant proposition de gc (défaut confirmé).
3. **Concurrence machine du juge** : 2 exécutions simultanées (4 shards chacune) (défaut confirmé, à relire à l'usage).
4. **Rétention des preuves** : **10 jours** (décision opérateur, remplace les 90 jours proposés).
5. **Place dans l'interface** : écran « Services » en cinquième entrée du switcher de Connexions (recommandation suivie, précédent #118).
6. **Contrôles de santé** : liste des 7 contrôles structurels (§8.5) et seuil « branche dormante » tels que proposés (§8.5) — ajustables à l'usage.

## 11. Critères falsifiables

Un critère est falsifié si la mise en œuvre observée le contredit ; chaque lot d'implémentation exécutera ces cas comme tests.

**Juge de paix**

- **J1** Deux demandes concurrentes sur le même worktree : l'une active, l'autre en file (`attente`), jamais deux exécutions simultanées sur le même arbre.
- **J2** Un acteur absent de la politique est refusé `JUGE_ACTEUR_INCONNU` ; la politique absente ⇒ `JUGE_POLITIQUE_ABSENTE` (fail-closed, pas de politique implicite permissive).
- **J3** Le reçu d'un run dont `test-parallel.log` est introuvable à l'archivage n'est jamais `fini` (état incertain), jamais éligible E6.
- **J4** Un run au-delà du budget passe à `expire`, est interrompu proprement, et son reçu n'est pas éligible E6 ; aucune relance automatique.
- **J5** Une demande identique à un run `fini` existant retourne le reçu existant sans relancer (idempotence), et l'audit trace le rejeu.
- **J6** La preuve d'acceptation E6 d'un lot n'a jamais été demandée par l'auteur du code de ce lot (vérifiable par croisement des reçus : demandeur ≠ auteur, SOD).
- **J7** Un arbre sale produit un reçu `arbre_sale=true` non éligible E6, même si les tests passent.
- **J8** `digest_suite` du reçu permet de détecter a posteriori toute modification de la suite entre la preuve et l'intégration.

**Service dépôt**

- **D1** Tout push émis par le service porte un refspec explicite `<br>:refs/heads/<br>` (jamais de push nu) ; un push vers dev/main hors §7 ⇒ `DEPOT_DEV_RESERVE`.
- **D2** Un worktree sous la zone d'un autre acteur ne peut être ni modifié ni supprimé par le demandeur : `DEPOT_ARBRE_ETRANGER` (cas codex/* vérifié).
- **D3** Un worktree auto-géré non enregistré est listé `non_enregistre` et ne peut servir ni d'arbre d'intégration ni de source de preuve E6 nominative.
- **D4** `worktree-libere` refuse un arbre sale (`DEPOT_SALE_REFUSE`) ; avec `--sec`, un patch de récupération existe avant toute suppression.
- **D5** `aligne` classifie correctement les cinq états (ALIGNE / RETARD / DIVERGENCE / AVANCE_LOCAL / APPORT_VIDE) sur les cas réels du 24-27/11 (ffff1e6 = AVANCE_LOCAL, 144 = APPORT_VIDE).
- **D6** `integre` sans go tracé ⇒ `DEPOT_GO_ABSENT` ; avec un go dont le sha ne correspond plus à la pointe ⇒ `DEPOT_CONSENT_EXPIRE` (anti-TOCTOU).
- **D7** Un conflit de fusion ⇒ `DEPOT_CONFLIT` + `git merge --abort`, aucun conflit résolu par le service.
- **D8** La vérification juge sur la pointe intégrée précède toujours le push de dev ; rouge ⇒ jamais de push.
- **D9** Un FF de dev sans les trois conditions de l'exception (go explicite + RETARD pur + fille directe) ⇒ `DEPOT_FF_REFUSE`.
- **D10** Le clone racine `source/` n'est jamais checkouté sur une autre branche par le service (statut de branche inchangé avant/après toute opération).

**Interface graphique**

- **I1** Tout ce que l'écran Services affiche provient des reçus, du registre, du flux `activity.json` ou des audits des services — aucune copie d'état dans l'app (une vue SwiftUI ne réimplémente jamais la logique du service).
- **I2** Chaque acte sensible (intégrer sur dev, ff, gc, coupe-circuit) exige une confirmation unique ; le go tracé de l'opérateur apparaît dans l'audit correspondant.
- **I3** Aucun bouton désactivé n'est muet : sa raison nommée est affichée (file pleine, preuve à rafraîchir, service désactivé…).
- **I4** Les worktrees étrangers sont visibles et inertes : cadenas, zéro action mutante disponible, lecture seule.
- **I5** Un badge sans preuve est impossible : chaque badge d'état ouvre sa preuve (reçu, log digesté).
- **I6** Les refus `JUGE_*`/`DEPOT_*` sont traduits en phrase d'action dans l'interface, le code nommé restant consultable en Avancé ▸.
- **I7** La maquette HTML est validée par l'opérateur avant tout port SwiftUI (précédent #118).
- **I8** Une vérification en cours montre sa progression par shard et ses erreurs déjà visibles **avant la fin du run** ; une opération dépôt en cours montre son étape courante et son horodatage.
- **I9** L'activité en cours se lit depuis le flux `activity.json` des services — aucun état d'activité réimplémenté dans l'app.
- **I10** L'indicateur de santé ne lit jamais le code : ses contrôles portent uniquement sur Git et l'inventaire de l'arbre.
- **I11** Chaque contrôle de santé affiche sa preuve et son « pour améliorer » ; un score global sans détail est interdit ; le score n'induit aucun refus de service (diagnostic, pas verrou).

## 12. Plan d'implémentation proposé (lots)

- **S1 — juge minimal** : `reprise/juge_paix.py` + CLI + politique initiale + reçus/audit + verrou/file ; tests L1.
- **S2 — dépôt minimal** : `reprise/depot_service.py` + registre + zones + `worktree-claim/libere/liste` + `branche-cree` + `push` refspec ; tests L2.
- **S3 — aligne + integre** : classification des cinq états, intégration « Intégration X » avec go tracé + garde anti-TOCTOU + juge sur pointe avant push ; tests L3 (hermétiques, HOME jetable, clones jetables).
- **S4 — livraison par harness** : shims `bin/reprise-juge` + `bin/reprise-depot`, intégration installeur, doc USAGE/AGENTS ; consommation reçus juge par le dépôt (gardes D1/D6-D8).
- **S5 — interface graphique** : maquette HTML (livrée avec ce contrat) validée par l'opérateur — puis sections SwiftUI natives lisant l'état via les modules services (JSON sur stdout, `activity.json`, mêmes politiques) : bande d'activité en cours (shards, étapes, erreurs visibles), cartes juge/dépôt, santé du dépôt (structure seule), journal commun, confirmation unique des actes sensibles, refus traduits ; jamais d'état parallèle.
- SOD : auteur des tests ≠ auteur du code sur chaque lot (contrat E4) ; juge de paix avant push à chaque lot.

## 13. Références

- `docs/contracts/E4-TASK-CONTRACT.md` (Q7-Q13, SOD, interdits) ; `docs/contracts/E4-O1-COMPETENCE-CONTRACT.md` (routage mass code)
- `docs/contracts/HARNESS-ROLES-CONTRACT.md` (rôles = chaînes de routes ; ironclaw non admissible)
- `docs/contracts/ROUTEUR-DELEGATION-CONTRACT.md` (routes, refus nommés)
- `docs/specs/WORKFLOW-REPRISE.md` v1.2 (E1-E8, écho-clé, anti-TOCTOU, go lié)
- Issue #270 et le module `reprise/reprise_gh.py` (précédent service centralisé + shims + politique fail-closed + audit JSONL)
- `reprise/ci_test.py` (verificateur épinglé 3.13, log `reprise/.runtime-py/test-parallel.log`, HOME jetable)
- AGENTS.md §Workflow Git (worktrees `<workspace>/tmp/<sujet>`, coordination par origin) ; USAGE.md §Hygiène (empreinte de release : jamais de log/reçu dans l'arbre)
- `docs/design/interface-structure/maquette-services-juge-depot.html` — maquette de l'écran « Services » (langage visuel #118, données fictives)
