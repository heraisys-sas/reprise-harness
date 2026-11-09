# Installer et mettre à jour une installation locale

Premier incrément CLI, issue #77. Il compose les adaptateurs gérés existants ;
ce n’est ni un téléchargement automatique ni l’installation des harness eux-mêmes.
Le bundle source est choisi explicitement, construit par `release_build.py` et
vérifié intégralement avant toute publication. Les preuves locales ne remplacent
pas signature, notarisation ou confiance dans la provenance du paquet.

```sh
python3 -B reprise/managed_update.py inspect
python3 -B reprise/managed_update.py plan \
  --bundle /chemin/du/build/Reprise.app \
  --components app,scheduler,harness,claude,codex --profile desktop \
  --codex-entry reprise_codex --codex-entry reprise-harness \
  --activate-scheduler --relaunch-app > /tmp/reprise-update-plan.json
# Lire le plan. Fermer normalement Reprise avant son remplacement.
python3 -B reprise/managed_update.py apply --plan /tmp/reprise-update-plan.json
python3 -B reprise/managed_update.py verify --receipt /chemin/affiché/receipt.json
# Après vérification et observation des nouveaux logs : suppression explicite
# des seules sauvegardes enregistrées dans ce reçu.
python3 -B reprise/managed_update.py cleanup --receipt /chemin/affiché/receipt.json
```

Sans `--components`, seul le bundle est installé. `--home`, `--app` et `--state`
permettent de choisir explicitement une installation ; ils ne déplacent ni
n’initialisent ses données. Les intégrations DSH et Claude sélectionnées doivent
déjà être gérées. Pour une première installation, poser le bundle seul puis
préparer les états et les intégrations par les parcours d’onboarding existants.
Le code est aussi embarqué : utiliser le Python du paquet avec `-E -s -B`
et le chemin `Contents/Resources/helpers/managed_update.py`.

Le plan conserve projets, scopes, rôle, droits d’implémentation, options de
continuation DSH, option de raisonnement ASI1 du preset, PATH et intervalle du scheduler. L’activation du scheduler
est explicite : sans `--activate-scheduler`, son remplacement le laisse arrêté.
La pause d’automatisation enregistrée dans SQLite n’est jamais modifiée.
Un changement implicite du registre principal ou Quick Apply du connecteur
Claude est refusé. Un rattachement de daemon observé contradictoire empêche
la validation et le nettoyage, même si les outils MCP sont découverts.
Une activation peut exécuter les travaux déjà autorisés au prochain tick.

Les étapes et empreintes sont enregistrées sous
`Application Support/Reprise/maintenance/updates/UUID/receipt.json`.
Une reprise utilise le même plan ; elle reconnaît une publication déjà achevée,
refuse une dérive et conserve les sauvegardes. Elle ne réinitialise pas de
résultat, de tentative ou de barrière incertaine. En cas de conflit, ne pas éditer
le reçu pour forcer l’avancement. Inspecter la situation et préparer un nouveau
lot ; les journaux propres aux adaptateurs restent l’autorité de leur reprise.

`verify` contrôle la release réellement chargée et exige un tick postérieur au
début de l’opération lorsqu’un scheduler actif est sélectionné. Si ce tick n’a
pas encore eu lieu, refaire uniquement `verify` après le passage planifié.
Les ponts sont sondés directement avec initialize/tools/list, sans inférence.
Le chargement par les clients ouverts reste `unknown`. Le contrôle des logs
porte sur les nouveaux octets depuis le plan (64 Kio maximum) ; rotation,
volume supérieur ou signature d’erreur conserve les sauvegardes pour examen.
Aucun contenu de journal n’est imprimé dans le résultat public.

Limites explicites : pas de redémarrage de Codex, DSH ou Claude Desktop,
pas d’arrêt implicite de Reprise, pas d’arrêt d’un daemon à partir d’un simple
fichier PID. L’option `codex` met à jour uniquement les entrées MCP explicitement sélectionnées
(`--codex-entry`), avec leurs scopes et arguments existants. Le plan lit le bundle
sans le publier ; l’application vérifie et prépare la release après avoir écrit
une intention durable, puis utilise `config/batchWrite` avec la version native
attendue et sans rechargement. La sauvegarde de configuration est conservée dans
`maintenance/codex-config/UUID`, séparément des sauvegardes nettoyées ici. Le
numéro de version natif reflète la configuration sémantique, pas les commentaires.
Une réponse perdue laisse l’opération indéterminée ; la conformité actuelle ne
prouve pas que cette opération a écrit. Les commandes `reprise
codex-integration-inspect` et `reprise codex-integration-verify-operation --id UUID`
permettent de retrouver et vérifier l’état courant sans réémettre d’écriture. Le remplacement authentifié de
l’app Claude (ReClaude, canal dispatch natif QTi depuis #124) demeure séparé (#25/#27). Le reformatage YAML d’un bloc DSH
peut encore exiger une réconciliation (#76) : cet updater ne contourne pas
les contrôles de propriété des adaptateurs. Aucun téléchargement, credential
transféré, nouvelle permission de modèle ou appel payant n’est ajouté.

Les sauvegardes personnalisées après création, chemins symboliques et chemins
hors de l’opération sont refusés au nettoyage. Les anciens releases, registres,
reçus, sauvegardes d’autres opérations et clones de développement ne sont jamais
purgés par cette commande. Après `cleanup`, le retour arrière correspondant
n’est plus disponible ; le reçu et les preuves de vérification sont conservés.
