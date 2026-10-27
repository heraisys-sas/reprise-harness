# DESKTOP-APPLY — contrat du transport `apply`

Ce document est écrit pour **un agent qui reprend le travail**. Il distingue
systématiquement ce qui est prouvé de ce qui ne l'est pas, liste les pièges déjà
payés, et donne les critères d'acceptation de ce qui reste.

Références : issue #83 (découvertes), PR #84 (ce lot), PR #85 (correctif `resume`).

## 0. Statut

| Élément | État |
|---|---|
| `claude_desktop_apply.py` — lecture d'état, quota, navigation | **testé sur machine** |
| `claude_desktop_apply.py` — envoi (`stage: envoyé`) | prouvé avec le pilote Node équivalent, **pas encore avec ce portage Python** |
| `claude_quota_log.py` | testé (10 tests) + vérifié sur journal réel |
| `resume desktop-apply` (CLI) | testé (`--status`, `--raise`) |
| `ClaudePanel.swift` | **non compilé** (build interne à Reprise) |

## 1. Décision d'architecture, et ce qui a été écarté

| Voie | Verdict | Preuve |
|---|---|---|
| CDP sur Claude Desktop | refusé par l'app | `refusing to start — a debugging or network-override switch…` ; jeton `CLAUDE_CDP_AUTH` Ed25519, TTL 300 s |
| Overlay Quick Entry | exige l'Accessibilité | IPC interne, aucun socket |
| claude.ai en WKWebView | refusé au login | hCaptcha en boucle, ITP bloque les cookies tiers |
| Chrome headless | refusé | interstitiel « Un instant… », aucun composeur |
| **claude.ai dans un Chrome contrôlé, via CDP** | **retenu** | envoi réel observé |

Ne pas retenter les voies écartées sans une preuve nouvelle : chacune a coûté
une session de travail.

## 2. Machine à états

| `stage` | `ok` | `staged` | Sens | Action attendue |
|---|---|---|---|---|
| `envoyé` | ✓ | ✗ | clic d'envoi effectué | aucune |
| `posé` | ✓ | ✓ | texte en place, envoi non demandé | aucune |
| `quota` | ✗ | ✓ | bouton désactivé, texte en place | **attendre**, ne pas réessayer en boucle |
| `session` | ✗ | ✗ | connexion claude.ai requise | `--raise`, puis humain |
| `composeur` | ✗ | ✗ | page non prête | vérifier le navigateur |
| `frappe` | ✗ | ✗ | texte absent après insertion | voir §5.1 |
| `connexion` | ✗ | ✗ | navigateur absent | `launch()` |
| `vide` | ✗ | ✗ | rien à envoyer | — |

`quota` et `session` **ne sont pas des pannes**. Les traiter comme telles est
l'erreur la plus coûteuse : elle pousse à réessayer, ce qui consomme du quota.

## 3. Cohérence avec l'existant

Le dépôt possède déjà le contrat de soumission : `claude_desktop.review()` envoie
`{prompt, request_key, chatId, filePaths}` via `quick_entry/submit`, et rend
`{status, resumable, model, admission_only, response}`. Ce transport **ne crée pas
un second vocabulaire** : `to_submission(result)` projette le détail technique sur
ces statuts.

| `stage` du transport | `status` rendu | Champs ajoutés |
|---|---|---|
| `envoyé` | `finished` | `dispatch: cdp-web`, `admission_only: true` |
| `posé` | `finished` | `admission_only: false` (aucun tour émis) |
| `quota` | `blocked_session` | `staged: true`, `reason` |
| `session` | `blocked_auth` | `reason: connexion requise` |
| autre | `engine_unavailable` | `reason: <stage>` |

Autres alignements :

- `chat_id` est validé par `claude_desktop.validate_chat_id` (ré-export public),
  pas par une regex locale ;
- nommage : `claude_desktop_*` pour les modules, `CLAUDE-*.md` pour la doc —
  comme `CLAUDE-QUICK-ENTRY-INTERNALS.md` et `CLAUDE-CONNECTOR.md` ;
- point d'insertion : le gestionnaire `quick_entry/submit` de
  `claude_desktop_quick.py`, là où l'envoi est aujourd'hui laissé au
  « supervised driver ».

## 4. API

```python
from desktop_apply import apply, send_staged, quota, launch, shutdown, raise_for_login

apply(prompt, chat_id, port=9333, on_login="fail"|"raise")  # pose + envoie
send_staged(port)          # envoie ce qui est déjà posé, sans retaper
quota(port)                # {"blocked", "sendDisabled", "hasText", "resetAt"} ou available=False
raise_for_login(port)      # fenêtre visible + navigation vers /login
```

CLI (appelé par le Swift via `setupCommand`) :

```bash
resume desktop-apply --status
resume desktop-apply --text "…" --chat <uuid> [--raise-on-login]
resume desktop-apply --raise          # connexion humaine
```

## 5. Invariants — à ne pas casser

1. **Aucune heure déduite d'un « HH:MM » sans date.** L'ambiguïté
   aujourd'hui/demain reste refusée, comme dans `claude_quota.parse_pause`.
2. **Un composant absent ne prouve rien.** Pas de bouton d'envoi ⇒
   `available: false`, jamais `blocked: false`.
3. **Un événement de quota expiré n'est pas un blocage** (`resetsAt <= now`).
4. **Ne pas fermer le navigateur sur `session`** : la connexion deviendrait
   impossible.
5. **Le quota ne se lit qu'après insertion** : à vide, le bouton est désactivé
   pour une raison triviale.
6. **`chat_id` absent ⇒ nouvelle conversation.** Pour viser une existante, l'UUID
   est obligatoire.
7. **`request_key` reste au démon.** Le transport ne le reçoit pas, ne le
   stocke pas et ne déduplique rien : il est sans état. Vérifiable par
   `grep -c request_key claude_desktop_apply.py` → `0`.
8. **Borne à 12 000 caractères**, identique à `claude_desktop.review()`. Un
   transport plus permissif accepterait ce que le démon refuse.

## 6. Pièges déjà payés

| # | Symptôme | Cause | Correctif appliqué |
|---|---|---|---|
| 5.1 | texte visible, bouton inerte | `execCommand('insertText')` met à jour le DOM, **pas** l'état de l'éditeur | `Input.insertText` (CDP) ; en WebKit, `NSTextInputClient.insertText` |
| 5.2 | frappe envoyée à la mauvaise app | `NSWorkspace.openURL_` n'active pas la cible | `open(1)` |
| 5.3 | succès annoncé sans rien faire | un `null` JS arrive en `NSNull`, pas en `nil` (Swift) | tester le type, pas `!= nil` |
| 5.4 | « composeur présent » sur la page de login | la page de login contient un `contenteditable` | exiger `[data-testid="chat-input-send"]` |
| 5.5 | CLI refusé au démarrage | `args.raise` : `raise` est un mot réservé Python | `dest='raise_window'` |
| 5.6 | Quick Entry n'envoie rien, sans erreur | seuil **strictement** > 2 caractères | documenté ; ne pas tester avec « ok » |
| 5.7 | quota invisible dans le journal | blocage **client** : aucune requête émise | croiser journal + page (§6) |
| 5.8 | quota jamais détecté | regex de ligne consommant `.*$` → corps vide | motif sans `.*$` |
| 5.9 | quota jamais détecté (bis) | JSON **doublement échappé** (`\"type\"`) | déséchapper avant extraction |
| 5.10 | `claude --resume` « réussi » à tort | `session_id` et JSONL écrits **même si l'appel échoue** | lire `is_error` + `result` |
| 5.11 | `retry` cliquait le menu d'une conversation | le libellé contient le texte du message | cibler `data-testid` (`action-bar-retry`) |

## 7. Quota : deux sources, une conjonction

| Source | Voit | Ne voit pas |
|---|---|---|
| journal Desktop (`exceeded_limit` + `resetsAt`) | rejets **serveur**, datés | blocage client |
| page (`sendDisabled` + texte présent) | blocage client, heure affichée | au-delà de l'instantané |

`blocked = journal.blocked OR (page.sendDisabled AND page.hasText)`.

## 8. Découpe « lien + collage » : hors périmètre du démon

Le plafond du deep link est de 14 336 caractères, mais `review()` borne la
question à 12 000 : **la découpe n'a donc pas lieu sur le chemin du démon**. Elle
ne sert qu'aux appels directs au transport. Ne pas la considérer comme une
capacité du canal.

## 9. Recettes de test (sans consommer de quota)

```bash
# 1. navigateur absent : dégradation propre
resume desktop-apply --status

# 2. profil vierge : détection de session + fenêtre rendue à l'humain
TMPP=$(mktemp -d); CLAUDE_CDP_PROFILE=$TMPP resume desktop-apply --raise --port 9340
CLAUDE_CDP_PROFILE=$TMPP resume desktop-apply --status --port 9340   # → session

# 3. quota, sans envoi
python claude_quota_log.py     # événement + resetsAt + expired
```

Ne jamais tester un envoi quand le quota est bloqué : cela ne prouve rien et
consomme la fenêtre de reprise.

## 10. Ce qui reste, avec critère d'acceptation

| # | Tâche | Acceptation |
|---|---|---|
| 1 | prouver l'envoi du portage Python | `stage: "envoyé"`, puis un message présent dans la conversation ciblée |
| 2 | compiler `ClaudePanel.swift` | build Reprise vert, panneau affiché, bouton « Se connecter » sur `session` |
| 3 | câbler `claude_desktop_quick.py` | un payload reçu produit `envoyé` ou `quota/staged`, jamais un échec silencieux |
| 4 | arbitrer `evidence_sha256` | décision écrite : ligne de journal ou bandeau corroborant |
| 5 | cycle de vie du navigateur | résident ou éphémère, documenté, sans port ouvert par défaut |
| 6 | non-régression des sélecteurs | `inspect` en CI manuelle après refonte de l'UI |

## 11. Décisions ouvertes

- `on_login` par défaut : `fail` (sûr) ou `raise` (utile) ;
- navigateur résident (rapide, port ouvert) ou éphémère (propre, +6 s) ;
- source de `evidence_sha256` ;
- où vit le profil Chrome dans le déploiement.

## 12. Règle de preuve

Toute affirmation de ce document a été vérifiée par une commande dont la sortie
est citée dans l'issue #83. Une affirmation non vérifiée est écrite comme telle
(§0). Conserver cette discipline : elle a évité trois faux positifs en une
session.
