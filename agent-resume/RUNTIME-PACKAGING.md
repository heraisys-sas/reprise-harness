# Prototype du runtime Python embarqué

Incrément D1 de l’issue #6, 21 octobre 2026. Le lancement embarqué utilise exclusivement `Contents/Resources/runtime/bin/python3`. Le mode développement avec chemin source/interpréteur explicites reste disponible. L’installation autonome complète et la distribution publique ne sont pas validées.

## Construction reproductible à partir d’une archive épinglée

`python-runtime.lock.json` épingle CPython **3.13.16**, release python-build-standalone **20261020**, cible **aarch64-apple-darwin**. Seul ARM64 est accepté pour ce prototype ; Intel et universal ne sont pas validés. Le plancher du runtime indiqué par les métadonnées amont est macOS 11.0 : l’application Swift impose explicitement macOS 26.0 et un SDK de build 26+. Ce minimum n’est pas encore validé sur une machine macOS 26.

L’archive officielle `install_only` est fournie explicitement au build. Aucun téléchargement au lancement ni dans `native-install` :

```sh
curl -fL 'https://github.com/astral-sh/python-build-standalone/releases/download/20261020/cpython-3.13.16%2B20261020-aarch64-apple-darwin-install_only.tar.gz' -o /private/tmp/reprise-python.tar.gz
sh agent-resume/native-install --runtime-archive /private/tmp/reprise-python.tar.gz
```

Le build exige encore Python >= 3.11 et Swift sur le poste développeur ; le lancement du bundle construit utilise son propre Python. Sans archive, un build conserve et vérifie le runtime déjà présent. En son absence, il reste un build de développement dont le manifeste indique `external-python-required` ; le lancement embarqué refuse alors explicitement l’absence du runtime. `--open` conserve le lancement de développement historique ; aucune de ces validations ne l’a utilisé.

Le SHA256 est vérifié **avant extraction et exécution**. L’extraction refuse traversées, destinations non vides, liens sortants, écritures sous liens, doublons et fichiers spéciaux. Le runtime est testé en zone temporaire : version, architecture, préfixe relogeable et modules natifs nécessaires (`sqlite3`, SSL, compression, `ctypes`, sockets…). Le manifeste enregistre provenance, empreinte d’archive, versions observées et empreintes/liens des fichiers. Le payload des helpers reprend ces informations ; un rebuild refuse un runtime modifié ou incompatible avec le verrou.

Le remplacement du runtime est préparé avant renommage ; une exception de publication restaure l’ancienne copie. Un marqueur `runtime-installing.json` bloque le rebuild sans archive après une interruption brutale ; reconstruire avec l’archive épinglée permet de reprendre. Des répertoires `.runtime-*` peuvent subsister après interruption : ce ne sont pas des installations actives. Ce mécanisme de build n’est ni une mise à jour transactionnelle du bundle complet ni un mécanisme de restauration des données utilisateur. Construire une copie distribuable séparée avant toute future mise à jour de production.

## Exécution et dépendances

Swift ne cherche plus Homebrew, Python.framework ou `env python3` en mode embarqué. Il lance les helpers avec `-E -s -B`, retire les variables de personnalisation Python et borne les sous-processus comme auparavant. Les imports des helpers voisins restent disponibles ; `-I` est réservé à la sonde de bibliothèque standard. Un runtime absent produit un diagnostic visible. Les helpers continuent d’utiliser `sys.executable` pour leurs enfants : l’interpréteur reste dans le bundle.

L’archive n’est pas élaguée : bibliothèque standard, bibliothèques natives, ressources et notices de pip restent présentes. Aucune dépendance Python supplémentaire de production, aucun `pip install` lors de l’installation utilisateur. Les harness et leurs authentifications restent externes. Les chemins stables pour intégrations/LaunchAgents, la sélection graphique d’état, la signature/notarisation et le compte vierge relèvent encore des incréments suivants.

## Licences et sources

- [Documentation des archives python-build-standalone](https://docs.astral.sh/python-build-standalone/distributions/) : format `install_only`, métadonnées de plateforme et licences.
- [Instructions officielles d’exécution](https://github.com/astral-sh/python-build-standalone/blob/main/docs/running.rst) et [release épinglée](https://github.com/astral-sh/python-build-standalone/releases/tag/20261020).
- `PYTHON-THIRD-PARTY-NOTICES.txt` regroupe sans modification les notices désignées par `PYTHON.json` de l’archive complète ARM64 `pgo+lto`, dont le SHA256 est enregistré dans le verrou. Ces notices sont copiées dans le runtime ; les licences déjà présentes dans `install_only`, notamment pip, sont conservées. Les empreintes assurent l’intégrité par rapport au verrou ; elles ne constituent pas une signature/notarisation de Reprise.

La lecture ponctuelle de l’archive complète a utilisé `zstandard` dans un environnement jetable sous `/private/tmp` ; cet outil ne fait partie ni du dépôt ni du runtime livré. La maintenance du verrou et la veille de sécurité du runtime restent nécessaires avant une diffusion publique.

## Validation enregistrée

- Construction `native-install --runtime-archive …` réussie, sans `--open`.
- Suite complète exécutée avec le CPython réellement embarqué : **330 tests OK, 2 ignorés** (dont le test d’archive opt-in sans sa variable).
- Suite ciblée avec `REPRISE_TEST_RUNTIME_ARCHIVE` : **13 tests OK**, intégration réelle incluse. Bundle déplacé dans un chemin avec espaces ; préfixe et interpréteur dans ce bundle, `state-init` et `scheduler-status` dans un HOME temporaire, état hors bundle. Provenance altérée, absence du bit exécutable et interruption simulée refusées ; publication échouée puis reconstruction avec archive testées.
- Environ 67 Mio pour le runtime décompressé ; archive officielle d’environ 25,4 Mo. Machine de validation ARM64, macOS 27.0.1 ; cette mesure ne valide pas les autres versions macOS.
- `py_compile`, analyse syntaxique Swift et `git diff --check` réussis. Avertissements de ressources non fermées préexistants dans les anciennes fixtures ; Ruff/Gitleaks absents, aucun scan revendiqué.

Aucune application lancée, aucun service redémarré, aucune migration de données réelles, aucune inférence externe. Le test en HOME temporaire n’équivaut pas à une validation sur compte macOS vierge sans outils de développement.

## Dépendances natives Apple

`FoundationModels` est un framework Swift système, distinct du runtime Python et du SDK Python Apple FM (non utilisé). La synthèse est facultative et exige un modèle Apple Intelligence disponible ; `FoundationExplain --status` permet une sonde sans génération. `NaturalLanguage` fournit séparément les embeddings. Les modèles ne sont pas embarqués. Le manifeste `native-manifest.json` vérifie et enregistre minimum macOS 26.0, SDK et empreintes des quatre exécutables natifs. Voir le [contrat d’installation](INSTALLATION-CONTRACT.md#prérequis-apple--foundation-models).
