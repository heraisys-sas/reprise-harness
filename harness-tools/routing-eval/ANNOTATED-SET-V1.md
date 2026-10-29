# Jeu de décisions annoté V1 — spécification

> Statut : spécification consolidée (28/10/2026), issue de deux sous-agents (asi1 : conception du protocole ; asi1-mini : inventaire des sources de données). Conflit identifié et tranché ci-dessous.
> Cadre de référence : `SUPER-DECIDER-INTEGRATION.md` §7 (décisions faisant autorité) et `PROTOCOL.md` (interprétation à deux couches).

## 0. Conflit des sources — constat de l'inventaire

L'inventaire du registre montre que les données de production sont quasi inexistantes :

| Source | Volume réel |
|---|---|
| `state/tasks.sqlite` (`tasks`, `task_engines`, `automation_events`, `run_details`) | 0 ligne — scheduler inactif (`auto_enabled=false`) |
| `state/connection.sqlite` (`task_engines`) | 0 ligne |
| `quick-state/quick.sqlite` (`delegation_requests`) | 1 entrée |
| Logs Claude Desktop (`reprise_logs.parse_quick_entry()`) | 3 submissions, 5 routing events |
| **Total réel** | **~5 décisions** |
| Tests unitaires (`test_codex_expert_mcp.py`, `test_delegation_persistence.py`, `test_engine_events.py`, `test_codex_rpc.py`, `test_codex_expert.py`, `test_owner_route.py`, `test_ironclaw.py`) | ~120–140 cas étiquetés (synthétiques : prompt mocké + engine + état attendu) |

**Décision :** un jeu construit sur les tests unitaires évaluerait la classification de *simulacres produits par le même code*, pas des décisions réelles — biais de circularité rédhibitoire (couche 1 du PROTOCOL.md : biais d'annotation). Le protocole V1 s'exécute donc en deux phases :

- **Phase 0 — accumulation** : activer la journalisation des décisions réelles (events/task_engines, déjà implémenté mais inactif) et collecter jusqu'à ~100 décisions de production. Pas de test A/B/C avant.
- **Phase 1 — évaluation** : quand ~100 cas réels sont réunis, appliquer le protocole ci-dessous. Les ~120–140 cas de tests servent de **cas de fumée** (vérifier que le pipeline d'évaluation tourne, états extrêmes), jamais de holdout.

## 1. Taxonomie des classes (5 classes, V1 = `best_model` + `confidence`)

| Classe | Description | Faux positif | Faux négatif | Asymétrie |
|---|---|---|---|---|
| **D1 — lot cadré/Flash** | tâche localisée, modèle léger suffisant | lot raté → re-travail | surcoût tokens/temps | FP > FN (modéré) |
| **D2 — lot complexe/escalade** | difficulté algorithmique, refonte structurante | surcoût inutile | Flash bloque et boucle (échec observé) | **FN > FP, net** |
| **D3 — confiance haute** | sortie validable sans re-travail | sortie défectueuse acceptée | vérification inutile, coût faible | **FP > FN, fort** |
| **D4 — reprise post-quota** | reprendre à l'échéance vs nouvelle session | reprise prématurée | délai sans dommage | FP > FN |
| **D5 — refus/dégradation explicite** | bonne décision = « ne pas router » (repli C) | routage tenté inutilement | repli raté | équilibré, faible |

Hors V1 (décision §7.1) : `tool_call_ok`, `handoff_ready`, etc. — ne pas les annoter.

## 2. Protocole d'annotation

- **Source** : décisions réelles de production (phase 0). Amorçage D3/D5 possible sur les 8 cas de `routing-eval/cases/cases.json`.
- **Annotateur** : coordinateur seul (double annotation indisponible). Compensations : étiquettes figées **avant** toute observation des bras A/B ; 20 cas (~20 %) re-annotés à 2 semaines (intra-annotateur) ; désaccord intra > 15 % → préciser la taxonomie avant de continuer.
- **Désaccord** : la seconde étiquette prime si justifiée par un événement du registre ; sinon le cas est exclu, pas arbitré.
- **Taille par classe** (distribution = production réelle, consignée) : D1 ~35, D2 ~25, D3 ~20, D4 ~10, D5 ~10. D4/D5 : 10 cas = verdict conventionnel, pas estimation d'intervalle (assumé).
- **Chaque cas porte** : anonymisation P0 (chemins, IDs de session, code — hashés ou réécrits), étiquette, référence `events`, décision de C calculée par les règles déterministes (baseline pré-remplie).

## 3. Métriques

- **Par classe** : précision, rappel, F1 — jamais de précision globale (Guo et al., *On Calibration of Modern Neural Networks*, ICML 2017, arXiv 1706.04599 : mal-calibration systématique + asymétrie des coûts).
- **ECE (10 bins)** par classe pour B — la calibration des probabilités est l'hypothèse de valeur de B, à tester. Pour A (FoundationModels) : la `confidence` de `@Generable` est un **score déclaré**, à traiter comme tel pour l'ECE (voir §8.6) — jamais comme une probabilité calibrée native.
- **C évalué sur les mêmes cas** ; son score par classe est la baseline (couche 1).

## 4. Critère d'activation de B — ⚠️ SUSPENDU (voir §8.1)

> **Seuils suspendus par la revue Astra (28/10/2026)** : ECE non estimable à ~4,5 obs/bin, précision vs C mal définie si C ne prédit jamais certaines classes. Remplacement : perte pondérée préétablie (§8.1). La règle 4 (anonymisation P0) reste applicable.

B est activé (uniquement sur les classes où il tient) si, sur le holdout :
1. Aucune classe où B perd ≥ 10 points de précision vs C ;
2. Précision pondérée ≥ C + 15 points sur D1+D2 (le cœur `best_model`) ;
3. ECE ≤ 0,15 sur D2 et D3 — sinon B en conseil affiché seulement, jamais en décision automatique ;
4. Zéro prompt non anonymisé sur les 100 cas (contrôle P0 sur échantillon de logs).

Sinon : **B inutilisé**, bras A priorisé (§7.2 : A est le candidat par défaut, B n'a de sens que si A échoue aussi).

## 5. Prérequis de la phase 0

1. Vérifier pourquoi le scheduler est inactif (`auto_enabled=false`) et si l'activer est souhaité — décision opérateur.
2. S'assurer que chaque décision de routage future (délégations Reprise, Quick Apply, reprises) est journalisée dans `events`/`task_engines` avec assez de contexte pour être étiquetée a posteriori.
3. Suivre le volume : à ~5 décisions/semaine, ~100 cas ≈ 5 mois ; si trop lent, élargir à d'autres signaux (choix de modèle des sous-agents Harness) — à arbitrer.

## 6. Questions ouvertes

1. **D4** : les cas de reprise réelle datée seront-ils assez nombreux à l'issue de la phase 0 ? (l'inventaire montre 1 seule `delegation_requests` aujourd'hui — probablement non au démarrage).
2. **Bras A** : guided generation (FoundationModels) expose-t-elle des scores de confiance exploitables, ou faut-il échantillonnage multiple ?

> **Résolu (28/10/2026)** : oui — les tests de fumée (`harness-tools/foundation-models-smoke/RESULTS.md`) montrent que `@Generable` expose `confidence` nativement. L'ECE du bras A est mesurable sans échantillonnage multiple.

## 7. Journalisation des décisions (contrat §7.5 de SUPER-DECIDER-INTEGRATION.md)

Trois types d'événements alimentent le jeu annoté : `routed` (décision du routeur local, avec `confidence`), `explicit` (route forcée par l'appelant et disponible), `routing_substituted` (route forcée indisponible → équivalent disponible, avec route demandée → servie + motif). Les cas `explicit` et `routing_substituted` sont étiquetables a posteriori comme les `routed` et enrichissent la phase 0 sans activer le scheduler.

## 8. Corrections Astra du 28/10/2026 (font autorité, cf. §7.6 de SUPER-DECIDER-INTEGRATION.md)

La revue `gpt-6-astra` corrige ce document :

1. **Les seuils d'activation du §4 (C+15 pts, ECE ≤ 0,15 sur D2/D3) sont suspendus** : ECE sur ~45 cas = ~4,5 obs/bin (instable) ; précision vs C mal définie si C ne prédit jamais certaines classes ; la `confidence` de `@Generable` est un score déclaré, pas une probabilité calibrée. Remplacement : **perte pondérée préétablie** (sous-classement, refus injustifié, coût, latence), comparée entre politiques sur les mêmes cas, avec effectifs, incertitude et taux d'abstention.
2. **Phase 0 accélérée** — 3 ensembles distincts : (a) développement immédiat (historique autorisé + synthétiques indépendants + scénarios adversariaux — les tests unitaires restent des régressions, jamais un holdout) ; (b) évaluation gelée (jamais utilisée pour ajuster instructions/seuils ; paraphrases d'un même cas ≠ observations indépendantes) ; (c) observation prospective (A propose, journalise, aucun effet sur l'exécution). Les résultats des trois ensembles sont publiés séparément.
3. **Taxonomie D1–D5 à corriger avant toute collecte** : chevauchements (D1/D2 = difficulté, D4 = situation, D5 = action, D3 ambigu — circulaire si la confiance du routeur définit la vérité terrain). Annotation séparée par cas : capacités/niveau minimal, contraintes/destinations admissibles, état opérationnel (dont quota), décision attendue (exécuter / demander précision / bloquer), coût d'erreur. Seconde annotation indépendante au moins sur les cas critiques et ambigus.
4. **100 cas = pilote diagnostique**, pas une certification (zéro erreur sur 10 ⇒ borne sup. 95 % ≈ 26 %).
5. **Bras A passe les mêmes exigences de qualité que B** — la priorisation de A en cas d'échec de B ne vaut pas activation.
6. **Question ouverte n°2 corrigée** : `@Generable` expose bien un score, mais c'est un score déclaré — son utilisation pour l'ECE suppose de le traiter comme tel, non comme une probabilité native calibrée.
