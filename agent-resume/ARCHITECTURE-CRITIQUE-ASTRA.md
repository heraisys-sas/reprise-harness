# Critique Astra de l'architecture multi-harness

**Mise à jour du 20/10 :** plafond quotidien local Codex supprimé du code ; les compteurs/plafonds mentionnés ci-dessous décrivent l’état historique lors de l’avis. Contrôles fournisseur conservés. Aucun nombre d’appels restants local ne doit être utilisé pour Codex.

20 octobre 2026 — sous-agent `gpt-6-astra`, raisonnement high, lecture seule du code et des documents. Aucun test ni essai fournisseur exécuté. Revue du contrat cible, pas constat que toutes ses fonctions seraient livrées.

## Verdict

Bonne direction de supervision ; feuille de route acceptable, contrat d'exécution Work encore à consolider. Conserver SQLite, la séparation observation/action, les états incertains et la distinction checkpoint/compactage. Pas de besoin établi de bus distribué.

## Réserves prioritaires

1. **P1 — Faisabilité Work avant généralisation.** Prouver créer une véritable session Work/Cowork, produire un fichier, retrouver session et résultat. `claude_desktop.py` retourne une soumission Chat avec `response: None` ; `claude_expert.py` désactive les outils. Ni l'un ni l'autre ne démontre Work. Un connecteur ne crée pas une capacité native absente.
2. **P1 — Course de propriété.** `codex_rpc.py` observe idle puis lance une commande distincte. Un utilisateur peut reprendre entre les deux. Le verrou local Reprise ne verrouille pas Desktop. Documenter l'arbitrage natif ; condition atomique si disponible, sinon concurrence résiduelle explicite et automatisation limitée.
3. **P1 — Identité du compte/quota.** Définir une identité opaque de compte/route et `quota_pool_key`, fraîcheur et portée de preuve ; invalider l'admission si compte/profil change. Le quota global de `scheduler_meta` n'est pas un modèle multi-compte. Compteur d'appels, tokens et plafond financier restent distincts. Budget strict seulement si imposé par l'exécutant ; sinon alerte avec dépassement possible.
4. **P1 — Droits réellement imposables.** Une restriction dans un prompt n'atténue pas les outils d'une session Work existante. Lier demandeur/projet/destination/autorisation et déclarer les restrictions exécutoires. Ne pas promettre techniquement la non-augmentation des droits sans mécanisme. Pilote sans effet externe, entrées figées et sorties dédiées ; réutiliser les autorisations utilisateur déjà données.
5. **P1 — Récupération asynchrone.** Le demandeur doit persister sa clé de requête avant l'envoi et retrouver le job par cette clé après perte d'accusé. Définir aussi récupération de réservation et acquittement du résultat par le parent. `ux_review_probe.py` réserve avant réponse et refuse ensuite de redonner la tâche : une réponse perdue peut bloquer une réservation. Rejouer une lecture n'est pas relancer une inférence. Traiter parent archivé/remplacé/bloqué par quota. Résultat accepté par Reprise ≠ résultat pris en compte par le parent.
6. **P2 — Couverture des checkpoints.** Déclarer sources absentes et caractère restaurable/documentaire. Vérifier les contraintes explicites connues, sans promettre leur conservation sémantique complète dans le contexte natif. Un hash ne prouve pas l'exhaustivité.
7. **P2 — MVP trop large.** Commencer par session/interruption/parent/action/reçu ; ajouter le graphe détaillé selon le besoin réel. Les répétitions sont des indices de boucle, pas une preuve suffisante d'absence de progrès.

## MVP recommandé

- Codex : interruption confirmée → checkpoint déterministe → action durable → reprise native → résultat observable ; préserver le scheduler.
- DeepSeek Harness : observation et reprise manuelle guidée tant que la disponibilité quota n'est pas prouvée.
- Boucles : observation seulement, fixtures positives et contre-exemples (polling, compilation, retries réseau).
- Un pilote Work réel conditionné au point d'entrée natif : une entrée, un livrable, aucun effet externe ; façade MCP initiale si déjà accessible au demandeur.
- Tests de panne : accusé perdu, demandeur redémarré, reprise utilisateur concurrente, compte changé, résultat tardif, arrêt non confirmé.

## Décision après revue

Réserves retenues comme exigences de validation avant implémentation active. Elles ne démontrent pas l'impossibilité des capacités visées. Priorité au spike Work et à un parcours de reprise minimal ; garder l'équivalence multi-harness comme objectif produit, sans construire tout le socle d'avance. A+B demeure une solution de retour contractualisé Claude Chat, pas une preuve d'accès Cowork.
