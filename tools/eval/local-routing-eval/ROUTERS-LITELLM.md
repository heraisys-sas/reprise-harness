# LiteLLM : deux routages différents et trois caches

Recherche officielle **16 octobre 2026** : documentation actuelle et `litellm/router.py` sur `main`, sans installation, proxy lancé ou appel payant. Les nouveautés documentées ne prouvent pas leur présence dans une ancienne version installée. « lite-lmm » est ici interprété comme **LiteLLM**.

## Répartir des endpoints n'est pas choisir l'expert

Le Router standard résout un alias `model_name` vers des déploiements déclarés. On peut y placer plusieurs endpoints du même modèle pour disponibilité/quota ; la configuration peut aussi regrouper des modèles différents, sans garantir leur équivalence. Les stratégies ne comprennent pas, par elles-mêmes, le besoin de codage FR/EN.

| Stratégie | Décision |
|---|---|
| `simple-shuffle` | Tirage pondéré par poids/capacité déclarée, sinon aléatoire ; défaut recommandé par la documentation |
| `latency-based-routing` | Privilégie les déploiements selon latences observées, fenêtre et marge configurables |
| `usage-based-routing`, `usage-based-routing-v2` | Suivi TPM/RPM et filtrage des limites ; v2 async, Redis utile au partage d'état |
| `least-busy` | Moins d'appels en cours |
| `cost-based-routing` | Coût déclaré/estimé le plus faible parmi candidats ; pas qualité de tâche validée |

L'affinité de déploiement est un contrôle optionnel distinct, utile à la stabilité ; ce n'est ni l'affinité du serveur interne du fournisseur ni une preuve de cache partagé. [Documentation load balancing](https://docs.litellm.ai/docs/routing).

Le [code officiel Router](https://github.com/BerriAI/litellm/blob/main/litellm/router.py) confirme `simple-shuffle` par défaut, les sélecteurs séparés et le chemin coût async. Il expose également un failover pondéré intra-groupe optionnel, async, avant fallback inter-groupe : ce n'est pas une consultation d'expert. Un SDK local peut donc faire de la sélection déterministe sans appel de classification ; les completions réellement envoyées restent soumises au fournisseur.

## Auto Routing récent : classification puis sélection

La documentation **Beta, v1.94.x** présente heuristiques, mots-clés lexicaux/sémantiques, classificateur LLM, JEV et plugin personnalisé (ce dernier annoncé **v1.99.x**, config fichier). L'heuristique par défaut n'appelle aucune API. La classe mène à un modèle, un pool aléatoire ou un pool adaptatif par Thompson sampling. L'affinité de session est optionnelle, désactivée par défaut ; elle fige le nom du modèle, pas nécessairement son déploiement. Les décisions ont un marqueur de cause. LLM/JEV et embeddings distants peuvent ajouter coût et latence ; même « Test Routing » peut les facturer sans appeler le modèle final. Le backend d'embeddings est configuré : l'exemple sémantique utilise `voyage-3-5`, pas un encodeur local automatique. JEV transmet par défaut des tours précédents au endpoint configuré : ne pas l'introduire implicitement. [Auto Routing actuel](https://docs.litellm.ai/docs/proxy/auto_routing).

L'ancien [Semantic Auto Router](https://docs.litellm.ai/docs/proxy/auto_routing_semantic) est désormais **déprécié**, bien qu'encore compatible avec des configurations existantes. Son exemple configure `encoder_type: openai`, `text-embedding-3-large` et un endpoint d'embeddings explicite : il peut donc ajouter un appel embeddings, distinct d'un juge LLM. Ce n'est pas une configuration locale par défaut. Sa proximité demande/exemples ne prédit ni compétence réelle du modèle ni permission. Une intégration Apple serait un classificateur personnalisé à évaluer ; elle n'est pas fournie automatiquement.

## Fiabilité : retries et fallbacks sont du travail supplémentaire

Les erreurs sont séparées : `fallbacks` général, `context_window_fallbacks` pour dépassement de contexte, `content_policy_fallbacks` pour erreurs de politique. Les chaînes sont ordonnées ; retries, délais et cooldowns sont configurables. Les contrôles préalables doivent être activés pour l'enforcement local de fenêtre de contexte. Le proxy documente une vérification des droits et budgets pour chaque cible de fallback ; le SDK seul ne doit pas être assimilé à cette authentification de proxy. Une autre clé/fournisseur peut nécessiter l'abandon du raisonnement chiffré précédent : une bascule n'est pas une continuité garantie de contexte. [Fiabilité et fallbacks](https://docs.litellm.ai/docs/proxy/reliability).

Pour notre pilote : aucune bascule après refus de sécurité, aucun endpoint non autorisé, nombre total d'essais explicitement borné. Compter les tentatives interrompues/corrections et conserver la cause. Un basculement pour capacité de contexte ne justifie pas de retirer les consignes.

## Trois mécanismes à ne pas confondre

| Mécanisme | Ce qui est réutilisé | Limite |
|---|---|---|
| Cache exact LiteLLM | Réponse d'une requête identique selon clé de cache | Versions, portée client/projet et permissions doivent rester valides |
| Cache sémantique LiteLLM | Réponse antérieure proche dans l'espace d'embeddings | Risque de réponse obsolète ou d'un autre utilisateur |
| Cache de préfixe fournisseur | Calcul interne sur préfixes compatibles | Mesurer compteurs fournisseur ; pas de transfert automatique modèle/endpoint |

LiteLLM documente caches mémoire/disque et backends partagés ; le cache de réponses peut éviter une completion mais ne rend pas sa réponse correcte ou autorisée aujourd'hui. [Caching](https://docs.litellm.ai/docs/proxy/caching).

La documentation déconseille expressément le **cache sémantique pour agents multi-tours** : des tours successifs très similaires peuvent récupérer la même réponse et répéter un outil ; augmenter le seuil ne corrige pas fiablement ce problème. Plusieurs utilisateurs derrière une même clé virtuelle partagent par défaut le bucket sémantique. Notre futur cache ne doit donc pas prendre un `project_id` ou `session_id` libre comme autorisation. [Semantic caching et isolation](https://docs.litellm.ai/docs/proxy/caching_semantic).

## Ce qu'on reprend, ce qu'on n'installe pas

Retenir la séparation **autorisation → classification consultative → route stable → vérification de résultat**, la cause de décision, l'abstention, les contrôles de capacité et les essais bornés. Garder Flash/expert dans des rôles cohérents ; ne pas refaire une classification distante à chaque token ou appel outil. Pour notre cas, prouver « Flash suffit / expert nécessaire après échec vérifié » compte davantage que reproduire quatre niveaux abstraits de difficulté. La faible latence d'une heuristique ne valide pas ses décisions FR/EN. Un bandit adaptatif exige un signal de qualité fiable et un budget d'exploration ; il n'est pas justifié par notre petit jeu.

Le cœur hors répertoire `enterprise/` est sous MIT ; les fonctions Enterprise ont une licence distincte. Auto-hébergement ne signifie pas zéro coût : ressources locales, stockage et opérations restent nécessaires ; classificateurs/embeddings/completions distants suivent leur facturation. [Licence officielle](https://github.com/BerriAI/litellm/blob/main/LICENSE).

LiteLLM apporte surtout une passerelle multi-endpoints et des mécanismes de fiabilité. Pour le prototype actuel, ses principes peuvent être testés avec nos outils existants, sans daemon ni dépendance supplémentaire. Il ne remplace ni notre politique projet, ni une défense contre injection de consignes, ni une preuve d'économie par tâche validée.
