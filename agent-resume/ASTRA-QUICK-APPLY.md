# ASTRA-QUICK-APPLY — contre-analyse et expérience discriminante

**Base de preuve** : recensement statique en lecture seule du bundle installé **Claude Desktop 3.6556.2** (rapport fourni par l'opérateur le 25/10), et `dev` à la date de rédaction. `MISSION.md`, `FOLLOWUP-EVIDENCE.md`, `ANALYSIS.md`, `QUICK-APPLY-NEXT.md` et `COWORK-RPC-MAP.md` n'ont toujours pas été fournis : les conclusions d'Astra restent connues par #36 seulement.

Niveaux : **[RECENSEMENT]** extrait cité du bundle 3.6556.2, **[DÉPÔT]** lu dans le dépôt, **[RAPPORTÉ]** affirmé dans #36 sans pièce brute, **[HYPOTHÈSE]** à confirmer.

Contraintes respectées : aucune automatisation d'interface (accessibilité ou web), aucun contournement d'authentification, d'attestation, de fuses ou d'intégrité, aucune modification ni injection dans Claude, aucun secret, cookie ou historique privé, aucune mutation de service, de VM, de redémarrage ou de permission, aucun repli par API payante.

## 0. Mise à jour du 29/10/2026 (`dev` `69f5eb3`, `VERSION` 0.1.17)

**Architecture produit retenue par l'opérateur.**
- **Livraison et retour par MCP** : `get_task`, `submit_result` et `await_task`, intégrés dans `dev` en 0.1.17 (`RELEASE-0.1.17.md`). `await_task` est masqué par défaut et s'active par `--enable-await` dans l'intégration Claude gérée. **Un essai réel supervisé dans Claude Desktop a confirmé le retour `await_task` → `submit_result` et son acquittement** ; il ne démontre ni le réveil autonome d'une conversation froide, ni l'isolation par identité de chat. Intégration et essai réalisés par le coordinateur à partir de mon travail de #79.
- **Secours** : le transport CDP de `dev` (`claude_desktop_apply.py`) relance une session **interrompue** dans son chat web existant (UUID exact), tant qu'aucune voie native propre dans Claude Desktop sans Accessibilité n'est trouvée. Preuve live du 27/10 : cette conversation (#79). Non qualifié : bouton natif « Continuer », reprise après quota, `submit_result`.
- **Capture du reset sans écran** : `claude_quota_log.py` lit dans `~/Library/Logs/Claude/claude.ai-web.log` la ligne `[COMPLETION] Request failed {"type":"exceeded_limit","resetsAt":<epoch>,…}`. Le texte du bandeau a changé (« Réinitialisation : 14:40 », sans date) et ne se parse plus ; le module refuse de deviner une heure.

**Toujours vrai** : résultat négatif sur Claude Desktop 3.6556.2 (aucune entrée externe non visuelle n'atteint le dispatch).
**Résolu depuis la première version** : fichiers de mission et sorties du recensement fournis, version mesurée (3.6556.2), inspector fermé (aucun accès étroit démontré à `ZIr`/`QXr`), question de la voie web tranchée (secours autorisé et testé une fois).

## 1. Verdict

1. **Objectif direct (entrée externe qui rejoint `dispatchOnQuickEntrySubmit` avec `chatId`/prompt) : négatif pour 3.6556.2.** La fonction de dépôt n'a que deux appelants (§2), tous deux liés à l'interface de Quick Entry. Aucun événement externe (`open-url`, `open-file`, `second-instance`, `continue-activity`) n'apparaît dans sa chaîne d'appels. Réserve : le graphe est heuristique ; le comptage global de l'outil mis à jour (§6) le confirmera sur tous les scripts.
2. **Aucune piste positive vérifiée, donc pas d'appel exact à fournir.** Créer l'entrée manquante reviendrait à modifier ou injecter dans Claude : interdit.
3. **Une piste indirecte reste ouverte** : les liens profonds `claude://` aboutissent au renderer (`handleDeepLink`). Ils permettraient au mieux de **préparer** un chat, pas de **livrer** un prompt, sauf si le renderer soumet de lui-même une valeur reçue (à instruire, §6).

## 2. Preuves du recensement (3.6556.2)

| Fait | Extrait | Niveau |
|---|---|---|
| Ancre présente dans `index.chunk-jPKPzV-P.js` | `$Xr(e,t,n)` : `qVt.getDispatcher(e)?.dispatchOnQuickEntrySubmit(t)` puis `focus()` | [RECENSEMENT] |
| Appelants de `$Xr` | `QXr` (envoi au chat `e.chatId` si la fenêtre principale est visible, sinon `eZr`) et `eZr` (nouveau chat) | [RECENSEMENT] |
| `QXr` = `requestQuickWindowDismissWithPayload` | exporté par `LXr` avec `activateQuickEntryWindow` et `stampSubmission` ; sans `chatId` → nouveau chat | [RECENSEMENT] |
| Appelant 1 de `QXr` | `ZIr({prompt, images, filePaths, chatId})`, enregistré par `rW?.on("quickEntrySubmitted", ZIr)` après le chargement de `claude-swift` ; il obtient `QXr` par l'espace de noms `LXr` (appel par alias, non vu par l'outil) | [RECENSEMENT] |
| Appelant 2 de `QXr` | `XXr(e)` → `QXr({text: e ?? "", images: []})`, appelée par `requestDismiss` (implémentation posée sur la fenêtre Quick **héritée** via `kSe.for(hq.webContents).setImplementation`) et par le `blur` de cette fenêtre. Sans `chatId` | [RECENSEMENT] |
| Fenêtre héritée ou overlay natif | `AIr()` choisit l'overlay Swift ; la `BrowserWindow` Quick n'est utilisée que sinon | [RECENSEMENT] |
| Entrées externes | `open-url` → `$z(url, mainView, {coldStart})` (gestionnaire de liens) ; `continue-activity` : lien web → `cGa`, Spotlight → `uGa`, session Claude Code → `atr` ; `open-file` : skills, fichiers DXT/MCPB, dossiers ; `second-instance` : uniquement l'assistant de relance `nRr` | [RECENSEMENT] |
| Aucune de ces entrées n'atteint `QXr`/`$Xr` | arêtes `uGa → TGa`, `EGa`, `r`, `pe`, `CGa` du rapport = artefacts de proximité de l'heuristique (le vrai lien de `TGa` est l'action `QUICK_ENTRY` qui **affiche** l'overlay) | [RECENSEMENT] + lecture |
| Liens profonds | schéma `claude` enregistré ; actions du Dock : `claude://claude.ai/new?surface=chat&source=desktop_action`, `claude://code/new?…`, `claude://code/continue?session=last&…` ; l'application les transmet au renderer par l'événement `handleDeepLink(url)` | [RECENSEMENT] |
| Absents | `NSServices`, dictionnaire AppleScript, App Intents, `PlugIns`, `XPCServices` | [RECENSEMENT] (confirme Astra) |
| Clavier | clé de configuration `globalShortcut` (`setGlobalShortcut`) : exige des événements clavier | [RECENSEMENT] |

## 3. Contre-analyse

**C1 — Versions : résolu pour la structure.** Trois versions apparaissent : 2.42304.2 (document d'extraction du dépôt), 3.1623.4 (inspection d'Astra), 3.6556.2 (installée). Les identifiants minifiés ont changé (`FCr/V4r/K4r/G4r` → `ZIr/QXr/eZr/$Xr`) mais le flux est identique : événement Swift → fonction de dépôt → dispatch avec `chatId` optionnel. Les conclusions d'Astra sur AppleScript et App Intents sont **confirmées** sur 3.6556.2.

**C2 — Entrées natives non écartées par le texte d'Astra : examinées, aucune n'atteint le dispatch.** Voir §2. Le chemin de repli `z4r` du vieux document est devenu `XXr`, et ne se déclenche que depuis la fenêtre Quick héritée.

**C3 — `companion-web`** interagit par l'interface web : à trancher avant tout prototype, car cela ressemble à l'automatisation d'interface interdite et ne rejoint pas le dispatch.

**C4 — Continuation par prompt (décision de l'opérateur du 25/10, #36 corrigée).** Si une entrée `(chatId, prompt)` existait, un message de continuation explicite remplacerait le bouton. **[HYPOTHÈSE]** à valider : la pause gratuite n'empêche pas un nouveau message après le reset.

**C5 — Limites du recensement.** Heuristique : les faux liens ci-dessus le prouvent. Le premier rapport ne comptait les références que dans le script de l'ancre ; l'outil mis à jour compte désormais l'ancre, `requestQuickWindowDismissWithPayload`, `quickEntrySubmitted`, `submit_quick_entry`, `handleDeepLink` et `claudeURLHandler` dans tous les scripts. Le module natif `claude-swift` n'est pas du JavaScript : l'émission de `quickEntrySubmitted` par l'overlay n'est pas analysable ici.

## 4. Pistes classées

| Mécanisme | Atteint le dispatch ? | Verdict |
|---|---|---|
| Événement Swift `quickEntrySubmitted` | oui, avec `chatId` | exige l'overlay natif (interaction d'interface ou accessibilité) : exclu |
| `requestDismiss` / `blur` de la fenêtre héritée | oui, sans `chatId` | IPC interne du renderer Quick : exclu (injection) |
| `claude://` → `$z` → `handleDeepLink` (renderer distant) | non | **indirect** : préparation seulement ; à instruire |
| Lien web universel / Spotlight / Handoff | non | exigent une action utilisateur ou le navigateur |
| `open-file` (skills, DXT/MCPB) | non | installent des outils MCP : concerne le **retour MCP**, jamais la livraison ; confirmation de l'utilisateur probable **[HYPOTHÈSE]** |
| `globalShortcut` configuré | via touche | interface : exclu |
| Modifier `claude_desktop_config.json`, inspecteur, injection | — | interdits |

## 5. Blocage précis

Dans 3.6556.2, **aucun appelant non visuel** de `QXr`/`$Xr` : l'entrée recherchée n'existe pas nativement. Livrer un tour sans interface demanderait d'émettre `quickEntrySubmitted` depuis le module natif ou d'appeler `QXr` depuis l'intérieur du processus, c'est-à-dire d'injecter ou de modifier Claude.

## 6. Expérience discriminante (exécutée) et suites

Les trois commandes de lecture seule ont été exécutées par Codex le 26/10 (`routes.md`, `sqe.md`, `census2.md`) et leurs conclusions sont intégrées aux §1 à §5. L'essai réel `open 'claude://…'` n'a pas été fait et n'est pas nécessaire tant que le canal MCP et le secours CDP couvrent le besoin ; il resterait une option pour préparer (jamais livrer) un chat.

**Suite utile** : le POC de `await_task` est fait (voir §0). Restent à mesurer la durée maximale d'un appel en attente et la limite d'étapes par tour (inconnues), et à trancher le réveil à froid, que seul le secours CDP couvre.

## 7. Quota et échéance

Principe cohérent avec #64 et #59 : enregistrer l'échéance, tenter **une** fois à l'échéance connue sans lecture préalable obligatoire, diagnostiquer en lecture seule après un échec.
- **Capture** : `resetsAt` exact (epoch) depuis le journal du renderer (`claude_quota_log.py`) ; sans date exacte, aucune heure inventée. Cas distinct : bouton d'envoi désactivé avec du texte présent et aucun rejet serveur (donc aucune ligne de journal), à croiser avec le journal.
- **Tentative** : sur un chat armé et vivant, par MCP ; sinon par le secours CDP, qui dépose dans le **même chat** le message de continuation explicite de #36 (« Continue. », consigné au reçu, jamais le remplacement d'un prompt de tâche). Une seule tentative par interruption.
- **Limites** : la preuve CDP du 27/10 ne qualifie ni la reprise après quota ni le bouton natif ; elle repose sur un navigateur dédié, un port ouvert et une connexion humaine.

## 8. Cowork (voie indépendante)

Le bundle contient un protocole `cowork-artifact://`, un serveur MCP `office365-mcp` et des clés de fonctionnalité Cowork : **aucun déclencheur externe** n'en ressort pour l'instant, et `COWORK-RPC-MAP.md` manque. **[HYPOTHÈSE]** à vérifier : des « tâches planifiées » natives (le déclenchement par horloge serait alors fourni par l'application elle-même, sans entrée externe). Vérification : `--grep 'scheduled' --limit 12` et `--grep 'schedule' --limit 12`. Cette voie ne concerne pas le chat gratuit et peut dépendre du plan : à qualifier avant tout travail.

## 9. Décisions restantes

1. Qualifier le secours CDP pour la reprise **après quota** (un essai borné, un seul chat, nonce) ; le bouton natif reste non démontré.
2. Garder ou non l'essai réel `open 'claude://…'` comme option de préparation.
