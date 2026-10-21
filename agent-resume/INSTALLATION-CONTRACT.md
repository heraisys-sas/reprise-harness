# Installation autonome et intégrations maintenables

Décision du 21 octobre 2026. **D1 partiellement implémenté ; installation autonome non validée**. Ce contrat complète le [service bidirectionnel](HARNESS-SUPERVISION-ARCHITECTURE.md) ; il ne change ni les autorisations d’inférence ni les politiques de reprise/quota.

## Objectif et périmètre

Installer Reprise sur un compte macOS vierge, sans clone Git, Swift ni Python fourni par l’utilisateur, puis rendre les outils Reprise accessibles dans un profil DeepSeek Harness depuis l’application. Le harness cible est déjà installé et son authentification reste native. L’installation des harness eux-mêmes est un chantier séparé.

L’écran « Intégrations » doit permettre de détecter, choisir, installer, vérifier, réparer, mettre à jour et désinstaller les intégrations. Commencer par un parcours complet Harness avant de généraliser aux autres clients. Les outils exposés correspondent aux capacités choisies et permissions nécessaires ; une installation n’autorise pas leur utilisation automatique.

## État de départ vérifié

- `native-install` compile dans le dépôt et lance, avec `--open`, en transmettant le chemin du dépôt et le Python de l’environnement.
- `install-expertise.py` installe le pont MCP et la skill dans un profil Harness, avec sauvegarde et détection de contenu identique ; les chemins du pont et de son état dépendent du clone.
- `install-quick-entry.py` prépare le LaunchAgent de Quick Apply, sans activation implicite ; son helper doit actuellement rester dans le dépôt.

Ces scripts sont des briques existantes, pas encore un installateur autonome transactionnel.

## Usage inter-harness depuis Codex et les autres clients

Exigence utilisateur du 21/10 : une intégration installée doit rendre les outils Reprise découvrables et fournir des consignes adaptées au harness pour les employer efficacement et économiquement. Codex doit pouvoir solliciter un autre harness via le même service ; la disponibilité des outils propres à Desktop ne conditionne pas ce parcours. Cette exigence ne signifie pas que tous les adaptateurs soient déjà opérationnels.

Reprise installe/configure son pont MCP et, selon les mécanismes supportés du client, une skill ou des instructions ciblées et versionnées. Il ne modifie ni le binaire Codex ni les fichiers internes du plugin Desktop. Préserver les instructions et permissions utilisateur ; activation et autorisation restent distinctes. Vérifier sur un profil neuf la découverte effective des outils et la disponibilité des consignes, puis séparer cet examen sans inférence d’un échange supervisé.

Les consignes doivent enseigner : découverte des backends/capacités réellement disponibles ; tâche bornée et critères de réussite ; entrées minimales et chemins autorisés ; identité persistante, lignée et récupération d’un résultat avant tout nouvel envoi. Réutiliser une session utile et envoyer les seuls changements, sans supposer un cache partagé entre modèles ou fournisseurs. Pas de délégation systématique lorsque le traitement local déterministe suffit, ni cascade automatique d’experts.

Le choix doit chercher le coût total de la tâche validée : capacité et qualité requises, coût connu, contexte transféré, latence et probabilité de reprise. Ces éléments restent des observations ou heuristiques tant qu’ils ne sont pas mesurés ; ne pas annoncer un routage optimal. Escalade pour difficulté concrète ou échec borné, revue indépendante quand utile, contrôle des doublons/cycles et respect des budgets utilisateur. Les modèles ne sont pas figés dans le contrat ; disponibilité et facturation sont vérifiées au moment du choix. Quota inconnu reste inconnu ; aucun repli payant ni automatisation nouvelle fondée sur des quotas non fiables.

Acceptation à ajouter au parcours D2/D3 : sur profil Codex neuf et autorisé, outils Reprise et consignes installables/réparables/retirables ; découverte sans inférence ; test supervisé d’une délégation vers un backend validé puis récupération de son résultat sans double exécution. Une admission Quick Apply seule ne valide pas ce test. Les adaptateurs non validés restent signalés comme tels.

## Paquet et données

Le paquet contient les helpers versionnés et un runtime Python maîtrisé. Le mécanisme de packaging reste à choisir par prototype, en vérifiant architectures/macOS supportés, dépendances, licences et taille. Aucun composant installé ne dépend du chemin du clone ni d’un interpréteur de développement.

Les données durables vont dans `~/Library/Application Support/Reprise/`, séparées du bundle ; les journaux dans `~/Library/Logs/Reprise/`. Les emplacements exacts des sockets, profils et services sont inventoriés et stabilisés par l’adaptateur, sans déplacer arbitrairement une ressource appartenant au harness. Choisir un chemin d’exécution stable pour les clients et LaunchAgents ; un déplacement ou remplacement du bundle ne doit pas laisser de référence morte.

L’import des états existants est explicite, sauvegardé et vérifié : conserver identités, résultats non réclamés et barrières de tentatives incertaines. Prévoir compatibilité app/helpers/schémas SQLite, sauvegarde cohérente (y compris WAL) et récupération après mise à jour interrompue. Ne pas proposer de rétrogradation d’un binaire sur un schéma incompatible ; restauration seulement avec compatibilité vérifiée et sans effacer des travaux nouveaux.

## Contrat commun des adaptateurs

| Opération | Garantie |
|---|---|
| `inspect` | Lecture seule ; version, profils, mécanismes supportés, preuves et inconnus ; aucun secret collecté |
| `plan` | Modifications exactes, préconditions, empreintes des entrées, versions et autorisations nécessaires |
| `apply` | Revalidation avant écriture, sauvegarde, étapes journalisées et récupération d’un échec partiel |
| `verify` | Résultats distincts pour configuration, chargement, découverte et échange ; versions effectivement observées |
| `remove` | Retrait des seuls éléments gérés par Reprise ; données durables conservées par défaut |

Chaque plan cible explicitement le harness, son profil et son projet autorisé. Les mutations passent par les mécanismes supportés vérifiés pour la version du client ; une capacité inconnue reste inconnue. Revalidation, verrou coopératif et remplacement atomique réduisent les conflits ; ils ne garantissent pas l’exclusion d’un client non coopératif. Tout conflit détecté suspend l’opération plutôt que d’écraser une personnalisation.

Un manifeste local versionné inventorie les fichiers/blocs gérés, leurs empreintes, sauvegardes, versions et étapes. Une seconde installation identique est sans effet. Réparation et désinstallation comparent aussi l’état courant : un contenu personnalisé depuis l’installation exige réconciliation, jamais suppression aveugle. Plusieurs profils utilisent le même contrat et conservent des périmètres explicites.

L’activation d’un service est une action identifiée du plan ; ne pas interrompre une session active ni redémarrer un harness silencieusement. Aucun credential n’est copié entre clients. Permissions macOS demandées au besoin et avec une explication ciblée. Les diagnostics exportables sont expurgés.

## Niveaux de vérification

1. Configuration écrite et syntaxiquement valide.
2. Intégration chargée par le client cible (un redémarrage nécessaire est affiché).
3. Outils et versions réellement découverts, sans inférence.
4. Échange de bout en bout borné, lancé explicitement, avec résultat durable vérifié.

Un socket accessible ne prouve pas la découverte des outils ni la réception d’un résultat. Le niveau 4 peut consommer le quota du compte : aucune inférence implicite ni repli payant. Pour Claude Desktop, Quick Apply reste un déclencheur ; seul `submit_result` vaut retour pour un job MCP Reprise. Work/Cowork reste une capacité distincte à démontrer.

## Lots et critères d’acceptation

- [ ] **D1 — paquet autonome** : runtime/helpers embarqués, chemins stables, données séparées et import sauvegardé ; lancer la copie distribuable sans clone ni outils de développement.
- [ ] **D2 — première intégration Harness** : exposer le parcours `inspect → plan → apply → verify → remove` depuis Reprise, puis réparation/mise à jour ; outils limités aux capacités sélectionnées.
- [ ] **D3 — validation sur compte macOS vierge** : harness préinstallé ; installation répétée sans effet, profils isolés, configuration personnalisée préservée, conflit détecté, échec partiel récupérable, mise à jour interrompue réconciliable, résultats/identités conservés après retrait et réinstallation. Vérifier réellement les niveaux 1–3 puis un échange supervisé de niveau 4.
- [ ] **D4 — autres clients et distribution** : ajouter les adaptateurs selon interfaces supportées et preuves ; préparer signature, notarisation, installation Gatekeeper et procédure de mise à jour/restauration testée avant diffusion publique.

Le chantier peut avancer indépendamment de l’observation des quotas Claude. Il ne remplace pas la priorité fonctionnelle de l’aller-retour Claude Desktop ; l’installateur ne doit pas présenter le parcours autonome comme déjà validé.

## Suivi et preuves

[Issue de suivi #6](https://github.com/heraisys-sas/reprise-harness/issues/6).

La roadmap porte l’ordre/priorité ; ce contrat porte les garanties ; l’issue GitHub porte l’avancement et les preuves. Les PR/commits référencent l’issue. Ne cocher un critère qu’avec une validation enregistrée, en distinguant tests simulés, compte vierge et distribution réelle. Une issue par incrément seulement lorsqu’il devient actionnable ; pas de duplication de toute cette spécification.

### Premier incrément D1 — 21/10

`native-install` embarque maintenant une liste explicite de helpers/assets dans `Contents/Resources/helpers`, avec manifeste de version et empreintes. Sans arguments de développement, l’application cherche ce payload ; Vault/Embedding sont résolus dans `Contents/MacOS`. Le CLI déplacé utilise `Application Support/Reprise/state` et le daemon Quick Apply un sous-dossier séparé. Le lancement `native-install --open` conserve explicitement le mode développement et son état historique. Aucun état utilisateur n’est copié dans le paquet.

Une garde refuse un état vierge lorsqu’un dossier historique voisin du bundle contient des bases SQLite. Cette détection est locale : un bundle déplacé loin du clone ne peut pas découvrir tout ancien état. Pour éviter un registre vierge silencieux, toute première initialisation embarquée sans état cible existant est aussi refusée ; seules une sélection explicite `AGENT_RESUME_STATE` ou une création neuve volontaire `REPRISE_ALLOW_NEW_STATE=1` permettent le prototype. **Cet état décrit le premier incrément ; l’import hors ligne explicite est ajouté ci-dessous.** L’installation de production reste non validée. Aucun registre réel déplacé ou modifié par ce lot.

Deux tests sur copies temporaires couvrent payload déplacé, CLI sans chemin source, état hors bundle, garde historique, chemins binaires et remplacement des ressources obsolètes. Compilation `sh agent-resume/native-install` sans lancement réussie ; suite complète avec accès aux sockets : **299 tests, OK, 1 ignoré** (avertissement SQLite non fermé préexistant). En sandbox restreinte : 6 échecs et 8 erreurs dus aux permissions des sockets, 3 ignorés. `git diff --check` réussi. Le runtime reste externe (`external-python-required` dans le manifeste) : ni autonomie Python, architectures/macOS distribuables, licences du futur runtime ni compte vierge ne sont validés. Aucun téléchargement ni nouvelle dépendance. Le redémarrage groupé est explicitement indisponible dans ce prototype embarqué ; installation des services/clients, export des sources et chemins d’exécution stables restent à adapter. D1 reste décoché.

### Deuxième incrément D1 — import hors ligne et initialisation explicite

Les commandes suivantes fonctionnent avant toute ouverture du registre courant, y compris depuis les helpers déplacés :

```sh
python3 /chemin/helpers/resume state-init --target "/chemin/nouvel-etat"
python3 /chemin/helpers/resume state-import --source "/chemin/ancien-etat" --target "/chemin/nouvel-etat" --source-stopped
```

`state-init` crée un manifeste `.reprise-state.json` reconnu par le lancement embarqué, sans base ni activation de service. Une cible existante est refusée, même vide. Le chemin par défaut embarqué est `~/Library/Application Support/Reprise/state` ; chaque commande exige néanmoins sa cible explicite. L’état historique du développement reste inchangé. `AGENT_RESUME_STATE` demeure une sélection explicite, et l’ancienne variable de prototype reste compatible.

`state-import` exige que l’opérateur ait arrêté tous les écrivains de la source ; la commande ne les arrête pas. Elle vérifie une copie privée par empreintes, puis utilise SQLite `backup()` et `integrity_check` sur cette copie, WAL compris. Elle vérifie de nouveau la source avant publication. Aucun schéma n’est migré, aucune identité n’est régénérée, aucun statut n’est réarmé, aucun acquittement n’est ajouté. Les versions SQLite `user_version` non nulles sont refusées : aucune compatibilité future ou rétrogradation n’est présumée. Les schémas historiques sans version sont conservés tels quels ; leur compatibilité fonctionnelle relève encore des helpers correspondants.

Une sauvegarde indépendante `CIBLE.backup-UUID` vérifiée est conservée à côté de la cible avant publication par renommage. Son manifeste contient empreintes, identifiant d’opération et chemin de sauvegarde, sans contenu des résultats. Fichiers et répertoires sont synchronisés avant publication. Un échec avant sauvegarde laisse la cible absente ; un échec ensuite conserve la sauvegarde complète. Une nouvelle tentative utilise une nouvelle cible, sans écrasement. La restauration automatique et la fusion sont exclues : ne pas effacer des travaux nouveaux en recopiant une sauvegarde. Les fichiers temporaires d’un processus tué peuvent rester sous `.reprise-import-*` ; ils ne valent pas état installé. Seuls la cible publiée et son manifeste `ready` signalent la fin de l’opération.

L’import couvre exactement le dossier sélectionné, récursivement : `state`, `quick-state` voisin et dossiers UX arbitraires nécessitent chacun une sélection explicite. Les liens symboliques et types spéciaux inconnus sont refusés ; sockets, verrous, PID et journaux SQLite auxiliaires ne sont pas publiés. Les autres fichiers réguliers sont privés et conservés, sans réécriture de leurs chemins internes. L’identité persistante Quick Apply est retrouvée depuis ses reçus historiques puis conservée dans `quick_identity` ; plusieurs identités contradictoires bloquent l’ouverture plutôt que rendre des tentatives invisibles.

La cohérence entre plusieurs bases exige l’arrêt effectif des écrivains : l’attestation et la détection par empreintes ne constituent pas un verrou contre un autre processus. Ne pas publier simultanément vers la même cible ; le remplacement atomique ne fournit pas d’exclusion contre un acteur non coopératif. Aucun état réel n’a été importé pendant le développement. Le runtime Python embarqué, l’interface graphique d’installation et la validation sur compte macOS vierge restent à livrer ; D1 reste décoché.

Validation de cet incrément : **317 tests, OK, 1 ignoré**, dont 17 tests d’import/initialisation sur copies temporaires et 3 tests du payload déplaçable. `py_compile`, `git diff --check` et `sh agent-resume/native-install` hors sandbox réussis, sans `--open`. Les avertissements de ressources non fermées dans les anciennes fixtures restent présents. Ruff/Gitleaks absents, aucun scan de ces outils revendiqué. Aucun redémarrage, aucune migration de données réelles.

### Troisième incrément D1 — runtime Python ARM64

Le prototype embarque maintenant CPython 3.13.16 (python-build-standalone 20261020) via une archive locale officielle épinglée par SHA256. `native-install --runtime-archive FILE` vérifie, extrait et teste le runtime avant de l’intégrer ; aucun téléchargement implicite. Le lancement embarqué utilise exclusivement ce runtime, sans repli système, avec environnement Python assaini. Provenance, empreintes et notices tierces sont conservées. Les rebuilds refusent un runtime modifié et un remplacement interrompu sans archive explicite. Détails, commande de build et limites : [RUNTIME-PACKAGING.md](RUNTIME-PACKAGING.md).

Le runtime est ARM64 seulement ; Intel, universal, compte macOS vierge, parcours graphique d’initialisation et chemins stables d’intégrations restent non validés. L’application n’a pas été lancée. D1 reste décoché : ce prototype n’établit ni distribution complète ni mise à jour transactionnelle/signée du bundle.

Preuves du troisième incrément : **330 tests OK, 2 ignorés** avec CPython embarqué ; **13 tests ciblés OK** avec archive réelle, dont déplacement, initialisation temporaire, altération et reprise après échec de publication. Compilation sans lancement réussie. Aucun service ni état réel modifié.
