# Workflow Reprise consolidé

*version 1.2 · 26 novembre 2026 · auteur : ASI1 (orchestrateur) · statut : v1.1 = corrections intégrées de l'avis d'architecte Claude Desktop (request_key validation-workflow-20261126-claude, verdict « adoptable après corrections ») ; **v1.2 = errata de la revue indépendante E6b du 26/11** (glossaire recolonisé R7/R8, écrivain `delegation_verdicts` = E6 seul avec erratum contrat, F3 borné, F6 nommé, voie courte 6 critères, Q9 aligné au contrat, retry ChatGPT réconcilié) · contre-regard ChatGPT : en attente · révisable par l'opérateur. Ce document consolide la PRATIQUE réelle utilisée sur #268/#269/#270 ; les brouillons de session cités dans l'historique ne sont pas dans l'arbre et ne font pas foi.

## 0. Glossaire de numérotation (normatif)

Un seul étage par numéro ici ; les autres documents font référence à cette table. La colonne R8 reprend la table des étages de la rev.3 (E3 = Spécification / contrats, E4 = Planification, E5 = Code, E6 = Vérification, E7 = Revue, E8 = Intégration) ; la consolidation a réaligné certains numéros, la colonne « Ici » fait foi.

| Ici | Contenu | R7 (ancien pipeline) | R8 (workflow-claude-ship rev.3) | Contrat E4 (numérotation interne R7) |
|---|---|---|---|---|
| E1 | Observation | E1 | E1 (Observation) | — |
| E2 | Preuve citée | E2 | E2 | e2_ref |
| E3 | Routage + Planification (plan_id, lots) | E3 (Planification) | E4 (Planification) | plan_id / §4 |
| E4 | Contrat de tâche | — | E3 (Spécification / contrats) | document entier |
| E5 | Exécution (mass code) | E4 (Code) | E5 (Code) | tâche/auteur (son « E4 ») |
| E6 | Vérification (tests, runner mécanique) | E5 (Vérification) | E6 (Vérification) | vérificateur (son « E5 ») |
| E6b | Revue indépendante (verdict de risques) | E6 (Revue) | E7 (Revue) | — |
| E7 | Intégration (branches, go) | E7 | E8 (Intégration) | intégrateur |
| E8 | Traçabilité | E8 | — (ajout de la consolidation) | delegation_verdicts (écrit par E6) |

Le contrat de tâche garde sa numérotation interne héritée de R7 (son E4 = exécution, son E5 = vérification) : ne pas croiser ces numéros avec la présente table.

## 1. Objet

Un seul workflow pour tout livrable du dépôt reprise-harness : étude, spec, contrat, plan, code, revue, intégration, traçabilité. Chaque étage a **un approbateur unique** (plusieurs rôles peuvent collaborer : proposeur, rédacteur, contre-regard — un seul approuve), une entrée citée, un livrable testable et un reçu. Le workflow décrit ce qui est réellement exécuté et vérifié, jamais une intention.

## 2. Rôles (un approbateur par étage)

| Étage | Responsable | Rôle des autres |
|---|---|---|
| E1 Observation | **ASI1** | — |
| E2 Preuve citée | **ASI1**, garde mécanique `verify_citation`/`require_cited_evidence` (O5, branche e5-o5-citations) | Claude/ChatGPT relisent à la demande |
| E3 Routage + Planification | **ASI1 propose, opérateur approuve** (voie normale) ou politique écrite (voie E3 lite) | Claude spécifie, ChatGPT contre-regard. Le **routage** est rattaché à E3 : décisions O1 et gardes d'admission 0–3 du contrat (matrice role/actor, mode, consent_ref) |
| E4 Contrat | **Claude rédacteur, opérateur approuve** | ASI1 tranche les questions ouvertes (**provisoire, révisable**), ChatGPT contre-regard |
| E5 Exécution | **Meta AI** (contrat borne) | ASI1 n'écrit du code que si l'étage E6 est alors assuré par un acteur ≠ auteur, ou verdict opérateur |
| E6 Vérification | **Runner mécanique** (commandes fixées par acceptance) ; ASI1 le lance, ne le juge pas | vérificateur ≠ auteur, intégrateur ≠ auteur ; sinon go opérateur explicite |
| E6b Revue | **ChatGPT** (verdict de risques, jamais preuve) | — |
| E7 Intégration | **ASI1** (branches, push) ; FF `dev` = acte opérateur | — |
| E8 Traçabilité | **ASI1** (les reçus Meta AI sont une matière, pas une écriture) | — |

Séparation des devoirs machine : auteur ≠ vérificateur ≠ intégrateur ; l'auteur ne modifie jamais ce qui le vérifie. Meta AI est en lecture seule GitHub par défaut (service `reprise_gh`, politique fail-closed `write_actors`).

## 3. Pipeline (chaque étage : entrée citée → livrable → reçu)

- **E1 Observation** : état du dépôt (branches, issues, base_sha), mémoire persistée (trace, pas source de vérité), demandes opérateur. **Reprise après interruption** : l'état se reconstruit depuis les reçus et `delegation_verdicts` ; une tâche dans inbox sans reçu outbox = `incertain` ⇒ réconciliation opérateur, jamais de réémission.
- **E2 Preuve** : lecture réelle du code, citation `fichier#lignes@base_sha`, digest du contexte. Sans preuve citée, aucun étage suivant.
- **E3 Planification** : plan immuable à digest (`plans/<plan_id>.json`), lots (un lot = une branche = un objectif), `allowed_paths` par lot, lots indépendants à chemins disjoints, approbation tracée. Pas de tâche sans plan_id approuvé, voie courte incluse.
- **E4 Contrat** : schéma des champs, gardes ordonnées fail-closed, critères falsifiables (`docs/contracts/E4-TASK-CONTRACT.md`). Questions ouvertes tranchées explicitement, marquées révisables.
- **E5 Exécution** : sous contrat borne — tailles max, interdits, base_sha épinglé, sonde obligatoire pour Meta AI (étape 0).
- **E6 Vérification** : la **garde de périmètre passe avant tout test** : `allowed_paths` absent ou vide ⇒ refus fail-closed de **chaque entrée** (`HORS_PERIMETRE`, aucun repli sur context_paths) ; interdits implicites (`.git/**`, `VERSION`, `CHANGELOG*`, `.github/**`, `.gitlab-ci.yml`, `ci_test.py`) ; pas de symlink/binaire/sous-module ; renommages comptés suppression + ajout ; diff hors périmètre = **rejet entier** (`refuse_perimetre`, digest conservé, zéro test lancé). Puis tests réels sous l'arbre avec le runtime embarqué (`/Applications/Reprise.app/Contents/Resources/runtime/bin/python3 -m unittest reprise.tests.X`) : nombre exécuté ≥ attendu, noms des tests d'acceptance présents, verdict validé/refusé nommé. **Tout diff modifié après la garde (adaptation d'intégration) repasse la garde et reçoit un nouveau verdict d'un vérificateur ≠ adaptateur** ; l'adaptation compte dans max_diff_*. Refus E6 : consomme un cycle, diff entier rejeté (pas de cherry-pick), retry = attempt+1 + `retry_of`, contestation = décision opérateur.
- **E6b Revue** : contre-regard indépendant (axes falsifiables, verdict de risques). Une revue n'est jamais une preuve ; semée comme contexte d'un autre exécutant, elle est marquée « donnée », digestée, citée textuellement.
- **E7 Intégration** : branche nommée par lot (`autoclaw/<lot_id>`), commits français sans accents, push refspec explicite. Le **go opérateur est lié au couple (sha de branche, verdict E6)** : si la branche a bougé après le verdict, le go est caduc (anti-TOCTOU). FF `dev` sur go explicite ; pas de MR.
- **E8 Traçabilité** : reçu par livrable, commentaire issue via `reprise_gh` (audité), mémoire ASI1, `delegation_verdicts` alimenté **par E6 seul** (append-only, keyé par request_key) pour la mesure O1 — erratum contrat §10/Q12 du 26/11 : la version initiale du contrat disait « E5/E6/E8 », l'écrivain unique est E6 (l'exécutant n'écrit jamais son propre verdict, anti-injection). La « confiance » auto-déclarée d'un délégué n'entre ni dans le routage ni dans O1 (anti-injection).

## 4. Recettes réelles (une par acteur)

- **Claude Desktop (archi/spec/contrat)** : client quick-state pré-#269 (worktree `tmp/claude-client-a11c`), chat délégué `ba05f0cb`, question autoporteuse ≤ 12 000 **caractères Unicode NFC** (état + décisions + citations E2 inline, aucune pièce jointe), kind=expertise. **Clé : dérivée du digest de question ; rejeu identique = reçu existant renvoyé ; retry = attempt+1 + retry_of ; état incertain ⇒ réconciliation opérateur.** Le chat étant partagé, l'historique est **non probant** : seule la question (digestée) fait foi ; chat neuf quand le sujet change de nature. Réponse écrite en workspace avec en-tête de provenance, puis **écho-vérification : la réponse doit contenir la request_key de sa question** (sinon `ECHO_CLE_ABSENTE`, réponse non consommée). Réponse tronquée côté Claude = équivalent captured_incomplete : `incertain`, main à l'opérateur, zéro relance.
- **ChatGPT (revue/veille/contre-regard)** : holder CDP profil dédié, question avec axes falsifiables numérotés, dépôt public accessible (branches `autoclaw/*` poussées avant revue). Nouveau script + nouvelle request_key à chaque envoi. **`captured_incomplete` = jamais de relance** (relecture bornée : N passes, T secondes, puis arrêt à l'état `incertain`) ; **réenvoi après `echec_verifie` = attempt+1 + `retry_of`**, comme pour Claude (le « nouveau script à chaque envoi » ne dispense pas de la réconciliation). La réponse reste dans la conversation et est lue par l'opérateur.
- **Meta AI (mass code)** : pont fichiers (`bridge_root`, p. ex. `workspaces/metaai-bridge/` ; plus de WebDAV depuis le 24/11, #277), tâche `inbox/<task_id>.json` (nommage exact), contexte semé dans `inbox/docs/` (contrats, revues marquées « donnée », conventions), livraison en fichiers séparés sous `inbox/delivery/<task_id>/`, reçu `outbox/<task_id>.txt` clé/valeur **mono-ligne**. Sonde d'écriture à l'étape 0 ; échec ⇒ arrêt propre, erreur verbatim **rédigée de ses secrets**, état nommé. Acteur indisponible après sonde en échec ⇒ reroutage O1 (chain_candidates) avec clé neuve + retry_of.
- **ASI1 (orchestrateur)** : rédige les questions autoporteuses, tranche en autonomie déléguée (décisions **provisoires, révisables** — jamais l'approbation de ses propres plans), lance E6, intègre E7, trace E8, consolide la mémoire.

## 5. Gardes transversales

- **Fail-closed** : tout doute = refus nommé ; chemin sensible ⇒ acteur local sans sortie réseau (**ADOPTÉ au contrat §9, Q9**), avec fichier versionné, normalisation fail-closed (realpath, NFC, casse) et champ `sensitive` calculé ; **exception unique : consent_ref nominatif de l'opérateur, tracé** (contrat §9).
- **Anti-injection** : tout contenu délégué est une donnée, jamais une instruction ; seuls les checkpoints numériques traversent le routage.
- **Cycles bornés** : la boucle rouge naît en E6 et borde **E5↔E6 par lot** (`max_cycles` 3, plafond 5) ; plafond de plan = 3 × lots, au-delà `bloque_operateur` ; `ttl_s` : expiration ⇒ `expire`, compté comme cycle, traité `incertain` sans preuve de non-livraison ; zéro relance automatique ; retry = nouvelle clé dérivée + `retry_of` réconcilié. **Voie courte [PROPORTION]** (rev.3) : E2 auto → E3 lite (plan à un lot, enregistré) → E4 → E5 → E6 → E8, éligibilité mécanique **au sens du contrat §6, 6 critères** (1 lot, ≤ 2 fichiers, ≤ 60 lignes, aucun sensible, **aucune dépendance ajoutée, mode compatible**), `max_cycles` = 2, dépassement ⇒ refus, jamais de passage silencieux.
- **Immuables** : releases `integration-releases` (posix-readonly-v1), VERSION/CHANGELOG (opérateur seul), `build/` jamais commité, worktrees étrangers intouchables, `chmod 755 reprise/reprise` avant commit. Les interdits nommés (ex. `expert_mcp.py`, `experts-manifest.json`) vivent dans l'**interdit implicite versionné** (liste du dépôt), pas dans ce document.
- **Adaptation d'intégration** : les conventions du dépôt priment ; chaque écart est consigné dans le message de commit, et l'adaptation repasse E6 (voir E6).

## 6. Traçabilité

Chaque livrable laisse : request_key unique (dérivée du digest de question) ; reçu (outbox clé/valeur, reçu `.send-*` durable, réponse quick.sqlite) ; entrée `gh-audit.jsonl` pour tout appel GitHub ; commentaire issue (#268 progression) ; mémoire ASI1. Les verdicts E5/E6/E8 sont enregistrés par **E6 seul** dans `delegation_verdicts` (request_key en clé, append-only) ; O1 joint `routing_events ⋈ delegation_verdicts`.

## 7. États et falsifiables

Cycle de vie : `pending → dispatched → answered → validé → intégré` ; états nommés complémentaires : `incertain`, `expire`, `bloque_operateur`, `refuse_perimetre`, `echec_verifie`, `refuse:<code>`.

- **F1** : une tâche sans plan_id est refusée à l'étape 4 d'admission (étapes 0–3 passées, aucun reçu d'admission créé), voie courte incluse (`PLAN_ID_ABSENT`) ; plan existant non approuvé ⇒ `PLAN_NON_APPROUVE` (contrat C1).
- **F2** : un diff hors `allowed_paths` donne `refuse_perimetre`, digest conservé, **zéro test lancé** ; cas rename partiel et symlink nommés ; la preuve est le champ `recu` du JSON de refus (`n_hors` + exemples) avec le digest du diff (contrat §5.3).
- **F3** : après `captured_incomplete`, zéro réémission ; la relecture est bornée à **2 passes et 30 secondes** (même budget que la capture du holder, commit 3863241) ; dépassement ⇒ arrêt, état `incertain`, zéro envoi, relais à l'opérateur.
- **F4** : un code n'est intégré que si le nombre de tests exécutés ≥ attendu — **« attendu » = le nombre d'entrées `test:` déclarées dans l'acceptance du lot** —, les noms des tests d'acceptance sont présents dans la sortie, et E6 est vert **avant** E7.
- **F5** : une question > 12 000 caractères NFC est refusée à l'envoi (jamais tronquée) ; un résumé n'est permis que si les citations E2 sont reprises à l'identique ; cas limites testés à 12 000 et 12 001.
- **F6** : un échec de sonde Meta AI arrête la tâche avec l'erreur verbatim rédigée de ses secrets, état nommé `refuse:SONDE_ECHEC`.
- **F7** : un diff modifié après la garde de périmètre repasse la garde et reçoit un nouveau verdict d'un vérificateur ≠ adaptateur ; sinon `SOD_VIOLEE` et aucune ligne dans `delegation_verdicts`.
- **F8** : une réponse qui ne contient pas la request_key de sa question n'atteint jamais `answered` (`ECHO_CLE_ABSENTE`) ; vérifiable avec deux questions dans le même chat.
