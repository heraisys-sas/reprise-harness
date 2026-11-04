# `apply` via WKWebView — spécification

Statut : prototype archivé. Dans le run observé, la connexion est bloquée sur
`/login` par le cycle hCaptcha/ITP; le prototype n'a atteint ni insertion ni
envoi. Il ne s'agit pas d'une fonctionnalité active dont l'insertion « reste à
prouver ».

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

La seconde n'est qu'une piste de conception : elle n'est pas implémentée dans ce
prototype archivé et son efficacité n'a pas été établie.

## 6. Sécurité

- le delegate filtre les schémas, mais autorise tout `https`, tout `http` et
  `about`; ce n'est **pas** une liste blanche d'origine, et des pages HTTP de
  toute origine restent autorisées. Aucune restriction d'hôte n'est implémentée ;
- les popups sont rejetés par le delegate; aucun contrôle de téléchargement ni
  permission média spécifique n'est configuré ici ;
- `WKWebsiteDataStore.default()` fournit le stockage persistant utilisé par ce
  webview; le prototype ne définit pas de frontière d'isolation supplémentaire ;
- aucun secret dans les arguments de ligne de commande (le prompt passe en
  base64 sur stdin/argv du processus, jamais dans une URL).

En conséquence, la protection par origine annoncée initialement n'est pas
implémentée. Ce prototype archivé ne doit pas être traité comme une surface web
à isolation vérifiée.

## 7. État des essais à l'archivage

Le run documenté a atteint `/login`, où le cycle hCaptcha/ITP a bloqué la
connexion. Les stages `connecté`, `prêt`, `envoyé` et `quota` n'ont donc pas été
qualifiés pour ce prototype. La table précédente de critères n'est pas un plan
actif de validation; aucune soumission réelle n'est établie.

## 8. Risques connus

- insertion ProseMirror (cf. §5) ;
- expiration de session : à traiter comme un état affiché, pas comme une erreur ;
- la page peut changer de structure : `chat-input-send` et le composeur sont des
  sélecteurs à revalider ;
- pas de retour de réponse : le canal reste d'émission.
