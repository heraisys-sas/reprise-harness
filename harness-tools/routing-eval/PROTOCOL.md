# Protocole pilote de routage — observation uniquement

Ce protocole prépare une comparaison, sans appel API, dépense, daemon ni activation quotidienne. Les seuils sont des conventions pratiques de décision sur un petit pilote, pas des garanties statistiques. Les résultats précédents du sélecteur (6/8 en développement) et du routeur sémantique (20/40 contre 19/40) ne justifient aucun routage automatique.

## Conditions de lancement

- Revalider au lancement les couples fournisseur/modèle et les tarifs documentés dans `MODELS.md` : Flash officiel `deepseek-official` / `deepseek-flash`, efforts `low` et `high` ; expert conditionnel `openai-codex` / `gpt-6-astra`. Confirmer le catalogue, l’accès, les niveaux d’effort et la facturation effectifs ; aucune substitution silencieuse.
- Utiliser uniquement une authentification existante autorisée ou une configuration locale officielle ; aucune clé dans le chat, aucun jeton interne, aucun achat. Une authentification absente bloque seulement le pilote payant.
- Geler un manifeste : commit des outils/routeur, fournisseur/modèle exact par rôle, effort, paramètres réellement supportés, version des schémas d'outils, instructions intégrales, politique projet, empreintes des sources, critères de validation et plafonds. Une modification ouvre une nouvelle série.
- Vérifier avant tout paiement que les observations couvrent tous les agents et tentatives. Un smoke local permet de tester le collecteur, jamais de prouver une économie réelle.

## Trois bras comparables

| Bras | Configuration | Question |
|---|---|---|
| A | Agent unique, `deepseek-official` / `deepseek-flash`, effort `low` | Référence économique |
| B | Même fournisseur et même modèle, effort `high` | L'effort supplémentaire suffit-il ? |
| C | Flash `low` planificateur, lot cohérent confié à `openai-codex` / `gpt-6-astra` si accès vérifié, retour au planificateur | La délégation améliore-t-elle le résultat total ? |

Le challenger `deepseek-official` / `deepseek-v4-pro` est un bras séparé éventuel, après vérification d’accès et dans une enveloppe disponible ; il ne remplace jamais Astra ou Flash silencieusement. Si Astra n’est pas accessible, C reste indisponible. Si le modèle n'expose pas d'effort configurable, B est déclaré indisponible ; ne pas simuler ce réglage par une consigne en prétendant mesurer le même facteur. Pour C, figer la règle d'escalade avant les tâches : échec d'un vérificateur déterministe après une correction, ou catégorie « migration » déclarée dans le manifeste. Une seule délégation experte et un seul lot cohérent par tâche ; conserver l'agent et sa route pendant ce lot. Comptabiliser aussi la planification, la transmission et la synthèse. L'évaluateur ne choisit pas le meilleur bras après avoir vu les solutions.

## Jeu et déroulement

Le corpus livré contient **huit cas publics de développement**, décrits dans `cases/cases.json` : `py-window`, `py-usage`, `js-retry`, `js-prefix`, `rust-usage` (une migration Python→Rust), `devops-command`, `compact-resume` et `project-isolation`. Il ne contient aucun scénario JS→Rust. Ce corpus distinct des huit requêtes initiales du préparateur reste un jeu de développement : ni évaluation aveugle ni heldout indépendant, puisque consignes, fixtures et vérificateurs sont visibles.

Les six premiers cas constituent le pilote de routage ; les deux derniers sont réservés à une exécution de reprise/isolation, pas à une confirmation statistique indépendante. Tous les contrats de sécurité de ces deux cas doivent néanmoins être exécutés gratuitement en prévol, avant tout appel payant. Les petites fixtures n’utilisent ni secrets ni conversations existantes. Vérifier état initial, fichiers autorisés, invariants et oracle ; pour Python→Rust, exiger l’équivalence sur entrées normales et limites, pas seulement la compilation. DevOps se valide localement sans déploiement.

Figer les cas avant exécution et ne pas les modifier selon les réponses. Un relecteur distinct vérifie que les oracles expriment la demande. Une future généralisation nécessitera de nouveaux cas indépendants, sans présenter ces huit cas publics comme un heldout.

1. Prévol gratuit sans modèle : oracles des huit fixtures, contrats `project-isolation` et `compact-resume`, collecte simulée des coûts/échecs, redaction des sorties et arrêt au plafond. Ne pas payer pour vérifier ces contrats locaux.
2. Première vague : deux cas × trois bras, un run par paire. Si aucun bras n'a une collecte complète exploitable, corriger l'instrumentation avant de poursuivre.
3. Deuxième vague : quatre cas restants × trois bras, sous réserve du budget. Alterner l'ordre A/B/C par cas ; chaque run part du même état dans un répertoire ou worktree explicitement autorisé, avec une session neuve.
4. Exécution distante optionnelle des deux cas réservés × A et le candidat retenu, seulement si utile et après nouvelle vérification du budget total de 2 €. Ce sont des cas publics, pas une confirmation heldout ; leur contrôle déterministe gratuit est déjà obligatoire au prévol.

Chaque bras reçoit les mêmes consignes, sources et outils. Pour cette série, conserver le dossier de contexte identique et consultatif ; ne pas mélanger comparaison du routage et amélioration du préparateur. Une preuve omise impose un accès explicite à la source autorisée, enregistré comme travail/coût du bras. La sélection locale ne remplace jamais les consignes intégrales.

## Mesures et preuves

Conserver une ligne par requête terminale, identifiée sans ambiguïté par tâche/run/agent/session/événement ; ne jamais additionner les snapshots successifs d'un même stream. Regrouper toutes les corrections sous le même identifiant de tâche logique. Produire les observations normalisées par `harness_usage.py` puis les agréger avec `usage_report.py` ; conserver à part le manifeste et le verdict indépendant.

| Mesure | Règle |
|---|---|
| Qualité | Verdict oracle passé/échoué/non exécuté, contraintes respectées, référence privée aux preuves et code de sortie ; revue distincte des critères non automatiques, sans prétendre à une évaluation aveugle |
| Coût total | Toutes les requêtes racine/enfants, tentatives interrompues ou échouées, corrections et synthèses ; facturation observée avec devise, période et périmètre identifiables |
| Tokens | Entrées, cache lu/écrit/non caché si exposés, sorties, raisonnement ; documenter la sémantique propre au fournisseur et éviter le double comptage |
| Temps | Temps mural depuis lancement jusqu'au verdict ou abandon ; temps local de préparation et vérification inclus, corrections incluses |
| Fiabilité | Réussite au premier essai, réussite finale, nombre de corrections, délégations, erreurs outils/fournisseur, timeouts et abandons |
| Cache | Compteurs réels fournisseur par requête ; aucun hit déduit d'une empreinte, proximité sémantique ou cache local |

Toute valeur absente reste `null`, jamais zéro. Le raisonnement exposé comme sous-ensemble de sortie n'est pas ajouté une seconde fois. Le temps de step Harness n'est pas une pure latence réseau, et la somme des latences n'est pas le temps mural en présence de parallélisme. `--cli` ne voit que les steps racine : insuffisant seul pour évaluer C. Les adaptateurs peuvent normaliser des inconnues en zéro ; confronter les compteurs aux observations fournisseur avant toute conclusion économique. Les retries invisibles, montants non attribuables et facturations agrégées non réconciliées restent explicitement inconnus.

Rapporter pour chaque bras les succès/total, échecs, coût de tous les runs et temps. Le ratio « coût par tâche validée » = coût de tous les runs du bras / nombre de tâches validées, échecs inclus au numérateur ; indéfini si aucun succès ou si un coût requis manque. Présenter aussi les différences par tâche appariée, sans masquer les abandons. Un tarif officiel multiplié par les tokens donne seulement une estimation distincte du coût facturé observé ; ne pas inventer de devise ou de conversion. Aucune économie démontrée si les coûts complets sont inconnus.

## Plafonds et arrêts

Plafond total du pilote confirmé par l’utilisateur : **2 €**, sans achat ni souscription, et sous réserve du budget mensuel supplémentaire restant de 10–20 €. Si la dépense mensuelle actuelle est inconnue, ne pas supposer l’enveloppe disponible. Pas de conversion USD/EUR inventée : fixer une limite prudente dans la devise fournisseur avant lancement.

Limites conventionnelles : 0,10 € par run à trois bras sur six cas (18 runs = 1,80 € au maximum), dix minutes murales, une correction après premier verdict, une délégation experte pour C. La réserve de 0,20 € couvre uniquement des frais déjà engagés ou retards de comptabilisation ; elle n'autorise pas une répétition. Les deux cas réservés ne peuvent être exécutés à distance que si une enveloppe explicitement bornée reste disponible à l’intérieur des 2 € ; la réserve de sécurité ne finance aucun run supplémentaire.

Ce document ne fournit pas de contrôleur de budget : le plafond n’est pas encore techniquement appliqué. Avant tout appel payant, vérifier compte, facturation, tarifs actuels et limites, puis implémenter et tester le contrôle ou garder les appels bloqués. Le contrôleur devra vérifier avant chaque requête le coût acquis plus une borne prudente de la requête suivante, sortie maximale comprise. Si la borne d'entrée/sortie ou le coût n'est pas estimable, ne pas lancer. Une limite locale ne garantit pas un plafond dur fournisseur : préférer aussi une limite de dépenses native disponible, sans modifier de réglage global. Couper au premier plafond atteint ; coûts d'une requête en vol et dépassement éventuel restent rapportés. Aucun retry automatique supplémentaire.

Arrêt immédiat d'un run : tentative d'accès hors politique, consigne importante perdue, modification non autorisée, compteur incohérent ou absence de collecteur pour un enfant. Arrêt de la série : budget indisponible/atteint, oracles défectueux ou deux échecs d'infrastructure indépendants des bras ; conserver ces faits séparés des erreurs du modèle. Aucun contournement des contrôles de confiance ou d'autorisations pour terminer un cas.

## Cache, préfixes et compactage

Ne pas promettre de cache fournisseur « froid » : une session neuve ne le garantit pas. Reporter l'état initial comme inconnu et les hits réels, sans requêtes artificielles de préchauffage. Garder stable l'ordre des instructions et schémas d'outils ; changer uniquement le suffixe de tâche. Un changement de modèle ne transfère pas automatiquement son cache. L'ordre alterné réduit certains biais, sans les éliminer.

Le cas de conversation longue inclut un état non compacté et un résumé figé contenant des références aux décisions et contraintes originales. Vérifier après reprise les mêmes invariants et citations, puis le refus d'une source d'un projet au nom proche. Les consignes originales sont rechargées intégralement. Si Harness permet un vrai compactage explicite observable, enregistrer son événement et le contexte remis au modèle ; sinon étiqueter cette variante « reprise simulée », sans prétendre tester le compactage natif.

Tester le contrat d'un éventuel cache partagé seulement avec données synthétiques et simulateur local : deux identités, deux politiques détenues par le serveur, récupération autorisée/refusée avant/après résumé, révocation, version de source modifiée, absence de provenance, namespaces distincts. Une chaîne `project_id` n'authentifie personne. Aujourd'hui aucun service MCP ni cache persistant partagé n'existe ; ces tests ne démontreraient pas la sécurité d'un futur transport ou de clients réels. Ne pas réactiver l'import global Reprise : les entrées sans provenance restent exclues sans déchiffrement.

## Décision après le pilote

Conserver A par défaut et le routage quotidien désactivé. Un candidat est seulement admissible à une confirmation s'il ne perd aucune tâche réussie par A, ne viole aucune contrainte, et apporte soit ≥20 % de réduction du coût complet par tâche validée sans hausse du temps médian >20 %, soit au moins une tâche validée supplémentaire dans l'enveloppe. Ces seuils conventionnels limitent les changements pour un gain marginal ; sur six cas ils ne démontrent pas une généralisation.

Présenter les données brutes par tâche et les inconnues, puis une recommandation observation/poursuite/abandon. Même un résultat favorable ne déclenche pas automatiquement l'installation quotidienne. Les journaux complets et dossiers restent privés hors Git ; versionner seulement fixtures synthétiques, manifeste public sans secrets et résultats agrégés audités. Supprimer les sorties temporaires privées après la période de revue définie pour le pilote.
