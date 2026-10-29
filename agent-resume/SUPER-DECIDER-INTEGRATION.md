# Intégration de Super Decider dans reprise-harness

> **⚠️ Statut révisé (28/10/2026) :** ce document reflète la proposition initiale. Une revue critique menée par trois modèles distincts (route `asi1`, route `asi1-mini`, Morph Flash) a rejeté plusieurs éléments : la chaîne de traduction FR→EN, l'intégration dans `engines.py`, et l'idée d'un fallback NLEmbedding fiable (le routage local est documenté à 20/40 sur le holdout dans `routing/README.md` — insuffisant). Voir la **section 7** pour la révision qui fait autorité. Détail de la revue : [`SUPER-DECIDER-CRITIQUE.md`](SUPER-DECIDER-CRITIQUE.md).

**Date :** 2026-10-28  
**Source :** [GitHub Singularity-Compute/super-decider](https://github.com/Singularity-Compute/super-decider), [docs.cudos.org](https://docs.cudos.org/docs/asi-cloud/inference/super-decider/introduction), [BSCN article](https://bsc.news/news/super-decider-singularity-compute-open-model)  
**Licence :** Apache-2.0  
**Modèle :** DiffusionGemma 26B-A4B (4B actifs), NVFP4, 18 Go  

---

## 1. Qu'est-ce que Super Decider ?

Super Decider n'est **pas un modèle de langage général**. C'est un classificateur décisionnel « System One » : il ne rédige pas de texte. Il prend un état et des questions typées, puis retourne pour chaque réponse une probabilité calibrée en ~0,3 s.

| Type de question | Format | Exemple |
|---|---|---|
| `choice` | Sélection unique parmi options | `refund_now`, `start_return`, `escalate` |
| `noul` | Non/Oui/Uncertain | `Is cancelling allowed? → 0.03` |
| `score` | Note continue avec légende | `urgency: 0.59 (legend: low→medium→high)` |

**API :** Jev System One compatible (`POST /v1/systemone`) + OpenAI chat complet (`POST /v1/chat/completions`). Endpoint hébergé : `https://llm.a.singularitycompute.com/v1/systemone`. Clé sur [ASI:Cloud](https://asicloud.cudos.org/).

**Coût hébergé :** $0,03/M tokens d'entrée, sortie gratuite. Auto-hébergement : Docker + GPU 48 Go (L40S).

---

## 2. Comment l'obtenir gratuitement ($0)

### Option A : crédit gratuit ASI:Cloud (hébergé)
- Créer un compte sur [asicloud.cudos.org](https://asicloud.cudos.org/) ou écrire à `tech@singularitycompute.com`.
- Les benchmarks publient un coût de $0,016/1000 décisions via l'API hébergée.
- Le plan gratuit inclut un crédit initial ; vérifier les conditions actuelles sur le site.

### Option B : auto-hébergement local (totalement gratuit)
```bash
docker build -t super-decider runtime/
docker run --gpus all -p 8011:8011 -v $PWD/models:/data/models super-decider
```
- Télécharge les poids depuis HuggingFace automatiquement au premier démarrage (NVFP4, 18 Go).
- Nécessite un GPU NVIDIA 48 Go (L40S). **Pas viable sur Mac sans GPU dédié.**

### Option C : appel distant sans clé API (à confirmer)
- Le README mentionne `tech@singularitycompute.com` pour obtenir une clé. Vérifier si un accès démo existe.

---

## 3. Points d'intégration concrets dans reprise-harness

### 3.1 Routage modèle automatique (priorité haute)

Super Decider est idéal pour la couche de routage existante (`routing/`, `harness-tools/routing-eval/`). Au lieu des embeddings Apple NLEmbedding heuristiques, on utilise un classificateur calibré avec confiance mesurable.

**Question type :**
```json
{
  "state": "Task description and context...",
  "questions": {
    "best_model": {
      "type": "choice",
      "instructions": "Which model should handle this task?",
      "criteria": {
        "flash": "Simple framing, localized fix, syntax/config",
        "pro": "Demonstrated failure, algorithmic difficulty, critical review",
        "codex_sol": "Substantial implementation or review",
        "codex_astra": "Most difficult problems"
      }
    },
    "needs_thinking": {
      "type": "noul",
      "instructions": "Does this require extended reasoning?"
    }
  }
}
```

**Intégration dans `engines.py` :** Ajouter un routeur `super-decider` qui pose des questions typées au lieu de calculer des cosinus. La confiance (< 0.7 → fallback standard) remplace les seuils arbitraires 0,40 / 0,025.

### 3.2 Orchestration de délégation multi-agent

Dans le protocole court du coordinateur (voir `AGENTS.md`), Super Decider peut décider :
- **Quel expert contacter** (Codex vs Claude Code vs sous-agent local)
- **Si une tâche doit être escaladée** (faible confiance → humain ou modèle plus capable)
- **Combien d'agents parallèles lancer** (choix entre 1, 2, ou 3 agents max configurés)

### 3.3 Détection d'anomalies post-update

Reprise surveille les sessions d'agents et détecte les anomalies post-update. Super Decider peut trier les alertes :

```json
{
  "state": "Session log excerpt showing potential anomaly...",
  "questions": {
    "is_anomaly": {"type": "noul", "instructions": "Is this a real anomaly or expected behavior?"},
    "severity": {"type": "score", "instructions": "How severe is the issue?",
                 "criteria": ["low", "medium", "high", "critical"]},
    "action": {"type": "choice", "instructions": "Recommended action?",
               "criteria": {"log_only": "Log and continue", "alert_user": "Alert user",
                            "pause_session": "Pause session", "manual_review": "Manual review"}}
  }
}
```

### 3.4 Validation des outils (Agentverse guardrails)

Le modèle excelle sur When2Call (85.6/100) — décision de quand appeler un outil, demander, ou refuser. Dans Reprise, cela correspond aux canaux de délégation :

```json
{
  "state": "Agent wants to execute: git push origin dev",
  "questions": {
    "should_act": {"type": "noul", "instructions": "Should the agent execute this tool call?"},
    "needs_approval": {"type": "noul", "instructions": "Does this need operator approval?"},
    "risk_level": {"type": "score", "instructions": "Risk of this action?",
                   "criteria": ["safe", "moderate", "risky", "destructive"]}
  }
}
```

### 3.5 Triage d'issues GitHub

Avant de déléguer une issue à un agent (label `agent:*`), Super Decider peut pré-classer :
- Complexité estimée
- Modèle recommandé
- Estimation de tokens/coût

---

## 4. Schéma d'intégration complète — Super Decider au centre du réseau de harnesss

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│                        REPRISE — Supervision commune                          │
│                                                                              │
│  ┌────────────────────────────────────────────────────────────────────────┐  │
│  │                    ASI1 / DeepSeek Harness (Hyperviseur)               │  │
│  │                                                                        │  │
│  │   ┌────────────────────────────────────────────────────────────────┐   │  │
│  │   │              SUPER DECIDER — Routeur décisionnel               │   │  │
│  │   │                                                                  │   │  │
│  │   │   Entrée : état normalisé (work_id, session_key, event_id)      │   │  │
│  │   │   Questions typées :                                             │   │  │
│  │   │     • best_model    → choice   (codex-sol | claude-astra |    │   │  │
│  │   │                              deepseek-flash | local)           │   │  │
│  │   │     • needs_expert  → noul     (doit-on escalader ?)           │   │  │
│  │   │     • confidence    → score    (0.0 → 1.0)                     │   │  │
│  │   │     • loop_risk     → score    (faible → critique)             │   │  │
│  │   │     • needs_thinking→ noul     (raisonnement étendu requis ?)   │   │  │
│  │   │     • tool_call_ok  → noul     (l'agent peut-il appeler cet    │   │  │
│  │   │                              outil sans approbation ?)         │   │  │
│  │   │     • severity      → score    (log_only → pause → alert →    │   │  │
│  │   │                              manual_review)                    │   │  │
│  │   │     • handoff_ready → noul     (le travail est-il prêt pour    │   │  │
│  │   │                              passation inter-harness ?)        │   │  │
│  │   │   Sortie : probabilités calibrées + confiance par décision     │   │  │
│  │   │   Endpoint : ASI:Cloud hébergé OU auto-hébergé Docker          │   │  │
│  │   └────────────────────────────────────────────────────────────────┘   │  │
│  │                ▲            │            ▲            │                 │  │
│  │                │            │            │            │                 │  │
│  │    ┌───────────┘            │            │            └───────────┐    │  │
│  │    │                       │            │                       │    │  │
│  │  ┌─▼──────────┐    ┌───────▼──────┐  ┌─▼──────────┐    ┌───────▼──────┐ │  │
│  │  │ ADAPTATEUR │    │ ADAPTATEUR   │  │ ADAPTATEUR │    │ ADAPTATEUR │ │  │
│  │  │ CODEX      │    │ CLAUDE       │  │ HARNESS    │    │ AUTOCLAW   │ │  │
│  │  │            │    │ DESKTOP      │  │ CLI        │    │            │ │  │
│  │  │ • sessions │    │ • Quick      │  │ • sessions │    │ • sessions │ │  │
│  │  │ • events   │    │   Apply      │  │ • events   │    │ • detector │ │  │
│  │  │ • quota    │    │ • MCP        │  │ • usage    │    │ • state    │ │  │
│  │  │ • transport│    │   get_task   │  │ • headless │    │ • tokens   │ │  │
│  │  │            │    │   submit_    │  │   profile  │    │ • cost     │ │  │
│  │  │            │    │   result     │  │            │    │            │ │  │
│  │  └─────┬──────┘    └───────┬──────┘  └─────┬──────┘    └───────┬──────┘ │  │
│  │        │                   │                │                   │        │  │
│  │        ▼                   ▼                ▼                   ▼        │  │
│  │  ┌──────────┐    ┌───────────┐    ┌───────────┐    ┌───────────┐       │  │
│  │  │  codexd  │    │ claude-   │    │  dsh      │    │  ironclaw │       │  │
│  │  │ (daemon) │    │ desktopd  │    │ (headless)│    │ (worker)  │       │  │
│  │  │          │    │ (Quick    │    │           │    │           │       │  │
│  │  │          │    │  Apply)   │    │           │    │           │       │  │
│  │  └──────────┘    └───────────┘    └───────────┘    └───────────┘       │  │
│  └────────────────────────────────────────────────────────────────────────┘  │
│                                                                              │
│  ┌────────────────────────────────────────────────────────────────────────┐  │
│  │                    REGISTRE DURABLE (SQLite privé)                      │  │
│  │                                                                        │  │
│  │  • task_engines     → tâche → engine/options                           │  │
│  │  • engine_settings  → engine → options                                 │  │
│  │  • work_sessions    → work_id → session_key, modèle, fournisseur       │  │
│  │  • events           → event_id, turn_id, tool_call_id, provenance      │  │
│  │  • checkpoints      → versionné, sourcé, avec décisions & prochaines  │  │
│  │  • unclaimed_results→ résultat non récupéré, demandeur, destination    │  │
│  │  • capabilities     → harness → supported/unsupported/unknown/dégradé  │  │
│  │  • loop_signatures  → empreintes appels/résultats, détection cycles    │  │
│  │  • quota_knowledge  → route/profil/compte/pool/source/fraîcheur        │  │
│  └────────────────────────────────────────────────────────────────────────┘  │
│                                                                              │
│  ┌────────────────────────────────────────────────────────────────────────┐  │
│  │                    POLITIQUE COMMUNE                                    │  │
│  │                                                                        │  │
│  │  Quota / Boucle / Contexte / Budget / Reprise après interruption       │  │
│  │  Handoff inter-harness (GitHub issue + labels agent:* + work:*)        │  │
│  │  Clarification en cours de mission                                     │  │
│  │  Work/Cowork comme capacité délégable                                  │  │
│  └────────────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────────────┘
         │                                       │
         ▼                                       ▼
┌──────────────────────┐              ┌──────────────────────┐
│  DASHBOARD UI        │              │  GITHUB API          │
│  (Dashboard.swift)   │              │  Issue coordination  │
│                      │              │  Labels agent:*      │
│  • Santé des sockets │              │  Branches liées      │
│  • File d'attente    │              │  Checkpoints         │
│  • Routing decisions │              │  Passation work:*    │
│  • Quotas            │              │  Réservations atom.  │
│  • Résultats non     │              └──────────────────────┘
│    réclamés          │
│  • Sessions archivées│
└──────────────────────┘
```

### Flux type : routage d'une demande utilisateur

```text
Utilisateur → prompt FR
       │
       ▼
asi1-mini (traduction EN fidèle ~99%)
       │
       ▼
Super Decider pose ses questions :
  {"state": "prompt traduit", "questions": {...}}
       │
       ├─ best_model.choice == "codex-sol" && confidence > 0.85
       │    → deleguer à Codex via reprise_delegate (mode expertise, niveau Sol)
       │
       ├─ best_model.choice == "claude-astra" && confidence > 0.80
       │    → soumettre via Quick Apply + attendre MCP get_task/submit_result
       │
       ├─ best_model.choice == "deepseek-flash" && confidence > 0.70
       │    → sous-agent local morph-dsv41flash-1017 (route gratuite)
       │
       ├─ best_model.needs_expert == true
       │    → escalader vers gpt-6-astra ou gpt-6-sol via openai-codex
       │
       ├─ best_model.tool_call_ok == false
       │    → demander approbation opérateur avant exécution
       │
       └─ confidence < seuil_min
            → fallback vers règles heuristiques existantes (NLEmbedding)
               ou vers humain selon severité.score
```

### Flux type : supervision et reprise après quota

```text
Harness actif (ex: codexd)
       │
       ├─ Quota refusé par le fournisseur
       │    ↓
       ├─ Enregistrer dans quota_knowledge : cause, échéance, source
       │    ↓
       ├─ Figer checkpoint dans checkpoints.versionné
       │    ↓
       ├─ scheduler_service.py programme la continuation à l'échéance
       │    ↓
       ├─ À l'échéance : Super Decider évalue si la reprise est justifiée
       │    → state: "session X, work_id Y, checkpoint Z, dernière action W"
       │    → questions: { re_take_over: {type:"choice", criteria:{...}}, ...}
       │    ↓
       ├─ Si re_take_over.choice == "continue" && confidence > 0.7
       │    → reprendre la session avec son identité native
       │    ↓
       └─ Si re_take_over.choice == "handoff" && confidence > 0.6
            → marquer GitHub issue work:available
            ← un autre harness (ironclaw worker) prend work:active
```

### Intégration dans le code existant

| Composant actuel | Modification requise | Fichier cible |
|---|---|---|
| `engines.py::ENGINES` | Ajouter `'super-decider': ('Super Decider · routeur', 'Decision')` | `agent-resume/engines.py` |
| `engines.py::capabilities()` | Détecter disponibilité ASI:Cloud (clé configurée ou endpoint local) | `agent-resume/engines.py` |
| `routing/plugin/` | Remplacer ou compléter NLEmbedding par Super Decider comme classificateur principal | `routing/plugin/router_config.py` |
| `harness-tools/routing-eval/` | Ajouter Super Decider comme bras D dans l'évaluation A/B/C/D | `harness-tools/routing-eval/MODELS.md` |
| `scheduler_service.py` | Appeler Super Decider pour décider de la reprise post-quota | `agent-resume/scheduler_service.py` |
| `context_pack.py` | Utiliser Super Decider pour décider quoi compacter vs préserver | `harness-tools/context_pack.py` |
| Dashboard UI | Afficher les décisions de Super Decider (modèle choisi, confiance, probabilités) | `agent-resume/Dashboard.swift` |

### Comparaison : routeur actuel vs routeur Super Decider

| Aspect | Actuel (NLEmbedding + règles) | Avec Super Decider |
|---|---|---|
| **Type de sortie** | Cosinus 0..1 (similarité sémantique) | Probabilité calibrée par classe |
| **Seuils** | Fixes : 0,40 / 0,025 (heuristiques) | Dynamiques : basés sur confidence > seuil |
| **Langue** | FR/EN uniquement | EN optimal, FR acceptable |
| **Multi-question** | Une classification par demande | Plusieurs décisions en un appel (~0,3s) |
| **Confiance mesurable** | Non (score brut de similarité) | Oui (champ `confidence` natif) |
| **Fallback explicite** | Règles hardcoded | Seuil configurable, fallback automatique |
| **Coût par décision** | Gratuit (local Apple ML) | $0,03/M tokens entrée (hébergé) |
| **Auto-apprentissage** | Non | Non (modèle statique) |
| **Maintenance** | Mises à jour Apple ML | Mise à jour modèle via CUDOS |

---

## 5. Limitations à connaître

| Limite | Impact | Contournement |
|---|---|---|
| Anglais principal | Moins bon en FR | Utiliser prompts EN pour les questions système |
| Pas de génération prose | Ne remplace pas un LLM | Seulement pour décisions, pas réponses |
| Auto-hébergement GPU 48 Go | Impossible sur Mac standard | Privilégier API hébergée |
| Thinking ajoute 0.5-2s | Latence accrue | Mode thinking only si confiance faible |
| Images supportées mais non affinées | Qualité variable | Éviter pour tâches visuelles critiques |

---

## 6. Prochaines étapes recommandées

1. **Obtenir une clé ASI:Cloud** — créer compte sur asicloud.cudos.org ou contacter tech@singularitycompute.com
2. **Prototyper le routeur** — fichier `harness-tools/super_decider_router.py` avec tests unitaires
3. **Évaluer sur le jeu diagnostique existant** — utiliser `routing/dataset-holdout.json` comme baseline comparatif
4. **Intégrer progressivement** — d'abord sur le triage d'anomalies (lecture seule), puis sur le routage modèle

---

*Ce document conserve les sources réellement utilisées. Les résultats de benchmark sont auto-déclarés par Singularity Compute et non indépendamment vérifiés.*

---

## 7. Révision après revue critique (fait autorité)

Trois revues indépendantes (route `asi1`, route `asi1-mini`, Morph Flash) convergent. Leurs conclusions, ajoutées aux données déjà mesurées du dépôt :

### 7.1 Décisions de la revue

| Proposition initiale | Verdict | Fondement |
|---|---|---|
| Chaîne FR→EN via asi1-mini avant Super Decider | **Rejetée** (3/3) | Valeur non mesurée ; ajoute latence, coût et une seconde surface d'exposition. Tester Super Decider directement en FR avant toute couche de traduction. |
| Ajouter `super-decider` dans `engines.py::ENGINES` | **Rejetée** (2/3) | Super Decider est un routeur, pas un moteur d'exécution. Créer un `router.py` distinct. |
| Utiliser Super Decider dans `context_pack.py` | **Rejetée** (2/3) | Le compactage est une opération destructive : pas de non-déterminisme externe là. |
| Fallback NLEmbedding comme filet de sécurité | **Révisé** | Le routage sémantique local est documenté à 20/40 sur le holdout (`routing/README.md`) et jamais activé. Ce n'est pas un filet fiable. Le repli en cas d'incertitude doit être déterministe : niveau `standard`/modèle par défaut. |
| 8 questions typées en un appel | **Révisé** | Sorties hétérogènes avec coûts d'erreur asymétriques (`tool_call_ok` faux négatif ≠ `best_model` sous-optimal). V1 restreinte : `best_model` + `confidence` seulement, seuils par question. |
| Dépendance ASI:Cloud | **Révisée** | Jamais en chemin critique : circuit breaker (timeout, cache via `loop_signatures`, trace dans `events`), option de désactivation 100% locale. |
| Envoi de prompts bruts | **Rejeté** (3/3, P0) | Anonymisation obligatoire avant tout envoi externe (chemins, IDs de session, code). |

### 7.2 Le vrai concurrent : les capacités locales Apple

Le Mac cible n'a pas que NLEmbedding. Deux capacités locales Apple sont directement pertinentes pour le rôle envisagé de Super Decider :

**Apple Intelligence / Foundation Models framework (WWDC25).** Le modèle on-device (~3B paramètres, Apple Silicon) est accessible en Swift natif via le framework `FoundationModels` : gratuit, hors-ligne, privé, sans clé API. Il offre **guided generation** (sortie contrainte par un type Swift — exactement le format `choice`/`noul`/`score` de Jev System One), **constrained tool calling** et fine-tuning LoRA. Limite connue : contexte ~4096 tokens, guardrails parfois trop zélés. Le modèle on-device d'Apple *match ou dépasse les open baselines de taille comparable* ([tech report Apple, arXiv 2507.13575](https://arxiv.org/html/2507.13575v3)). Les ~25 Go d'assets Apple Intelligence sur disque couvrent aussi les modèles serveur optionnels (Private Cloud Compute) — mais le modèle on-device seul suffit pour des décisions de routage courtes.

**Xcode Predictive Code Completion.** Le modèle local de complétion de Xcode 16+ démontre qu'Apple embarque et exécute déjà des modèles de code compétents localement. Ce n'est pas une API réutilisable, mais c'est un signal : la machine cible exécute en routine des modèles de plusieurs milliards de paramètres sans dégradation d'expérience.

**Implication pour l'architecture.** Le test comparatif prioritaire n'est pas « Super Decider vs NLEmbedding » (NLEmbedding a déjà perdu, 20/40) mais :

| Bras | Description | Coût | Privé |
|---|---|---|---|
| **A. Foundation Models on-device** | Guided generation sur 2-3 questions typées, Swift natif, même forme contractuelle que Jev System One | 0 $, hors-ligne | 100 % |
| **B. Super Decider (ASI:Cloud)** | API Jev System One, probabilités calibrées auto-déclarées | ~0 $ (crédits), réseau | Non |
| **C. Repli déterministe** | Niveau `standard` / modèle par défaut, sans prédiction sémantique | 0 $ | 100 % |

Le bras A est probablement gagnant par défaut : même espace de décision, zéro dépendance réseau, zéro fuite de données, et une API Swift qui s'intègre directement dans l'app Reprise sans passer par `engines.py`. Super Decider n'a de sens que si le bras A échoue sur le jeu d'évaluation (~100 décisions annotées) — ou pour un comparatif de calibration des probabilités, point où Jev System One prétend être supérieur.

### 7.3 Plan d'action révisé

1. **Construire le jeu d'évaluation d'abord** (~100 décisions de routage annotées, extraites de `events`/`task_engines`) — il sert aux deux bras.
2. **Tester le bras A (FoundationModels, guided generation)** sur ce jeu. Swift natif, gratuit, privé.
3. **Tester le bras B (Super Decider)** à conditions égales, en FR directement, avec prompts anonymisés.
4. **Comparer à C** (repli déterministe). Ne brancher un classificateur que s'il bat C de façon nette sur le holdout.
5. Le routage reste un `router.py` distinct, jamais un engine ; option de désactivation locale totale ; TTL sur les réservations SQLite.

### 7.4 Rappel de priorisation

La revue Astra (`ARCHITECTURE-CRITIQUE-ASTRA.md`) identifie des sujets plus urgents (faisabilité Work, course de propriété, identité du compte). Super Decider ne doit pas disperser l'effort — c'est une option d'optimisation, pas un prérequis.

### 7.5 Contrat de routage à l'interface MCP (décisions opérateur, 28/10/2026)

> **⚠️ §7.5 invalidé en partie par la revue Astra (§7.6, 28/10/2026) :** le point 2 (« pas d'injection ») est faux, le point 3 (« pas d'échec sec ») est rejeté → refus explicite si aucune destination admissible, et le mode `exact`/`routing_substituted` doivent être réécrits selon §7.6. Le §7.6 fait autorité en cas de divergence.

Décisions consécutives aux tests de fumée FoundationModels (`harness-tools/foundation-models-smoke/RESULTS.md`) — l'invariant est que **le routeur tourne dans Reprise, pas dans le harness appelant** :

1. **Le modèle n'est pas imposé par défaut.** Le harness (ou toute session avec connecteur) *peut* spécifier une route à l'appel MCP, mais ne le fait pas par défaut : Reprise route avec son routeur local (bras A, FoundationModels).
2. **Pas d'injection d'instructions possible.** Les instructions du routeur (registre figé flash/pro/codex/decline) appartiennent à la `LanguageModelSession` locale ; l'appelant ne fournit que le texte de tâche, en *contenu*. La sortie est contrainte par `@Generable` (`best_model` + `confidence`), vérifié empiriquement.
3. **Trois cas journalisés** dans `events` :
   - `routed` — pas de route fournie, décision du routeur local (avec `confidence`) ;
   - `explicit` — route fournie par l'appelant et disponible : honorée telle quelle ;
   - `routing_substituted` — route fournie mais indisponible (quota, moteur arrêté) : **Reprise route sur l'équivalent disponible** (décision du routeur local), consigne route demandée → route servie + motif. Pas d'échec sec, pas de repli silencieux.
4. **Sémantique** : la route demandée exprime un besoin de compétence, pas un identifiant figé ; le routeur local résout l'équivalence à l'instant de l'appel.
5. **Limite assumée** : le contenu de la tâche peut biaiser la classification (manipulation du signal, pas injection d'instructions) — c'est ce que le jeu annoté (`harness-tools/routing-eval/ANNOTATED-SET-V1.md`) mesurera via calibration et classe D5.

### 7.6 Revue Astra du 28/10/2026 (corrections faisant autorité)

Revue indépendante `gpt-6-astra` (effort high), thread `01a18465-9316-7391-b9d4-0f57573fa86a`, request_key `arch-review-super-decider-astra-inline-20261028`. Verdict : **§7.5 n'est pas acceptable en l'état**. Corrections retenues :

1. **« Pas d'injection possible » est invalidé.** `@Generable` contraint la forme, pas le choix ; une tâche peut biaiser une sortie conforme au schéma. Le contenu de tâche et la confiance générée **n'accordent aucun droit**.
2. **Registre à séparer en 3 dimensions** (compétence / famille de destination / décision de ne pas exécuter). Modes de routage : `auto` (Reprise choisit sous contraintes), `capability` (exigences minimales + destinations autorisées), `exact` (cible imposée — blocage sauf substitution préautorisée).
3. **« Pas d'échec sec » rejeté** : aucune destination admissible ⇒ refus explicite ou attente. `routing_substituted` reste utile si le reçu porte demande originale, cible, motif, contraintes vérifiées ; distinguer `selected_route` de `executed_route`.
4. **FoundationModels in-process : validé pour un pilote en observation** (proposition journalisée, aucun effet sur l'exécution, aucun nouveau lancement). Pas de démon ; conditions d'un futur démon listées si besoin mesuré (routage app fermée, clients multiples, isolation de pannes, coût d'init dominant). La complétion Xcode et le volume d'assets Apple ne prouvent rien — arguments retirés de la décision.
5. **Seuils V1 (C+15 pts, ECE ≤ 0,15) non défendables en l'état** — remplacés par une perte pondérée préétablie (sous-classement, refus injustifié, coût, latence) ; 100 cas = pilote diagnostique, pas une certification. La `confidence` de `@Generable` est un score déclaré, pas une probabilité calibrée.
6. **A passe les mêmes exigences que B** : « B échoue donc A est activé » est invalide.
7. **Phase 0 accélérée** : 3 ensembles — développement immédiat (historique + synthétiques indépendants + adversarial), évaluation gelée (jamais utilisée pour ajuster), observation prospective. Les tests unitaires restent des régressions, jamais un holdout. Taxonomie D1–D5 à corriger avant collecte (chevauchements ; D3 circulaire si la confiance du routeur définit la vérité terrain) ; annotation séparée : capacités, contraintes, état opérationnel, décision attendue, coût d'erreur ; seconde annotation indépendante sur les cas critiques.
8. **Priorité** : identité compte/pool, admissibilité et arbitrage de propriété AVANT toute substitution automatique ; routage actif seulement après validation ; bras externe et démon seulement si bénéfice mesuré.

**Décision proposée par Astra, retenue** : accepter le pilote local en observation, réécrire §7.5 selon les corrections ci-dessus, refuser l'activation et la substitution automatique dans leur forme actuelle.

### 7.7 Contre-analyse asi1 du 28/10/2026

Contre-analyse par le sous-agent `asi1` ayant produit les revues initiales (§7.1), sur le verdict Astra. **Accord 7/8**, fondé sur les données mesurées du dépôt (smoke D5 raté à confiance 0,91 ; ~4,5 obs/bin rend l'ECE non estimable). Les deux apports les plus solides d'Astra : suspension des seuils → perte pondérée, et rejet de « pas d'échec sec » (la substitution inventée par un score reproduit exactement l'erreur D5 du smoke).

**Réserves sur Astra** :
1. **Point 2 (modes auto/capability/exact)** : correct mais spéculatif — aucun cas `exact` n'existe aujourd'hui. Design à garder, pas un travail prioritaire.
2. **Angle mort : l'inventaire du dépôt** (~5 décisions réelles, scheduler inactif). Le « pilote d'observation » d'Astra observe un flux vide tant que la journalisation n'est pas activée — prérequis absolu non mentionné.
3. **Angle mort : HARNESS-ROLES-CONTRACT** — le principe « aucun repli payant implicite » renforce la décision d'Astra sur `routing_substituted` (substituer = consommer un quota que l'appelant n'a pas accepté) ; conclusion bonne, fondement incomplet.
4. **`loop_signatures` absent** de sa réflexion journalisation : l'annotation D2 a posteriori est impossible sans réconciliation des boucles.

**Contradictions documentaires signalées par la contre-analyse, corrigées** : §3 vs §8 d'ANNOTATED-SET-V1 (sémantique de la confiance — harmonisée) ; §4 sans marqueur « suspendu » (marqué) ; §7.5.3 vs §7.6.3 (bannière d'invalidation ajoutée en tête de §7.5, §7.6 fait autorité).

**Séquence consolidée retenue** : 1) activer la journalisation des décisions réelles (prérequis §5 d'ANNOTATED-SET-V1) ; 2) identité compte/pool + admissibilité (Astra point 8, cohérent avec ARCHITECTURE-CRITIQUE-ASTRA.md) ; 3) correction taxonomie D1–D5 avant collecte. **Restent gelés** : démon FoundationModels, substitution de routes, tout branchement automatique.
