# Question architecturale en attente — récupération de la sortie IA dans Reprise

**Statut :** demande à Codex Astra (deep) **bloquée le 19/10 — `budget_blocked`**. Origine vérifiée : c'est le **compteur local du pont**, pas un quota ChatGPT (voir « D'où sort le plafond » ci-dessous). Le compteur a remis à zéro le 20/10 (0 appel sur 8) → **relance possible maintenant**, sous réserve que le quota fournisseur reste disponible.

## D'où sort le plafond (vérifié le 20/10)

Deux compteurs **locaux et distincts**, ni l'un ni l'autre n'est lié à un plan ChatGPT :

| Compteur | Fichier | Portée | Plafond |
|---|---|---|---|
| Expertises (`reprise_delegate`) | `expert_calls` dans `expert.sqlite` (état du pont) | `scope` = sha256(`project_root` + socket), jour = `int(time.time()//86400)` (UTC) | 8/jour, défaut `--daily-limit 8` non surchargé par la config |
| Soumissions Quick Entry | `quick_calls` dans `quick.sqlite` | idem | 8/jour, même défaut |

Les deux retournent `budget_blocked` **avant** toute transmission (`codex_expert_mcp.py:179`, `claude_desktop_quick.py:63`) ; un échec admis est compté aussi. État du compteur expert : 17/10 = 7, 18/10 = 2, **19/10 = 8 (plafond atteint)**, 20/10 = absent (0).

Côté fournisseur, le statut distinct est **`quota_blocked`** : `codex_expert.py:30` refuse si `ordinaryUsageAllowed != true` ou si `primary`/`secondary.usedPercent >= 95` (réserve 5 %, `account/rateLimits/read`). Mesures locales des journaux Codex : 19/10 maxima `primary 25 %` / `secondary 58 %` → **loin du seuil** ; vraies erreurs « You've hit your usage limit » observées du 14 au 18/10, **aucune les 19 et 20/10**. Plan enregistré dans `~/.codex/auth.json` : `chatgpt_plan_type = "plus"` (claims du 12/10, `last_checked` 22/09 — à re-vérifier en cas de refus fournisseur). Le plan « free » n'est donc pas en cause.

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
Compteur local remis à zéro le 20/10 : relancer `reprise_delegate` kind=expertise level=deep avec le texte des questions ci-dessus. Une seule demande à la fois ; si le pont renvoie `quota_blocked`, c'est le fournisseur (réserve 5 %) et non ce compteur — ne pas réessayer dans la foulée.
