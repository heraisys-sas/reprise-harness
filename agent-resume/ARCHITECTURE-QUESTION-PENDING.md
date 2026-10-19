# Question architecturale en attente — récupération de la sortie IA dans Reprise

**Statut :** demande à Codex Astra (deep) **bloquée — `budget_blocked`** (plafond quotidien d'appels MCP atteint, 8 appels/projet/jour). À relancer le jour suivant le déblocage du compteur.

## Contexte
Deux canaux Claude Desktop pour récupérer une analyse dans Reprise :

| Canal | Sens | Retour | Limite observée |
|---|---|---|---|
| **A — Quick Entry** (`dispatchOnQuickEntrySubmit`) | Reprise → Desktop | ❌ aucun | Prompt riche (images, fichiers, chatId ciblé) mais réponse bloquée dans l'UI |
| **B — MCP `get_task`/`submit_result`** | aller-retour | ✅ via SQLite | 1 soumission/tâche, 1 fichier joint, Claude doit invoquer l'outil (pas push) |

Contrainte : Claude Desktop tel quel (pas d'API d'inférence publique ; `sampling/createMessage` refusé par le client MCP Desktop).

## Questions posées à Astra (à relancer)
1. Quel canal (A, B, ou combinaison) est le plus robuste pour une analyse UX/UI substantielle à retourner dans Reprise ? (fiabilité, vérifiabilité, gros/multi-fichiers)
2. Existe-t-il un chemin de retour plus propre — ex. faire écrire l'analyse dans un fichier surveillé par Reprise, file de tâches sur le canal MCP, ou 2ᵉ appel MCP pour la réponse Quick Entry ?
3. Recommander **une** architecture concrète (flux de données + invariant de vérifiabilité).

## Avancement fait pendant l'attente
- `ux_review_probe.py` : serveur MCP à aller-retour (fichier joint + analyse longue 24k). Round-trip validé (get_task → submit_result).
- Prêt à brancher dans la config Claude Desktop (bloc `reprise_ux_review`), en attente de validation.

## Relance
Dès que le plafond se réinitialise (UTC day), relancer `reprise_delegate` kind=expertise level=deep avec le texte des questions ci-dessus.
