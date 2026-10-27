# DESKTOP-APPLY — contrat expérimental du transport `apply`

Ce transport est **expérimental** et n'est pas raccordé au daemon Quick Apply,
au routage, ni à la reprise automatique. Les exemples CLI sont des interfaces de
prototype, pas une capacité de production validée.

Ce document est écrit pour **un agent qui reprend le travail**. Il distingue
systématiquement ce qui est prouvé de ce qui ne l'est pas, liste les pièges déjà
payés, et donne les critères d'acceptation de ce qui reste.

Références : issue #83 (découvertes), PR #84 (ce lot), PR #85 (correctif `resume`).

## 0. Statut

| Élément | État |
|---|---|
| `claude_desktop_apply.py` — lecture d'état, indice de quota, navigation | prototype testé manuellement; 14 tests unitaires avec CDP simulé |
| `claude_desktop_apply.py` — envoi (`stage: envoyé`) | rapporté par l'auteur dans le commentaire 6437860868 de l'issue #83 pour le pilote Node équivalent; non revalidé ici et **pas encore avec ce portage Python** |
| `claude_quota_log.py` | 13 tests unitaires; lecture d'un journal réel observée, pas une validation du transport |
| `resume desktop-apply` (CLI) | interface expérimentale; transport non raccordé au daemon |
| `ClaudePanel.swift` | **non compilé** (build interne à Reprise) |

## 1. Décision d'architecture, et ce qui a été écarté

| Voie | Verdict | Preuve |
|---|---|---|
| `--remote-debugging-port` au lancement de Claude Desktop | refus au démarrage de l'application rapporté | ce constat ne vient pas d'une observation du renderer et ne porte pas sur l'inspecteur principal natif |
| Inspecteur principal natif observé le 26 octobre | capacité distincte | non qualifié pour une soumission; ne constitue pas une voie d'envoi démontrée |
| Overlay Quick Entry automatisé par frappes | prototype testé avec permission Accessibilité requise | limite de ce chemin testé, pas preuve d'une impossibilité universelle |
| claude.ai en WKWebView | refusé au login | hCaptcha en boucle, ITP bloque les cookies tiers |
| Chrome headless | refusé | interstitiel « Un instant… », aucun composeur |
| **claude.ai dans un Chrome contrôlé, via CDP** | prototype exploratoire | rapport d'envoi du pilote Node dans le commentaire 6437860868 de l'issue #83; non revalidé ici |

Ces constats portent sur les chemins listés et leurs conditions de test; ils ne
démontrent pas l'impossibilité générale d'autres intégrations.

## 2. Machine à états

| `stage` | `ok` | `staged` | Sens | Action attendue |
|---|---|---|---|---|
| `envoyé` | ✓ | ✗ | clic d'envoi effectué | vérifier une preuve indépendante d'admission; le clic seul ne suffit pas |
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

Le dépôt possède déjà le contrat Quick Apply : `claude_desktop.review()` envoie
`{prompt, request_key, chatId, filePaths}` via `quick_entry/submit`, et rend
`{status, resumable, model, admission_only, response}`. Ce transport **ne crée pas
un second vocabulaire** : `to_submission(result)` projette le détail technique sur
ces statuts pour compatibilité, mais il n'est pas raccordé au chemin Quick Apply.
Le statut `finished` décrit ici le résultat local du clic ou du dépôt dans le
composeur : **clic ≠ admission du tour ≠ retour MCP**. Aucun résultat de modèle
n'est lu; `response` reste `None`. Continuer le même chat dans claude.ai ne prouve
pas que les outils MCP stdio locaux de Claude Desktop seront disponibles dans le
tour web, ni qu'un `submit_result` sera renvoyé; ces capacités restent à qualifier
séparément.

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
- intégration envisagée : le gestionnaire `quick_entry/submit` de
  `claude_desktop_quick.py`; cette intégration n'est pas incluse dans ce portage.

## 4. API

```python
from claude_desktop_apply import apply, send_staged, quota, launch, shutdown, raise_for_login

apply(prompt, chat_id, port=9333, on_login="fail"|"raise")  # pose + envoie
send_staged(port)          # envoie le composeur actif, sans vérifier le chat exact
quota(port)                # indice UI; available=False/blocked=None si navigateur ou bouton absent
raise_for_login(port)      # affiche une fenêtre Chrome; ne navigue pas vers /login
```

CLI expérimental (non appelé par le daemon ou le Swift dans ce portage) :

```bash
resume desktop-apply --status
resume desktop-apply --text "…" --chat <uuid> [--raise-on-login]
resume desktop-apply --raise          # connexion humaine
```

## 5. Invariants — à ne pas casser

1. **Aucune heure déduite d'un « HH:MM » sans date.** L'ambiguïté
   aujourd'hui/demain reste refusée, comme dans `claude_quota.parse_pause`.
2. **Un composant absent ne prouve rien.** Navigateur ou bouton d'envoi absent ⇒
   `available: false`, `blocked: null`, jamais `blocked: false`.
   `sendDisabled` et le texte de la page sont des indices d'interface, pas une
   preuve de quota. Une confirmation peut venir d'un message explicite ou d'un
   événement renderer, à condition de l'attribuer au même compte et au même
   pool. Si une échéance connue autorise une tentative, aucune pré-mesure n'est
   obligatoire.
3. **Un événement de quota expiré n'est pas un blocage** (`resetsAt <= now`).
4. **Ne pas fermer le navigateur sur `session`** : la connexion deviendrait
   impossible.
5. **Le quota ne se lit qu'après insertion** : à vide, le bouton est désactivé
   pour une raison triviale.
6. **`chat_id` absent ⇒ nouvelle conversation.** Pour viser une existante, l'UUID
   est obligatoire pour `apply`. `send_staged` n'a pas de paramètre chat et n'est
   pas sécurisé contre l'envoi du texte présent dans le mauvais chat; ne pas
   l'utiliser pour reprendre une conversation précise.
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

## 7. Quota : éléments de preuve et indices d'interface

| Source | Voit | Ne voit pas |
|---|---|---|
| Message explicite de quota | le refus annoncé dans ce contexte | événements non visibles dans ce message |
| Journal renderer (`exceeded_limit` + `resetsAt`) attribué au même compte/pool | rejet serveur et échéance | blocage client sans requête émise |
| Page (`sendDisabled` + texte présent) | un indice de blocage client | la cause certaine du bouton désactivé |

`page.sendDisabled` avec texte présent est seulement un indice de blocage client;
le bouton peut être désactivé pour d'autres raisons. `quota()` ne prouve donc pas
un quota. Un événement explicite attribué au bon compte/pool ou une échéance déjà
connue peut autoriser une tentative sans mesure préalable obligatoire. Aucun
retour MCP n'est produit par le transport.

## 8. Découpe « lien + collage » : hors périmètre du démon

Le transport Python ne découpe pas les messages et n'implémente pas le parcours
« lien + collage ». Il rejette les prompts de plus de 12 000 caractères. Une
limite de taille connue pour les deep links ne constitue pas une capacité de
découpage de ce transport.

## 9. Recettes de test (sans consommer de quota)

```bash
# 1. navigateur absent : dégradation propre
resume desktop-apply --status

# 2. profil vierge : fenêtre rendue à l'humain; ouvrir la page de connexion manuellement
TMPP=$(mktemp -d); CLAUDE_CDP_PROFILE=$TMPP resume desktop-apply --raise --port 9340
CLAUDE_CDP_PROFILE=$TMPP resume desktop-apply --status --port 9340   # état UI/quota, pas un stage session

# 3. quota, sans envoi
python claude_quota_log.py     # événement + resetsAt + expired
```

Ne jamais tester un envoi quand le quota est bloqué : cela ne prouve rien et
consomme la fenêtre de reprise.

## 10. Ce qui reste, avec critère d'acceptation

| # | Tâche | Acceptation |
|---|---|---|
| 1 | qualifier l'envoi du portage Python | vérifier la cible effective, puis une preuve indépendante d'admission; le clic et l'affichage d'un message ne suffisent pas |
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

## 12. Provenance et limites de qualification

Les tests unitaires listés au §0 ont été exécutés localement; ils simulent CDP
et ne valident pas un envoi réel. Les observations de navigation et l'envoi du
pilote Node sont des rapports d'auteur consignés dans l'issue #83, notamment le
commentaire 6437860868; ils n'ont pas été reproduits pendant ce portage. Les
propriétés non établies sont indiquées comme expérimentales ou non qualifiées.

`apply` valide la forme de `chat_id`, mais ne revalide pas après navigation que
l'URL et l'identifiant de chat effectifs correspondent à la cible avant
l'insertion ou le clic. Cette vérification est requise avant toute utilisation
en production. Même après vérification de la cible, il faut distinguer le clic,
l'admission effective du tour et le retour MCP.
