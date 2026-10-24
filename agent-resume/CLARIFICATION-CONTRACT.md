# Clarifications pendant une délégation

Décision du 21/10/2026. **Contrat cible, non implémenté**. Complément du [service inter-harness](HARNESS-SUPERVISION-ARCHITECTURE.md) et du [contrat de persistance](DELEGATION-PERSISTENCE.md).

## Objectif

Un expert sollicité doit pouvoir remettre en cause le cadrage, demander des faits manquants et examiner des alternatives. Le harness demandeur doit pouvoir répondre pendant la même mission pour améliorer la qualité de l’investissement, sans délégation systématique ni contexte démesuré. Les skills/instructions installées enseignent ce comportement ; le service garantit la durabilité, le routage, les autorisations et les limites. Une consigne seule ne fournit pas ces garanties.

## Tous les modèles : demander plutôt que boucler

Cette règle vaut pour tous les modèles, y compris les modèles à faible coût. Un fait déterminant manquant, plusieurs interprétations incompatibles ou une tentative répétée sans progrès déclenchent une demande ciblée au harness d’origine, plutôt qu’une suite d’essais spéculatifs. Les consignes à déployer doivent l’enseigner explicitement. L’expert coûteux n’a pas l’exclusivité des clarifications.

La demande précise l’objectif, ce qui est établi, le doute bloquant, les essais et erreurs déjà observés, et la réponse minimale nécessaire. Le demandeur exploite d’abord ses informations ou une vérification déterministe bornée ; il sollicite l’utilisateur si nécessaire. Clarifier un fait et escalader une difficulté de raisonnement sont deux actions différentes. Après deux échecs sur un même volet, changer d’approche ou d’auteur conformément aux instructions du projet ; ne pas renouveler la même tentative ni demander indéfiniment la même précision. La détection de boucle du service doit compléter cette consigne, sans dépendre uniquement de l’obéissance du modèle.

## Échange durable

La mission conserve son identité et sa session experte lorsque le harness le permet. Une clarification est un événement intermédiaire corrélé à la mission, à la tentative et à un identifiant de question stable ; ce n’est ni un résultat final ni une nouvelle mission enfant. Enregistrer la question avant notification. Les noms et schémas des futurs outils de question/réponse restent à définir ; ne pas les présenter comme disponibles.

Le demandeur récupère les questions, répond avec faits sourcés et inconnus explicites, ou sollicite l’utilisateur pour une préférence, une autorisation ou un fait qu’il ne connaît pas. Il ne fabrique pas de réponse et ne transforme pas une demande d’autorisation en accord. L’expert peut poser des questions ouvertes utiles et contester les prémisses ; ne pas imposer un questionnaire fermé. Regrouper les questions indépendantes, préciser les hypothèses et poursuivre les volets indépendants lorsque possible.

Réponses et acquittements sont persistants et dédupliqués. Avant toute continuation, revalider la tentative et la question courantes ; une réponse ancienne ou en double ne déclenche pas un second tour. Un délai écoulé ne vaut ni réponse ni autorisation. Après redémarrage de l’un des participants, retrouver les questions/réponses non réclamées. Si la session est perdue, préparer un checkpoint borné puis une réaffectation explicite avec traitement de l’incertitude, sans supposer la conservation du cache.

Une question n’achève pas la mission : état d’attente de clarification distinct de l’exécution active, du blocage quota et du résultat reçu. Aucun verrou SQLite, slot d’inférence global ou canal de retour ne reste bloqué pendant l’attente ; conserver seulement une réservation logique de mission. Un transport synchrone peut retourner un reçu d’attente plutôt que garder sa connexion ouverte. Pour les jobs MCP Reprise, seul `submit_result` final accepté vaut résultat ; les reçus natifs restent applicables aux autres adaptateurs.

## Économie et contrôle

Transmettre seulement les différences et les faits utiles, réutiliser une session pertinente si disponible ; ne promettre ni cache partagé ni économie déduite de la latence. Les clarifications consomment éventuellement de nouveaux tours : appliquer le budget choisi par l’utilisateur et les capacités/facturation vérifiées. Pas de nouveau plafond quotidien arbitraire. Détecter répétition des mêmes questions sans progrès, réponses contradictoires et ping-pong de délégations ; avertir ou demander réorientation, sans assimiler une investigation légitime à une boucle.

Conserver provenance des observations, portée projet, destinataire autorisé et lignée. Ne pas partager automatiquement secrets, historique complet ou permissions d’un autre harness. Une clarification peut affiner la mission ; une extension de périmètre, dépense ou autorisation nécessite validation appropriée. Résumer le résultat final, hypothèses, preuves et incertitudes, avec critères d’acceptation explicites.

## Livraison et vérification

D’abord valider un échange entre un demandeur et un backend opérationnel ; généraliser par adaptateur avec capacités explicites. Les follow-ups actuels permettent des échanges manuels sur certains backends, mais ne prouvent pas ce protocole automatique ni un arrêt/reprise durable en attente de question. Déclarer un mode manuel si le harness ne sait pas suspendre ou reprendre nativement.

Acceptation : question non terminale ; réponse utile du demandeur et escalade humaine si nécessaire ; question/réponse retrouvées après redémarrage ; doublon/réponse tardive sans nouvelle exécution ; parent indisponible sans perte ni polling modèle permanent ; budget atteint et quota bloqué distincts ; aucune attente sous verrou ; hypothèse contestée et alternative examinée ; finalisation unique. Tests sur fixtures avant essai supervisé borné, sans appel payant implicite.
