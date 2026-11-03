# AFM Apple comme aide à la décision de type Super Decider

**Date :** 2 novembre 2026. **Statut :** analyse en lecture seule ; expérimentation en observation seulement. Baseline du clone avant travaux : `606fcd2458d1f7ae742ac4597a68eab2dfa25000`. Les nouveaux bancs et prototypes cités sont encore des travaux locaux non publiés. Cette analyse ne modifie aucune politique de routage et n’active aucune route.

## Décision

Le modèle Apple local est un **candidat techniquement pertinent** pour qualifier une demande courte, suggérer une capacité ou une destination parmi celles présentées, demander une précision et proposer l’abstention. Son aptitude à remplacer Super Decider et sa fiabilité pour choisir automatiquement un harness **ne sont pas établies**. Le pilote peut produire des propositions observées, jamais des permissions ni une exécution automatique. Cette décision applique les [corrections faisant autorité §7.6–7.7](SUPER-DECIDER-INTEGRATION.md#76-revue-astra-du-28102026-corrections-faisant-autorité).

Le contrat de rôles conserve DeepSeek Harness / ASI1 comme affectation initiale souhaitée de l’hyperviseur, configurable par dépôt. Un éventuel rôle d’interface ou d’hyperviseur AFM est une option à évaluer, pas une substitution déjà décidée. Voir [HARNESS-ROLES-CONTRACT.md](HARNESS-ROLES-CONTRACT.md#rôles-configurables-par-dépôt).

## Comparaison des capacités

| Sujet | AFM Apple local | Super Decider |
|---|---|---|
| Interface | Génération Swift structurée et appels d’outils ; sortie `choice`/`abstain` réalisable par schéma applicatif. | Le fournisseur décrit des questions typées `choice`, `noul` et `score` et des distributions de probabilités. |
| Confiance | Un champ `confidence` émis par `@Generable` est **un nombre déclaré par le modèle**, pas une probabilité native prouvée calibrée. L’interface SDK Swift installée examinée n’expose pas d’API publique de logits ou logprobs identifiée dans ses déclarations ; cela ne prouve pas leur inexistence dans toute autre API. | Le fournisseur présente les distributions comme calibrées ; leur calibration sur les décisions et données Reprise reste à mesurer. |
| Exécution | Local et hors ligne lorsque le modèle système est prêt ; disponibilité et version dépendent de la machine. Pas de coût d’API cloud pour cette exécution locale. | Service ASI:Cloud et poids/runtime publiés par son fournisseur ; accès, facturation, compatibilité matérielle et coût réellement applicables doivent être vérifiés avant tout essai. Aucun recours payant implicite. |
| Portée | Un modèle de langage général, sans preuve de qualification spécifique à la politique de routage Reprise. | Modèle spécialisé en décision ; ses résultats de benchmark fournisseur ne démontrent pas sa performance sur le périmètre Reprise. |

Sources : [Apple, génération guidée](https://developer.apple.com/documentation/foundationmodels/generating-swift-data-structures-with-guided-generation), qui décrit une garantie sur le **format** ; [carte Super Decider](https://huggingface.co/Singularity-Compute/super-decider), source fournisseur ; [Apple, Foundation Models](https://developer.apple.com/foundation-models-framework/). Les informations fournisseur ne constituent pas une validation indépendante de notre usage.

La prépublication de Geuter, Cheng et Alvarez-Melis, [*Releasing the Inner Jev* (2026)](https://arxiv.org/abs/2610.17205), montre que certains modèles ouverts peuvent être évalués comme modèles de décision avec accès à leurs logits et/ou un ajustement spécialisé. Elle n’évalue pas AFM et ne démontre pas que l’API Apple expose les logits nécessaires. La calibration de scores verbalisés varie selon les modèles, tâches et invites : voir la prépublication [*On Verbalized Confidence Scores for LLMs* (2024)](https://arxiv.org/abs/2412.14737). Aucun chiffre auto-déclaré ne constitue un droit d’action.

### PCC et Siri

`PrivateCloudComputeLanguageModel` est une voie Apple **distincte**, distante, avec quota et contexte plus large d’après [Apple WWDC26](https://developer.apple.com/videos/play/wwdc2026/319/). Apple conditionne l’accès des applications à l’entitlement et aux critères énoncés dans [Private Cloud Compute](https://developer.apple.com/private-cloud-compute/) ; disponibilité, éligibilité et distribution de Reprise ne sont pas démontrées. Les essais ici n’ont pas établi d’inférence PCC depuis le processus Reprise. Ne pas basculer automatiquement du local vers PCC ou une API payante.

Siri est une interface distincte, intégrable par App Intents ; les prototypes locaux n’ont pas établi une API pour installer directement dans le chatbot Siri des serveurs MCP, skills ou son routeur interne. Pour le pilote Reprise, les voies examinées sont les API Foundation Models et la CLI Apple, pas le contrôle du chatbot Siri. Voir [Apple, App Intents et Siri](https://developer.apple.com/videos/play/wwdc2026/240/).

## Éléments locaux et limites

- [Smoke Foundation Models du 28 octobre](../harness-tools/foundation-models-smoke/RESULTS.md) : structure générée et quatre choix conformes sur cinq cas artisanaux ; un cas d’abstention attendu a été classé `flash` avec `confidence=0.91`. Les cinq cas partagent une session, ne sont pas un jeu indépendant et ne démontrent aucune fréquence générale d’erreur. Le champ de confiance est explicitement demandé dans `smoke2.swift`.
- Les 7/8 cas du petit banc [Apple orchestrator](APPLE-ORCHESTRATOR-EVALUATION.md) portent sur des sélections d’outils synthétiques ; une seule passe, pas une mesure de qualité de routage ni de mémoire réelle.
- Le banc de coordination v2 a rapporté **0/8 sur une seule passe** : scénarios synthétiques, marqueurs textuels heuristiques et outils souvent non appelés. C’est un signal à examiner sur les trajectoires de coordination multi-étapes, **pas une mesure de l’aptitude spécifique au triage Super Decider**. Le rapport et les critères demandent examen ; aucune fréquence générale ne peut être déduite de huit cas.
- Le premier 1/8 du banc coordinateur v1 est **invalide** : des identifiants ou détails attendus n’avaient pas été fournis au modèle et les critères étaient mal spécifiés. Ne pas le comparer à la v2 ni le traiter comme une mesure de capacité. Voir [APPLE-COORDINATOR-EVALUATION.md](APPLE-COORDINATOR-EVALUATION.md).
- Le score 20/40 du [holdout routeur](../routing/README.md#comparaison-avec-la-version-dorigine) concerne règles et embeddings NLEmbedding, **pas** l’AFM génératif. Il ne peut lui être attribué.
- Apple documente une fenêtre locale de 4 096 tokens ; le smoke du dépôt a observé 8 192 sur une configuration précise. Ne pas figer l’une ou l’autre comme propriété universelle : lire `contextSize` et `tokenCount` selon la version, inclure instructions et outils, traiter explicitement le dépassement. [Apple : fenêtre de contexte](https://developer.apple.com/documentation/foundationmodels/managing-the-context-window).
- Le [rapport technique Apple 2025](https://arxiv.org/abs/2507.13575) est une source primaire sur les modèles annoncés en 2025. Ses résultats déclarés ne démontrent ni les performances de la version système 27 installée, ni une aptitude au routage Reprise.

## Protocole comparatif avant activation

1. **Définir les décisions séparément** : besoin de précision, compétence et capacité minimales, destination(s) admissibles, route exacte ou choix autorisé, abstention/refus/attente. Le modèle ne décide ni identité, ni propriété, ni droits, ni budget, ni état quota ; Reprise vérifie ces faits et règles avant toute exécution.
2. **Figer les données et les coûts d’erreur avant les mesures** : cas réels autorisés et anonymisés, cas synthétiques indépendants et adversariaux en français ; séparation développement, évaluation gelée et observation prospective sans effet. Annoter indépendamment les cas critiques et les ambiguïtés. Les tests unitaires et les variantes paraphrasées d’un même cas ne constituent pas un holdout indépendant. Environ 100 cas forment un pilote diagnostique, non une certification.
3. **Comparer sur les mêmes cas** : A = AFM local avec schéma et contexte identiques ; C = règles déterministes et choix par défaut admissible ou abstention ; B = Super Decider seulement après autorisation de la route/facturation, avec données anonymisées et contrat vérifié. Mesurer également un éventuel PCC séparément si son accès est établi ; ne pas supposer que son résultat caractérise le modèle local.
4. **Rapporter les erreurs et les coûts séparément** : perte pondérée fixée à l’avance pour sous-classement critique, refus injustifié, route inadmissible, fausse fin, latence et coût ; taux d’abstention, effectifs, incertitude, latences p50/p95, coût total par lot validé, consommation locale mesurée si disponible. ECE/Brier et courbes de fiabilité ne sont interprétables que pour des scores probabilistes définis, avec échantillons suffisants ; un score verbalisé n’est pas supposé calibré.
5. Tester spécifiquement refus/absence de route, quota et échéance, propriété concurrente, injection dans une tâche ou un résultat, arrêt, réponse tardive et changement de permissions. Les règles d’admission et budgets sont imposés et testés dans le code, même lorsque le modèle recommande une route.
6. Journaliser de manière privée et bornée la proposition, la décision admise, la route effectivement exécutée et la preuve du résultat ; éviter les prompts sensibles en clair. Activer éventuellement un mode ultérieur seulement après revue des résultats gelés et observation prospective, jamais au seul motif qu’un autre candidat a échoué.

Aucune inférence ni modification de configuration n’a été effectuée pour rédiger cette analyse. La mesure coordinateur v2 est rapportée depuis l’essai communiqué au coordinateur ; ce document ne prétend pas l’avoir reproduite.
