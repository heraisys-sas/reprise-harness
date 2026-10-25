# Validation e2e du mode orchestrateur ASI1 — issue #75

Placement proposé : `agent-resume/ASI1-ORCHESTRATOR-E2E.md`

## Référence, algorithme et invariants

Ce protocole valide le lot 4/4 après application des lots suivants :

| Lot | Branche de référence | Commit |
|---|---|---|
| Proxy | `asi1/72-proxy-planner` | `11a2a1d` |
| Configuration | `asi1/73-config-planner-preset` | `cee048f` |
| LaunchAgent | `asi1/74-launchagent-proxy` | `e3d5673` |

**Évidence fournie :** les comportements et commits ci-dessus constituent la référence de ce document. `proxy/test-proxy.js` prouve déjà le rewrite en test unitaire selon les éléments fournis.

**À vérifier par l’opérateur :** ces trois lots sont effectivement présents dans l’installation testée ; le profil relancé utilise cette configuration. Aucun résultat e2e n’est présumé acquis.

Algorithme de validation :

1. Vérifier l’installation et préparer des fenêtres de mesure isolées.
2. Remplacer temporairement l’upstream par un serveur local d’inspection, sans clé.
3. Vérifier directement le rewrite et le nombre de requêtes upstream.
4. Restaurer le LaunchAgent et son upstream de production.
5. Exécuter deux tours dans un chat orchestrateur A, puis un tour dans un nouveau chat orchestrateur B.
6. Exécuter un chat normal `asi-cloud`, puis confirmer son fonctionnement avec le proxy arrêté.
7. Confronter observations client, fenêtres JSONL et capture locale ; renseigner les résultats.

Invariants :

- Le chat orchestrateur utilise `asi-cloud-planner` et le preset `asi1-orchestrator`.
- Le chat normal utilise `asi-cloud`, avec son upstream direct et sans header gate.
- Un même chat conserve son identité ; deux nouveaux chats ont des identités distinctes.
- Une requête entrante au proxy produit une requête upstream au maximum, sans retry.
- Les fenêtres mesurées excluent les contrôles `curl`, les autres chats et les requêtes encore en cours.
- Le proxy de production ne conserve ni corps, ni en-têtes, ni identifiant de session brut.
- Une réponse d’erreur, une mesure ambiguë ou un prérequis manquant ne peut pas être déclaré `PASS`.

**Écart explicite avec le critère 2 de l’issue :** le journal ne contient pas littéralement `planner_mode: true`, ni `x-session-id`. Exiger leur présence dans `traffic.jsonl` est incompatible avec son schéma volontairement minimal. La preuve proposée est la conjonction :

1. `sessionIdHash` non nul et stable dans les requêtes réelles ;
2. capture directe du corps réécrit par le même proxy au §3.

Cette équivalence doit être acceptée explicitement lors de la clôture de l’issue. Sinon, le critère littéral reste non satisfait ; ne pas enrichir le journal avec les corps pour le contourner.

## 1. Prérequis opérateur, dans l’ordre

### 1.1 Proxy installé et actif

```sh
test -f "$HOME/Library/LaunchAgents/sas.heraisys.asi1-planner-proxy.plist"
node --version
launchctl print "gui/$(id -u)/sas.heraisys.asi1-planner-proxy"
curl --noproxy '*' --silent --show-error \
  --output /dev/null --write-out '%{http_code}\n' \
  http://127.0.0.1:8481/v1/chat/completions
```

Attendu : service chargé dans `gui/$UID`, puis `404`. Cette requête sans gate vérifie la disponibilité du socket HTTP et le refus d’accès ; elle ne vérifie pas le rewrite.

Cette sonde ajoute une entrée `NOT_FOUND` au journal de production ; les fenêtres de mesure démarrent après, elles ne contiendront donc pas ces entrées. Cette entrée a `errorCode: "NOT_FOUND"`, `sessionIdHash: null` et `status: 404`.

Le chemin du plist ci-dessus est le chemin d’installation attendu. S’il diffère de l’installation documentée dans `launchagents/README.md`, relever le chemin réel avant de poursuivre.

Le dépôt de référence est celui du checkout utilisé à l’installation par `asi1_proxy_install`, qui le dérive avec `git rev-parse --show-toplevel` ; le vérifier avec `launchctl print` (`program arguments`) si le doute persiste.

### 1.2 Configuration #73 appliquée

Dans la configuration effective du profil, constater exactement :

- provider `asi-cloud-planner` ;
- `baseURL` : `http://127.0.0.1:8481/v1` ;
- header `x-asi-planner: 1` ;
- `cacheRetention: long` et `supportsLongCacheRetention: true` ;
- preset `asi1-orchestrator`, template `agent-resume/ASI1-ORCHESTRATOR.template.md` ;
- un seul outil exposé au modèle, consacré à la délégation ;
- provider normal `asi-cloud` conservant son upstream direct, sans header gate.

Relever le nom effectif de l’outil unique. Ne pas inventer son identifiant.

La clé reste configurée côté client. Toute référence documentaire emploie uniquement `<ASI_API_KEY>`. Ne pas afficher, copier ou exporter la configuration contenant la clé.

### 1.3 Relance du profil Harness

Relancer le profil après application de la configuration, puis ouvrir les nouveaux chats utilisés pour ce protocole. Ne pas réutiliser un chat créé avant la relance.

Vérifier dans le client le preset et le provider effectivement sélectionnés.

### 1.4 Préparer les mesures

Fermer ou laisser inactifs les autres chats susceptibles d’utiliser le proxy. Attendre la fin des requêtes en cours.

Dans le terminal de contrôle :

```sh
export LOG_PATH="$HOME/Library/Logs/asi1-planner-proxy/traffic.jsonl"
export E2E_DIR="$(mktemp -d /tmp/asi1-orchestrator-e2e.XXXXXX)"
export PLIST_PATH="$HOME/Library/LaunchAgents/sas.heraisys.asi1-planner-proxy.plist"
SCRIPT_PATH="$(plutil -extract ProgramArguments.1 raw -o - "$PLIST_PATH")"
export REPO="$(cd "$(dirname "$SCRIPT_PATH")/.." && pwd -P)"
test -f "$REPO/proxy/asi1-planner-proxy.js"
printf 'Artefacts : %s\n' "$E2E_DIR"
```

Les scripts ci-dessous créent des artefacts de test ; ils ne modifient pas les sources du dépôt. Garder ce terminal ouvert.

Installer ces deux fonctions pour délimiter une fenêtre de journal :

```sh
mark_window() {
  python3 - "$LOG_PATH" "$E2E_DIR/window-start.json" <<'PY'
import json
import pathlib
import sys

source, target = map(pathlib.Path, sys.argv[1:])
raw = source.read_bytes()
if raw and not raw.endswith(b"\n"):
    raise SystemExit("Journal incomplet : attendre, puis recommencer")
for line in raw.splitlines():
    json.loads(line)
stat = source.stat()
target.write_text(json.dumps({
    "device": stat.st_dev,
    "inode": stat.st_ino,
    "offset": len(raw),
}))
PY
}

collect_window() {
  python3 - "$LOG_PATH" "$E2E_DIR/window-start.json" "$1" <<'PY'
import json
import pathlib
import sys

source, marker, target = map(pathlib.Path, sys.argv[1:])
start = json.loads(marker.read_text())
stat = source.stat()
if (stat.st_dev, stat.st_ino) != (start["device"], start["inode"]):
    raise SystemExit("Journal remplacé : fenêtre invalide")
with source.open("rb") as stream:
    if stream.seek(0, 2) < start["offset"]:
        raise SystemExit("Journal tronqué : fenêtre invalide")
    stream.seek(start["offset"])
    raw = stream.read()
if raw and not raw.endswith(b"\n"):
    raise SystemExit("Entrée incomplète : attendre, puis recommencer")
rows = [json.loads(line) for line in raw.splitlines()]
target.write_bytes(raw)
print(f"{target.name}: {len(rows)} entrée(s)")
PY
}
```

Une fenêtre se commence par `mark_window`, puis se termine par `collect_window chemin`. Attendre la fin des réponses avant la collecte : une requête en cours peut ne pas avoir encore produit son entrée finale.

## 2. Grille critère → preuve

| Critère | Commande ou observation exacte | Condition de réussite |
|---|---|---|
| 1. L’orchestrateur délègue | Dans le chat réel, envoyer la consigne du §5 et ouvrir le détail des appels d’outils | Appel de l’unique outil de délégation avec une mission exploitable ; aucun outil local d’exécution exposé ; réponse finale fondée sur le retour de délégation |
| 2. Mode planner et identité | Assertions du §3 sur le corps reçu ; assertions du §5 sur les hashes | Rewrite direct conforme ; hash non nul, identique dans A et distinct dans B ; équivalence documentaire acceptée |
| 3. Chat normal non affecté | Fenêtre vide et observations du §4 ; nouvel échange normal avec proxy arrêté | Provider `asi-cloud` direct ; réponses complètes ; aucune nouvelle entrée proxy |
| 4. Streaming complet | Observation client du §7 et analyse des fenêtres réelles | Affichage progressif sans corruption ; fin attendue visible ; statut 200, `errorCode=null`, compteurs positifs et égaux |
| 5. Aucun retry proxy | Trois sondes entrantes et trois captures upstream au §3 ; unicité et comptage du §6 | Une capture par sonde ; identifiants distincts ; aucune requête inexpliquée |
| 6. Aucun secret | Contrôles de schéma et recherche du §8 | Schéma exact et zéro correspondance pour les motifs demandés |
| Global | Transcript réel de délégation, streaming et contrôle du chat normal | Tous les contrôles obligatoires passent ; aucune incertitude bloquante sur délégation ou régression |

Les résultats sont limités au scénario et à la durée observés. Une déclaration textuelle du modèle disant « j’ai délégué » ne constitue pas une preuve d’appel d’outil.

## 3. Preuve directe du rewrite, sans clé réelle

Cette phase interrompt temporairement le proxy utilisé par les chats. Aucun chat réel ne doit être envoyé pendant cette phase.

### 3.1 Serveur d’inspection local

Créer le serveur dans le répertoire temporaire :

```sh
cat > "$E2E_DIR/inspector.js" <<'JS'
'use strict';

const http = require('node:http');
const fs = require('node:fs');

const output = process.argv[2];
if (!output) throw new Error('Usage: node inspector.js OUTPUT_JSONL');

let sequence = 0;

http.createServer((req, res) => {
  const chunks = [];
  let size = 0;

  req.on('data', chunk => {
    size += chunk.length;
    if (size > 65536) {
      req.destroy();
      return;
    }
    chunks.push(chunk);
  });

  req.on('end', () => {
    let body;
    try {
      body = JSON.parse(Buffer.concat(chunks).toString('utf8'));
    } catch {
      res.writeHead(400, {'content-type': 'application/json'});
      res.end(JSON.stringify({error: 'INVALID_JSON'}));
      return;
    }

    fs.appendFileSync(output, JSON.stringify({
      sequence: ++sequence,
      method: req.method,
      url: req.url,
      body
    }) + '\n', {mode: 0o600});

    res.writeHead(200, {'content-type': 'application/json'});
    res.end(JSON.stringify({ok: true}));
  });
}).listen(8999, '127.0.0.1', () => {
  process.stderr.write('Inspector listening on 127.0.0.1:8999\n');
});
JS
```

Le serveur journalise les corps synthétiques reçus, jamais les en-têtes. Il est réservé à cette phase sans clé et ne doit pas recevoir de conversation réelle.

Dans un deuxième terminal, remplacer le chemin temporaire par celui affiché au §1 :

```sh
node /tmp/asi1-orchestrator-e2e.XXXXXX/inspector.js \
  /tmp/asi1-orchestrator-e2e.XXXXXX/inspection.jsonl
```

`XXXXXX` désigne ici le suffixe réel créé par `mktemp`, pas un chemin à exécuter littéralement.

### 3.2 Relancer le proxy vers l’inspecteur

Dans le terminal de contrôle :

```sh
launchctl bootout "gui/$(id -u)/sas.heraisys.asi1-planner-proxy"
```

Dans un troisième terminal, utiliser le même répertoire temporaire réel et dériver `REPO` du plist installé :

```sh
PLIST_PATH="$HOME/Library/LaunchAgents/sas.heraisys.asi1-planner-proxy.plist"
SCRIPT_PATH="$(plutil -extract ProgramArguments.1 raw -o - "$PLIST_PATH")"
export REPO="$(cd "$(dirname "$SCRIPT_PATH")/.." && pwd -P)"
ASI1_PLANNER_UPSTREAM=http://127.0.0.1:8999 \
ASI1_PLANNER_LOG_PATH=/tmp/asi1-orchestrator-e2e.XXXXXX/proxy-inspection.jsonl \
node "$REPO/proxy/asi1-planner-proxy.js"
```

Les overrides s’appliquent uniquement à ce processus. Ne pas utiliser `launchctl setenv`.

Attendre la disponibilité du proxy. Si le lancement échoue, restaurer le service avant toute reprise de chat réel.

### 3.3 Envoyer exactement trois sondes

Dans le terminal de contrôle :

```sh
for spec in '1 e2e-chat-a' '2 e2e-chat-a' '3 e2e-chat-b'; do
  set -- $spec
  probe_id="$1"
  session_key="$2"
  curl --noproxy '*' --silent --show-error --fail-with-body \
    --max-time 15 \
    http://127.0.0.1:8481/v1/chat/completions \
    -H 'Content-Type: application/json' \
    -H 'x-asi-planner: 1' \
    --data-binary "{\"model\":\"e2e-inspection\",\"messages\":[{\"role\":\"user\",\"content\":\"probe-$probe_id\"}],\"prompt_cache_key\":\"$session_key\",\"planner_mode\":false,\"reasoning_effort\":\"high\",\"temperature\":1,\"stream\":false,\"stream_options\":{\"include_usage\":true}}"
done
```

Attention : le découpage de `set -- $spec` dépend du shell. Pour une commande indépendante de ce comportement, exécuter le bloc ci-dessus avec `sh`, ou utiliser ce bloc complet à sa place :

```sh
python3 - <<'PY'
import json
import subprocess

for number, session in [(1, "e2e-chat-a"), (2, "e2e-chat-a"), (3, "e2e-chat-b")]:
    body = {
        "model": "e2e-inspection",
        "messages": [{"role": "user", "content": f"probe-{number}"}],
        "prompt_cache_key": session,
        "planner_mode": False,
        "reasoning_effort": "high",
        "temperature": 1,
        "stream": False,
        "stream_options": {"include_usage": True},
    }
    subprocess.run([
        "curl", "--noproxy", "*", "--silent", "--show-error",
        "--fail-with-body", "--max-time", "15",
        "http://127.0.0.1:8481/v1/chat/completions",
        "-H", "Content-Type: application/json",
        "-H", "x-asi-planner: 1",
        "--data-binary", json.dumps(body),
    ], check=True)
PY
```

**Exécuter une seule des deux variantes**, pour conserver exactement trois requêtes. Aucune ne transmet d’`Authorization`.

### 3.4 Vérifier les captures

```sh
python3 - "$E2E_DIR" <<'PY'
import collections
import hashlib
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
received = [json.loads(x) for x in (root / "inspection.jsonl").read_text().splitlines()]
traffic = [json.loads(x) for x in (root / "proxy-inspection.jsonl").read_text().splitlines()]

assert len(received) == 3, f"Upstream : {len(received)} requêtes au lieu de 3"
assert len(traffic) == 3, f"Proxy : {len(traffic)} entrées au lieu de 3"
assert collections.Counter(
    row["body"]["messages"][0]["content"] for row in received
) == {"probe-1": 1, "probe-2": 1, "probe-3": 1}

for row, session in zip(received, ["e2e-chat-a", "e2e-chat-a", "e2e-chat-b"]):
    body = row["body"]
    assert row["method"] == "POST"
    assert row["url"] == "/v1/chat/completions"
    assert body["planner_mode"] is True
    assert body["x-session-id"] == session
    assert body["reasoning_effort"] == "low"
    assert body["temperature"] == 0.25
    assert "prompt_cache_key" not in body
    assert "stream_options" not in body

expected = collections.Counter(
    hashlib.sha256(s.encode()).hexdigest()[:24]
    for s in ["e2e-chat-a", "e2e-chat-a", "e2e-chat-b"]
)
assert collections.Counter(r["sessionIdHash"] for r in traffic) == expected
assert len({r["correlationId"] for r in traffic}) == 3
assert all(r["status"] == 200 and r["errorCode"] is None for r in traffic)
print("PASS : rewrite direct, identités synthétiques et 3 requêtes upstream pour 3 sondes")
PY
```

Le serveur compte chaque réception upstream, même si le contenu est identique. Cette observation permet de détecter une duplication que les seuls identifiants du journal proxy ne révéleraient pas.

### 3.5 Restaurer immédiatement la production

Arrêter par `Ctrl-C` le proxy temporaire et le serveur d’inspection dans leurs terminaux respectifs.

Dans le terminal de contrôle :

```sh
launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"
launchctl print "gui/$(id -u)/sas.heraisys.asi1-planner-proxy"
curl --noproxy '*' --silent --show-error \
  --output /dev/null --write-out '%{http_code}\n' \
  http://127.0.0.1:8481/v1/chat/completions
```

Attendu : service chargé, puis `404`.

Cette sonde post-restauration ajoute elle aussi une entrée `NOT_FOUND` au journal de production (`errorCode: "NOT_FOUND"`, `sessionIdHash: null`, `status: 404`). Ces entrées 404 sont normales et ne doivent pas être interprétées comme du trafic orchestrateur : `sessionIdHash: null` les distingue des requêtes réelles ayant franchi la gate et la validation. Les fenêtres suivantes démarrent après cette sonde.

Si `bootstrap` échoue, ne pas poursuivre les échanges réels : restaurer le service selon `launchagents/README.md`. Le proxy temporaire ne doit jamais rester en place après cette phase.

## 4. Preuve du non-impact du chat normal

Attendre que toute requête orchestrateur soit terminée.

```sh
mark_window
```

Ouvrir un nouveau chat normal, sélectionner explicitement `asi-cloud`, puis envoyer :

> Réponds exactement `NORMAL-OK`.

Observation attendue : réponse complète `NORMAL-OK`, sans erreur liée au proxy.

```sh
collect_window "$E2E_DIR/normal.jsonl"
python3 - "$E2E_DIR/normal.jsonl" <<'PY'
import pathlib
import sys
assert pathlib.Path(sys.argv[1]).read_bytes() == b"", "Le chat normal a coïncidé avec du trafic proxy"
print("PASS : aucune entrée proxy pendant le chat normal")
PY
```

Renforcer la preuve d’indépendance :

```sh
launchctl bootout "gui/$(id -u)/sas.heraisys.asi1-planner-proxy"
curl --noproxy '*' --silent --show-error --connect-timeout 2 --max-time 3 \
  http://127.0.0.1:8481/v1/chat/completions
```

Attendu pour `curl` : échec de connexion. Un statut HTTP signifie qu’un processus écoute encore ; le contrôle d’arrêt n’est alors pas valide.

```sh
mark_window
```

Dans le même chat normal, envoyer :

> Réponds exactement `NORMAL-PROXY-OFF-OK`.

Attendu : réponse complète malgré le proxy arrêté.

```sh
collect_window "$E2E_DIR/normal-proxy-off.jsonl"
python3 - "$E2E_DIR/normal-proxy-off.jsonl" <<'PY'
import pathlib
import sys
assert pathlib.Path(sys.argv[1]).read_bytes() == b""
print("PASS : chat normal opérationnel sans proxy")
PY
launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"
launchctl print "gui/$(id -u)/sas.heraisys.asi1-planner-proxy"
```

La preuve réunit configuration effective directe, absence de trafic proxy et fonctionnement proxy arrêté. L’absence d’entrées seule ne prouve pas la destination réseau.

Si une preuve positive de l’URL réellement appelée est exigée, relever dans les diagnostics du client uniquement la destination et l’horodatage, sans en-têtes ni corps. La disponibilité de ces diagnostics n’est pas établie ici. À défaut, noter cette exigence renforcée `INCONCLUSIVE` plutôt que prétendre avoir observé la connexion directe.

## 5. Délégation réelle et stabilité de l’identité

Après restauration du service, ouvrir un nouveau chat A avec `asi1-orchestrator` et `asi-cloud-planner`.

```sh
mark_window
```

Tour A1 :

> Délègue à l’expert disponible une analyse courte des risques d’un retry automatique sur une requête non idempotente. Fournis-lui le contexte et demande trois risques et une recommandation. N’exécute aucune commande et ne modifie aucun fichier. Termine ta synthèse par `A1-END`.

Attendre le retour de l’outil et la réponse complète.

Tour A2, dans le même chat :

> Délègue à nouveau à l’expert une vérification de sa recommandation avec un contre-exemple concret. N’exécute aucune commande et ne modifie aucun fichier. Termine ta synthèse par `A2-END`.

Attendre la fin complète.

```sh
collect_window "$E2E_DIR/chat-a.jsonl"
mark_window
```

Ouvrir un **nouveau chat B**, avec le même preset et le même provider :

> Délègue à l’expert une explication courte de la différence entre timeout et annulation effective d’une tâche distante. N’exécute aucune commande et ne modifie aucun fichier. Termine ta synthèse par `B1-END`.

```sh
collect_window "$E2E_DIR/chat-b.jsonl"
python3 - "$E2E_DIR/chat-a.jsonl" "$E2E_DIR/chat-b.jsonl" <<'PY'
import json
import pathlib
import re
import sys

a, b = [
    [json.loads(line) for line in pathlib.Path(name).read_text().splitlines()]
    for name in sys.argv[1:]
]
assert len(a) >= 2 and len(b) >= 1, "Nombre insuffisant de requêtes observées"
for rows in (a, b):
    assert all(
        isinstance(r["sessionIdHash"], str)
        and re.fullmatch(r"[0-9a-f]{24}", r["sessionIdHash"])
        for r in rows
    )
ha = {r["sessionIdHash"] for r in a}
hb = {r["sessionIdHash"] for r in b}
assert len(ha) == len(hb) == 1, "Identité instable dans un chat"
assert ha.isdisjoint(hb), "Identité partagée entre deux chats"
print(f"PASS : identité stable ; A={len(a)} requêtes, B={len(b)} requêtes")
PY
```

Dans le transcript, constater pour chaque demande :

- appel effectif de l’outil unique avec une mission correspondant à la demande ;
- retour de l’outil, puis synthèse du modèle ;
- absence d’exécution locale par le modèle coordinateur ;
- marqueur final demandé visible dans la réponse.

Une délégation refusée, indisponible ou sans retour ne valide pas le scénario complet. Conserver le diagnostic sans imposer de repli payant.

La stabilité réelle est observée par hash. Le mapping exact vers `x-session-id` est prouvé avec les identités synthétiques au §3, sans exposer l’identité brute du chat réel.

## 6. Absence de duplicata et comptage

```sh
python3 - "$E2E_DIR/chat-a.jsonl" "$E2E_DIR/chat-b.jsonl" <<'PY'
import json
import pathlib
import re
import sys

rows = [
    json.loads(line)
    for name in sys.argv[1:]
    for line in pathlib.Path(name).read_text().splitlines()
]
ids = [r["correlationId"] for r in rows]
assert all(isinstance(x, str) and re.fullmatch(r"[0-9a-f]{32}", x) for x in ids)
assert len(ids) == len(set(ids)), "correlationId dupliqué"
print(f"{len(rows)} entrées ; {len(set(ids))} correlationId distincts")
PY
```

Renseigner :

| Fenêtre | Tours utilisateur | Requêtes HTTP client observées | Entrées proxy | Réceptions upstream observées |
|---|---:|---:|---:|---:|
| Inspection locale | 0 | 3 sondes | attendu 3 | attendu 3 |
| Chat A | 2 | à relever | à relever | non observées directement |
| Chat B | 1 | à relever | à relever | non observées directement |
| Chat normal | 1 | à relever | attendu 0 | non observées directement |

**Ne pas imposer une entrée par tour utilisateur.** Une délégation peut nécessiter un appel modèle avant l’outil et un autre après son retour. Un retry client créerait également une nouvelle requête entrante et un nouveau `correlationId`.

Les identifiants distincts prouvent l’unicité des entrées observées, pas l’absence de retry interne au proxy. La preuve directe de non-duplication upstream est le rapport 3/3/3 du §3. Le contrat du proxy exclut les retries, mais le test local ne couvre ici que le succès HTTP 200.

Pour le trafic réel, comparer les entrées au nombre de requêtes HTTP client si ce nombre est observable. Sinon, conserver le nombre de tours et d’appels d’outils comme contexte et déclarer le rapprochement exhaustif `INCONCLUSIVE`.

Alternative minimale si un contrôle de retry après erreur est exigé : nouvelle campagne isolée avec inspecteur répondant 503, une seule sonde et comptage upstream après la fin de la requête. Ne pas simuler une panne sur l’upstream réel.

## 7. Streaming et cohérence des compteurs

Pendant A1, A2 et B1, observer :

- texte apparaissant progressivement dans le client ;
- aucun fragment SSE ou JSON brut affiché comme réponse ;
- absence de texte tronqué ou de caractères corrompus ;
- marqueur final `A1-END`, `A2-END` ou `B1-END` visible ;
- réponse terminée normalement, sans annulation utilisateur.

Exécuter :

```sh
python3 - "$E2E_DIR/chat-a.jsonl" "$E2E_DIR/chat-b.jsonl" <<'PY'
import json
import pathlib
import sys

rows = [
    json.loads(line)
    for name in sys.argv[1:]
    for line in pathlib.Path(name).read_text().splitlines()
]
assert rows
for row in rows:
    assert row["status"] == 200, row["correlationId"]
    assert row["errorCode"] is None, row["correlationId"]
    assert row["bytesReceived"] > 0
    assert row["bytesSent"] == row["bytesReceived"], row["correlationId"]
    assert 0 <= row["headersMs"] <= row["totalMs"]
print("PASS : réponses réussies et compteurs de transfert cohérents")
PY
```

L’égalité des compteurs est une condition de cohérence, pas une preuve cryptographique d’intégrité ni un accusé de réception du rendu client. Elle doit être associée à l’observation de la réponse complète.

Le §3 utilise une réponse JSON courte : il prouve le rewrite, pas le streaming réel.

Les délais contractuels sont : connexion 2 s, en-têtes 10 s, idle 120 s, total 600 s. Un dépassement, une déconnexion ou une annulation invalide cette mesure nominale ; conserver le résultat puis ouvrir une nouvelle fenêtre pour un nouvel essai explicitement identifié.

## 8. Absence de secrets dans les logs

Contrôler le schéma exact du journal de production :

```sh
python3 - "$LOG_PATH" <<'PY'
import datetime
import json
import pathlib
import re
import sys

expected = {
    "timestamp", "correlationId", "sessionIdHash", "status",
    "headersMs", "totalMs", "requestBytes",
    "bytesReceived", "bytesSent", "errorCode",
}
count = 0
with pathlib.Path(sys.argv[1]).open() as stream:
    for count, line in enumerate(stream, 1):
        row = json.loads(line)
        assert set(row) == expected, f"Schéma incorrect à la ligne {count}"
        datetime.datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
        assert re.fullmatch(r"[0-9a-f]{32}", row["correlationId"])
        value = row["sessionIdHash"]
        assert value is None or re.fullmatch(r"[0-9a-f]{24}", value)
print(f"PASS : schéma exact pour {count} entrées")
PY
```

Rechercher les motifs demandés sans afficher les lignes :

```sh
grep -Eic 'Authorization|sk-|ASI_API_KEY' "$LOG_PATH"
```

Attendu : `0`. Le code de sortie `1` de `grep` signifie ici « aucune correspondance » ; un code supérieur indique une erreur de lecture ou d’exécution.

Vérifier les permissions :

```sh
python3 - "$LOG_PATH" <<'PY'
import pathlib
import stat
import sys

path = pathlib.Path(sys.argv[1])
assert stat.S_IMODE(path.stat().st_mode) == 0o600
assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
print("PASS : journal 0600 ; dossier 0700")
PY
```

Le schéma fermé et l’absence de corps ou d’en-têtes étayent l’absence de secrets par conception. La recherche de trois motifs ne détecte pas toutes les formes possibles de secrets et ne constitue pas à elle seule une preuve exhaustive.

Ne pas ajouter la vraie clé aux motifs de recherche, aux commandes, aux captures ou au document.

## 9. Tableau de résultats à remplir

Horodatage local avec fuseau :

```sh
date '+%Y-%m-%dT%H:%M:%S%z'
```

Statuts autorisés : `PASS`, `FAIL`, `INCONCLUSIVE`, `NOT_RUN`.

| Contrôle | Statut | Horodatage | Preuve / observation / réserve |
|---|---|---|---|
| Lots 1–3 appliqués à l’installation | NOT_RUN | — | |
| Proxy actif et gate 404 | NOT_RUN | — | |
| Configuration effective et profil relancé | NOT_RUN | — | |
| Rewrite direct sans clé | NOT_RUN | — | `inspection.jsonl` |
| Trois sondes = trois réceptions upstream | NOT_RUN | — | `proxy-inspection.jsonl` et `inspection.jsonl` |
| Service de production restauré | NOT_RUN | — | |
| Critère 1 : délégation réelle | NOT_RUN | — | Transcript et détail des outils |
| Critère 2 : identité stable et distincte | NOT_RUN | — | `chat-a.jsonl`, `chat-b.jsonl` |
| Équivalence du critère 2 acceptée | NOT_RUN | — | Décision opérateur / issue |
| Critère 3 : chat normal sans trafic proxy | NOT_RUN | — | `normal.jsonl` |
| Chat normal opérationnel proxy arrêté | NOT_RUN | — | `normal-proxy-off.jsonl` et observation client |
| Critère 4 : streaming complet | NOT_RUN | — | Marqueurs finaux et compteurs |
| Critère 5 : absence de retry observé | NOT_RUN | — | Portée locale directe ; rapprochement réel |
| Critère 6 : schéma, motifs et permissions | NOT_RUN | — | |
| Restauration finale du LaunchAgent | NOT_RUN | — | `launchctl print` |
| Critère global | NOT_RUN | — | |

La validation globale exige les preuves de délégation réelle, de streaming et de non-régression du chat normal. Toute réserve sur un critère doit rester visible dans la décision de clôture.

## 10. Limites, compromis et contrat d’implémentation

### Limites et compromis

- **Journal minimal :** préserver la confidentialité empêche la preuve littérale de `planner_mode` dans le JSONL. La capture synthétique apporte la preuve directe ; le hash réel rattache les requêtes au chemin de traitement validé.
- **Portée de cette conjonction :** selon le contrat fourni, `sessionIdHash` non nul signifie que gate, validation et rewrite ont été exécutés. Le §3 vérifie les valeurs produites par ce rewrite. Cela suffit comme preuve d’acceptation si le même code est utilisé en inspection et en production ; cela ne capture pas le corps de chaque requête réelle.
- **Hashes :** 24 caractères hexadécimaux correspondent à 96 bits. Une collision est théoriquement possible ; l’égalité des hashes est un indice d’identité très fort, pas une égalité mathématique des chaînes brutes. Le clamp à 64 caractères doit aussi être pris en compte si des identifiants clients partageaient le même préfixe.
- **Retry :** des `correlationId` distincts ne révèlent pas un éventuel retry upstream interne. Le comptage sur l’inspecteur apporte cette observation directe pour les sondes exécutées.
- **Comportement du modèle :** trois tours démontrent un scénario de délégation réussi, sans garantir toutes les conversations futures.
- **Journal sans rotation :** il peut croître sans limite. Surveiller son volume ; ne pas le tronquer pendant les mesures. La rotation est hors de ce lot.
- **Cycle de session macOS :** le domaine `gui/$UID` disparaît au logout. Une session ouverte est nécessaire aux contrôles LaunchAgent ; ce protocole ne démontre pas un service système persistant hors session.
- **Interruption temporaire :** l’inspection exige l’arrêt du service normal. L’override est limité au processus de test pour réduire le risque de conserver une destination incorrecte.
- **Transport réel :** les compteurs du proxy et le rendu client ne constituent pas une capture réseau exhaustive. Une exigence plus forte nécessite une instrumentation supplémentaire explicitement autorisée.
- **Non couvert :** matrice exhaustive des erreurs, annulation upstream, délais limites, résistance à la charge et disponibilité des experts. Ne pas présenter ces propriétés comme validées par cette campagne nominale.

### Contrat compact

| Élément | Contrat |
|---|---|
| Livrable | Ce document et les artefacts temporaires de validation ; aucun changement fonctionnel requis |
| Entrées | Installation des trois lots, profil relancé, outil de délégation disponible, session GUI active |
| Identité | `sessionId` client → `prompt_cache_key` clampé ≤64 → `x-session-id` dans le corps upstream |
| Admission | Header gate et chemin exacts ; JSON valide ; clé de cache conforme |
| Rewrite | `planner_mode=true`, identité transférée, effort `low`, température `0.25`, champs supprimés |
| Transport | Upstream local uniquement pendant l’inspection ; aucun retry ; streaming en production |
| Journal | Dix champs exacts ; aucun corps, en-tête ou identifiant brut ; permissions 0600/0700 |
| Preuves positives | Corps synthétique reçu ; appels réels de délégation ; réponse client complète |
| Preuves indirectes | Hashes d’identité, compteurs d’octets, absence de trafic proxy du chat normal |
| Échec | Assertion violée ou comportement incorrect → `FAIL` ; observation insuffisante → `INCONCLUSIVE` |
| Sortie obligatoire | Résultats horodatés, réserves visibles, inspecteur arrêté et LaunchAgent de production restauré |

La clôture de #75 doit mentionner explicitement l’équivalence retenue pour le critère 2 et la portée exacte de la preuve de non-retry.
