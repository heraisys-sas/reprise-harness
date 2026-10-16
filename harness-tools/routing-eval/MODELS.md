# Modèles de l'évaluation

Inventaire vérifié le 16 octobre 2026. Sources locales : `deepseek-harness-config/cordis.patch.yml`, `CONFIGURATION.md`, `AGENTS.md`. Il s'agit de la configuration de référence du dépôt, pas d'une preuve de configuration quotidienne, d'authentification ou de crédit disponible. Aucun identifiant, jeton interne, session ni coffre n'a été lu. Aucun appel modèle externe exécuté.

## Matrice priorisée

| Priorité | Fournisseur Harness / ID exact | Rôle et condition |
|---|---|---|
| P0 | `deepseek-official` / `deepseek-flash` | Baseline unique, thinking low ; comparer high sur les mêmes cas avant toute délégation. ID officiel actuel : DeepSeek-V4.1-Flash. |
| P0 | `deepseek-official` / `deepseek-v4-pro` | Comparateur historique explicitement configuré ; challenger de Flash, pas expert présumé supérieur. Version publique : DeepSeek-V4-Pro-0813. |
| P1 | `openai-codex` / `gpt-6-astra` | Expert ciblé sur les cas difficiles, si catalogue et accès autorisé dans Harness confirmés. Aucun transfert de jeton depuis Codex. |
| P1 | `openai-codex` / `gpt-6-sol` | Comparateur équilibré déjà déclaré ; garder cet ID, ne pas le remplacer silencieusement par `gpt-6.1-sol`. |
| P2 | `openai-codex` / `gpt-6-luna` | Alternative économique déjà déclarée ; après le noyau Flash/effort/expert. |
| P2 | `deepseek-account` / `deepseek-flash`, `deepseek-v4-pro` | Routes compte déclarées. Mesurer séparément du fournisseur API officiel ; conditions d'accès et facturation inconnues. Ne pas dupliquer toutes les expériences. |
| P3 | `asi-cloud` / `asi1-mini` | Déjà configuré comme test. CUDOS annonce le modèle et un accès gratuit ; éligibilité effective, quotas et outils restent à vérifier. |
| P3 | `asi-cloud` / `asi1`, `openai/gpt-oss-20b`, `deepseek/deepseek-v4-flash-0731` | Déclarés dans le preset ; catalogue exact, disponibilité et tarifs de cette route non établis. Ne lancer qu'après vérification officielle. |

GLM, MiMo, Kimi et OpenRouter ne sont pas des bras retenus : simples possibilités dans les consignes ; OpenRouter est documenté désactivé. Le catalogue public CUDOS mentionne GLM, ce qui ne constitue pas une sélection locale.

## Coût et limites

Tarifs publics DeepSeek API en USD par million de tokens, à revalider au lancement :

| Modèle | Entrée cache hit creux / pointe | Entrée cache miss creux / pointe | Sortie creux / pointe |
|---|---:|---:|---:|
| `deepseek-flash` | 0,003 / 0,006 | 0,15 / 0,30 | 0,60 / 1,20 |
| `deepseek-v4-pro` | 0,022 / 0,044 | 0,66 / 1,32 | 1,98 / 3,96 |

La page officielle annonce 1M de contexte et 384K de sortie maximale pour les deux ; ce sont des limites fournisseur, pas celles garanties par Harness. Les anciens IDs Flash sont redirigés vers V4.1 : figer le nom ne fige pas les poids. Enregistrer modèle/version retournés si exposés. Pointe : 01–04 et 06–10 UTC du lundi au vendredi hors jours fériés chinois. Comparer les bras dans des fenêtres comparables ou rapporter le barème de chaque requête. Aucun taux USD/EUR supposé ; ne pas sommer USD et EUR. Ces tarifs permettent une estimation distincte, pas une déclaration de montant facturé. [DeepSeek, Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing/).

Low/high/max sont les efforts documentés ; medium est mappé vers high. Le mode thinking ignore notamment temperature ; ne pas présenter temperature=0 comme une garantie de reproductibilité. Avec des outils, le protocole doit préserver les champs de raisonnement requis par le fournisseur, sans exporter leur texte dans le rapport public. [DeepSeek, Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode/).

Les pages publiques OpenAI confirment les trois IDs déclarés ; elles ne prouvent pas l'accès du compte dans Harness. L'accès Codex par ChatGPT suit le forfait et ses quotas ; une clé API relève d'une facturation distincte. Le coût marginal facturé de la route abonnement reste **inconnu**, jamais assimilé à zéro ni calculé aux tarifs API. Aucun accès API OpenAI supplémentaire n'est requis pour ce plan. Sources : [GPT-6 Astra](https://developers.openai.com/api/docs/models/gpt-6-astra), [GPT-6 Sol](https://developers.openai.com/api/docs/models/gpt-6-sol), [GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna), [authentification officielle](https://learn.chatgpt.com/docs/auth).

La [page officielle CUDOS](https://www.cudos.org/) confirme ASI:Cloud, ASI Mini et gpt-oss-20b, sans établir ici les IDs d'API exacts, les prix des autres modèles ni les champs de cache facturés. La documentation ASI:One d'un autre endpoint ne doit pas servir de tarif à ASI:Cloud. La documentation ASI:Cloud n'a pas été accessible lors de cette vérification : conserver prix, accès et limites inconnus.

## Ordre avant exécution distante

1. Relever le catalogue depuis l'interface ou la commande officielle prévue par Harness, sans copier de secrets ; vérifier chaque couple fournisseur/modèle. Une entrée de catalogue ne prouve pas l'accès.
2. Confirmer l'authentification par le mécanisme prévu du fournisseur et ses conditions de facturation ; aucun achat ni nouvel abonnement. Ne pas contourner un refus ni activer une reprise payante automatique.
3. Exécuter un seul cas synthétique borné par route éligible, puis vérifier compteurs, protocole outils, modèle observé et validation. Un refus devient un résultat indisponible, pas une substitution automatique.
4. Commencer Flash low/high et expert disponible sur le jeu commun ; étendre aux bras secondaires uniquement si le pilote révèle une question utile. Mesurer toute la tâche, y compris les échecs et corrections.

Apple `fm`/NLEmbedding restent la préparation locale, pas des experts de codage validés ; l'échec `fm` 1008 observé auparavant ne justifie aucun téléchargement. Le mock Harness prouve le transport et les invariants de route, aucune qualité ou économie réelle.
