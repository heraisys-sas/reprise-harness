# Question architecturale en attente — récupération de la sortie IA dans Reprise

**Mise à jour du 20/10 :** plafond quotidien local Codex supprimé du code ; les compteurs/plafonds mentionnés ci-dessous décrivent l’état historique lors de l’avis. Contrôles fournisseur conservés. Aucun nombre d’appels restants local ne doit être utilisé pour Codex.

**Statut :** **répondu le 20/10** par Codex Astra (`gpt-6-astra`, effort high) via `reprise_delegate` kind=expertise level=deep — thread `01a15938-9c52-7603-bfc8-4f182098799e`, turn `01a15938-9d06-7031-9cd1-f16cf568682f`, `resumable: true`. Réponse consignée dans « Réponse Astra » ci-dessous. Appel consommé : compteur local 1/8 pour le jour UTC 20/10.

Le blocage du 19/10 en `budget_blocked` venait du **compteur local du pont**, pas d'un quota ChatGPT (voir « D'où sort le plafond »).

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

## Questions posées à Astra
1. Quel canal (A, B, ou combinaison) est le plus robuste pour une analyse UX/UI substantielle à retourner dans Reprise ? (fiabilité, vérifiabilité, gros/multi-fichiers)
2. Existe-t-il un chemin de retour plus propre — ex. faire écrire l'analyse dans un fichier surveillé par Reprise, file de tâches sur le canal MCP, ou 2ᵉ appel MCP pour la réponse Quick Entry ?
3. Recommander **une** architecture concrète (flux de données + invariant de vérifiabilité).

## Réponse Astra (20/10, `gpt-6-astra` high) — synthèse

**Recommandation : A + B combinés.** Quick Apply (`dispatchOnQuickEntrySubmit`) sert uniquement à **déclencher** le tour ; MCP `get_task`/`submit_result` fournit la demande de référence et **reçoit** l'analyse ; SQLite est le registre durable. **Seul `submit_result` vaut retour utilisable** — un accusé de dispatch ou une réponse visible dans Desktop ne vaut jamais achèvement. L'appel MCP « dédié à la réponse Quick Apply » demandé en question 2 est *précisément* le rôle de `submit_result` ; un fichier écrit directement par le modèle supposerait une capacité filesystem non établie → écarté.

**Éléments établis** (distingués de l'inférence) : `CLAUDE-MCP-PROBE.md` documente l'aller-retour supervisé ; `ux_review_probe.py` apporte déjà transactions, ids tâche/tentative, reprise `indeterminate`, idempotence d'un résultat identique. Astra n'a **pas** réexécuté ces expériences. Limites précises relevées : `MAX_ANALYSIS = 24000` = **caractères**, pas octets ; prompt et pièce jointe restent en mémoire **hors SQLite** (aucun hash ne les lie durablement à la tâche) ; 1 fichier texte = limite du prototype, pas limite générale de MCP ; le branchement `reprise_ux_review` reste à valider par l'opérateur.

**Flux à implémenter** (contrat, pas fonctionnalités présentes) :
1. **Préparer avant d'envoyer** : helper Python fige prompt + fichiers dans `--state/ux_job.sqlite` → `job_id`, manifeste ordonné `{path, media_type, byte_length, sha256}`, contenus exacts, `request_hash` sur sérialisation **canonique versionnée**. Toute modification = nouvelle demande.
2. **Déclencher une fois** : après commit, Quick Apply envoie une instruction courte (`job_id`, `request_hash`, appel attendu à `reprise_ux_review.get_task`). `chatId` = routage, **jamais** preuve. Un dispatch incertain n'est jamais renvoyé auto.
3. **Réserver par MCP** : `get_task(job_id, request_hash)` vérifie la demande et fait atomiquement `pending → claimed` avec `attempt_id` neuf ; un second appel ne distribue pas de nouvelle tentative.
4. **Rendre une seule analyse** : `submit_result(job_id, attempt_id, request_hash, analysis, complete=true)` ; vérification ids/état/limites/structure, UTF-8 strict, taille + SHA-256, texte/métadonnées/reçu/`completed` commités **ensemble** ; accusé MCP envoyé **après** commit.
5. **Afficher après vérification** : Reprise relit SQLite périodiquement, recalcule hashes et taille, n'affiche qu'un résultat validé. Une notification peut accélérer la lecture, sans jamais la prouver.

Borne d'entrée totale fixée **avant** dispatch, refus sans troncature ; borne de sortie conservée à 24 000 caractères initialement ; images/volumineux = validation Desktop supplémentaire avant d'être déclarés pris en charge.

**Invariant vérifiable par Reprise :**
```text
accepted(result) ⇔
  job.status == completed
  ∧ result.job_id == expected_job_id
  ∧ result.attempt_id == job.current_attempt_id
  ∧ result.request_hash == job.request_hash
  ∧ hash(canonical_request_from_stored_snapshots) == job.request_hash
  ∧ result.complete == true
  ∧ required_structure_valid(result.analysis)
  ∧ len(UTF8(result.analysis)) == result.byte_length
  ∧ SHA256(UTF8(result.analysis)) == result.sha256
```
Écriture finale **atomique et immuable** : répétition strictement identique → même reçu ; répétition différente → rejet.

**Portée de la preuve** (limites explicites) : associe le résultat à sa demande et à sa tentative, prouve intégrité **depuis réception**. Ne prouve **ni** que Claude a étudié chaque fichier, **ni** l'exhaustivité intellectuelle, **ni** l'identité avec le texte affiché dans Desktop. Un hash calculé à réception ne détecte pas une omission antérieure. Sections obligatoires + liste de fichiers examinés rendent la complétude *structurelle* vérifiable, sans transformer une déclaration du modèle en preuve sémantique.

**Échecs et reprise manuelle (opérateur : attendre / annuler / relancer, jamais auto) :**

| Situation | Détection et traitement |
|---|---|
| Aucun `get_task` | `pending` hors échéance → « non réclamée » ; cause inconnue (livraison, permissions, quota, comportement) |
| Réservation sans retour | `indeterminate`, **sans redistribution** ; résultat tardif recevable tant que la tentative est active |
| Retour interrompu avant commit | aucun final accepté, tentative « incertaine » ; ne jamais présenter un fragment comme terminé |
| Mauvais id, ancienne tentative, dépassement, structure invalide | rejet explicite + événement durable consultable dans Reprise |
| Commit OK, accusé perdu | SQLite = `completed` ; la même soumission retrouve son reçu |
| Valide mais incomplet sur le fond | peut rester **silencieux** → revue humaine |
| Altération post-réception | détectable si texte et hash divergent ; pas si l'acteur modifie aussi les métadonnées de confiance |

Une relance invalide atomiquement l'ancienne tâche et crée un nouveau job lié par `supersedes_job_id` (mêmes instantanés ; anciens retours refusés) ; nouveau dispatch **uniquement** sur action explicite. Une échéance ne prouve jamais que l'inférence précédente s'est arrêtée.

**Hypothèses à valider dans Desktop** : connecteur activé, autorisations accordées, stockage local de confiance, contexte suffisant. L'invocation d'outils par le modèle reste faillible — l'absence de retour devient observable, sans cause automatiquement identifiable.

## Existant déjà (à conserver comme base)
- `ux_review_probe.py` : serveur MCP à aller-retour (fichier joint + analyse longue 24k). Round-trip validé (get_task → submit_result).
- Prêt à brancher dans la config Claude Desktop (bloc `reprise_ux_review`), en attente de validation opérateur.

## Suite opérationnelle
1. **Implémenter le contrat ci-dessus** au-delà du prototype : manifeste ordonné + `request_hash` canonique, `pending → claimed` atomique, `submit_result` vérifié (UTF-8, taille, SHA-256), écriture immuable, relecture/revalidation côté Reprise. Puis tests ciblés sur chaque branche d'échec du tableau.
2. **Brancher `reprise_ux_review`** dans la config Claude Desktop — validation opérateur requise.
3. **Précisions complémentaires** : réutiliser ce thread avec `codex_followup` (backend `codex`, modèle et effort conservés ; thread `01a15938-9c52-7603-bfc8-4f182098799e`) — compteur local **1/8** le 20/10, donc 7 appels restants aujourd'hui. Un `quota_blocked` viendrait du fournisseur (réserve 5 %), pas du compteur : ne pas réessayer dans la foulée.


Suite de conception après cet avis : [service Reprise commun bidirectionnel](HARNESS-SUPERVISION-ARCHITECTURE.md), regroupant réception, retour et sollicitation d’autres harness. Cette évolution ne modifie pas le texte historique de l’avis Astra. Priorité au retour Claude Desktop, compte gratuit compris ; unification non encore implémentée.
