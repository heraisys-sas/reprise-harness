# Plan — « Prendre la place de Quick Entry UI » (Claude Desktop 4.4212.0)

Statut : plan soumis à validation opérateur. Aucune modification de `/Applications/Claude.app` avant go explicite.
Contexte : issue #102 (aller-retour claude-desktop), compléments d'analyse #36 (commentaire 6548235280).
Objectif : permettre à Reprise de soumettre un prompt dans Claude Desktop **sans fenêtre Quick Entry**, en réutilisant le pipeline natif existant `TYr` (images, fichiers, `chatId` pour chat existant).

## 1. Rappel des faits (analyse statique 4.4212.0, chunk `index.chunk-m4oMImJ-.js`)

- Chaîne de soumission : `KW?.on("quickEntrySubmitted", TYr)` (claude-swift → main process), puis
  `TYr({prompt, images, filePaths, chatId})` → `stampSubmission` + redimensionnement images (max 800 px, JPEG) →
  `xei` → `bei` → `pqt.getDispatcher(mainViewWC).dispatchOnQuickEntrySubmit(payload)` → claude.ai (web app distante).
  `chatId` est supporté : soumission possible **dans un chat existant**.
- La réponse n'est pas lisible par ce canal (unidirectionnel) ; le retour reste MCP `get_task`/`submit_result` (#102).
- Voies écartées :
  - **CDP externe** : `index.pre.js` refuse de démarrer si `--remote-debugging-port`/`-pipe`/overrides réseau sont
    présents, sauf token `CLAUDE_CDP_AUTH` signé Ed25519 (clé publique Anthropic codée en dur, TTL 5 min,
    lié au user-data-dir) — non forgeable.
  - **swift_addon.node externe** : instance séparée ; l'événement n'atteint pas le main process de Desktop.
- Verrou restant : `ElectronAsarIntegrity` (SHA-256 de `app.asar` dans `Info.plist`, app signée).

## 2. Approche retenue : patch minimal d'`app.asar` + re-signature ad-hoc

### 2.1 Patch (un seul point d'injection)

Dans `index.chunk-m4oMImJ-.js`, juste après l'enregistrement existant :

```js
KW?.on("quickEntrySubmitted",TYr)
```

(offset ≈ 3 968 009 dans le chunk ; `node:net` est déjà requis dans le scope du module), injecter :

```js
try{const rq=require("node:net"),rp=require("node:path"),rf=require("node:fs");
const RS=rp.join(rf.realpathSync(require("os").homedir()),".claude-desktop","quick-entry-reprise.sock");
try{rf.unlinkSync(RS)}catch{}
const RSV=rq.createServer(s=>{let b="";s.on("data",d=>{b+=d;if(b.length>8*1024*1024)s.destroy()});
s.on("end",()=>{try{const p=JSON.parse(b);
if(typeof p.prompt!=="string"||!p.prompt.trim())throw Error("prompt required");
TYr({prompt:p.prompt,images:Array.isArray(p.images)?p.images.map(String):[],
filePaths:Array.isArray(p.filePaths)?p.filePaths.map(String):[],chatId:p.chatId||void 0})
.then(()=>s.write(JSON.stringify({ok:!0}))).catch(e=>s.write(JSON.stringify({ok:!1,error:String(e)})));
}catch(e){s.write(JSON.stringify({ok:!1,error:String(e)}))}});});
RSV.on("error",e=>N.warn("[repriseQE] socket error %o",e));
RSV.listen(RS,()=>N.info("[repriseQE] listening %s",RS));}catch(e){N.warn("[repriseQE] init failed %o",e)}
```

Propriétés :
- Réutilise `TYr` tel quel (garde `inactive_account`, `stampSubmission`, routage `chatId`) — aucune logique de
  soumission dupliquée.
- Échec d'init = log only, ne casse jamais le démarrage de Desktop.
- Socket distinct de `quick-entry.sock` (natif) : `quick-entry-reprise.sock`, accessible au user uniquement.
- Sécurité : socket Unix 0700 implicite (dossier `~/.claude-desktop`), payload borné 8 Mo, pas d'exécution, un
  seul `TYr` par connexion.

### 2.2 Re-signature (copie de prototypage d'abord)

1. Copie : `cp -R /Applications/Claude.app /tmp/Claude-Reprise.app` (ou dossier dédié opérateur).
2. Reconstruire `app.asar` avec le chunk patché (parseur asar stdlib Python déjà validé, ou `@electron/asar`).
3. Recalculer le hash : `shasum -a 256 app.asar` → remplacer la valeur `ElectronAsarIntegrity` de
   `Resources/app.asar` dans `Info.plist` (`ion-dist.asar` inchangé).
4. Re-signer : `codesign --force --deep --sign - /tmp/Claude-Reprise.app`.
5. Lancer la copie avec un user-data-dir dédié (`CLAUDE_USER_DATA_DIR`) et un compte de test.

### 2.3 Prototype — critères d'acceptation

- La copie démarre sans erreur (`ElectronAsarIntegrity` validée par le hash recyclé).
- `~/Library/Logs/Claude/main.log` (copie) montre `[repriseQE] listening`.
- Soumission via socket → nouveau chat créé dans la copie ; puis variante `chatId` → message dans un chat existant.
- Images base64 + `filePaths` traités (pipeline `TYr` inchangé).
- Rollback : supprimer la copie ; `/Applications/Claude.app` jamais touché pendant le prototype.

## 3. Risques et limites (à accepter avant production)

- **Signature** : re-sign ad-hoc perd la notarisation Apple ; Gatekeeper/TCC à revalider ; macOS peut demander une
  ré-autorisation micro/caméra/accessibilité.
- **Mises à jour** : chaque update Desktop écrase le patch → script de ré-application à intégrer dans
  `native-install` (détection version asar, re-patch, re-hash, re-sign) ; version 4.4212.0 uniquement au départ.
- **Pas de retour** : le canal reste unidirectionnel ; la réponse se lit via MCP `get_task`/`submit_result` (#102).
- **Fragilité** : offset/minification du chunk change à chaque release — le script de patch doit ancrer sur la
  chaîne littérale `KW?.on("quickEntrySubmitted",TYr)`, pas sur un offset.
- **Traçabilité** : consigner chaque application (version Desktop, SHA-256 asar patché) dans ce fichier.

## 4. Prochaines étapes

1. Go opérateur → prototype sur copie (§2.2–2.3).
2. Si prototype OK : script `agent-resume/tools/patch-claude-quickentry.sh` (patch + hash + sign, dry-run par
   défaut) + intégration `native-install`.
3. Côté Reprise : client socket dans `claude-desktopd` (format payload = signature `TYr`), statut `running`
   vérifié par `connect()` (règle socket orphelin = `stopped`).
