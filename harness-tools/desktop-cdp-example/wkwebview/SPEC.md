# `apply` via WKWebView — spécification

Statut : prototype compilé. WebKit rend bien l'application (vérifié : claude.ai
répond, redirige vers `/login`, aucun interstitiel anti-bot). Reste à prouver
l'insertion de texte et l'envoi.

## 1. Objet

Envoyer un tour dans une conversation claude.ai **sans CDP, sans Chrome séparé,
sans permission macOS**. L'application hôte rend la page elle-même et la pilote
par `evaluateJavaScript`.

## 2. Architecture

```
Reprise (app) ── WKWebView ──► claude.ai
                    │
                    └── evaluateJavaScript : lire l'état, écrire, cliquer
```

Ce qui disparaît par rapport au montage Chrome + CDP :

| Élément | Sort |
|---|---|
| port de diagnostic ouvert | supprimé |
| profil Chrome à maintenir | supprimé |
| pilote Node | supprimé |
| lancement/arrêt d'un navigateur externe | supprimé |
| session claude.ai dans un tiers | remplacée par un bac à cookies du bundle |

## 3. Bac à cookies

Identité : `com.heraisys.reprise.claudeapply` (Info.plist). Les cookies vivent
dans le conteneur WebKit de ce bundle et **persistent entre les lancements**.

Conséquence à assumer : c'est une **troisième** session Claude, distincte de
l'app Desktop et d'un éventuel Chrome de diagnostic. Une connexion manuelle,
une fois, dans une fenêtre visible (`--login`).

## 4. Contrat de retour

Identique à `desktop_apply.py` pour que l'appelant n'ait qu'un seul format :

| `ok` | `stage` | Sens |
|---|---|---|
| ✓ | `envoyé` | clic d'envoi effectué |
| ✓ | `prêt` | `--check` : composeur présent |
| ✓ | `connecté` | `--login` : session établie |
| ✗ | `composeur` | pas de champ (login, page non chargée) |
| ✗ | `frappe` | texte absent après insertion |
| ✗ | `quota` + `staged` | bouton d'envoi désactivé, texte en place |
| ✗ | `envoi` | clic refusé |

## 5. Insertion de texte : le point technique à valider

En Chrome, `document.execCommand('insertText')` mettait le texte dans le DOM
**sans** mettre à jour l'état de l'éditeur (ProseMirror) : bouton inerte. WebKit
peut se comporter autrement — c'est l'inconnue n°1 du prototype.

Deux stratégies, dans cet ordre :

1. `execCommand('insertText')` après sélection totale (implémenté) ;
2. si l'état ne suit pas : `NSTextInputClient.insertText(_:replacementRange:)`
   sur la WKWebView — l'API texte d'AppKit, qui alimente le contenu comme une
   frappe réelle, sans passer par le système.

La seconde est la raison pour laquelle cette architecture reste crédible même si
la première échoue.

## 6. Sécurité

- navigation en **liste blanche** : seule l'origine claude.ai est autorisée, le
  reste part vers le navigateur système ;
- pas de téléchargement, pas de popup, pas de permission média ;
- `WKWebsiteDataStore` dédié : aucune lecture croisée avec IronClaw ni avec un
  autre contenu web de l'app ;
- aucun secret dans les arguments de ligne de commande (le prompt passe en
  base64 sur stdin/argv du processus, jamais dans une URL).

## 7. Plan de test

| # | Test | Attendu |
|---|---|---|
| 1 | `--check` sans session | `stage: composeur` (constaté) |
| 2 | `--login` | fenêtre visible, connexion, `stage: connecté` |
| 3 | `--check` après login | `stage: prêt`, `hasComposer: true` |
| 4 | `--apply` sur une conversation existante | `stage: envoyé` |
| 5 | `--apply` sous quota épuisé | `stage: quota`, `staged: true` |
| 6 | relance après arrêt du processus | session conservée, pas de re-login |

## 8. Risques connus

- insertion ProseMirror (cf. §5) ;
- expiration de session : à traiter comme un état affiché, pas comme une erreur ;
- la page peut changer de structure : `chat-input-send` et le composeur sont des
  sélecteurs à revalider ;
- pas de retour de réponse : le canal reste d'émission.
