# Connaissance des quotas — lot du 21 octobre 2026

Ce lot observe des métadonnées et conserve leur provenance. Il ne décide d’aucune reprise, admission ou sélection de moteur. Le registre de délégation livré dans `3bae737` reste en place. La garde fournisseur Codex, dont sa réserve de 5 % et son refus des mesures inconnues, est inchangée.

## Parcours livré

- CLI : `./resume quota-status --engine codex-server` consulte le dernier état local ; `--refresh` demande explicitement une lecture native de métadonnées. `--socket` choisit une route explicite. Sans option, la configuration Codex enregistrée est utilisée, puis le socket natif par défaut ; aucune bascule vers un autre transport n’est tentée.
- `--engine codex-cli --refresh` utilise le lecteur stdio existant, sans lancer de tour. C’est un contexte distinct du socket propriétaire ; `CODEX_HOME` entre dans son identité de route, sans lecture de fichier d’authentification.
- MCP : `reprise_quotas({"engine":"codex-server","refresh":true})`. Sans `refresh`, lecture du registre uniquement. La route du pont est utilisée ; ce point d’entrée ne donne pas de choix libre de socket/profil. Aucune clé de demande d’inférence n’est requise pour cette lecture.
- Reprise native : bouton **Lire les quotas**, lecture explicite du socket configuré. Les actualisations ordinaires relisent seulement le registre pour recalculer sa fraîcheur. Aucun polling fournisseur supplémentaire n’est activé.
- Claude Code/Desktop et Harness ont une route consultable, mais une actualisation retourne `quota_source_not_implemented` et un quota inconnu. `--profile` identifie le profil Harness explicitement choisi ; ni son fournisseur ni son compte ne sont déduits de ce nom. L’alias Codex automatique est refusé : choisir le transport à observer.

Les observations sont privées dans `tasks.sqlite` pour le CLI/l’app et `expert.sqlite` pour le pont MCP. Ces deux installations ne partagent pas implicitement leur état. Aucun enregistrement brut de réponse, identifiant de compte, email, token ou contenu de conversation n’est persisté dans ce nouveau registre. La clé locale de pseudonymisation et les observations ne sont pas exportées par `share`.

## Identité et preuve

Chaque observation possède un identifiant SQLite, sa route opaque, son moteur, son fournisseur effectivement établi, son profil lorsqu’explicite, sa source, sa date de lecture et les fenêtres rapportées. Les champs absents restent `null`. Le fournisseur est établi pour la source native de limites ChatGPT Codex ; les autres moteurs ne reçoivent pas un fournisseur supposé à partir de leur nom.

Pour Codex, seul `account/rateLimits/read.accountId`, dans **la même réponse que les limites**, établit l’identité du compte de la mesure. Il n’y a ni jointure par email ni lecture du stockage d’authentification. Ce champ n’est pas présent dans toutes les versions : s’il manque, l’identité demeure inconnue et les fenêtres sont limitées à l’observation de route. Le contexte actif peut changer après la lecture ; une observation récente ne prouve pas à elle seule le compte actuellement sélectionné.

Les clés compte/pool/route/profil sont des HMAC-SHA-256 avec un sel aléatoire local au registre, conservé pour les redémarrages. Elles sont pseudonymes, pas anonymes. Deux registres indépendants ne se rapprochent pas par ces clés. Un `quota_pool_key` nécessite fournisseur + compte prouvé + identifiant de pool rapporté. Deux routes ne partagent un pool que si les mêmes preuves de compte et de pool sont présentes. Une clé de carte et un `limitId` contradictoires ou invalides ne deviennent pas une identité connue.

La carte `rateLimitsByLimitId` est prioritaire, y compris lorsqu’elle est vide. Le champ historique `rateLimits` n’est utilisé que lorsque cette carte est absente ou `null` ; il n’ajoute jamais une deuxième mesure du même pool. Un champ historique sans `limitId` ne permet pas d’inventer un pool. Les entrées invalides sont écartées et la couverture partielle est signalée.

`ordinary_usage_allowed_observed` est un booléen strict ou `null`, conservé à sa portée native de réponse. Ce n’est pas une permission calculée par pool. Les pourcentages de fenêtre sont validés indépendamment : booléens, chaînes, valeurs non finies et hors `[0,100]` sont inconnus. Un zéro valide reste zéro. Durée et reset sont facultatifs et ne conditionnent pas la conservation du pourcentage. Aucun nom de fenêtre ne fixe une durée supposée.

## Fraîcheur et échec

`observed_at` date la fin de la lecture locale, pas la dernière mesure du fournisseur. `source_observed_at` est inconnu pour cette API. La consultation calcule `recent_read`, `stale`, `clock_skew`, `reset_elapsed` ou `unobserved`, avec un âge maximal local de 300 secondes exposé dans la réponse. Cette durée est une convention d’observation, **pas un quota ni une garantie de fraîcheur du cache fournisseur**.

Un reset dépassé rend l’état actuel inconnu ; il ne signifie ni « épuisé » ni « renouvelé ». Une nouvelle tentative de lecture échouée devient le dernier état inconnu, sans réutiliser silencieusement une ancienne réussite. La dernière réussite reste conservée dans l’historique, référencée séparément avec `historical_only:true`. Le registre n’en déduit aucune nouvelle date de reprise. Les fenêtres et leurs pourcentages conservés sont toujours des observations, pas une promesse de capacité actuelle ; `effective_remaining_percent` reste `null`.

Consommation observée, crédit monétaire, budget choisi et quota fournisseur sont distincts. Ce lot ne convertit ni les tokens, ni le coût déclaré, ni les crédits, ni les anciens compteurs d’appels en quota restant. Les champs de budget et de consommation ne sont pas alimentés par la collecte de limites. Aucun plafond local quotidien n’est réintroduit.

## Compteur de cinq heures signalé

Le 21/10, l’utilisateur a signalé la fenêtre de cinq heures affichée expirée avec une date de reprise fausse (précision après avoir initialement évoqué la fenêtre hebdomadaire). La cause exacte de la version testée n’a pas été reproduite visuellement. Trois défauts établis dans les sources ont été corrigés : la dernière mesure globale des journaux était présentée comme « quota du compte » sans attribution ; tout reset passé devenait « épuisé » ; seul l’horaire du reset était affiché. Le pourcentage disparaissait aussi si le reset était absent.

La carte principale utilise désormais le registre et une lecture explicite. Les journaux restent dans un volet séparé « compte non attribué ». Le libellé « cinq heures » exige une durée rapportée de 300 minutes ; le libellé hebdomadaire exige 10 080 minutes. Un reset passé donne « mesure périmée » et quota actuel inconnu ; le reset, lorsqu’il existe, affiche date et heure locales complètes. C’est un reset déclaré par la source, jamais une date de reprise garantie. L’app est compilée sans être relancée ; la confirmation visuelle dans une nouvelle exécution reste à faire.

## Sources effectivement vérifiées

| Source | Vérification et portée |
|---|---|
| CLI installé `0.163.0-alpha.10.2` | `codex app-server generate-json-schema --out …` : `GetAccountRateLimitsResponse.accountId`, `ordinaryUsageAllowed`, carte des pools ; fenêtres `usedPercent`, `windowDurationMins`, `resetsAt`. Les schémas sont générés depuis le binaire, sans authentification ni inférence. |
| Socket Codex existant | Une lecture supervisée réelle `initialize` → `initialized` → `account/rateLimits/read` a confirmé `accountId` et un pool `codex`, fenêtres de 300 et 10 080 minutes, pourcentages et resets présents. Sortie expurgée ; compte brut non affiché ni conservé. Aucun lancement/redémarrage de daemon. |
| `quota.py`, `codex_rpc.py`, `codex_expert.py`, `owner_route.py`, `scheduler.py` | Inventaire des lecteurs existants. Le cache global de `scheduler_meta` n’est pas une preuve multi-compte ; sa politique n’est pas migrée dans ce lot. |
| `~/.codex/sessions/…` | Trois fichiers récents échantillonnés de façon bornée, uniquement champs de quota : `limit_id`, `primary/secondary.used_percent/window_minutes/resets_at`, crédits et contrôles monétaires distincts. Pas d’identité de compte de la mesure établie. Aucun collecteur supplémentaire de conversations n’est installé. |
| `~/.codex/state_5.sqlite` | Schémas de `threads`, `projects`, `project_roots` et relations consultés. `creator_account_id`/`creator_user_id` existent mais leurs valeurs n’ont pas été lues : le créateur d’un travail n’établit pas le compte fournisseur courant. |
| `~/.codex/logs_2.sqlite`, logs Desktop | Arborescence et schéma technique seulement, sans corps de journaux ni identifiants. Aucune table spécialisée de quotas démontrée. |
| Claude Code/Desktop | `detect.py` reconnaît une erreur `rate_limit_error` ; inventaire antérieur de `rate_limit_event` sans schéma de compte/pool validé ici. Les adaptateurs d’expertise gardent `quota_remaining:null`. Quick Apply n’est pas une sonde de quota. |
| DSH | `harness-events.cjs`, `detect.py` : `QUOTA`, `ACCOUNT_QUOTA`, `RATE_LIMIT` sont des symptômes de refus. `harness-tools/harness_usage.py` et `usage_report.py` décrivent tokens/cache/coût, pas quota restant. Le profil est une route, pas une preuve de compte/pool. |
| Futurs adaptateurs | Aucune identité, limite ou échéance déduite d’un nom de produit ou de modèle. Source structurée et portée à valider avant collecte. |

La [documentation officielle OpenAI App Server](https://learn.chatgpt.com/docs/app-server#6-rate-limits-chatgpt) distingue la carte multi-pool du champ historique et définit le pourcentage consommé, la durée en minutes et le reset Unix en secondes. Le champ d’identité de notre adaptation est justifié par le schéma du binaire installé, et non supposé depuis un exemple documentaire. Les preuves locales ne démontrent pas les capacités de versions non testées ni la portée d’un quota Claude ou DSH.

## Validation

`tests/test_quota_knowledge.py` couvre identité manquante/changée, routes et homes CLI distincts, redémarrage, pseudonymisation et liste blanche, pools multiples/incohérents, carte vide/ancienne, valeurs invalides, reset absent/passé, données périmées, horloge incohérente, échec après réussite et absence de commande d’inférence. Les gardes et tests du scheduler existant restent exécutés séparément dans la suite complète.

Validation finale : **297 tests, OK, 1 skipped**, suite complète hors sandbox (les tests de sockets existants requièrent cet accès). Les 21 tests ciblés de connaissance passent aussi séparément. `sh native-install` et `swiftc -parse` réussissent, sans `--open` ni relance. `py_compile` et `git diff --check` passent. Une sonde réelle de métadonnées du socket existant a été exécutée avec sortie expurgée ; aucune inférence ni aucun repli payant. Les `ResourceWarning` de fixtures déjà présentes ne sont pas des échecs. Confirmation visuelle des compteurs dans une nouvelle exécution encore à faire.
