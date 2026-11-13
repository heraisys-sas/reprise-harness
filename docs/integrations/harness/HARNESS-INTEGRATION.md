# Intégration Harness gérée depuis Reprise

Premier incrément vertical D2, issue #6, 21/10/2026. Profil synthétique et pont directement sondé ; chargement par le client, échange depuis Harness, interface visuelle et compte macOS vierge **non validés**.

## Parcours

Après préparation des états, l’onglet **Intégrations** permet de choisir explicitement le dossier Harness, son profil existant et le projet autorisé. Aucune inspection du profil réel n’est déclenchée à l’ouverture de l’onglet. Inspecter, prévisualiser l’installation/réparation ou le retrait, puis autoriser les modifications du plan affiché. L’option de délégation est désactivée par défaut. Installer sa configuration ne vaut ni autorisation d’inférence ni activation d’un client.

Le profil doit déjà posséder `profiles/PROFIL/cordis.patch.yml` ; Reprise n’installe pas Harness et ne crée pas son profil. Le projet doit être un dossier existant explicitement choisi. Aucun credential, réglage Codex ou service n’est modifié.

Les commandes `reprise harness-integration-inspect`, `harness-integration-plan`, `harness-integration-apply` et `harness-integration-verify` réutilisent le même adaptateur que l’interface. Les trois premières préparent une configuration ; `harness-integration-verify` sonde seulement le pont lui-même avec `initialize` et `tools/list`. Aucune commande ne démarre Harness ni un daemon. `harness-integration-apply` reçoit le plan JSON sur stdin ; le plan ne réserve pas les destinations et est revalidé.

Depuis #160, les anciens noms nus `integration-inspect`, `integration-plan`, `integration-reconcile-plan`, `integration-apply` et `integration-verify` restent acceptés comme alias dépréciés (avertissement sur stderr, comportement identique) ; `harness-integration-reconcile-plan` n’a jamais eu de nom nu et n’a donc pas d’alias.

## Éléments gérés et portées

- Un bloc délimité dans le patch du profil insère les plugins `@deepseek-ai/dsh-mcp-client` et `@deepseek-ai/dsh-skill-filesystem`.
- Le provider de skills est propre au profil : `includeDefaultRoots:false`, `customSkillDirs:[PROFIL/.reprise/skills]`, nom de provider distinct. Le provider déjà configuré et les skills globales ne sont pas modifiés.
- Le skill et ses quatre documents de référence vivent sous `PROFIL/.reprise/skills/reprise-delegation`. Leur contenu est versionné et suivi par empreinte.
- Une copie versionnée des helpers, du runtime et des binaires natifs est publiée sous `Application Support/Reprise/integration-releases/EMPREINTE` (ou au voisinage de l’état explicitement sélectionné). Les chemins MCP pointent vers cette copie, sans référence au code du clone ni au bundle mobile. Le chemin du projet reste intentionnellement explicite.
- Le pont reçoit le chemin d’état Reprise **déjà préparé**. Il conserve ainsi le registre existant, ses identités, résultats non réclamés et barrières incertaines. Aucun état par profil neuf n’est substitué au registre courant. Les scopes par projet restent ceux du pont existant.

Le mode développement reste disponible. Ce nouvel adaptateur exige néanmoins un payload/runtime construits et un état indépendant du clone ; il refuse de déployer une référence au clone comme état durable. L’ancien `install-expertise.py` reste un installateur de développement compatible, avec avertissement. Une ancienne installation ou un bloc Reprise non géré n’est pas adopté ni écrasé automatiquement : réconciliation opérateur nécessaire.

## Capacités et consignes

Le mode initial expose uniquement `reprise_backends`, `reprise_requests` et `reprise_quotas`. L’option explicite ajoute `reprise_delegate` et `reprise_followup`. L’allowlist est vérifiée dans `tools/list` **et** avant `tools/call`, pas seulement dans l’affichage. Les alias anciens et les outils d’historique/santé ne sont pas exposés par ce parcours. Les interfaces des anciens ponts sans allowlist restent compatibles.

Les consignes enseignent découverte effective, projet vérifié, tâche bornée, contexte minimal, récupération d’un reçu avant renvoi, permissions et budgets explicites. Tous les modèles, économiques compris, doivent demander un fait bloquant avant répétition sans progrès ; l’escalade de raisonnement est une action distincte. Deux échecs sur un volet demandent changement d’approche ou d’auteur. Pas de cascade ni de cache partagé promis. Le protocole automatique de clarification durable reste **non implémenté** ; seuls les follow-ups effectivement disponibles et un repli manuel sont décrits. Aucun plafond quotidien ni changement de réserve/routage n’est introduit.

## Propriété, sauvegardes et pannes

`PROFIL/.reprise/managed.json` inventorie le bloc, les fichiers, les empreintes, le projet et le release. `operation.json` est un journal privé de reprise ; un verrou coopératif borne une opération par profil. Les configurations et fichiers précédents sont sauvegardés sous `.reprise/backups/UUID` avant la première modification.

Les plans publics ne contiennent que chemins, action et empreintes, pas le contenu de la configuration personnalisée. Les sauvegardes et le journal privé peuvent contenir ce contenu pour sa récupération ; ils restent locaux, privés, et ne doivent pas être exportés sans expurgation.

Une installation identique est sans effet. Un fichier géré manquant peut être réparé ; un bloc ou fichier personnalisé bloque la modification, y compris le retrait. Un fichier préexistant sans propriété enregistrée n’est jamais écrasé, même au même emplacement prévu. Les modifications hors du bloc sont conservées. Les formes reconnues sont une liste YAML de patches et `[]` éventuellement commenté ; une forme inconnue est refusée sans réécriture. Le profil complet n’est pas reparsé en Python.

Après échec partiel, l’inspection retrouve le plan. La reprise accepte uniquement des fichiers correspondant à leur empreinte avant ou après ; elle ne réécrit pas une étape déjà publiée. Le texte du journal est vérifié contre le plan avant usage. Tout conflit suspend l’opération sans restauration automatique qui effacerait des travaux nouveaux. Liens symboliques gérés et traversées sont refusés ; les modifications d’un autre acteur non coopératif restent une limite d’exclusion.

Le retrait enlève uniquement le bloc enregistré et les fichiers qui correspondent encore aux empreintes gérées. Il conserve fichiers ajoutés par l’utilisateur, sauvegardes, manifeste de retrait, releases et états durables. Il ne révoque pas un pont déjà chargé dans un client : cette session nécessite une intervention explicite de l’opérateur. Aucun arrêt/redémarrage automatique. Plusieurs profils restent isolés ; ils peuvent partager le même registre projet sans en réinitialiser les données.

## Bornes des preuves

1. **Configuration écrite** : empreintes du bloc et des consignes contrôlées. Syntaxe du bloc produit analysée avec le `js-yaml` livré par Harness sur fixture ; syntaxe complète du profil et surcharges non validées par cette API (`full_profile_syntax:not_checked`).
2. **Intégration chargée dans Harness** : inconnue ; aucun client lancé pour ce lot.
3. **Outils découverts par le client** : inconnus. La sonde directe du pont annonce séparément ses outils et sa version, sans preuve de découverte par Harness.
4. **Échange confirmé depuis le client** : inconnu ; aucun appel d’inférence.

La sonde utilise un registre temporaire, jamais le registre réel. Elle refuse une copie installée altérée ou hors du dossier de releases sélectionné. Les sources installées Harness **0.2.1-rc.2** ont été examinées : app-boot parse le patch avec `js-yaml`/`JSON_SCHEMA` ; le plugin MCP découvre puis enregistre `mcp__SERVER__TOOL` ; skill-filesystem accepte un provider et des répertoires personnalisés. Le patch global et les overlays passent après le patch du profil et peuvent le neutraliser ; services `skills`/outil skill et permissions fichiers restent nécessaires. D’autres versions ou une configuration active particulière ne sont pas présumées compatibles.

La validation sur profil synthétique, même avec runtime réel et bundle déplacé/supprimé, ne vaut pas compte macOS vierge ni intégration client de bout en bout. L’extension Codex client reste un prochain adaptateur à vérifier, sans modification de sa configuration dans ce lot.

## Validation enregistrée

Suite complète **379 tests OK, 1 ignoré**, avec copie temporaire du CPython embarqué ; **22 tests ciblés Harness** avec archive officielle réelle rejoués après les derniers ajustements. Les tests de profils utilisent uniquement des dossiers synthétiques, aucun `~/.dsh` réel. Le pont reste joignable après déplacement/suppression du bundle source ; seules initialisation et liste d’outils sont envoyées. Registre fixture (identité, résultat non réclamé, tentative incertaine) inchangé après installation/vérification/retrait. Compilation sans lancement, parse/typecheck Swift, py_compile et diff-check réussis. L’interface n’a pas été lancée et le chargement dans Harness reste inconnu.

## Continuation après limite de sortie (issue #35)

L’option d’intégration **Continuer après une limite de tokens de sortie** installe
le plugin natif `harness-output-resume.cjs` dans le profil sélectionné. CLI :
`harness-integration-plan --home … --profile desktop --project … --action install --output-resume`
(en conservant les autres options voulues), puis application du plan vérifié.
Si la session utilise un clone différent du projet du pont, renseigner son chemin
dans « Projet de continuation » (`--output-resume-project`) : les deux périmètres
restent explicites et indépendants.
Son activation autorise explicitement les continuations, contrairement à la simple
exposition d’outils MCP. Le composant fait partie du bundle Reprise ; aucun
paquet distant ni credential supplémentaire n’est nécessaire.

Le plugin observe les événements natifs du propriétaire de la session. Après
`turn/end` avec `reason.kind=max-tokens`, il attend l’état libre, revérifie le
dernier événement, l’absence de message concurrent et l’identité du projet, puis
envoie « Continue » dans le même agent. Il conserve le profil, le modèle et les
permissions ; il n’utilise ni CLI concurrent, ni clic dans l’interface, ni repli
vers une API payante. Une limite de sortie n’est pas un quota de compte : aucun
reset fournisseur n’est attendu pour ce cas.

L’automatisation globale de Reprise en pause bloque la continuation. Deux reprises
consécutives automatiques maximum protègent contre une succession sans fin ; un
message opérateur réarme une nouvelle séquence. Les reçus privés sont conservés
dans `state/harness-output-resume/`. Une réservation est durable avant envoi :
un résultat incertain ne provoque jamais un second envoi du même événement.
L’option est désactivée par défaut, limitée au projet choisi et aux événements
observés après chargement. Une ancienne interruption déjà suivie d’un autre tour
ne doit pas être rejouée au prochain chargement.

Le client Harness doit recharger son profil pour charger une nouvelle version du
plugin. Le plan, la release installée et une sonde MCP directe ne prouvent pas ce
chargement. Coordonner ce rechargement après le travail actif et vérifier les
reçus de continuation. Retirer l’option du plan retire le plugin de la configuration
sans effacer les reçus. Les journaux Zstandard peuvent contenir plusieurs trames :
le lecteur Reprise les parcourt toutes et refuse une trame corrompue ou tronquée.

## Effort bas du mode `asi1-orchestrator` (25 octobre 2026)

L’option **Effort bas pour le mode asi1-orchestrator** ajoute une capacité
optionnelle au même parcours. CLI :
`harness-integration-plan --home … --profile desktop --project … --action install --asi1-effort`
puis application du plan vérifié. Elle exige une route `asi-cloud` avec le modèle
`asi1` déjà déclarée et un preset `asi1-orchestrator` existant ; Reprise ne crée
ni route, ni modèle, ni preset.

Installation :

- le plugin `asi1-orchestrator-effort.cjs`, empaqueté dans la release, est copié
  sous `PROFIL/.reprise/plugins/` ;
- une entrée marquée est insérée dans la liste `plugins` du preset
  `asi1-orchestrator` : elle ne s’applique qu’à ce mode, pas aux autres sessions ;
- si `llm-pi-ai` ne les déclare pas déjà, un bloc marqué
  `# BEGIN/END REPRISE ASI1 EFFORT` ajoute `reasoningEfforts` (low/medium/high)
  et `supportsReasoningEffort` / `supportsDeveloperRole` sur `asi1` pour la
  route directe `asi-cloud` ;
- une déclaration ou une entrée de preset déjà présente hors marqueurs n’est
  ni adoptée ni réécrite. Elle reste au propriétaire du profil et n’est pas retirée.

Le retrait enlève le plugin, l’entrée de preset et uniquement les blocs marqués
dont l’empreinte correspond au manifeste. Une modification d’un bloc marqué
bloque l’installation comme le retrait. Le modèle par défaut, les autres presets
et les workers ne sont pas modifiés. Le mode doit toujours être ouvert avec
`asi-cloud/asi1` ; le plugin garantit alors `reasoning_effort=low`. Le provider
proxy planner n'est pas inclus dans la configuration active : CUDOS a confirmé
que son planner n'est pas implémenté.

Comme pour la continuation, le plan et les empreintes prouvent la configuration
écrite, pas son chargement par Harness. Le client doit recharger le profil pour
charger une nouvelle version du plugin. `harness-integration-verify` contrôle le bloc,
le fichier du plugin et les blocs marqués, puis rapporte
`asi1_effort.configured` avec `client_loaded: unknown`. Aucun appel d’inférence
n’est effectué.

## Mémoire de projet partagée, sélection explicite

Le parcours géré accepte `--memory-access off|read|write`, `--memory-project-id UUID`
et, facultativement, `--memory-scope-id UUID` dans `harness-integration-plan`. Sans scope ID,
le mode projet historique reste inchangé. Avec un scope ID, le plan lie le MCP à un
scope v2 préexistant (user, project ou work) au démarrage. Il ajoute une entrée distincte
`reprise-memory` / `serverName: reprise_memory`, sans élargir les outils du pont
expert. `read` expose recherche/lecture ; `write` ajoute capture explicite,
correction, suppression du fait courant et checkpoint. Aucun transcript n’est
capturé automatiquement. Une installation ancienne sans sélection mémoire reste
`off`. Une option mémoire omise conserve la sélection installée ; changer d’UUID
depuis un grant `write` exige de sélectionner explicitement `write` à nouveau.

Le projet doit déjà exister dans le registre canonique, et son workspace
`harness` doit correspondre au `--project` choisi. Le plan consulte le registre
en lecture seule dans un processus séparé : aucune création, migration ou
association implicite. Un registre WAL, un journal actif, un lien ou une
association incohérente est refusé, sans sidecar SQLite créé par la sonde.
Le scope est aussi vérifié par observation administrative en lecture seule : le
stockage doit déjà être v2 et le scope doit exister. Les scopes projet/travail
doivent porter ce même UUID projet ; un scope user est une sélection explicite
attachée au projet hôte, pas une identité authentifiée. Un changement de scope
avec un grant `write` conservé exige `--memory-access write` explicite. Le plan
ne crée ni ne migre de scope.

Exemple de première activation en lecture, après création/association explicite
du projet par le parcours de registre existant :

```sh
reprise harness-integration-plan --home DSH_HOME --profile desktop --project WORKSPACE \
  --action install --memory-access read --memory-project-id REGISTERED_UUID
```

Pour sélectionner un scope user ou work déjà enregistré, ajouter
`--memory-scope-id SCOPE_UUID`. Pour un scope projet v2, le même flag sélectionne
explicitement ses métadonnées de provenance ; sans lui, la compatibilité projet
v1 est préservée. Choisir l’UUID depuis `reprise memory-scope-list --state STATE`
et contrôler son champ `kind` avant de préparer le plan ; cette commande admin
liste les métadonnées sans exposer le texte des faits.

Appliquer le JSON obtenu avec `harness-integration-apply` par le parcours existant. Pour
accorder l’écriture au processus MCP, remplacer explicitement `read` par `write`
dans un nouveau plan relu. Pour la désactiver, préparer un plan
`--memory-access read` ou `off`. Le grant est transmis au lancement du processus
MCP par `--allow-write` ; ce n’est ni une autorisation d’inférence, ni une
validation humaine de chaque contenu, ni une preuve de rechargement du client.
Une configuration persistante réapplique ce choix aux processus qu’elle lance ;
un processus déjà chargé conserve ses anciens arguments jusqu’à son rechargement.

Les options CLI de délégation, rôle, continuation et permissions déjà gérées sont
conservées lorsqu’elles sont omises, afin qu’un plan mémoire seul ne les réinitialise
pas. L’API Python garde ses arguments explicites de contrôle ; les valeurs mémoire
se retrouvent dans `plan.memory` et le manifeste. L’interface pourra utiliser ces
mêmes champs et `inspect.memory`, sans autre chemin d’écriture de configuration.

`harness-integration-verify` contrôle la commande mémoire et son association, puis lance
`initialize`/`tools/list` directement depuis la release vérifiée, sans lire ni écrire
un fait. `memory.evidence=direct_probe_tools_schema` ne prouve ni chargement DSH,
ni découverte par le client, ni échange réel : ces états restent `unknown`.
La capture ne doit être invoquée que pour une information explicitement sélectionnée.
Le retrait préserve le stockage, les checkpoints, les reçus et les sauvegardes.

La conservation des options ne transfère pas de permissions vers un autre projet :
un changement de `--project` avec délégation, écriture workspace, continuation,
rôle coordinator ou bash-sandbox désactivé exige des choix explicites pour le
nouveau périmètre. Les inverses CLI, mutuellement exclusifs avec leurs positifs,
permettent la révocation sans retrait complet : `--no-delegate`,
`--no-codex-workspace-write`, `--no-output-resume`, `--no-asi1-effort` et
`--enable-bash-sandbox`. Par exemple, révoquer délégation et écriture ensemble
respecte la contrainte préexistante selon laquelle un grant workspace nécessite
la délégation. Une modification de configuration ne révoque pas les processus
MCP déjà chargés ; leur rechargement reste une opération distincte.


## Réconciliation explicite d’un bloc divergent, sans sauvegarde

`harness-integration-reconcile-plan` est un parcours distinct de l’installation ordinaire.
Il exige l’empreinte du bloc réellement observé, le projet inchangé du pont, un
projet de continuation existant explicitement choisi et un choix explicite de
`--codex-workspace-write` ou `--no-codex-workspace-write`. La présence d’un droit
write dans un bloc divergent ne constitue jamais à elle seule une autorisation.
Le plan enregistre ces choix et les empreintes de configuration, manifeste,
journal ordinaire et journal de réconciliation. `harness-integration-apply` applique le
plan retourné ; aucune commande de chargement du client n’est exécutée.

Ce parcours exige une intégration gérée avec continuation déjà activée et des
fichiers skills/plugins intacts. Il peut remplacer les chemins connus du pont et
du plugin de continuation depuis une release vérifiée, le projet de continuation
et le droit write choisi ; toute autre différence sémantique bloque l’adoption.
Les outils, autres permissions, réglages de plugins, mémoire et projet du pont
ne sont pas adoptés depuis le bloc divergent. Les gardes de propriété de
`harness-integration-plan`, du retrait et de `harness-integration-verify` ne sont pas affaiblies.

Le remplacement conserve les octets avant et après le bloc à leur position
initiale, y compris commentaires et fins de ligne. Seul le bloc est régénéré sous
forme canonique. Le manifeste conserve ses autres métadonnées. Aucune copie de
sauvegarde n’est produite par la réconciliation ; le journal ordinaire et les
sauvegardes préexistantes restent intacts.

`.reprise/reconciliation.json` est un journal opérationnel privé : identité du
plan, empreintes, bloc cible et seuls champs à mettre à jour dans le manifeste.
Il ne contient ni copie de la configuration, ni ancien bloc, ni copie complète
du manifeste. La reprise reconstruit le manifeste depuis sa version survivante
et exige son empreinte avant ou après, ainsi que les octets hors bloc inchangés.
Une interruption peut donc rester bloquée : sans état courant reconnu, il n’y a
ni réémission de plan, ni restauration, ni rollback promis. Une opération terminée
n’est pas rejouée si ses fichiers ont ensuite divergé.

Le verrou du profil est coopératif et non bloquant. Une réconciliation partielle
bloque les plans et applications ordinaires. Les empreintes sont revérifiées
après préparation de la release, puis avant les publications ; la préparation
peut laisser une release vérifiée non référencée si un conflit survient. Aucun
CAS atomique multi-fichiers ni exclusion d’un éditeur externe non coopératif n’est
promis : arrêter le client et les autres auteurs reste une précondition opératoire.
Les écritures atomiques et synchronisations héritées n’ont pas de borne temporelle
stricte face à un stockage bloqué. Le dépôt permet de reconstruire les fichiers
gérés, pas de récupérer des réglages personnels perdus hors du bloc.


### Préconditions de concurrence et durabilité de la réconciliation

L’autorisation opérateur existante reste valable ; aucune confirmation humaine
supplémentaire n’est déduite d’un défaut technique. Le plan porte
`concurrency: operator-exclusive-access-required` : le client DSH, ses agents,
les éditeurs/synchroniseurs et tout autre auteur du profil doivent être arrêtés
pendant la prévisualisation et l’application. Le programme vérifie le verrou
coopératif du profil, l’absence d’opération ordinaire partielle, les empreintes et
les métadonnées avant publication. Ces contrôles ne peuvent démontrer l’absence
de tout écrivain non coopératif : une liste de processus à un instant ne le
prouverait pas non plus. Il n’y a pas de CAS atomique fichier fourni par ce parcours.
Si cette exclusivité opérationnelle ne peut être établie, ne pas appliquer au
profil actif ; rester sur une copie isolée ou conserver l’état bloqué.

La release est synchronisée récursivement (fichiers réguliers puis répertoires du
bas vers le haut) avant sa publication ; une release déjà présente est revérifiée
et resynchronisée. Une reprise resynchronise également configuration, manifeste,
journal et leurs répertoires, même si les renommages avaient réussi avant un
échec de synchronisation. Un journal déjà `completed` n’est reconnu comme succès
idempotent qu’après ces contrôles et synchronisations. `fsync` fournit la garantie
annoncée par le système de fichiers et le stockage, pas une preuve physique contre
toute panne matérielle.

Le mode POSIX, uid et gid des fichiers remplacés sont conservés et liés au plan.
Liens multiples, propriétaire différent, flags spéciaux et ACL étendues sont
refusés. Sur macOS, l’attribut système `com.apple.provenance`, automatiquement
présent sur des fichiers neufs, est transféré par copie des seules métadonnées et
son empreinte est revérifiée ; aucun contenu original n’est ainsi copié. Les autres
attributs étendus sont refusés plutôt que supprimés silencieusement. Une plateforme
ou un état de métadonnées non vérifiable reste bloqué. Ces refus ne suppriment ni
ne normalisent les attributs de l’original.
