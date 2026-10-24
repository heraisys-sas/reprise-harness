# Organisation des harness et continuité du travail

Décisions utilisateur du 24 octobre 2026. Contrat de conception ; les capacités ci-dessous ne sont pas déclarées implémentées par ce document. Référence : issue #58. Complète les contrats de [passation](HANDOFF-CONTRACT.md), [clarification](CLARIFICATION-CONTRACT.md) et [supervision](HARNESS-SUPERVISION-ARCHITECTURE.md).

## Rôles configurables par dépôt

| Responsabilité | Affectation initiale souhaitée | Limite |
|---|---|---|
| Supervision et configuration | Reprise | L’application Mac n’est pas un service distant toujours disponible |
| Hyperviseur | DeepSeek Harness / ASI1 | Trie, attribue, suit et demande des précisions ; ne résout pas seul les conflits structurants |
| Référent technique principal | Codex | Privilégié pour expertise, code complexe, conflits et revue sensible ; modèle adapté au lot |
| Worker distant permanent | IronClaw | Traite les issues courtes autorisées et conserve checkpoints/résultats |
| Contre-revue ciblée | Claude | Selon capacité/disponibilité prouvées ; pas une étape systématique |

Le rôle est attribué explicitement au couple harness/profil ; le modèle peut changer sans perdre le rôle. Porter l’abonnement principal motive une préférence économique, pas une compétence garantie ou de nouvelles permissions. Ces affectations sont des valeurs initiales de cette installation, pas des constantes universelles.

Dans l’onglet Dépôts (#57), choisir hyperviseur, référent technique et worker, puis afficher capacités, disponibilité et droits séparément. L’installation réutilise inspect/plan/apply/verify/remove et les instructions gérées ; aucun nouveau moteur de politique ou cascade d’orchestrateurs.

## Worker permanent : premier pilote

Un worker, une file sérialisée, un lot à la fois, sans prise concurrente par un autre harness. Veille déterministe sans sollicitation régulière du modèle. Le lot autonome autorisé a un périmètre, des critères vérifiables et un environnement Git isolé. Conflit de code/tests, choix structurant, sécurité, ambiguïté déterminante ou échecs répétés sans progrès → checkpoint puis expertise/clarification.

Le worker garde son état et les preuves à distance ; un lot déjà accepté ne dépend pas de la présence de l’interface Mac. Mac/expert inaccessible : poursuivre seulement les étapes autonomes déjà autorisées, conserver le résultat non réclamé et attendre pour le reste. Aucune route Codex/Claude n’est supposée disponible depuis le conteneur lorsque sa passerelle Mac est arrêtée. Le pilote ne garantit pas l’uptime grâce au seul conteneur.

Réutiliser l’identité durable du travail, les tentatives et les reçus existants. Les labels GitHub sont une projection humaine, pas un verrou atomique. Le pilote à un worker ne livre pas la réservation concurrente de #48. Aucun merge automatique, aucune nouvelle permission ni bascule payante implicite.

## Reprise après quota

Cette décision remplace l’ancienne exigence générale « mesurer/confirmer le quota avant chaque reprise » et le gel de conception jusqu’à disponibilité d’un compteur.

1. Au refus initial, enregistrer moteur/profil, session/tentative, cause et source, échéance annoncée et fuseau lorsqu’ils existent. Programmer la continuation à cette échéance ; ne pas inventer de reset si absent.
2. À l’échéance, tenter la continuation autorisée sans mesure de quota préalable obligatoire. Vérifier toujours la propriété, l’absence d’exécution active/arrêt volontaire, les permissions et l’incertitude des actions précédentes : ce sont des contrôles d’exécution, pas une sonde de quota.
3. Si la reprise échoue, exploiter la nouvelle erreur/échéance ; lire le quota seulement si cela aide à diagnostiquer ou reprogrammer. Si l’échéance reste inconnue, proposer une attente ou des essais espacés selon une politique visible ; pas de boucle rapide.
4. Acceptation par le transport, démarrage du tour, fin du tour et tâche métier achevée restent des preuves distinctes. Ni passage de l’échéance ni absence de compteur ne devient une preuve de succès.

La connaissance des quotas (#16, #20, #54) reste utile à l’affichage et au diagnostic, sans être un préalable universel à la reprise. Les refus réels du fournisseur, les budgets autorisés et la pause globale restent respectés. Les gardes préalables encore dans le code doivent être adaptées et testées dans le chantier de reprise ; ce contrat ne les désactive pas. Les sondes d’admission de nouvelles expertises décrites dans CACHE-POLICY.md sont un comportement existant distinct, pas la politique cible d’une continuation programmée.

## Nouvelle session et limites de requêtes

L’utilisateur observe que sa route Morph Flash 1017 peut poursuivre dans une nouvelle session après épuisement des requêtes de l’ancienne. Conserver cette observation avec sa source/portée ; ne pas en déduire un compteur, une fenêtre, un quota compte illimité ou une propriété de tous les fournisseurs. Modèle observé : morph-dsv41flash-1017 ; vérifier la route à l’usage, sans supposer l’inférence locale ou la compatibilité d’un paramètre reasoning_effort.

Autoriser une session de remplacement lorsque c’est le parcours adapté, avec checkpoint borné et rattachement au même travail ; enregistrer la nouvelle tentative et la provenance. Ne jamais rejouer une action distante incertaine. Après progrès utile confirmé, une nouvelle limite peut justifier une autre session : pas de plafond arbitraire de sessions.

Éviter les boucles : mêmes refus/créations répétés sans progrès → suspendre les rotations et demander réorientation ou attendre selon l’erreur. Une nouvelle session créée ou un accusé seul n’est pas du progrès utile. Ne pas changer de compte, acheter du quota ou utiliser une route payante implicitement. Distinguer les refus de session, compte/pool, rate-limit temporaire et transport ; ne pas supposer qu’un nouveau chat résout ces derniers. Cette décision remplace l’interdiction absolue des nouvelles sessions après quota dans les anciennes consignes.

## Archivage proposé dans l’interface

Dans Sessions, proposer « Archiver les sessions vides en échec ». Afficher les candidates, leur cause et la raison du classement avant confirmation. Vide signifie sans travail utile, résultat, modification ou action en cours/incertaine ; la présence d’un simple message d’erreur ne suffit pas à rendre une session intéressante pour l’archivage si elle porte un travail à reprendre.

Action explicite de l’utilisateur, pas de nettoyage automatique. Utiliser l’archivage natif lorsqu’il existe ; sinon proposer seulement le masquage local Reprise, clairement nommé. Préserver journaux, checkpoints, reçus et historique d’incidents ; aucune suppression de session. Prévoir réaffichage/désarchivage selon les capacités du harness et revalider les candidates avant application.

## Validation et suivi

- Couper le worker pendant un lot puis redémarrer : travail/preuves retrouvés, aucune double exécution ; action distante incertaine réconciliée avant continuation.
- Mac éteint : lot autonome poursuit, expertise indisponible attend, résultat conservé.
- Échéance issue du refus → reprise tentée sans lecture de quota ; nouveau refus → reprogrammation/diagnostic ; arrêt volontaire et session déjà active empêchent le doublon.
- Rotation de session avec checkpoint et progrès ; refus répétés sans progrès → pas de boucle ; pas de plafond de sessions arbitraire.
- Archivage sélectionné et confirmé, exclusions des sessions utiles/actives/incertaines, aucune suppression, réaffichage possible ; capacité native absente annoncée.

#57 couvre l’interface de configuration, #48 les réservations/passations, #10 la reprise multi-harness, #54 les observations Morph, #28 le découplage GUI/service. #59 couvre l’adaptation de la politique de reprise ; #60 l’archivage proposé dans Sessions. Aucun service réel modifié par cette décision.
