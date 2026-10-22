# Intégration Harness gérée depuis Reprise

Premier incrément vertical D2, issue #6, 21/10/2026. Profil synthétique et pont directement sondé ; chargement par le client, échange depuis Harness, interface visuelle et compte macOS vierge **non validés**.

## Parcours

Après préparation des états, l’onglet **Intégrations** permet de choisir explicitement le dossier Harness, son profil existant et le projet autorisé. Aucune inspection du profil réel n’est déclenchée à l’ouverture de l’onglet. Inspecter, prévisualiser l’installation/réparation ou le retrait, puis autoriser les modifications du plan affiché. L’option de délégation est désactivée par défaut. Installer sa configuration ne vaut ni autorisation d’inférence ni activation d’un client.

Le profil doit déjà posséder `profiles/PROFIL/cordis.patch.yml` ; Reprise n’installe pas Harness et ne crée pas son profil. Le projet doit être un dossier existant explicitement choisi. Aucun credential, réglage Codex ou service n’est modifié.

Les commandes `resume integration-inspect`, `integration-plan`, `integration-apply` et `integration-verify` réutilisent le même adaptateur que l’interface. Les trois premières préparent une configuration ; `integration-verify` sonde seulement le pont lui-même avec `initialize` et `tools/list`. Aucune commande ne démarre Harness ni un daemon. `integration-apply` reçoit le plan JSON sur stdin ; le plan ne réserve pas les destinations et est revalidé.

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
