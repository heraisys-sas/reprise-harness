# Banc exploratoire de l’orchestrateur Apple

Ce banc explore la sélection d’outils et l’abstention du modèle `system` d’Apple. Les huit scénarios sont fictifs et versionnés dans `apple_orchestrator_eval.py` ; leurs données ne viennent ni des projets ni des utilisateurs. Les outils sont des descriptions envoyées au modèle : **aucun outil réel n’est exécuté**. Pour deux scénarios, le banc retourne un résultat fictif fixe et examine la réponse suivante. Le scénario de reprise démarre un contexte neuf et fait récupérer un checkpoint simulé ; il ne prouve ni la persistance d’une mémoire ni une reprise réelle après redémarrage de Reprise.

Le seuil diagnostique, fixé avant l’essai, est de 90 % de réussite et de 100 % des scénarios critiques. Ce seuil et le jeu réduit servent à révéler des défauts, non à approuver une mise en production : `production_qualified` reste toujours `false`. Les vérifications textuelles sont rudimentaires ; elles peuvent manquer des erreurs sémantiques. Le banc n’évalue pas les séquences longues, cycles, permissions réelles, durabilité, compaction, quotas réels, outils MCP ou l’interface Siri.

Vérifier le banc avec un oracle synthétique, qui ne lance aucune inférence :

```sh
python3 agent-resume/apple_orchestrator_eval.py --fake
python3 -m unittest discover -s agent-resume/tests -p 'test_apple_orchestrator_eval.py' -v
```

Pour une mesure locale, l’opérateur démarre séparément `fm serve --socket <socket-privé>`, puis exécute :

```sh
python3 agent-resume/apple_orchestrator_eval.py --socket <socket-privé>
```

L’opérateur arrête ensuite son processus `fm serve`. Utiliser un répertoire privé et un socket de confiance : le banc envoie les prompts synthétiques au service choisi. La sortie JSON contient identifiants de scénarios, succès, motif générique d’échec et durées ; elle n’enregistre ni prompts, ni réponses, ni arguments, ni erreurs brutes. `model=system` est explicite. Des erreurs de transport ne sont pas interprétées comme des échecs prouvés du modèle.

## Mesure exploratoire du 2 novembre 2026

Sur macOS 27.2, hors sandbox, un processus `fm serve` temporaire sur socket Unix a été démarré puis arrêté par le lanceur. Une seule exécution de la suite v1 : **7/8** réussites ; le cas `checkpoint_after_restart` a échoué au choix d’outil (`wrong_tool`). Les cinq cas critiques ont été réussis. Seuil 90 % non atteint (87,5 %). Cette mesure n’établit aucune fréquence statistique et ne démontre pas la capacité d’orchestration ; le processus ne s’est connecté à aucun outil réel. Une enquête sur cet échec et des scénarios de mémoire persistante réelle restent nécessaires.
