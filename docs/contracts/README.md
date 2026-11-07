# Index des contrats

Point d'entrée des spécifications : chaque contrat, son rôle et son statut.
L'audit d'écart courant est dans [#119](https://github.com/heraisys-sas/reprise-harness/issues/119) ;
le reste à faire vit dans la [ROADMAP](../../ROADMAP.md). Statuts : **livré** (vérifié en conditions
réelles ou simulées avec preuve), **partiel** (une partie livrée, le reste tracé), **cible**
(décision de conception, non implémenté), **référence** (règles courantes, non daté).

| Contrat | Rôle | Statut |
| --- | --- | --- |
| [HARNESS-SUPERVISION-ARCHITECTURE](HARNESS-SUPERVISION-ARCHITECTURE.md) | Architecture cible multi-harness : matrice, chaîne de reprise, registre, livraison progressive | Révisé (audit #119) — matrice à tenir à jour |
| [DELEGATION-PERSISTENCE](DELEGATION-PERSISTENCE.md) | Reçus durables, barrières de dispatch, résultats et incertitude | Référence courante |
| [HARNESS-ROLES-CONTRACT](HARNESS-ROLES-CONTRACT.md) | Rôles, reprise après quota, archivage, routage | Contrat courant ; implémentations partielles |
| [QUOTA-KNOWLEDGE](QUOTA-KNOWLEDGE.md) | Sémantique quota/échéance, classes de facturation | Référence courante |
| [CACHE-POLICY](CACHE-POLICY.md) | Cache de préfixe, plafonds retirés, réserve fournisseur | Référence courante |
| [INSTALLATION-CONTRACT](INSTALLATION-CONTRACT.md) | Installation/maintenance autonome `inspect/plan/apply/verify/remove` | Livré composants Reprise ; D3/D4 restants |
| [PROJECT-REGISTRY-CONTRACT](PROJECT-REGISTRY-CONTRACT.md) | Registre projets (UUID ↔ workspaces) | Livré |
| [ORCHESTRATOR-MEMORY-CONTRACT](ORCHESTRATOR-MEMORY-CONTRACT.md) | Mémoire et checkpoints orchestrateur, scoping par work | Livré (scoping récent) ; migration en cours |
| [CODEX-EXECUTION-CONTRACT](CODEX-EXECUTION-CONTRACT.md) | Mode implementation Codex (grant par projet) | Livré (canal) ; validation réelle restante |
| [COORDINATOR-JOBS-CONTRACT](COORDINATOR-JOBS-CONTRACT.md) | Jobs du coordinateur | Livré |
| [HANDOFF-CONTRACT](HANDOFF-CONTRACT.md) | Passation inter-harness | Convention livrée ; réservation atomique à venir |
| [CLARIFICATION-CONTRACT](CLARIFICATION-CONTRACT.md) | Clarifications de mission | Partiel (pilot local ; inter-harness à venir) |
| [APPLE-PILOT-CONTRACT](APPLE-PILOT-CONTRACT.md) | Pilote conversationnel Apple | Prototype expérimental |
| [ASI1-ORCHESTRATION-CONTRACT](ASI1-ORCHESTRATION-CONTRACT.md) | Effort raisonnement asi1 orchestré | Livré |
| [LOCAL-EXECUTION-CONTRACT](LOCAL-EXECUTION-CONTRACT.md) | Courtier Git/GitHub | Cible (évaluateur prototype) |
| [PROCESS-SUPERVISION-DESIGN](PROCESS-SUPERVISION-DESIGN.md) | Supervision des processus (registre PID, relance) | Cible non implémentée (issues #25-#28) |

Autres familles de documentation : [intégrations par canal](../integrations/),
[runbooks d'opération](../runbooks/), [gabarits](../templates/),
[maquette interface](../design/interface-structure/).
