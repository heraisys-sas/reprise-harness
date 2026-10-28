# Contrat ASI1 pour les orchestrateurs

État documentaire vérifié le 26 octobre 2026, baseline `757be0406fef57e0bb19c99a6b3edd18026fed6c`. Aucune inférence ni modification de profil. Le contrat publié pour `api.asi1.ai` ne prouve pas sa parité avec `inference.asicloud.cudos.org`.

## Orchestration locale et planner serveur

Un orchestrateur DSH/Reprise peut utiliser directement les outils du harness : envoyer leurs définitions, exécuter les appels retournés et restituer leurs résultats. **Aucun proxy ni `planner_mode` n'est nécessaire à cette boucle.** Voir [Tool Calling](https://docs.asi1.ai/documentation/build-with-asi-one/tool-calling).

Le 28 octobre 2026, l’opérateur rapporte la confirmation d’un employé CUDOS : le planner serveur n’est pas implémenté chez ce fournisseur. À sa demande, le proxy et son profil ont été retirés du produit. L’orchestration utilise la route directe `asi-cloud`, sans modification de fournisseur ou de facturation. [Archive technique inactive](../archive/asi1-planner-proxy/README.md).

## Paramètres par modèle

Selon [Reasoning](https://docs.asi1.ai/documentation/build-with-asi-one/reasoning), `asi1` raisonne toujours ; `reasoning_effort` sélectionne low/medium/high, avec défaut équilibré. Pour **`asi1-mini`**, `enable_thinking` active ou désactive la pensée ; `reasoning_effort` n'a aucun effet et `thinking_budget` ne change pas sa profondeur. Ne pas présenter trois efforts comme trois capacités mini. `use_reasoning_history` est recommandé pour les parcours agentiques asi1/ultra avec rejeu exact de `reasoning_content` ; son effet mini est déclaré non mesuré.

Un effort ou une température imposés par le harness constituent une politique locale, pas une exigence mini. La [fiche Models](https://docs.asi1.ai/documentation/models) publie pour mini 262144 tokens totaux et 160000 de fenêtre de travail ; température par défaut 0.7, ou 0.6 avec pensée activée. Ces chiffres ASI:One ne justifient ni une modification automatique des 128000 configurés dans DSH, ni une conclusion sur CUDOS.

## Capacités DSH vérifiées

Inspection de DSH **0.2.1-rc.2**, build `332895e0f7c9d4dbfb29729f5eb367fa263ff986`. Dans `@deepseek-ai/dsh-llm-pi-ai/lib/index.js` : `reasoningEfforts` matérialise `model.reasoning` (586–606), `headers` est statique (1060), température et session sont transmises depuis les options (1953–1989). Les capacités d'affinité dynamique sont explicitement non exposées (421–424). Aucun champ de configuration JSON arbitraire ou `onPayload` n'est exposé ; l'existence de ce hook dans pi-ai ne le rend pas configurable dans DSH.

Le format pi-ai `qwen` produit `enable_thinking` (`@earendil-works/pi-ai/dist/api/openai-completions.js:631–638`). Exemple **au niveau du modèle mini**, à valider par capture de payload factice avant adoption :

```yaml
reasoningEfforts:
  off: null
  low: low
compat:
  thinkingFormat: qwen
  supportsReasoningEffort: false
  supportsDeveloperRole: false
```

`qwen` désigne ici le format de transport, sans identifier le modèle. Les deux états proposés signifient off/on. Conserver séparément les efforts asi1. `supportsDeveloperRole: false` sélectionne les messages `system` ; aucun proxy requis.

## Identité et validation

L'hypothèse « alias CUDOS `asi1` → `asi1-mini` » reste **non démontrée**. Fenêtre configurée DSH, limite gateway et modèle exécuté sont trois faits distincts. Ni taille observée, ni style, ni écho `model`, ni catalogue de noms n'attestent le backend. Demander la documentation ou l'attestation du fournisseur sur le mapping exact/version ; consulter des métadonnées authentifiées seulement après autorisation, sans exposer de secret. Ensuite, vérifier séparément les payloads locaux et, si autorisé, l'acceptation du endpoint. Aucune sonde d'inférence nécessaire à la présente documentation.

Éviter un enfant `agent-default-model` dans le preset : la sélection globale persistée est distincte du preset. Installation, configuration, chargement et paramètres réellement envoyés doivent être attestés séparément.


## Disponibilité du fournisseur

L’absence du planner CUDOS ne désactive pas les appels d’outils ordinaires. Les erreurs et quotas du fournisseur restent propagés par le client direct. Aucun proxy de substitution, rejeu automatique supplémentaire ou changement de modèle n’est introduit par le retrait. Les anciennes propositions et recettes planner sont archivées, hors du parcours d’installation actif.
