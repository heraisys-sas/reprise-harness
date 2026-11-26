# Contrat E4 — lot O1 profil de compétence (Claude Desktop)

*Contrat du 26 novembre 2026 — request_key contrat-e4-o1-competence-20261126-claude-r2, chat ba05f0cb, question autoporteuse (deltas spec v1.1 inlinés), flux orchestré compatible daemon publié (sans taskPrompt).*

CONTRAT E4 — LOT O1 « profil de compétence »
plan_id : contrat-e4-o1-competence-20261126 · réf. : spec-e3-o1-competence-20261126@1.1 (2fba0bf) · base : 20eb2ca · statut : contrat, aucun code.
Limites : je n'ai pas de clone. Les ancrages (e) viennent de la tâche (revalidation « git diff bc32055..20eb2ca vide »), je ne les ai pas relus. Le contrat E4 exige un base_sha de 40 hex : Q7 = fournir le sha complet, je ne le devine pas.

(a) DÉCOUPAGE
Règle : chaque lot = tâche tests Lxt (écrite d'abord, rouge) + tâche code Lxc (depends_on Lxt). Auteurs distincts, allowed_paths disjoints : l'auteur du code ne touche pas ses tests. Python 3.9 : aucun `X | Y` ; test AST test_t00_py39_compat (dans L1t) appliqué à tous les modules competence_*.
- L0 (conditionnel) : delegation_verdicts, schéma + écrivain E6 seul. Si la table existe déjà à 20eb2ca, L0 disparaît et L5 la lit seulement (Q8).
- L1 reprise/competence_types.py : enums, codes de refus, schéma strict de sortie @1.1, JSON canonique + digest_entree, rejet des champs E4 interdits et du champ « confiance » en entrée. depends_on : —. Tests : T9 (sérialisation), T12 (ENTREE_INVALIDE), test_t00_py39_compat, test_t00_confiance_exclue.
- L2 reprise/competence_stats.py : fenêtre 30 j ET 50 obs (la plus restrictive), n_min=20, non_mesure, incertains à part, Wilson 90 %, recouvrement. Pur : lignes passées en paramètre ; role/kind via une table de correspondance tool+level passée en paramètre, sinon non_mesure (Q9). depends_on : L1. Tests : T3, T10 (comptage), T11 (équivalence), T9.
- L3 reprise/competence_rank.py : ordre P → admissibilité → local → prior (null au jalon 1) → quota → statique ; motifs sourcés ; refus nommés. Pur. depends_on : L1, L2 (types seulement). Tests : T1, T2, T4, T5, T6, T7, T8, T11, T9, sur des drapeaux de contraintes déjà calculés en entrée.
- L4 reprise/sensitive_paths.py + contracts/sensitive-paths.v1.json : appariement avec normalisation injectable. depends_on : L1. Tests : T4 (.env ; symlink non résoluble ⇒ sensible ; casse ; NFC ; non listé ⇒ non sensible).
- L5 reprise/competence_inputs.py : lecteur lecture seule (routing_events ⋈ delegation_verdicts, BACKENDS, admissibilité, vetos, populate_from_quota) qui calcule P et alimente L2/L3. depends_on : L1–L4, L0 si créé, E2 complète (e). Tests sur SQLite temporaire : T2, T6, T7, T10 (jointure), T12 (lignes corrompues), T9 (digest stable).
Couverture : T1 L3 · T2 L3+L5 · T3 L2 · T4 L4+L3 · T5 L3 · T6 L3+L5 · T7 L3+L5 · T8 L3 · T9 L1 · T10 L2+L5 · T11 L2+L3 · T12 L1+L5. Falsifiable : un T sans test nommé test_tNN_<nom> ⇒ lot refusé.
Acceptation par lot : cmd:/Applications/Reprise.app/Contents/Resources/runtime/bin/python3 -m unittest reprise.tests.test_competence_<x> (L4 : test_sensitive_paths) avec ≥ N tests exécutés ET les noms test_tNN présents (zéro test ne vaut pas vert). N suggérés : L1 4 · L2 4 · L3 9 · L4 5 · L5 6.
Auteurs proposés (Q10) : Meta AI = L1c, L2c, L3c ; Codex = L1t–L3t, L0, L4c, L5c ; ASI1 = auteur de L4t et L5t seulement ; verdict E6 = runner, jamais un auteur.

(b) CONTEXT / ALLOWED / PROHIBITED / BUDGETS
context_paths (lecture seule, digest recalculé @base_sha) :
- tous lots : docs/specs/spec-e3-o1-competence-claude-desktop.md (référence O1), docs/contracts/E4-TASK-CONTRACT.md (gardes).
- L1–L3 : rien d'autre (isolation, pas d'intégration).
- L4 : docs/contracts/E4-TASK-CONTRACT.md (règle sensible).
- L5 : reprise/routing_events.py (colonnes, admissibilité), reprise/backend_registry.py (BACKENDS), reprise/external_rankings.py (statut quality_index).
allowed_paths (un fichier par tâche) : L1c competence_types.py · L1t tests/test_competence_types.py · L2c competence_stats.py · L2t tests/test_competence_stats.py · L3c competence_rank.py · L3t tests/test_competence_rank.py · L4c sensitive_paths.py + contracts/sensitive-paths.v1.json · L4t tests/test_sensitive_paths.py · L5c competence_inputs.py · L5t tests/test_competence_inputs.py (tous sous reprise/ sauf contracts/).
prohibited (complément de l'interdit implicite) : reprise/expert_mcp.py, experts-manifest.json, VERSION, CHANGELOG*, build/**, reprise/routing_events.py, reprise/backend_registry.py, reprise/external_rankings.py, ci_test.py, les tests des autres lots, tout chemin hors dépôt.
Budgets suggérés, non mesurés : wall_clock_s / max_diff_files / max_diff_lines / ttl_s.
L1c-t 1500/1/250/7200 · L2c-t 1800/1/300/7200 · L3c 2400/1/450/7200 · L3t 2400/1/500/7200 · L4c 1800/2/250/7200 · L4t 1500/1/250/7200 · L5c 2700/1/400/10800 · L5t 2700/1/450/10800. max_parallel=1, sauf L2c∥L3c = 2 (écritures disjointes). quota_ceiling = référence populate_from_quota, max_pct non fixé par moi (Q11).

(c) LOT PRIORITAIRE META AI
Désigné : L3c (précédé de L1c ; L2c en parallèle). Raisons falsifiables : (1) module pur, sans E/S, DB, réseau ni FS : un test AST refuse tout import de sqlite3, socket, subprocess, urllib, http, os ; (2) il porte 9 des 12 comportements T ; (3) aucun secret, aucune intégration touchée ; (4) tests synthétiques déterministes, donc parallélisable.
Exclus de Meta AI : L0, L5 (routing_events, registre, SQLite), L4 (le contenu de la politique sensible est une garde : code sensible, interdit à Meta AI par la table des rôles), tout module qui lit ou écrit des secrets. Falsifiable : un diff Meta AI qui touche l'un de ces chemins ⇒ PERIMETRE_DIFF.

(d) REGISTRE DES CHEMINS SENSIBLES
Emplacement : contracts/sensitive-paths.v1.json, versionné, propriétaire Olivier. Format :
{"schema":"sensitive-paths@1","version":1,"owner":"Olivier","semantique":{"non_liste":"non_sensible","doute":"sensible"},"regles":[{"id":"<kebab unique>","classe":"<str>","glob":"<*, **, ?>"}]}
additionalProperties=false ; pas de regex ; un appariement suffit à rendre sensible.
Contenu initial : cle-ssh ~/.ssh/** · cle-pem **/*.pem · cle-key **/*.key · env **/.env et **/.env.* · trousseau **/*keychain* et ~/Library/Keychains/** · jeton **/*token* · cookie **/*cookie*.
Normalisation avant appariement : realpath, ~ expansé, NFC, insensible à la casse, relatif à la racine du dépôt. Fail-closed : non résoluble ou ambigu ⇒ sensible.
Revue : ajout = commit sur dev avec go d'Olivier ; retrait = justification écrite ; version monotone. Falsifiable : validation du schéma, ids uniques, et pour chaque règle un exemple positif et un négatif dans les tests (pas dans le registre). Tout changement après le lot L4 est hors portée des délégués.
Faux positifs assumés : **/*token* rend sensible tout fichier du dépôt dont le nom contient « token », donc actor local seulement (Q13).

(e) ANCRAGES @20eb2ca (statut explicite)
- routing_events, colonnes SCHEMA_V2 : reprise/routing_events.py#L40-61 : id, ts, type ∈ {routed, explicit, routing_substituted}, tool, channel, requested_route, served_route (JSON), level, terminal_state (défaut 'dispatched'), request_key, thread_hash, loop_signature, reason, account_key, admissibility (défaut 'not_required'). Repris de la tâche.
- admissibilité : reprise/routing_events.py#L55-58. Repris de la tâche.
- backend_registry.BACKENDS : reprise/backend_registry.py#L29-60 (billing, modes, workspace_write, consent, memory_snapshot). Repris de la tâche.
- NON ANCRÉS (décrits dans le dossier, aucune ligne fournie) : tables route_admissibility et route_refusals, fonctions admissible(), vetos(), populate_from_quota, delegation_verdicts. Je n'affirme rien dessus ; L5 reste bloqué jusqu'à une E2 citée fichier#lignes@sha40 (Q12). L1–L4 n'en dépendent pas.
- quality_index (external_rankings) : « réservé, non implémenté » d'après le dossier, sans ligne : NON ANCRÉ. Contrat : source=null au jalon 1 ; L5 force le prior à null ; test T1 inclut un prior non nul injecté.
- role et kind ne sont pas des colonnes : dérivation par table passée en paramètre (Q9).

QUESTIONS OPÉRATEUR
Q7 sha complet (40 hex) de 20eb2ca, et confirmation que le « diff vide » a bien été relu par une E2.
Q8 delegation_verdicts existe-t-elle à 20eb2ca (schéma, écrivain) ? Sinon L0 est à créer par Codex.
Q9 qui fournit et valide la table tool+level → role/kind ? Sans elle, tout est non_mesure.
Q10 attribution des auteurs code/tests telle que proposée (SOD) ?
Q11 max_pct de quota_ceiling.
Q12 E2 manquante pour les ancrages non cités avant L5.
Q13 faux positifs de **/*token* et **/*cookie* acceptés (fail-closed) ?

RÉPONSES OPÉRATEUR (27 nov. 2026, délégation d'autonomie — révisables par Olivier)

Gouvernance (directive Olivier du 27 nov., « sauvegarde ou corrige les specs/contrats au fur et à mesure ») : chaque spec/contrat et chaque réponse opérateur est versionné au dépôt AVANT tout dispatch E5 ; les corrections passent par des commits sur la branche courante, jamais par une réécriture silencieuse.

- Q7 : SHA complet de 20eb2ca = `20eb2ca7f4f84b14e16ffc4a2780a002271818fa`. Le diff vide `bc32055..20eb2ca` sur routing_events.py et backend_registry.py a été relu par une E2 réelle (ASI1, commandes git exécutées le 27 nov., sortie vide constatée).
- Q8 : `delegation_verdicts` N'EXISTE PAS à 20eb2ca (recherche sur reprise/*.py hors builds : zéro hit) → lot L0 CONFIRMÉ, à créer par Codex : table append-only keyée request_key, écrivain E6 seul (réf. WORKFLOW-REPRISE.md §E8, erratum contrat §10/Q12).
- Q9 : la table tool+level → role/kind sera fournie par ASI1 à partir de la cartographie des canaux réels (lignes routing_events observées), au plus tard avec L5 ; d'ici là, `non_mesure=true` est le comportement requis, jamais une déduction silencieuse.
- Q10 : CONFIRMÉ, attribution telle que proposée : Meta AI = L1c, L2c, L3c ; Codex = L1t–L3t, L0, L4c, L5c ; ASI1 = L4t et L5t seulement ; E6 = runner, jamais auteur.
- Q11 : au jalon 1, quota_ceiling = veto binaire via populate_from_quota (reprise/routing_events.py#L232@20eb2ca) seulement ; PAS de max_pct (le champ n'existe pas dans le schéma de sortie O1 et n'est pas requis).
- Q12 : E2 COMPLÉMENTAIRE citée @20eb2ca — `admissible()` reprise/routing_events.py#L178 ; `vetos()` reprise/routing_events.py#L205 ; `populate_from_quota()` reprise/routing_events.py#L232 ; table route_admissibility reprise/routing_events.py#L55 ; table route_refusals reprise/routing_events.py#L142 ; quality_index « réservé… non implémenté » reprise/external_rankings.py#L11-L13, METRICS (inclut quality_index) #L31, `route_choice()` #L475. L5 est débloqué au plan des ancrages.
- Q13 : CONFIRMÉ, faux positifs assumés fail-closed. Impact vérifié dans le dépôt : reprise/vendor/yaml/tokens.py et tools/probes/mimo/token-plan-ams.patch.yml deviennent sensibles (acteur local seulement pour ces chemins). Le registre reste propriété d'Olivier ; le contenu initial est livré avec L4 et tout retrait exigera une justification écrite.