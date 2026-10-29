# Revue critique : Super Decider + Traduction FR→EN

**Date :** 2026-10-28
**Auteur :** ASI1 (architecte logiciel)
**Statut :** Analyse en lecture seule — aucune implémentation

---

## 1. Pertinence du Super Decider pour le routage multi-agent

### Forces
- **Latence et coût prévisibles.** Un classificateur léger (~$0,03/M tokens) est bien moins cher qu'un LLM généraliste pour des décisions binaires/multiclasse. Si les 8 questions sont réellement calibrées, le coût marginal par décision est négligeable.
- **Séparation des préoccupations.** Un modèle dédié aux décisions probabilistes évite la dérive où un même LLM à la fois décide *et* exécute, ce qui introduit un biais d'autoréférence (le modèle tend à choisir la route qu'il connaît déjà).
- **Intégration avec `engines.py`.** Ajouter `super-decider` comme engine dans `ENGINES` est trivial (lignes 12-20). Le contrat `validate_session()` / `options()` / `assign()` existe déjà.

### Faiblesses critiques
- **Le problème n'est pas une classification.** Le routage multi-agent ne se réduit pas à 8 scores indépendants. Les questions clés sont :
  - Quelle est la **capacité requise** (expertise, review, batch, work) ?
  - Quel harness a cette capacité **et** est disponible (quota, session active, permissions) ?
  - Y a-t-il un **conflit de propriété** (un autre agent travaille déjà sur ce lot) ?
  - La tâche nécessite-t-elle un **contexte spécifique** que seul un harness possède ?
  
  Un classificateur produit des probabilités ; il ne peut pas interroger un registre de capacités en temps réel. Il doit *deviner* la disponibilité plutôt que la *lire*.

- **#1 sur JevBench API n'est pas une preuve de pertinence.** Un benchmark composite mesure la performance sur un ensemble de tâches prédéfinies. Cela ne garantit pas que le modèle généralise au routage de délégation Reprise, qui implique des contraintes spécifiques (quotas, ownership, handoff).

- **Les heuristiques NLEmbedding existantes font déjà ~80% du travail.** Le code actuel utilise des règles déterministes (fichiers `routing/smoke.py`, `routing/test_prototypes.py`). Remplacer ou compléter par un modèle probabiliste ajoute une couche d'indirection sans résoudre les problèmes fondamentaux identifiés par la revue Astra (course de propriété, identité du compte, récupération asynchrone).

### Verdict P1
**Non adapté comme routeur principal.** Peut servir de *couche d'aide* pour des cas ambigus (ex: "ce prompt ressemble-t-il à une expertise vs une simple question ?"), mais ne doit jamais être le seul arbitre. Les heuristiques déterministes doivent rester la source de vérité.

---

## 2. Fiabilité de la chaîne FR→EN→Décision

### Analyse technique
```
Utilisateur (FR) → asi1-mini → anglais EN → Super Decider → décision
```

### Problèmes
- **Perte sémantique irréversible.** La traduction FR→EN introduit systématiquement une erreur de composition. Même à 99% de fidélité, sur 30 prompts testés, cela signifie 1 prompt mal traduit. Sur un pipeline de 50 décisions/jour, c'est 0,5 erreur/jour. Une erreur de traduction d'un identifiant technique (`session-key`, `request_hash`, chemin Unix) peut rendre la décision incompréhensible ou dangereuse.
  
- **asi1-mini n'est pas un traducteur spécialisé.** Le modèle `asi1-mini` est un LLM généraliste léger. Son rôle ici est de traduire du texte technique français vers anglais. Ce n'est pas un outil de traduction dédié (comme DeepL ou un modèle fine-tuned pour la traduction). Sa "fidélité à 99%" n'est pas mesurée statistiquement de manière rigoureuse — c'est une estimation subjective sur un échantillon de 30 prompts.

- **La latence totale (<1s) est illusoire.** 0,5s pour la traduction + 0,3s pour la décision suppose un appel API séquentiel sans retry, sans timeout, sans gestion d'erreur. En conditions réelles :
  - Timeout réseau : +5s
  - Retry après erreur 429 : +10-30s
  - Dégradation du service externe : variable

- **Pourquoi traduire ?** Si Super Decider est hébergé sur ASI:Cloud et reçoit des prompts en anglais, c'est une hypothèse non justifiée. Rien ne prouve qu'un classificateur performe mieux en anglais qu'en français, surtout si les prompts contiennent du français technique (noms de fichiers, commandes shell, termes Swift/Objective-C).

### Recommandation
**Supprimer l'étape de traduction.** Soit :
1. Super Decider accepte le français directement (prompt en FR),
2. Ou on garde les heuristiques NLEmbedding qui fonctionnent en FR sans traduction.

La chaîne FR→EN→décision ajoute du bruit, de la latence, et un point de défaillance sans valeur ajoutée démontrée.

---

## 3. Dépendance externe : CUDOS/ASI:Cloud comme fournisseur unique

### Risques
- **Single point of failure (SPOF).** Si ASI:Cloud est indisponible (panne, rate-limit, changement de pricing, fermeture du service), toute la décision de routage est bloquée. Le fallback "heuristiques NLEmbedding" est mentionné mais n'est pas implémenté dans le code actuel — c'est une intention, pas une garantie.

- **Coût caché de la dépendance.** $0,03/M tokens entrée semble faible, mais :
  - Chaque décision inclut "état + questions typées" = potentiellement plusieurs milliers de tokens d'entrée.
  - À 100 décisions/jour × 3000 tokens × $0,03/M = ~$0,009/jour. Moins d'$1/mois. Le coût est acceptable.
  - **Mais** si le modèle commence à recevoir des contextes complets (checkpoints, historique de session) en entrée, le coût explose.

- **Verrouillage propriétaire.** Le modèle est classifié comme "#1 sur JevBench API". Si JevBench évolue ou si le modèle change sans préavis, Reprise perd sa capacité de décision sans moyen de bascule.

### Recommandation
**Implémenter le fallback avant d'activer le Super Decider.** Le fallback heuristique doit être :
1. Fonctionnel et testé (pas juste documenté),
2. Activé automatiquement si l'appel API échoue > 3s,
3. Loggé explicitement ("Super Decider indisponible, fallback heuristique activé").

---

## 4. Architecture globale : conflits potentiels

### Conflits avec le code existant

| Fichier | Conflit potentiel |
|---|---|
| `engines.py` | Ajouter `super-decider` dans `ENGINES` est propre, mais `super-decider` n'est pas un *moteur d'exécution* — c'est un *routeur*. Il ne devrait pas entrer dans `ENGINES` qui liste les exécutables (codex, claude, dsh). Créer un module `router.py` séparé. |
| `routing/plugin/` | NLEmbedding existe dans `routing/smoke.py` et `routing/test_prototypes.py`. Le remplacer par Super Decider créerait une duplication : deux routeurs parallèles. Mieux : ajouter Super Decider comme *option* derrière NLEmbedding, pas à la place. |
| `scheduler_service.py` | Le scheduler gère le cycle de vie du service launchd. Appeler Super Decider "pour reprise post-quota" mélange deux responsabilités : le scheduler doit gérer l'ordonnancement, pas la décision de routage. Déplacer l'appel dans un module `router.py` indépendant. |
| `context_pack.py` | Décider quoi compacter via Super Decider est problématique. Le compactage est une opération destructive (perte de contexte). Une décision probabiliste ne doit pas déclencher une action irréversible. Utiliser des heuristiques déterministes (taille du contexte, nombre de tours, marqueurs de bordure). |
| `Dashboard.swift` | Afficher les décisions Super Decider dans l'UI est pertinent (transparence), mais attention à ne pas exposer de secrets (clés API, chements internes). |

### Conflits avec les contrats existants
- **HARNESS-SUPERVISION-ARCHITECTURE.md** (§ Adaptateurs et équivalence fonctionnelle) : chaque adaptateur doit traiter les mêmes rubriques. Super Decider n'est pas un adaptateur — c'est un composant de routage transversal. Il ne rentre dans aucune catégorie existante.
- **ARCHITECTURE-CRITIQUE-ASTRA.md** : les réserves P1 (Work faisabilité, course de propriété, identité du compte) sont prioritaires sur l'ajout d'un routeur probabiliste. Le Super Decider ne résout aucun des points P1.

---

## 5. Single Point of Failure : Reprise (app macOS)

### Réponse directe
**Oui, si Reprise est fermée, l'orchestration s'arrête.** C'est une limitation architecturale connue, documentée dans HARNESS-ROLES-CONTRACT.md :

> "L'application Mac n'est pas un service distant toujours disponible"

Cette limitation est inhérente au design macOS-native. Les solutions possibles :
1. **Worker permanent** (IronClaw) : traite les issues courtes et conserve checkpoints/résultats. Documenté dans HARNESS-ROLES-CONTRACT.md § Worker permanent. Mais IronClaw dépend aussi de Reprise pour la coordination.
2. **Service launchd** : `scheduler_service.py` installe un service `local.agent-resume` qui tourne en arrière-plan. Mais ce service gère le cycle de vie, pas la logique de décision.
3. **Décentralisation** : déplacer une partie de la logique de routage dans chaque harness (Codex, Claude, etc.) pour qu'ils puissent continuer autonomement. Complexité élevée.

### Recommandation
Accepter la limitation. Reprise est une app macOS, pas un service cloud. Documenter clairement ce qui se passe quand elle est fermée (les workers en cours continuent, mais aucune nouvelle délégation n'est initiée). Ne pas promettre une orchestration 24/7 depuis une app desktop.

---

## 6. Sécurité : envoi de prompts techniques à un service externe

### Analyse
- **Prompts envoyés :** textes utilisateurs + questions typées (8 champs structurés). Si les textes utilisateurs contiennent du code, des chements de fichiers sensibles, des identifiants API ou des données métier, ils transitent vers ASI:Cloud.
- **Risques :**
  1. **Fuite de code source** : un prompt technique peut contenir du code propriétaire.
  2. **Fuite d'infrastructure** : chements de sockets, noms de sessions, IDs de tâches.
  3. **Conformité RGPD** : si les prompts contiennent des données personnelles.
  4. **Supply chain** : le modèle "Singularity Compute" n'est pas un fournisseur audité. Qui contrôle ses données ? Combien de temps sont-elles conservées ?

- **Atténuation minimale requise :**
  1. Anonymiser les prompts avant envoi (remplacer les chements absolus par des placeholders, masquer les IDs de session).
  2. Politique de rétention claire côté fournisseur (suppression immédiate ou configurable).
  3. Option pour désactiver complètement l'envoi externe (fallback 100% local).

### Verdict
**Acceptable uniquement si les prompts sont anonymisés.** Sans anonymisation, c'est inacceptable pour un outil de supervision d'agents travaillant sur du code propriétaire.

---

## 7. Alternatives : approche meilleure ou plus simple

### Alternative A : Améliorer les heuristiques existantes (Recommandée)
Au lieu d'ajouter un modèle externe :
1. Enrichir `routing/smoke.py` avec des règles basées sur le **contenu réel** de la tâche (type de fichier modifié, langage détecté, complexité estimée).
2. Utiliser `context_pack.py` pour extraire des features structurées (pas de texte brut envoyé à un modèle externe).
3. Ajouter un scoring local (pondération de règles) au lieu d'un classificateur externe.

**Avantages :** zéro dépendance externe, 100% privé, prévisible, testable unitairement.
**Inconvénient :** moins "intelligent" qu'un LLM, mais les heuristiques couvrent 80% des cas courants.

### Alternative B : Router interne avec le modèle courant (Pragmatique)
Utiliser le modèle déjà disponible (DeepSeek Harness, Codex) pour décider du routage, sans passer par un service externe :
1. Le coordinateur (ASI1) pose les questions de routage au modèle courant.
2. Le modèle retourne une décision structurée (JSON).
3. Aucun token supplémentaire consommé (cache de préfixe réutilisé).

**Avantages :** gratuit (déjà payé), privé, cohérent avec le reste du système.
**Inconvénient :** le modèle peut être biaisé vers sa propre route.

### Alternative C : Hybrid Heuristics + LLM (Compromis)
1. Heuristiques déterministes pour les cas clairs (>80%).
2. Appel Super Decider uniquement pour les cas ambigus (<20%).
3. Toujours garder le dernier mot aux heuristiques (vérification post-décision).

**Avantages :** coût minimal, couverture maximale, sécurité préservée.
**Inconvénient :** complexité accrue de maintenance.

---

## Synthèse : Priorités

| Priorité | Action | Impact |
|---|---|---|
| **P0** | Supprimer l'étape FR→EN. Pas de valeur ajoutée, ajoute du bruit et un SPOF. | Haut |
| **P0** | Implémenter le fallback heuristique AVANT d'activer Super Decider. | Haut |
| **P1** | Anonymiser les prompts avant tout envoi externe. | Critique |
| **P1** | Ne pas intégrer Super Decider dans `ENGINES`. Créer un module `router.py` distinct. | Moyen |
| **P2** | Ne pas utiliser Super Decider pour décider du compactage (opération destructive). | Moyen |
| **P2** | Explorer Alternative A (heuristiques enrichies) comme routeur principal. | Haut |
| **P3** | Documenter les limites de Reprise (orchestration stoppée si app fermée). | Faible |

---

## Conclusion

L'idée de séparer la décision du routage de l'exécution est **bonne en principe**. Mais l'implémentation proposée — un classificateur externe dépendant d'un service tiers, avec une chaîne de traduction FR→EN superflue — introduit plus de risques que de valeurs.

**Recommandation finale :** commencer par l'Alternative A (heuristiques enrichies locales), valider que ça couvre les cas courants, puis envisager Super Decider comme optionnelle pour les cas ambigus (Alternative C), jamais comme routeur principal.

La revue Astra (#5, ARCHITECTURE-CRITIQUE-ASTRA.md) identifie déjà des priorités P1 (Work faisabilité, course de propriété, identité du compte) qui sont **plus urgentes** que l'ajout d'un routeur probabiliste. Ne pas disperser les efforts sur Super Decider au détriment de ces enjeux fondamentaux.
