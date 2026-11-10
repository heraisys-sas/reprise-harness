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

- L'injection (`reprise/desktop_patch.py`, constante `INJECTION`) ouvre le
  **socket Quick Apply** `~/.claude-desktop/quick-entry.sock` (0600) dans le main
  process, protocole `reprise-dispatch-1`. Ce socket n'existe que quand ReClaude
  tourne ; c'est lui que le scheduler (`claude_quota.run_quick`) et le backend
  `claude-desktop` de `reprise_delegate` utilisent via `REPRISE_DISPATCH_SOCKET`.
  **Fusion du 10/11/2026 (confirmée par l'opérateur et par sonde live)** : les
  deux sockets créés par le patch #124 initial (`quick-entry.sock` /
  `quick-entry-reprise.sock`) ont été fusionnés en un seul, au nom historique
  `quick-entry.sock` ; l'ancien nom n'est plus créé. Un seul socket = un seul
  canal de dispatch (natif QTi + CDP par configuration).
- Protocole (`reprise-dispatch-1`) : à la connexion, le serveur envoie d'abord le
  greeting `{"hello":true}` ; puis JSON-RPC, **une ligne par requête/réponse** :
  - `initialize` → `serverInfo {name: "reprise-dispatch", version: "1"}` ;
  - `quick_entry/capabilities` → `{dispatch_supported, resume_validation_supported,
    protocol: "reprise-dispatch-1"}` ;
  - `quick_entry/submit` : `params.request_key` **requis** (erreur `-32602`
    sinon), `prompt` ≥ 3 caractères après trim, `chatId` optionnel (sans chatId,
    un nouveau chat est créé), `purpose` ; un `request_key` déjà vu renvoie
    `{duplicate: true}` (déduplication mémoire, perdue au redémarrage de l'app —
    elle ne justifie jamais un renvoi sans opérateur).
- Fragilité QTi : `QTi` est un identifiant minifié du bundle Claude Desktop ;
  l'extraction côté patch dépend de la forme du bundle et peut casser à une mise
  à jour. L'abort reste propre : en cas d'échec, le patch se retire sans laisser
  l'app dans un état intermédiaire.
- Modèle de sécurité same-user : tout processus du même uid peut se connecter au
  socket et soumettre un prompt au nom de ReClaude. Atténuation actuelle : droits
  `0600` sur le socket. Ce modèle est documenté et assumé, pas supprimé.
- Procédure après mise à jour Desktop/Squirrel : re-patcher l'asar
  (`reprise/desktop_patch.py`), exécuter `verify_patch`, re-signer ad-hoc
  (`codesign --force --deep --sign -` — les helpers Anthropic refusent sinon le
  Framework ad-hoc), puis lancer ReClaude avant toute soumission (le socket
  n'existe pas tant que ReClaude ne tourne pas).
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
5. **Nommage validé par l'opérateur (09/11/2026)** : l'originale reste
   `/Applications/Claude.app` (intacte, mise à jour par Squirrel) ; la copie
   patchée s'appelle **`~/Applications/ReClaude.app`** — app par défaut de
   `desktop_patch` (`COPY_APP`). Le nom du bundle n'a pas d'effet sur la
   signature (vérifié : `codesign --verify --deep --strict` OK après
   renommage) ; seule l'identité de signature ad-hoc compte. C'est la copie
   qui crée le socket `~/.claude-desktop/quick-entry.sock` (fusion 10/11/2026,
   un seul socket) : un socket présent + greeting `{"hello":true}` prouve que
   ReClaude (et non l'originale) tourne.
