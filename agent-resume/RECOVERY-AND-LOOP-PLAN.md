# Reprise : reprise après quota, contexte et détection des boucles

Demandes utilisateur du 20 octobre 2026. Statut : exploration et plan, aucune nouvelle surveillance ni interruption activée.

Architecture affinée : [socle commun multi-harness](HARNESS-SUPERVISION-ARCHITECTURE.md). Ce contrat généralise les lots ci-dessous à tout nouvel adaptateur, y compris AutoClaw.

## Priorité produit

La mission première est de reprendre une tâche interrompue par quota dans son harness d'origine. La délégation externe sert cet objectif ; elle ne le remplace pas. Préserver la session, son parent, les décisions, les fichiers et les effets déjà produits. Un changement de harness est un transfert explicite de contexte, pas une reprise native garantie.

## Inventaire Claude vérifié localement

Inspection en lecture seule le 20/10, sans copie de conversations, identifiants de compte ou pièces jointes dans le dépôt. Les nombres décrivent ce poste à cet instant, pas une garantie de format fournisseur.

- `~/Library/Application Support/Claude/local-agent-mode-sessions/*/*/local_*.json` : 416 métadonnées et 416 dossiers avec `audit.jsonl` ; 404 dossiers `outputs`.
- Métadonnées : sessionId, cliSessionId, cwd, dates, modèle, état archivé ; peuvent aussi contenir prompts, permissions et informations de compte : appliquer une liste blanche de champs.
- Lecture bornée à 5 000 lignes par audit : 29 528 événements examinés ; 850 `rate_limit_event`, 459 `system/api_retry`, 27 `system/compact_boundary`, 6 230 blocs `tool_use`, 6 224 blocs `tool_result`. Ces nombres ne prouvent aucune boucle.
- Audits : messages, identifiants d'appels, liens `parent_tool_use_id` sur certains événements, usage input/output/cache/iterations ; certaines enveloppes `result` portent modelUsage, total_cost_usd, stop_reason. Leur sémantique comptable et leur granularité restent à valider avant agrégation ; coût déclaré ne signifie pas facture additionnelle sur abonnement.
- `compact_metadata` observé : trigger, pre_tokens, post_tokens, duration_ms, preserved_segment, preserved_messages et pre_compact_discovered_tools. Présence de ces champs ne prouve pas que Reprise peut déclencher le compactage.
- `outputs` contient des livrables réels : principalement Markdown, aussi scripts et documents. Un fichier présent n'est pas une preuve d'achèvement ni d'exhaustivité.
- `claude-code-sessions/*/*/local_*.json` : deux métadonnées, dont cliSessionId/cwd/completedTurns ; aucun audit trouvé à cet emplacement. Raccordement aux transcriptions à vérifier.
- `~/.claude/projects/*/*.jsonl` : échantillons avec messages, usage, parentUuid, compactMetadata et isCompactSummary. Ne pas confondre avec l'historique Chat Desktop ordinaire.
- Logs `~/Library/Logs/Claude` : main, MCP, web, cowork et VM. Reprise lit déjà main/MCP ; `scan_cowork` ne lit actuellement que les métadonnées d'activité.
- IndexedDB/Local Storage/Session Storage présents : aucun décodage ni preuve d'un historique Chat complet. Cookies, tokens et bases d'authentification exclus. Un lien symbolique de debug cassé a interrompu le premier survol ; l'inventaire utile a ensuite été repris par chemins ciblés.

## Lots proposés et critères de validation

### 1. Observer sans agir

Adaptateurs en lecture seule : Codex, DeepSeek Harness, Claude Code, Cowork ; Chat Desktop limité aux signaux établis. Normaliser source/version/session/parent/tour/appel, horodatage, type, usage et provenance. Distinguer inconnu de zéro ; ne pas compter plusieurs fois un message diffusé en fragments ou un snapshot cumulatif. Lecture incrémentale avec offsets et gestion rotation/troncature ; ne pas parcourir intégralement les 416 audits à chaque tick. État local privé, contenu sensible exclu du diagnostic partagé.

Tests : ligne partielle, rotation, événement inconnu, doublon, timestamp tardif, mauvais parent, usage absent, snapshot répété et coexistence de plusieurs conversations.

### 2. Préparer la reprise après quota

Checkpoint durable : objectif et contraintes, décisions, tâches terminées/restantes, essais échoués, liens parent/enfants, état Git et fichiers pertinents, tests réellement exécutés, outils en attente, effets externes incertains, prochain pas proposé. Séparer faits extraits et résumé généré. Préserver les sources ; ne pas réécrire les transcriptions natives.

Ne reprendre automatiquement que si quota confirmé, session inactive, propriété disponible, capacité native validée et absence d'arrêt volontaire. Une échéance ou un silence ne prouve pas l'arrêt. Reprendre un enfant via son parent lorsque le harness l'exige. Après un dispatch incertain, aucune seconde inférence automatique.

Tests : quota réel vs erreur réseau/auth/permission ; retour tardif ; concurrence ; parent encore actif ; redémarrage Reprise ; tâche déjà finie ; absence d'échéance fournisseur. Conserver les protections existantes du scheduler Codex.

### 3. Détecter les boucles en mode observation

Combiner répétitions d'appels normalisés, mêmes erreurs et empreintes de résultats, cycles A/B, relances identiques d'enfants, lectures répétées de grandes sorties et consommation croissante avec progrès insuffisant. Progrès = signaux pluriels (nouveau résultat utile, diagnostic changé, validation passée, tâche avancée), jamais seulement fichiers modifiés. Ne pas inférer le progrès sémantique avec certitude.

Seuils à calibrer sur traces : répétitions consécutives et cycles dans une fenêtre, budget par enfant ET par tâche parente, durée et tokens lorsque disponibles. Séparer retries transport natifs des décisions répétées du modèle. Les règles ne doivent pas appeler un LLM à chaque événement. Pour un cas ambigu, une revue ponctuelle bornée peut être envisagée après détection locale.

Réponse graduée : suspicion avec preuves → avertissement au parent → checkpoint → demande de changement d'approche/escalade → interruption ciblée seulement si une commande native fiable est disponible et la politique autorise cette action. Aucun kill global ni relance automatique de la même boucle. Arrêt demandé et arrêt confirmé sont deux états différents.

Tests : boucle exacte, alternance A/B, boucle répartie entre enfants, retry réseau borné, polling avec backoff, longue compilation, exploration légitime, progrès sans écriture, sortie volumineuse répétée, données manquantes. Mesurer faux positifs et délai d'alerte avant activation d'un arrêt automatique.

### 4. Compactage et consommation

D'abord observer les frontières natives et leur effet mesuré ; préparer un checkpoint après un jalon ou à la reprise. Le compactage natif nécessite une capacité vérifiée par moteur ; un résumé externe est une aide au transfert, pas une réduction garantie de la fenêtre native. Ne pas compacter pendant une action à effet incertain ou effacer les erreurs qui expliquent une boucle.

Réduire les dépenses : sorties d'outils bornées, recherches ciblées, réutilisation d'enfants cohérents, contrats de sous-tâche et budgets explicites, retour au parent après échecs répétés, escalade plutôt que répétition. Mesurer tokens totaux/cache, réussite validée et coût lorsqu'il est connu ; ne pas promettre une économie à partir du seul taux de cache. Ne pas multiplier les résumés payants.

Tests : checkpoint avant/après compactage, conservation des contraintes et essais échoués, reprise avec contexte partiel, annulation pendant résumé, coût du résumé supérieur au gain. Les capacités automatiques restent désactivées tant qu'elles ne sont pas validées par harness.

## Chaîne de retour Claude

Les audits et outputs enrichissent le diagnostic et permettent de proposer un résultat candidat récupérable. Pour les tâches déléguées Reprise, le contrat Quick Apply + MCP + SQLite reste valable : seul `submit_result` validé et commité marque l'achèvement accepté. L'activité disque ne remplace pas ce reçu. Les événements d'absence de retour servent à expliquer et proposer une reprise, sans prouver sa cause.

## Revue experte

Revue ciblée reçue du sous-agent `gpt-6-astra` high, fondée sur le code local, sans appel expert externe ni test exécuté. Recommandations retenues :

- Réutiliser `harness-tools/harness_usage.py` et `usage_report.py` ; la projection CLI ne couvre que les étapes racines, pas tous les enfants. Ne pas ajouter les tokens de raisonnement une seconde fois aux tokens de sortie.
- P0 : observateur incrémental et checkpoints ; P1 : boucles en observation et préparation de reprise ; P2 : arrêt ciblé et compactage natif, après validation des capacités.
- Seuils initiaux proposés, non calibrés : deux échecs identiques sur un volet déclenchent un conseil de changement d'approche ; trois appels équivalents avec mêmes résultats en cinq minutes ET aucun progrès observé donnent un candidat de boucle. Un silence de quinze minutes reste une incertitude. Dédupliquer les alertes par incident.
- `codex_rpc.py` possède `turn/interrupt` pour les tours lancés par Reprise : cela ne prouve pas un contrôle général des tours externes. Identité, propriétaire et confirmation d'arrêt sont nécessaires.
- Le callback `controller.abort` des jobs ponctuels Harness ne garantit pas l'arrêt des enfants persistants. `agentOptions.maxTokens` ne démontre pas un budget cumulé multiétape.
- Après quota, privilégier un checkpoint déterministe sans appel de résumé. Les seuils de contexte ne sont utilisables que si taille ET limite sont connues. Un résumé externe ne compacte pas la conversation native.

La revue est une proposition architecturale appuyée sur les sources locales, pas une étude des meilleures pratiques externes ni une garantie des API actuelles des fournisseurs. Compléter la matrice des capacités par documentation officielle et essais sur les versions installées avant intervention active.


Décision complémentaire : le [service Reprise bidirectionnel](HARNESS-SUPERVISION-ARCHITECTURE.md) regroupera réception, retour et délégation pour chaque harness. Priorité immédiate à la réponse Claude Desktop récupérable, y compris compte gratuit ; les fonctions de reprise et surveillance réutilisent ce registre.

## Ordre de travail quotas actualisé

Le plafond local quotidien des sollicitations Codex est supprimé. Priorité à une observation fiable et sourcée des quotas par compte/pool, avec portée, fraîcheur et inconnus explicites. Le comportement de reprise dépendant des quotas sera conçu ensuite ; ne pas étendre l’automatisation actuelle avant cette validation. Les contrôles fournisseur et protections de concurrence existants sont conservés.

## Lot planifié — remplacer les plafonds arbitraires restants

Décision utilisateur du 20/10. Statut au 21/10 : **premier lot implémenté en simulation**, voir [contrat livré et limites](DELEGATION-PERSISTENCE.md). Identité/récupération, barrière incertaine, lignée et suppression des plafonds locaux sont présentes ; intégration autonome Claude et validation réelle restent à confirmer. Suppression Codex antérieure (`4e3f39b`). Ce lot ne modifie pas la priorité de l’aller-retour Claude Desktop.

1. **Protections ciblées** : identité durable de demande/tentative, déduplication persistante, récupération après accusé perdu, absence de redistribution d’une tentative incertaine, limites de concurrence et détection des cycles de délégation. Surveiller les répétitions avec preuves et conserver une intervention graduée ; ne pas remplacer le plafond quotidien par un arrêt aveugle après un nombre fixe d’itérations.
2. **Supprimer les plafonds quotidiens locaux restants** : admission non-Codex de `codex_expert_mcp.py` (Claude Code/Desktop) et soumissions de `claude_desktop_quick.py`. Mettre à jour installateurs, options `--daily-limit`, tests et documentation ; traiter les compteurs hérités sans blocage résiduel. Aucun nouveau canal ne doit hériter de ces plafonds arbitraires. Les limites réelles du fournisseur et budgets explicitement demandés par l’utilisateur restent distincts.
3. **Fiabiliser la connaissance des quotas** : preuve par compte/profil/pool, portée, fraîcheur, échéance connue ou inconnue, changement de compte et indisponibilité de mesure ; aucune conversion arbitraire d’un compteur d’appels en quota.
4. **Ensuite seulement**, définir le comportement de reprise selon ces quotas ; ne pas généraliser entre-temps la reprise automatique actuelle.

Acceptation : appels légitimes au-delà des anciens plafonds sans `budget_blocked` local ; anciens compteurs/options sans blocage fantôme ; doublon après redémarrage sans nouvelle exécution ; envoi incertain sans renvoi automatique ; cycle de délégation signalé ; répétition légitime non assimilée automatiquement à une boucle ; refus fournisseur conservé et quota inconnu affiché explicitement. Tests simulés d’abord, aucun appel payant requis pour ces contrôles.
