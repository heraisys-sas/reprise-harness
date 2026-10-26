# LaunchAgent utilisateur — ASI1 planner proxy

Le service `sas.heraisys.asi1-planner-proxy` exécute le proxy Node.js
au premier plan, sous le contrôle exclusif de launchd, dans `gui/$UID`.

## Prérequis et périmètre

- macOS avec une session graphique ouverte pour l'utilisateur concerné.
- Commandes exécutées par cet utilisateur, sans `sudo`.
- Dépôt situé à un emplacement stable et accessible au LaunchAgent.
- Proxy `proxy/asi1-planner-proxy.js` intégré dans le checkout utilisé.
  Il est absent de la baseline `dev @ 9f3fc07` : son intégration est un
  préalable distinct de cette installation.
- Runtime DSH exécutable à cet emplacement :
  `$HOME/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/node/bin/node`.
- Node.js >= 18.
- Outils macOS `launchctl`, `plutil`, `curl`, ainsi que Git.

Le proxy écoute uniquement sur `127.0.0.1:8481`. Sans l'en-tête
`x-asi-planner: 1`, `/v1/chat/completions` répond 404.

Aucune clé n'est nécessaire au démarrage. La clé reste dans
l'environnement du client DSH, qui transmet son en-tête Authorization
lors de l'appel. Ne pas importer les secrets dans launchd avec
`launchctl setenv`.

Le plist ne configure pas les surcharges optionnelles
`ASI1_PLANNER_UPSTREAM` et `ASI1_PLANNER_LOG_PATH`. Les valeurs par défaut
du proxy sont attendues ; vérifier qu'aucune surcharge involontaire
n'existe dans l'environnement launchd.

## Ordre et invariants

1. Arrêter toute instance manuelle du proxy et son éventuel superviseur.
2. Vérifier les chemins, la version Node et la syntaxe du script.
3. Vérifier que le port est libre pour une première installation.
4. Créer les journaux avant de charger le service.
5. Générer le plist avec des chemins absolus, valider son XML,
   puis le remplacer par renommage dans le même répertoire.
6. Charger la définition et vérifier le service puis la réponse HTTP.

Invariants :

- Un seul label géré dans `gui/$UID`, sans lancement manuel parallèle.
- Node directement supervisé ; aucun wrapper ni processus détaché.
- Deux ProgramArguments : binaire Node et script, tous deux absolus.
- Aucun secret, aucun placeholder restant dans le plist installé.
- Aucun WatchPaths.
- Répertoire des journaux en 0700 ; fichiers stdout/stderr en 0600.
- Un échec ne déclenche pas de repli vers une autre instance du proxy.

L'unicité suppose que les autres lanceurs ont été arrêtés. launchd ne
bloque pas un lancement manuel ultérieur. Le contrôle préalable du port
ne constitue pas un verrou atomique ; un concurrent peut prendre le port
ensuite. Le bind exclusif du proxy empêche alors deux écoutes simultanées
sur la même adresse, mais le processus perdant peut entrer en redémarrage.

## Préparer les commandes

Depuis le checkout à installer, copier cette définition dans le terminal.
Elle fonctionne dans bash ou zsh et utilise uniquement Node et les outils
existants. La fonction travaille dans un sous-shell.

```sh
asi1_proxy_install() (
    set -eu
    umask 077

    mode="${1:-install}"
    case "$mode" in
        install|update) ;;
        *) echo "Usage : asi1_proxy_install install|update" >&2; exit 1 ;;
    esac

    case "$HOME" in
        /*) ;;
        *) echo "HOME doit être absolu." >&2; exit 1 ;;
    esac

    repo="$(git rev-parse --show-toplevel)"
    repo="$(cd "$repo" && pwd -P)"
    node_bin="$HOME/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/node/bin/node"
    proxy_script="$repo/proxy/asi1-planner-proxy.js"
    template="$repo/launchagents/sas.heraisys.asi1-planner-proxy.plist"
    agents_dir="$HOME/Library/LaunchAgents"
    logs_dir="$HOME/Library/Logs/asi1-planner-proxy"
    installed="$agents_dir/sas.heraisys.asi1-planner-proxy.plist"
    domain="gui/$(id -u)"
    service="$domain/sas.heraisys.asi1-planner-proxy"

    [ -x "$node_bin" ] || {
        echo "Binaire Node absent ou non exécutable : $node_bin" >&2
        exit 1
    }
    [ -f "$proxy_script" ] && [ -r "$proxy_script" ] || {
        echo "Script absent ou illisible : $proxy_script" >&2
        exit 1
    }
    [ -f "$template" ] && [ -r "$template" ] || {
        echo "Template absent ou illisible : $template" >&2
        exit 1
    }

    "$node_bin" -e '
        const major = Number(process.versions.node.split(".")[0]);
        if (major < 18) {
            console.error("Node >= 18 requis ; version : " + process.version);
            process.exit(1);
        }
    '
    "$node_bin" --check "$proxy_script"
    launchctl print "$domain" >/dev/null

    if [ "$mode" = install ]; then
        if launchctl print "$service" >/dev/null 2>&1; then
            echo "Service déjà chargé ; utiliser le mode update." >&2
            exit 1
        fi

        "$node_bin" - <<'NODE'
const net = require("node:net");
const socket = net.createConnection({host: "127.0.0.1", port: 8481});

function finish(code, message) {
    socket.destroy();
    if (message) console.error(message);
    process.exitCode = code;
}

socket.setTimeout(2000);
socket.once("connect", () => finish(
    1, "Port 8481 occupé : arrêter et identifier son gestionnaire avant bootstrap."
));
socket.once("timeout", () => finish(
    1, "État du port 8481 indéterminé : installation interrompue."
));
socket.once("error", error => {
    if (error.code === "ECONNREFUSED") finish(0);
    else finish(1, "Contrôle du port impossible : " + error.code);
});
NODE
    else
        launchctl print "$service" >/dev/null
    fi

    mkdir -p "$agents_dir" "$logs_dir"
    chmod 700 "$logs_dir"
    touch "$logs_dir/stdout.log" "$logs_dir/stderr.log"
    chmod 600 "$logs_dir/stdout.log" "$logs_dir/stderr.log"

    staged="$(mktemp "$agents_dir/.asi1-planner-proxy.XXXXXX")"
    trap 'rm -f "$staged"' EXIT

    "$node_bin" - "$template" "$HOME" "$node_bin" "$proxy_script" >"$staged" <<'NODE'
const fs = require("node:fs");
const path = require("node:path");
const [template, home, node, script] = process.argv.slice(2);
const values = {HOME: home, NODE: node, SCRIPT: script};

for (const value of Object.values(values)) {
    if (!path.isAbsolute(value) || /[\u0000-\u001f]/u.test(value)) {
        throw new Error("Chemin absolu sans caractère de contrôle requis.");
    }
}

function xml(value) {
    const escapes = {
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        "\"": "&quot;",
        "'": "&apos;"
    };
    return value.replace(/[&<>"']/g, character => escapes[character]);
}

const source = fs.readFileSync(template, "utf8");
for (const key of Object.keys(values)) {
    if (!source.includes("{{" + key + "}}")) {
        throw new Error("Placeholder manquant : " + key);
    }
}

const rendered = source.replace(/\{\{([A-Z_]+)\}\}/g, (token, key) => {
    if (!Object.hasOwn(values, key)) {
        throw new Error("Placeholder inconnu : " + token);
    }
    return xml(values[key]);
});

if (rendered.includes("{{") || rendered.includes("}}")) {
    throw new Error("Placeholder non résolu.");
}
process.stdout.write(rendered);
NODE

    plutil -lint "$staged"
    chmod 600 "$staged"
    mv -f "$staged" "$installed"

    if [ "$mode" = update ]; then
        launchctl bootout "$service"
    fi

    launchctl enable "$service"
    launchctl bootstrap "$domain" "$installed"

    if [ "$mode" = update ]; then
        launchctl kickstart -k "$service"
    fi

    launchctl print "$service"
)
```

La génération échappe les caractères XML, y compris dans les chemins
contenant `&`, et préserve les espaces. Ni launchd ni un shell lancé
par launchd ne réalisent la substitution.

## Installation

Arrêter d'abord le proxy lancé manuellement, avec Ctrl-C dans son terminal
ou par son gestionnaire actuel. Arrêter également tout lanceur automatique
concurrent. Ne pas utiliser `killall node`, qui affecterait d'autres services.

Après avoir chargé la fonction précédente, depuis le checkout :

```sh
asi1_proxy_install install
```

La fonction exécute notamment l'équivalent exact de :

```sh
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/sas.heraisys.asi1-planner-proxy.plist"
```

Le template du dépôt n'est jamais chargé directement.

## Validation opérateur

```sh
launchctl print "gui/$(id -u)/sas.heraisys.asi1-planner-proxy"
curl --noproxy '*' --connect-timeout 2 --max-time 5 \
    -sS -o /dev/null -w '%{http_code}\n' \
    http://127.0.0.1:8481/v1/chat/completions
```

Résultat attendu : service `running` avec un PID, puis HTTP `404`.

L'option `--noproxy '*'` garantit un appel direct à la boucle locale,
même si le terminal possède des variables de proxy HTTP.

Ce contrôle n'envoie aucun secret et vérifie le comportement attendu
sans en-tête de gate. Il ne démontre ni l'accès upstream ni la validité
d'une clé, et un 404 seul n'identifie pas le processus répondant.

Si `lsof` est disponible et autorisé, comparer le PID d'écoute au PID
affiché par `launchctl print` :

```sh
lsof -nP -iTCP:8481 -sTCP:LISTEN
```

Un test authentifié par le client DSH reste une validation distincte.

## Mise à jour

Conserver le checkout à son emplacement installé. Après modification du
template, du runtime ou du script, recharger si nécessaire la définition
de fonction ci-dessus, puis exécuter depuis le checkout :

```sh
asi1_proxy_install update
```

Ordre appliqué : validation, remplacement atomique du fichier,
`bootout`, `bootstrap`, puis `kickstart -k`.

`kickstart -k` seul ne recharge pas une définition plist modifiée.
Le bootstrap démarre déjà le proxy avec RunAtLoad ; le kickstart final
impose le redémarrage demandé et peut donc provoquer un démarrage
supplémentaire.

Pour un changement du script uniquement, à chemins et plist inchangés :

```sh
launchctl kickstart -k "gui/$(id -u)/sas.heraisys.asi1-planner-proxy"
```

Une mise à jour interrompt les requêtes en cours. Elle n'est pas une
transaction avec rollback : si le bootstrap échoue après bootout,
corriger la cause puis relancer l'installation. Le plist validé et les
journaux restent disponibles.

## Déconnexion et reconnexion

RunAtLoad lance le service au chargement ; KeepAlive le maintient actif
dans le domaine graphique existant.

Au logout, le domaine `gui/$UID` est détruit et le proxy s'arrête.
Au login suivant, macOS recharge le plist utilisateur et relance le proxy.
Il ne fournit donc pas de service pendant l'absence de session graphique.
Verrouiller l'écran ne constitue pas un logout.

Vérifier une fois le retour au login sur la machine cible avec les
commandes de validation. Les réglages macOS d'autorisation des éléments
en arrière-plan peuvent aussi empêcher son lancement.

## Dépannage

Lire d'abord stderr pour les erreurs de démarrage, puis stdout et le
journal applicatif JSONL :

```sh
tail -n 80 "$HOME/Library/Logs/asi1-planner-proxy/stderr.log"
tail -n 80 "$HOME/Library/Logs/asi1-planner-proxy/stdout.log"
tail -n 20 "$HOME/Library/Logs/asi1-planner-proxy/traffic.jsonl"
```

Le JSONL peut être absent avant une initialisation réussie ou ne pas
contenir le contrôle HTTP rejeté par la gate. Son contenu dépend du
proxy ; le consulter localement sans publier de données sensibles.

- `EADDRINUSE` : identifier le détenteur du port. Si c'est une ancienne
  instance manuelle, arrêter aussi son superviseur. Si c'est un autre
  service légitime, résoudre le conflit avant de charger ce proxy.
- Redémarrages répétés : ThrottleInterval vaut 30 secondes. Il limite
  la fréquence des relances rapides ; il ne corrige pas leur cause et
  n'impose aucun nombre maximal d'essais. Ne pas boucler sur kickstart.
- Échec de lecture du script : vérifier les chemins et les permissions.
  Les protections macOS de confidentialité peuvent bloquer un checkout
  placé dans Documents ou un autre emplacement protégé. Déplacer au
  besoin le checkout vers un emplacement autorisé, puis régénérer le plist.
- Service désactivé : la fonction appelle `launchctl enable` avant
  bootstrap. Vérifier également les réglages macOS des éléments
  en arrière-plan.
- Modification du plist sans effet : effectuer bootout/bootstrap.
- Journaux volumineux : aucune rotation n'est configurée dans ce lot.
  Prévoir une procédure séparée ; ne pas supposer que renommer un fichier
  fait rouvrir automatiquement les descripteurs déjà ouverts.

Pour arrêter durablement le service chargé tout en conservant son fichier :

```sh
launchctl disable "gui/$(id -u)/sas.heraisys.asi1-planner-proxy"
launchctl bootout "gui/$(id -u)/sas.heraisys.asi1-planner-proxy"
```

## Désinstallation

Si le service est chargé, le retirer avant de supprimer le plist :

```sh
(
    set -eu
    launchctl bootout "gui/$(id -u)/sas.heraisys.asi1-planner-proxy"
    rm -f "$HOME/Library/LaunchAgents/sas.heraisys.asi1-planner-proxy.plist"
)
```

Si bootout indique un service introuvable, vérifier son absence :

```sh
launchctl print "gui/$(id -u)/sas.heraisys.asi1-planner-proxy"
```

Après confirmation qu'il est absent, supprimer seulement le fichier :

```sh
rm -f "$HOME/Library/LaunchAgents/sas.heraisys.asi1-planner-proxy.plist"
```

Les journaux sont conservés. Supprimer uniquement le plist sans bootout
laisserait le service chargé jusqu'à la fermeture du domaine.

## Références et statut de validation

Références officielles : manuels Apple `launchd.plist(5)` et
`launchctl(1)`, consultables sur la machine cible :

```sh
man 5 launchd.plist
man 1 launchctl
```

Les clés utilisées sont documentées dans le manuel local consulté.
Le format de sortie de `launchctl print` n'est pas une API stable :
l'inspection proposée est humaine, sans analyse automatisée de ses champs.

Cette livraison fournit les fichiers et commandes. Aucun service n'a été
installé ni démarré ; aucune validation opérateur n'est déclarée réussie.
