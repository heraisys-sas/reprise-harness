# Claude-Mem et la mémoire partagée Reprise

Évaluation documentaire du 12 novembre 2026, sur la baseline Reprise
`a70c0cd9f4a2f7276797368ac7ef5586c86d9b65`. Aucune installation, inférence,
activation de hooks ou consultation de données personnelles n'a été effectuée.

## Décision

Conserver Reprise comme source canonique de la mémoire partagée DSH/Claude.
Claude-Mem n'est pas adopté en l'état comme stockage partagé. Une éventuelle
interface ou adaptation doit démontrer qu'elle respecte la capture sélectionnée,
les autorisations et l'historique Reprise sans créer une autre autorité mémoire.
Ce document ne constitue ni une autorisation d'installation ni un plan d'activation.

## Faits documentés et compatibilité

L'[architecture amont](https://github.com/thedotmack/claude-mem/blob/main/docs/public/architecture/overview.mdx)
décrit des hooks Claude Code collectant prompts et événements outils, un worker
de traitement par SDK, un stockage SQLite et une réinjection de contexte lors
des sessions suivantes. Ce parcours ne démontre pas la capture explicitement
sélectionnée prévue par le [contrat Reprise](../contracts/ORCHESTRATOR-MEMORY-CONTRACT.md).

La [recherche amont](https://github.com/thedotmack/claude-mem/blob/main/docs/public/architecture/search-architecture.mdx)
expose des outils MCP passant par un worker HTTP, avec SQLite/FTS5 et une
projection Chroma éventuelle. Son parcours progressif
`search → timeline → get_observations` est une piste pour une consultation
bornée ; il ne prouve aucune intégration avec Reprise ou DSH. Un filtre de
recherche par projet ne vaut pas autorisation de scope.

Le support direct DSH, le remplacement du stockage amont par Reprise et la
préservation des identités projet/travail, grants `off/read/write`, provenance
et reçus Reprise restent **non vérifiés**. Les essais synthétiques de l'adaptateur
Reprise existant ne démontrent pas ces capacités de Claude-Mem, ni le chargement
effectif d'un connecteur dans les clients produits.

## Risques et hypothèses à vérifier

Le parcours standard documenté introduirait une capture automatique et un autre
stockage durable. Sans adaptation vérifiée, corrections, suppressions et historique
pourraient diverger entre deux autorités. Une éventuelle projection doit rester
dérivée et reconstruisible, sans réintroduire un contenu corrigé ou supprimé.

Deux scénarios demeurent des **hypothèses**, sans implémentation démontrée :

- Une interface de consultation utilisant exclusivement les outils Reprise existants.
- Une importation explicitement sélectionnée comme candidat à relire, avec provenance.

La première question de faisabilité est donc : une interface utile peut-elle
fonctionner sur Reprise sans activer la capture automatique ni un stockage
canonique parallèle ? Réutiliser le suivi mémoire existant avant d'ouvrir un
chantier ou une issue faisant doublon.

## Validations futures

Sur fixtures synthétiques : absence de capture non sélectionnée et de second
stockage canonique ; isolation projet/travail et refus hors scope ; grants ;
provenance ; idempotence ; correction/suppression sans résurrection ; retrait
préservant les données. Vérifier ensuite, sur les clients réellement autorisés, les
catalogues réellement chargés dans DSH/Claude puis un nonce explicite A→B avec
reçus. Un catalogue sondé directement ou une configuration écrite ne suffit pas.

## Sources et limites

Les deux sources primaires liées ci-dessus ont été consultées le **12 novembre
2026** sur la branche amont `main`, mobile et non figée par SHA. Cette évaluation
porte sur la documentation, sans audit du code ni mesure de qualité de rappel,
coût, latence ou comportement effectif. Figer les références amont par SHA et
examiner les points d'extension avant toute décision d'implémentation. Aucun
scénario hypothétique n'est présenté comme une compatibilité acquise.
