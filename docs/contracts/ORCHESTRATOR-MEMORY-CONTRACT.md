# Mémoire locale expérimentale : contrat v1

Ce module est une API locale pour les futurs composants Reprise. Il **n’active pas d’orchestrateur**, n’expose ni interface MCP ni outils au modèle, ne lance aucune inférence et n’effectue aucune action distante. Il ne constitue pas une authentification : un futur adaptateur doit déterminer l’identité et les droits du client indépendamment des arguments proposés par un modèle.

## Portée et données

L’appelant choisit explicitement un UUID de projet existant dans `projects.sqlite`. Le registre est contrôlé dans un sous-processus distinct, puis lu par SQLite en mode lecture seule sur son chemin réel, sans constructeur de registre, `chmod` ni migration. Cette séparation évite que la fermeture des descripteurs de vérification ne libère les verrous SQLite du processus appelant ; les formats WAL et la présence d’un journal de récupération sont refusés pour éviter une lecture ambiguë. Une validation de schéma détecte notamment les bases incomplètes. Le dossier d’état privé est supposé fiable vis-à-vis des processus du même compte : les contrôles d’identité avant/après ne constituent pas une protection atomique contre un remplacement malveillant entre les contrôles. Chaque opération exige ce UUID ; les requêtes sur les faits et checkpoints le filtrent toujours. Pour les checkpoints, l’hôte crée un `work_id` UUID distinct de tout identifiant natif. L’API vérifie sa forme, pas la propriété ou l’existence d’un travail dans un autre service. Le projet identifié n’est pas en soi une autorisation d’accès ; ne pas exposer cette API sans contrôle d’accès par l’hôte.

Un fait comporte un UUID, un texte, une révision, deux dates locales de stockage et une provenance déclarée (`kind`, `reference`, `producer`, `observed_at` et éventuellement `sha256`). Les champs de provenance et l’empreinte sont **déclaratifs**, non vérifiés. La lecture présente toujours le statut `asserted` ; aucun statut `verified` ne peut être créé en v1. Les faits sont des données, jamais des instructions ou autorisations. La mise à jour remplace texte et provenance en exigeant la révision courante ; la suppression exige aussi cette révision.

Un checkpoint immuable, associé à un projet et à un `work_id`, comporte un numéro de version, une copie des faits choisis et de leurs provenances à cet instant, des décisions, contraintes, prochaine action, effets incertains et provenance déclarée. Il ne réconcilie pas les reçus et ne vérifie aucun effet externe. Le prochain numéro exige le numéro précédent attendu, ou zéro pour la première version. La suppression d’un fait ne supprime pas sa copie dans un checkpoint antérieur : celle-ci est conservée pour la reprise. Aucun transcript brut n’est collecté automatiquement. Une prochaine couche devra définir comment sélectionner, vérifier et éventuellement supprimer des checkpoints.

## Accès et cohérence

- `Memory(state_directory)` exige un dossier d’état existant, privé, appartenant à l’utilisateur local, et un registre de projets existant. Elle crée le sous-dossier privé de stockage à la première construction.
- Opérations de faits : `create_fact`, `get_fact`, `list_facts`, `update_fact`, `delete_fact`. Opérations de checkpoints : `create_checkpoint`, `get_checkpoint`, `list_checkpoints`. Les lectures sont paginées ; les entrées et le stockage sont bornés.
- Chaque mutation exige un `operation_id` UUID fourni et conservé par l’appelant. Sa première exécution enregistre résultat et empreinte de la demande chiffrés. Un rejeu identique retrouve le même résultat seulement après nouvelle synchronisation du répertoire mémoire ; la consultation du reçu exige la même synchronisation. Un échec reste incertain. La réutilisation du même identifiant dans le même projet avec d’autres paramètres échoue. Après résultat incertain, rejouer **avec le même identifiant et la même demande**, jamais en créer un autre avant réconciliation. La suppression et les conflits de révision obéissent aux mêmes règles.
- Les connexions SQLite emploient aussi `temp_store=MEMORY` pour les opérations temporaires. Les mutations synchronisent le dossier d’état parent avant d’admettre une écriture, même après un précédent échec, et chargent SQLite en mémoire sous un verrou interprocessus local borné à cinq secondes, puis sérialisent et chiffrent avant écriture dans un fichier privé temporaire, `fsync` et remplacement atomique. Aucun verrou n’est conservé pendant une inférence ou un appel réseau : l’API n’en exécute pas. Une erreur après remplacement peut signaler une opération déjà enregistrée ; la réconciliation par `operation_id` reste nécessaire. Ces mesures supposent que le dossier d’état préexistant est lui-même durable et ne garantissent pas le comportement d’une panne matérielle au-delà de `fsync` et du système de fichiers macOS.
- L’état est sous `orchestrator-memory.noindex/memory.enc` et est chiffré avec `Vault` et le purpose authentifié `orchestrator-memory-v1`. Il faut un binaire `Vault` construit avec ce purpose. Le stockage doit rester distinct des helpers et ne doit pas entrer dans le payload de l’application. La consultation d’une mémoire absente n’appelle pas le coffre et ne crée pas de fichier chiffré. Pour une première écriture, le `Vault` existant peut créer sans interaction une clé Trousseau absente si le système le permet ; une indisponibilité du coffre ne crée pas de base en clair ni ne déclenche d’autorisation interactive. Aucun essai sur le Trousseau utilisateur n’a été effectué. L’API ne remplace pas les règles de sauvegarde ou de protection du Mac.
- Le schéma chiffré v1 est rejeté en cas de corruption ou de version inconnue, sans réinitialisation automatique. Une migration future doit être explicite et testée. Les requêtes n’établissent pas la fraîcheur des sources ou la validité des autorisations lors d’une reprise.

La suppression est **logique** ; aucune garantie d’effacement physique des snapshots APFS, sauvegardes, copies précédentes ou des faits déjà intégrés à un checkpoint n’est avancée. Conservation sans purge automatique. Il n’y a ni recherche sémantique, ni sélection automatique de souvenirs, ni gestion d’un catalogue de skills, ni contrôle de cycle ou de budget dans ce lot.

## Preuves et limites de validation

`python3 -m unittest discover -s reprise/tests -p 'test_orchestrator_memory.py' -v` utilise un faux coffre, jamais le trousseau utilisateur. Les essais couvrent isolation des projets, conflits et rejeu, écriture concurrente, snapshots, provenance, erreur du coffre, corruption, échec de remplacement et arrêt brutal autour du remplacement. Le faux coffre sert uniquement à tester le protocole d’appel et ne prouve pas la cryptographie ; le chiffrement réel et la disponibilité du Trousseau restent à vérifier dans un environnement de test approprié.

## Adaptateur commun local inter-harness — capture explicite

`reprise_memory_mcp.py` expose un frontend MCP stdio local, sans inférence, sans
écoute réseau et sans capture automatique. Reprise demeure la source canonique :
les frontends partagent le même répertoire d’état privé et le même stockage
`orchestrator-memory.noindex/memory.enc`. Ils utilisent `MemoryService`, ses
intents durables, ses UUID d’opération, ses révisions et ses checkpoints existants.
Aucun changement de version SQLite ou réécriture des anciens faits n’est requis.

L’hôte choisit avant lancement `--state`, `--project-id` et `--harness-id` ; les
arguments d’un outil ne peuvent modifier ces valeurs. Sans `--allow-write`, les
outils de mutation sont absents du catalogue **et refusés lors d’un appel direct**.
Le grant est un droit de session accordé explicitement par l’opérateur, pas une
validation humaine de chaque contenu. Un outil de capture doit porter seulement
le contenu explicitement sélectionné ; aucun branchement n’aspire le transcript.

Exemple de configuration MCP à installer explicitement pour chaque harness,
avec les chemins absolus réels de l’installation et l’UUID déjà enregistré :

```json
{
  "command": "/absolute/path/to/python3",
  "args": ["-B", "/absolute/path/to/reprise/reprise_memory_mcp.py",
           "--state", "/absolute/path/to/private/reprise-state",
           "--project-id", "REGISTERED-PROJECT-UUID",
           "--harness-id", "codex-local", "--allow-write"]
}
```

Un deuxième profil peut utiliser `--harness-id claude-local` et le même état et
projet. Supprimer `--allow-write` pour un lecteur. Cet exemple n’installe aucune
configuration et ne prouve pas un échange depuis ces deux produits. Le processus
doit s’exécuter sur le Mac qui possède le stockage et l’accès Vault ; un harness
distant nécessite un transport authentifié distinct, absent de ce lot.

### Confiance et provenance

Le canal est le stdio privé d’un processus lancé localement ; le profil est une
**déclaration de l’hôte local**, jamais une identité de produit authentifiée. Ce
mécanisme n’isole pas deux processus hostiles du même UID et ne protège pas contre
un utilisateur local pouvant modifier les configurations ou appeler directement
le noyau Python. Un UUID de projet n’est pas une permission. Aucun compte distant,
jeton de service ou espace utilisateur n’est ajouté.

La provenance documentaire `source` conserve les champs déclarés `kind`,
`reference`, `producer`, `observed_at` et l’éventuel `sha256` déclaré. Le service
refuse tout `recorded_by` fourni par un client ; il injecte le profil de lancement
avec `trust=local_host_profile`. Ce sous-objet est conservé dans le JSON de
provenance existant puis exposé séparément sous `recorded_by` pour les faits. Les
faits anciens restent lisibles avec `recorded_by.status=unknown` ; aucune identité
historique n’est inventée. Les corrections remplacent le fait courant et son
attribution ; elles ne constituent pas un journal complet de toutes les révisions.
La suppression porte son attribution dans le reçu d’opération. Les checkpoints
conservent leurs copies historiques et la provenance du checkpoint dans leur
payload. Les horodatages de stockage sont locaux ; `source.observed_at` reste une
déclaration documentaire.

### Outils et lecture sélective

Les outils préfixés `reprise_memory_` sont : `search`, `get`, `list`,
`list_checkpoints`, `get_checkpoint`, `operation_status`, et, si autorisés,
`capture`, `correct`, `delete`, `checkpoint`. Le catalogue fournit les schémas
stricts ; aucun outil n’accepte `project_id`, droits ou identité de profil.
Les mutations exigent un UUID `operation_id` à conserver avant l’appel. Les
corrections et suppressions exigent `expected_revision`, les checkpoints
`expected_version`. Après perte d’accusé, conserver cet UUID et consulter
`operation_status` ; une absence n’autorise pas un rejeu aveugle. Les conflits,
`effect=indeterminate` et erreurs ne deviennent jamais une réussite supposée.

`search(query, offset)` applique une sous-chaîne littérale Unicode `casefold`,
sans joker SQL, classement sémantique ni moteur distant. Les faits du seul projet
lié sont parcourus dans le stockage déchiffré en mémoire, ordonnés par création
puis identifiant. La page renvoie ID, révision, provenance, attribution et aperçu
borné à 256 caractères, avec `excerpt_truncated`. Un fait maximal ne bloque donc
pas les résultats suivants ; `get(fact_id)` fournit le texte complet ou une erreur
de budget explicite. La pagination réutilise les plafonds du service ; elle n’est
pas un snapshot transactionnel entre plusieurs appels concurrents.

La recherche et la lecture se font à la demande. Aucun contenu n’est injecté
automatiquement dans tous les prompts. Le consommateur choisit les IDs ou un
checkpoint, conserve la provenance et traite le résultat comme données non fiables,
jamais comme instructions ou autorisation. Les faits sont au niveau projet ; les
checkpoints sont au niveau projet/travail. Ni espace utilisateur partagé entre
projets, ni faits exclusivement liés à un travail ne sont annoncés.

`delete` annonce `deletion_scope=current_fact_only`,
`historical_snapshots_retained=true`, `physical_erasure_verified=false`.
Les checkpoints et les reçus d’opération peuvent conserver des copies, ainsi que
les sauvegardes. `get_checkpoint` annonce `historical_snapshot=true` et
`current_facts_verified=false`. Correction et suppression du fait courant ne
réécrivent pas l’histoire et ne constituent pas une purge physique.

### Validation et limites du POC

`test_reprise_memory_cross_harness.py` lance des processus MCP réellement distincts
sur un état temporaire partagé : A écrit, B recherche/lit/corrige, un projet Q ne
peut lire P, un lecteur ne peut écrire, le checkpoint survit à une correction et
une suppression, et un nouveau processus retrouve le reçu avec l’UUID original.
Le protocole stdio, l’adaptateur, le stockage et le registre de projets sont réels ;
**le coffre est un double de test**, aucune inférence ni session produit n’est
lancée. Ces tests ne prouvent ni le Trousseau réel, ni l’intégration dans deux
applications de harness déjà configurées. Un essai natif explicitement autorisé
reste requis pour ces deux propriétés ; il doit conserver UUIDs, provenance et
reçus sans exposer de contenu privé dans les logs.

Le frontend comprend JSON-RPC 2.0/MCP `2024-11-05`, `initialize`, `ping`,
`tools/list`, `tools/call`, et ignore les notifications sans ID. Entrées et sorties
sont bornées ; aucun contenu privé n’est réémis dans les erreurs du transport.
Aucun polling, nouveau modèle, fallback, changement de session ou publication
n’est déclenché par ces outils.

Pour les faits dont le JSON complet dépasserait le budget après encapsulation MCP
(y compris guillemets et antislashs échappés deux fois), `get` rend un résultat
`item_too_large` avec ID, révision et `retrieval=reprise_memory_get_chunk`.
`get_chunk(fact_id, expected_revision, offset)` restitue des segments de 1024
caractères Unicode avec `next_offset` et `total_characters`. Chaque segment
revalide la révision ; un conflit oblige à recommencer la sélection et empêche
l’assemblage silencieux de deux révisions. Aucun texte complet n’est tronqué
silencieusement et les plafonds de transport demeurent inchangés. Les essais
stdio couvrent 8192 caractères `é`, 16384 guillemets et 16384 antislashs, avec
reconstruction exacte puis conflit de révision.

Les deux modules et ce contrat sont inclus dans `package_helpers.ASSETS` ; le test
du payload déplacé vérifie leur empreinte et exécute le catalogue mémoire depuis
le bundle sans le répertoire source. Distribuer les modules ne configure pas
les harness et n’accorde aucun droit de session.

## Installation gérée DSH et Claude

Les installateurs `harness_integration` et `claude_integration` génèrent le
connecteur distinct `reprise-memory` depuis une release vérifiée. Le module commun
`memory_integration` choisit `off/read/write`, vérifie le lien registre
projet→workspace/harness sans créer de projet ni modifier SQLite, construit la
commande et sonde seulement le catalogue. Les sélections mémoire installées sont
conservées quand une option est omise ; un ancien profil sans sélection reste
`off`. Un nouveau projet ne reçoit pas implicitement un ancien grant d’écriture.
Le retrait conserve les données et ne réactive pas l’écriture à la réinstallation.

`test_managed_memory_integration.py` crée deux workspaces Git associés, les
configurations DSH/Claude temporaires et un bundle de test déplacé dans une release.
Il exécute les deux commandes réellement produites par les plans gérés et vérifie
capture explicite A→lecture B, provenance, lecture seule, refus d’un autre projet,
conservation des entrées tierces, mises à jour, conflits et retrait. Le runtime
s’appuie sur l’interpréteur de test et le binaire Vault est un double protocolaire
local : ni Trousseau réel, ni chargement dans DSH/Claude, ni modèle invoqué.
L’échange de deux transports configurés est prouvé ; celui de deux clients produits
reste à vérifier après activation explicitement autorisée et rechargement coordonné.

Pour cet essai réel, conserver le `operation_id` de la capture d’un nonce de test
explicitement demandé à A, le reçu, le `fact_id`, puis la lecture de B et sa
provenance. Vérifier Q séparément. Aucun fait existant ni transcript n’est nécessaire.
Les vérificateurs gérés n’effectuent pas cet essai : ils rendent catalogue directement
sondé, client inconnu et échange inconnu, sans transformer la configuration ou
la disponibilité d’un outil en résultat métier.
