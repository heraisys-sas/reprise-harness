# ChatGPT Reprise.app — wrapper de lancement

Lance **ChatGPT Desktop** (`/Applications/ChatGPT.app`, bundle `com.openai.codex`) avec le
port DevTools Chromium ouvert, sans jamais modifier le bundle. Le backend Reprise s'y attache
en CDP pour piloter le chat (quota ChatGPT) ; le moteur Codex et codexd ne sont pas sollicités.

## Usage

- Installer depuis le dépôt : `cp -R reprise/wrappers/chatgpt-reprise/"ChatGPT Reprise.app" ~/Applications/`
  puis lancer **ChatGPT Reprise** à la place de ChatGPT.
- Port : `9333` par défaut, surchargeable par `CHATGPT_CDP_PORT`.
- Journal : `~/Library/Logs/chatgpt-reprise-launcher.log`.

## Comportement

1. Port déjà actif → active simplement l'app (voie rapide, idempotent).
2. App lancée sans le port → quit propre (AppleScript, arrêt forcé en secours) puis relance avec `--remote-debugging-port`.
3. App fermée → lancement direct avec le flag, puis attente du port (30 s max).

## Vérification

```bash
curl -s http://127.0.0.1:9333/json/version   # doit répondre Chrome/157…
node reprise/chatgpt_cdp.mjs discover        # sondes DOM de la fenêtre principale
node reprise/chatgpt_cdp.mjs send "ping"     # dispatch + capture de réponse
```

Pont CDP : `reprise/chatgpt_cdp.mjs` (targets / discover / newchat / send).
Sécurité : le port n'écoute que sur 127.0.0.1 ; usage mono-utilisateur local, même modèle de
confiance que les sockets 0600 de Reprise. Ne pas exposer hors de la machine.
