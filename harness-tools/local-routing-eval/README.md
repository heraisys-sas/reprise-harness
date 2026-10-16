# Moteur Apple de Reprise — mesure locale du 16 octobre 2026

Évaluation du modèle système et des briques locales existantes, distincte du pilote des modèles distants. Aucun appel payant, achat, nouveau modèle téléchargé, daemon ou réglage global. Aucun routage quotidien activé. Le budget de 2 € reste inutilisé.

## Ce qui est testé

Reprise utilise `/usr/bin/fm respond --model system` pour ses analyses consultatives et NLEmbedding pour sa recherche. Le banc réutilise cette commande et les classificateurs existants de `routing/` ; il n'ajoute pas de nouveau moteur à l'interface Reprise. Le routage de tâches vers Codex/Harness/Claude dans le fichier `engines.py` en cours de développement est une autre fonction : ces modifications sont préservées.

24 demandes FR/EN nouvelles, 6 par niveau. Les labels et huit annotations critiques ont été fixés avant exécution par un sous-agent. Un autre a audité les interfaces et recherché les fournisseurs. Les données sont publiques et synthétiques : jeu de développement distinct des précédents, pas benchmark indépendant, ni garantie sur les tâches réelles. Les niveaux sont des jugements éditoriaux et peuvent être discutés ; aucune calibration après résultat.

| Bras | Niveau exact | Critiques sous-classés / 8 | Latence médiane |
|---|---:|---:|---:|
| Heuristique existante seule | 8/24 | 4 | ~0,01 ms |
| Règles + embeddings de phrases | 12/24 | 2 | ~36,5 ms |
| Hybride existant | 10/24 | 3 | ~36,4 ms |
| `fm system`, proposition de niveau seule | 16/24 | 0 | ~1,17 s |

Pour `fm`, **1 réponse sur 24 seulement** respecte aussi la contrainte de citation exacte. Les autres paraphrasent la tâche ; elles restent invalides et ne peuvent pas être acceptées comme avis étayés. Le premier passage du même prompt avait aussi 1/24 réponse valide ; l'instrumentation du second conserve séparément le niveau proposé. Le 16/24 ne signifie donc pas 16 décisions autorisées ou prouvées. Le coût d'exécution local n'a pas été converti en euros et aucune économie de facture distante n'est démontrée.

Les trois bras déterministes sont exécutés chacun dans un processus Node neuf, avec appels séquentiels. Le temps répété en mémoire est ~0,01 ms pour les règles et ~0,05–0,07 ms pour les deux autres, avec niveau inchangé 24/24 ; c'est surtout une mesure du cache, pas une nouvelle inférence. Les assets système pouvaient déjà être chauds. Le bras « règles + phrases » n'est pas une mesure d'embeddings purs : les règles peuvent court-circuiter le modèle.

## Reprise et défaillances

Quatre paires avant/après résumé contradictoire conservent la tâche et les consignes autoritatives. Les propositions de niveau restent correctes et inchangées 4/4 paires. Mais seule 1/4 paire satisfait aussi la citation exacte avant ET après. Invariance, exactitude et validité des preuves sont rapportées séparément. Cette simulation ne déclenche pas le compactage natif d'une conversation ni la mémoire d'un agent réel.

Deux contrats exécutés sur l'hybride existant : helper absent et annulation explicite conservent tous deux le niveau des règles de référence. Les tests de politiques projet continuent de passer dans la suite ; aucun index/cache Reprise global n'est ouvert et aucune vraie session n'est lue. Le cache de décisions du routeur existant ne constitue pas une autorisation de projet ; aucune nouvelle interface partagée n'est exposée.

`fm` échoue avec erreur 1008 depuis le sandbox de cette exécution et répond hors sandbox avec autorisation normale. L'ancienne conclusion « modèle indisponible » était donc insuffisante. Aucune protection système n'a été affaiblie ; le banc ne fait pas d'escalade automatique. Les refus restent explicites.

Les appels `fm` ont un délai de 35 s, un contrôle de volume de 16 Kio par flux et un nettoyage du groupe de processus. Les sorties passent par des fichiers temporaires anonymes supprimés à la fermeture. Le contrôle de taille est observé périodiquement : pas une limite dure du disque entre deux sondages. Le client `fm` culmine autour de 14 Mio de RSS sur cette série, **sans compter le serveur d'inférence XPC**. La mémoire totale du modèle et la contention avec les applications restent inconnues ; ce chiffre ne justifie aucun choix de taille de nouveaux poids.

## Exécution et résultats

```sh
python3 harness-tools/local-routing-eval/evaluate.py
python3 harness-tools/local-routing-eval/evaluate.py --fm
"/Applications/DeepSeek Harness.app/Contents/Resources/runtime/primary-runtime/dependencies/node/bin/node" harness-tools/local-routing-eval/contracts.mjs
python3 -W error::ResourceWarning -m unittest discover -s harness-tools/tests
```

`--fm` génère uniquement sur appareil, sur le corpus synthétique figé, sans outils actifs. Sans ce flag, seules les règles/embeddings existants sont évalués. Les artefacts existants de `routing/plugin/lib` et `routing/plugin/native` sont nécessaires ; aucun build ou téléchargement automatique. Un ancien chemin d'import JS a été corrigé avant le tableau final ; ses échecs d'infrastructure n'ont pas été comptés comme qualité du routeur.

`results.json` conserve les deux types de verdict et toutes les erreurs de validation, la latence, les empreintes du corpus/prompt et les paires de reprise. `initial-fm-validation.json` conserve le passage initial ; il ne comportait pas encore les niveaux rejetés. Seuls des résultats synthétiques sont versionnés. **36 tests outils passent**, dont validation JSON non objet/citations, distinction proposition/preuve, invariance erronée, délai et volume.

## Nouveaux moteurs et décision

Voir [PROVIDERS.md](PROVIDERS.md) pour la recherche officielle et le guide d'ajout réversible Core AI/MLX. Une sonde Swift `ProviderProbe.swift` compilée et exécutée indique System et PCC disponibles via le SDK, sans génération PCC. La CLI installée refuse néanmoins `fm available --model pcc` (exit 64). PCC est distant et ne remplace pas le moteur local sans décision explicite.

Le modèle système apporte un signal de difficulté intéressant, avec surclassements et preuves textuelles peu fiables. Garder les règles et les permissions comme garde-fous ; conserver tout avis local en observation. Un prochain essai utile peut simplifier le contrat de génération à un niveau seul, avec référence de source calculée déterministement, sans présenter cette référence comme une preuve de justesse. Cela doit être évalué séparément avant de télécharger un autre modèle. Qwen3 0.6B quantifié est un petit candidat documenté pour comparer Core AI/MLX si une lacune justifie cette étape ; aucun gain de ce modèle n'est encore démontré ici.

Retour arrière : ne plus invoquer le banc, supprimer seulement les exécutables/caches de compilation temporaires du probe si inutiles. Les sources de Reprise, ses réglages, index, clés et conversations n'ont pas été modifiés par ce travail.
