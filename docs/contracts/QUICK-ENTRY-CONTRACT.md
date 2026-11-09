# Contrat Quick Entry natif (`QTi`) — pipeline de soumission Desktop

Source : chunk main `.vite/build/index.chunk-BBUaTQ8d.js` de Desktop 4.23118.0
(Electron 44.9.3), extrait par le recensement (`reprise/quickapply_census.py`).
Ce contrat décrit le pipeline natif que le patch #124 utilise pour déclencher
l'exécuteur sans intervention.

## Identité de la fonction

```js
module.exports = { activateQuickEntryWindow: qTi,
                   requestQuickWindowDismissWithPayload: QTi,
                   stampSubmission: ZTi }
function XTi(e) { QTi({text: e ?? "", images: []}) }   // wrapper historique
async function QTi(e, t, n) { … }
```

- `QTi` = export `requestQuickWindowDismissWithPayload` : le handler de soumission
  du Quick Entry natif (la petite fenêtre de saisie globale).
- `ZTi` = `stampSubmission` : résout l'identité (`{identity, multiAccount}`).
- `qTi` = `activateQuickEntryWindow` : ouvre/active la fenêtre de saisie.

## Signature

```
await QTi(payload, identity?, attachments?)
payload     {text: string, images: any[], chatId?: string}
identity?   {identity: …, multiAccount: boolean}   — précalculée via ZTi(), sinon résolue en interne
attachments? {count: number, maxFileMb: number}    — pièces jointes du Quick Entry (n)
```

## Garde d'admission (critique pour le déclencheur)

```js
let r = !!e.text && e.text.trim().length > 2 || e.images.length > 0
```

- Le texte doit faire **strictement plus de 2 caractères après trim**, ou des
  images doivent être présentes.
- Sinon : **rejet silencieux** — la quick window est fermée, rien n'est envoyé,
  aucune erreur remontée.
- Conséquence protocole : le déclencheur Reprise ne peut pas être `go` (2 chars) ;
  il doit faire au moins 3 caractères après trim (forme retenue : `go!`).

## Routage du payload

| Condition | Comportement |
|---|---|
| `chatId` absent | **Crée un nouveau chat** (`rEi(payload, identity)`) et y soumet le texte. |
| `chatId` fourni + fenêtre principale visible (`V` non détruite) | Focus de la fenêtre principale, résolution de la mainView webContents (`tEi()`), envoi au chat ciblé (`nEi(contents, {chatId, text, images})`). **C'est le chemin du chat armé.** |
| `chatId` fourni + fenêtre principale invisible | Fallback : nouveau chat (comme sans chatId). |
| mainView webContents indisponible | Payload **abandonné** (log `mainView webContents unavailable; dropping payload`). |

## Effets de bord

- `VTi()` : ferme/cache la quick window si ouverte.
- Télémétrie `desktop_quick_entry_submit` (longueur du texte, nombre d'images).
- L'identité est résolue par `ZTi()` (stampSubmission) si `t` n'est pas fourni.
- Le dispatch est **asynchrone** : la fonction rend la main après avoir lancé
  l'envoi ; le résultat n'est pas observable par l'appelant.

## Intégration Reprise (patch #124)

- L'injection (`reprise/desktop_patch.py`, constante `INJECTION`) ouvre un socket
  Unix `~/.claude-reprise-trigger.sock` (0600) dans le main process et appelle
  `QTi({text, images: []})` **en différé** (`setImmediate`), après avoir répondu
  `{ok: true, dispatched: "queued"}` au client (fire-and-forget).
- Client : `desktop_patch.send_trigger(text)` — une ligne JSON, puis
  `shutdown(SHUT_WR)` (le serveur répond sur `end`).
- Le patch de l'asar implique la mise à jour du hash `ElectronAsarIntegrity`
  (Info.plist) **et** du digest du dictionnaire dans le slot
  `__DATA_CONST,__asar_integrity` du Framework (voir REX #124) ; la re-signature
  doit être `codesign --force --deep --sign -` (les helpers Anthropic refusent
  sinon le Framework ad-hoc).

## Contraintes d'usage pour la boucle Reprise

1. **Texte du déclencheur ≥ 3 caractères après trim** (`go!`, pas `go`).
2. **Router vers le chat armé exige son `chatId`** — à relever une fois (URL du
   chat) et à stocker dans l'état Reprise ; sans chatId, chaque déclencheur crée
   un nouveau chat (utile pour le test, pas pour la production).
3. **La fenêtre principale doit être visible** pour le routage par chatId.
4. La copie patchée partage l'userData avec l'originale (même login, mêmes
   chats, mêmes connecteurs) ; single-instance : les deux apps ne tournent pas
   en même temps.
