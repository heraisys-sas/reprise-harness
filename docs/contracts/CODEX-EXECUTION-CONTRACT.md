# Délégation Codex : expertise et implémentation

Décision du 24/10/2026, issue #62. La création d’une session Codex et son accès au dépôt existent déjà ; la lecture seule était un choix du pont Reprise, pas une incapacité de codexd. Ce canal natif est distinct du prototype de courtier de commandes [LOCAL-EXECUTION-CONTRACT.md](LOCAL-EXECUTION-CONTRACT.md), qui reste désactivé.

## Contrat minimal

- `reprise_delegate` conserve kind expertise/review/batch et accepte mode expertise (défaut) ou implementation. Le mode décrit les permissions ; kind décrit le résultat demandé.
- L’installation doit accorder explicitement `codex_workspace_write` au profil/projet. Le pont reçoit `--codex-workspace-write` depuis cette configuration opérateur ; un argument de l’appel modèle ne peut pas activer cette capacité. Une installation existante reste en expertise sans opt-in.
- Découverte : modes autorisés, writes et projet réellement liés au pont. Ne pas confondre configuration autorisée et essai réel réussi. Le premier installateur géré est celui de DeepSeek Harness ; les autres clients ne reçoivent pas ce droit implicitement.
- Expertise : read-only, instructions de conseil/patch, mêmes protections de fichiers d’entrée qu’avant.
- Implémentation : sandbox native workspace-write, racine canonique du projet explicite, réseau désactivé et approbations on-request. Les accès temporaires natifs peuvent rester disponibles ; ce n’est pas une isolation des lectures ou une sandbox réseau autorisant GitHub. Respecter les instructions du projet, les réservations et les changements des autres agents.
- La session d’implémentation n’est pas forcée à chatgpt_hidden. Cela ne garantit ni sa présence dans la barre latérale ni son classement : la synchronisation Desktop/projets doit être vérifiée séparément.
- Le mode est stocké avec le thread et conservé par reprise_followup, y compris après redémarrage. Une session historique sans mode est expertise. Refuser changement de mode, accès après révocation du grant ou usage des alias codex_review/codex_followup pour une implémentation.
- Même identité de demande et mêmes garanties de reçu/résultat/incertitude. Le rejeu d’une implémentation retrouve le résultat même si elle a changé ou supprimé ses entrées. Une nouvelle demande valide ses entrées avant admission. Les modifications attendues ne déclenchent pas l’incident input_changed réservé aux expertises.

## Exécution réelle et limites

Le modèle peut appliquer des modifications de fichiers et lancer des vérifications locales. Une proposition de patch n’est plus son seul résultat possible. Les permissions natives peuvent néanmoins protéger .git, des fichiers hors workspace ou le réseau. L’accès au dépôt n’autorise pas automatiquement push, fusion, installation de service ou déploiement ; vérifier les autorisations applicables à chaque lot.

Le pont n’auto-approuve rien : une demande d’approbation ou de précision produit blocked_permission avec pending_request, et le tour est interrompu selon le comportement existant. Un parcours interactif d’approbation durable reste à traiter dans #24. Ne pas annoncer que le mode implémentation garantit l’autonomie complète des opérations système.

Le parcours synchrone garde le délai existant de 300 secondes. Un timeout conserve l’incertitude et tente l’interruption ; il ne prouve pas l’arrêt. Une mission plus longue exige un checkpoint puis continuation/réconciliation, ou un futur suivi asynchrone. Aucun nouveau scheduler ni courtier n’est requis pour ce premier lot.

## Installation et preuve

Dans Intégrations Harness : exposer la délégation, choisir « Autoriser Codex à modifier le projet sélectionné », examiner le plan puis appliquer. Le plan et le manifeste portent le grant ; la révocation est également planifiée et vérifiée. Aucun appel modèle ni changement de service à l’installation. Le rechargement du client est une étape distincte.

Tests : défaut lecture seule ; refus sans grant/hors backend ; mode immuable et révocable ; entrées invalides avant admission ; rejeu après modification/suppression sans seconde exécution ; manifeste, idempotence et révocation installateur ; politiques RPC natives et approbation jamais automatique. L’essai réel doit démontrer un fichier effectivement écrit et sa continuation dans un projet jetable, sans en déduire que GitHub, commit ou services sont autorisés.
