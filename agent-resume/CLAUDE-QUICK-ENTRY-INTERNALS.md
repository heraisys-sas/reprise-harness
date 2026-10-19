# Claude Desktop — Quick Entry internals (2.42304.2)

Extraction vérifiée depuis `Contents/Resources/app.asar` (bundle 2.42304.2). Ce document consolide la chaîne exacte qui transporte un prompt vers une conversation Claude Desktop, pour l'intégration au superviseur local. Toutes les références `XxYr` sont les identifiants minifiés réels du bundle.

## 1. Déclencheurs natifs (ouverture de Quick Entry)

Préférences lues dans `~/Library/Application Support/Claude/claude_desktop_config.json` :

- `quickEntryShortcut: "double-tap-option"` — bascule l'overlay Swift (`SW.quickAccess.overlay.toggle()` via `CCr()`).
- `quickEntryDictationShortcut: "capslock"` — dictée, distincte de la soumission.

Le module natif `swift_addon.node` émet `optionDoubleTapped` et `capsLockChanged`, écoutés par le process principal (`SW.on(...)`). L'overlay Swift expose `setRecentChats`, `setActiveChatId`, `setLoggedIn`.

## 2. Chaîne de soumission (payload → conversation)

Ordre exact vérifié dans le bundle :

1. **Overlay Swift → process principal** : événement natif `quickEntrySubmitted`, géré par `FCr({prompt, images, filePaths, chatId})`.
2. **`FCr`** — construit `c = {text: prompt, images: [], chatId}` :
   - chaque image devient JPEG base64 (`mimeType: "image/jpeg"`, redimensionnée à 800 px) ;
   - chaque fichier joint est lu depuis `fileURLToPath`, limité à `PCr = 31457280` octets (30 Mo), encodé base64 avec son mime réel ;
   - appelle `requestQuickWindowDismissWithPayload(c, stamp, {count, maxFileMb})` avec le tampon de `stampSubmission()`.
3. **`V4r(e, t, n)`** — validation et routage :
   - garde : `text.trim().length > 2` **ou** au moins une image ;
   - journalise `desktop_quick_entry_submit` ;
   - identité : `t ?? await B4r()` (compte courant + flag multi-account `96101707`) ;
   - si `e.chatId === undefined` → **`K4r(e, r)`** crée une nouvelle conversation ;
   - sinon si la fenêtre principale est vivante → envoi vers `e.chatId` via `G4r(webContents, {chatId, text, images}, "the open chat")` ;
   - sinon repli : nouvelle conversation.
4. **`K4r(e, t)`** — création de conversation :
   - récupère le webContents principal via `W4r()` ;
   - `iR()` attend que la page web soit hydratée ;
   - garde anti-changement de compte `U4r(t)` (compare l'identité au moment de la soumission ; si différente, **rejette le payload** et affiche un toast `quick_entry_inactive_account`) ;
   - `G4r(webContents, payload, ...)` ;
   - si la page n'est pas prête, recharge l'URL racine (`loadURL(Mjn().toString())`) puis redélivre.
5. **`G4r(e, t, n)`** — point d'injection réel dans l'app web :
   ```js
   IFt.getDispatcher(e)?.dispatchOnQuickEntrySubmit(t)
   ```
   puis focalise la fenêtre principale et le webContents.

## 3. Pont renderer (IPC)

Le bridge expose `claude.web.QuickEntry` :

- **méthode** `setRecentChats(chats, activeChatId)` → canal `QuickEntry_$_setRecentChats` ;
- **événement** `onQuickEntrySubmit(payload)` → canal `QuickEntry_$_onQuickEntrySubmit` (émis par `dispatchOnQuickEntrySubmit`).

Le payload IPC porte `prompt`, `images`, `filePaths`, `chatId`, `activeChatId`, `chatName`. Le web app écoute `onQuickEntrySubmit` et injecte le prompt dans le composer de la conversation ciblée, **envoyé sans clic supplémentaire** (confirmé par l'essai du 19 octobre : un seul `Return` dans Quick Entry a lancé le tour).

## 4. Déclencheur manuel (sans Swift)

`z4r(e)` : `yCr() || V4r({text: e ?? "", images: []})` — chemin de repli appelant directement le routage quand l'overlay Swift est indisponible.

## 5. Conséquences pour l'intégration superviseur

- Le seul point d'entrée non UI est `dispatchOnQuickEntrySubmit` (dispatcher IPC du webContents principal), qui **émet un tour complet** — contrairement à MCP Apps `ui/message` (pré-remplissage seul) et à `sampling/createMessage` (refusé, `-32601`).
- Pour piloter depuis le superviseur, il faut soit (a) déclencher le flux natif `quickEntrySubmitted` avec un payload `{prompt, images, filePaths, chatId}`, soit (b) émettre `dispatchOnQuickEntrySubmit` sur le webContents principal. Aucun des deux n'expose d'API publique ni de socket réseau : internes à l'instance Electron en cours.
- Le garde `U4r` impose un compte connecté stable entre la construction du payload et la livraison.

## 6. Limites de preuve

- L'essai réel (19 octobre, job `619a69f2-03a9-44cd-9b81-f53028d763b5`, conversation `178dcceb-fcd3-4f2d-ac4f-116a8a153bc3`) a prouvé le round-trip **avec ouverture manuelle de Quick Entry par l'utilisateur** et un seul `Return`.
- Le mode non surveillé (ouverture + soumission entièrement automatisées) **n'a pas été prouvé** : l'outil d'automatisation a refusé le double-appui sur Option (`keyPressIncludedNoNonModifierKeys`) et n'a pas pu capturer d'écran.
- La nécessité d'un clic sur le menu de destination reste indéterminée ; dans l'essai la conversation active était déjà correcte.

## Revue de code (MCP Reprise, 19 octobre 2026)

Une revue via `reprise_delegate` (Codex, Sol/medium, thread `01a154ca`) a porté sur l'analyse de journaux. Correctif intégré : le probe de liveness distingue désormais `running` / `stopped` / `indeterminate` (un socket présent mais rejeté n'est plus déclaré arrêté).

Retours restants à intégrer, transposables à `reprise_logs.py` / `reprise_health.py` :
- **[High]** Un parsing incomplet peut préserver un état périmé : signaler les trous de décodage / enregistrements tronqués, retourner un état incertain plutôt que de conclure.
- **[Medium]** Exiger une identité confirmée avant de déclarer une tâche « reprise possible » (`resumable()`).
- **[Medium]** Valider la forme des entrées avant accès ; normaliser les échecs de validation en `ValueError` (une entrée malformée ne doit pas interrompre l'analyse).
- **[Medium]** Lier les explications mises en cache à un digest source ; les marquer historiques quand la session avance.

Les constats d'origine visaient `detect.py`, `engine_events.py`, `local_analysis.py`, `shared_mode.py`, `connection.py` — non couverts par les tests actuels de ces fichiers.
