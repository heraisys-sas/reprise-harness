# Revue UX : démarrage et pilote Apple

Revue statique du code à la baseline `aeb445aa6aeb27b9f2d622e162f30371e8dc3a38` et des changements locaux en cours. Aucun test graphique, VoiceOver ou parcours utilisateur réel n'a été réalisé. Les propositions ci-dessous ne constituent pas des fonctionnalités validées.

## Priorité 1 — démarrage et contexte

- **Pilote Apple :** afficher un récapitulatif du projet, travail, checkpoint et de la skill restaurés. Distinguer « activé », « session ouverte » et « modèle disponible », et donner une action ciblée lorsque le contexte n'est pas disponible. Vérifier par un test de cycle complet que les changements de projet/travail pendant la restauration n'effacent pas le checkpoint mémorisé.
- **Connexions :** distinguer configuration chargée et service effectivement joignable. Les libellés corrigés dans `Dashboard.swift` n'affirment plus qu'une simple configuration prouve la connexion ; un futur indicateur pourrait présenter séparément un résultat de test et son horodatage.
- **Relance :** les textes de `Dashboard.swift` ne promettent plus le redémarrage de `codexd`. Le script appelé sans argument peut exiger une archive runtime ; son résultat doit rester visible.

## Priorité 2 — pilotage de la mémoire

- Afficher les faits et checkpoints sous forme de champs lisibles au lieu de la description brute d'un dictionnaire. Garder les identifiants complets copiables dans un détail technique.
- Donner près des boutons désactivés une explication contextuelle et une action, par exemple sélectionner le projet ou résoudre une opération incertaine. Préserver l'interdiction d'écriture lorsque le contexte ou le résultat sont incertains.
- Nommer clairement les brouillons conservés lorsqu'on change de contexte, et vérifier au clavier et avec VoiceOver que leurs avertissements et les changements d'état sont annoncés.

## Priorité 3 — autres écrans

- **Reprises :** rendre immédiatement visibles l'état observé, l'heure de l'observation, la prochaine action et la cause d'une suspension. Ne pas présenter un délai dépassé comme un quota disponible.
- **Sessions et recherche :** vérifier que les résultats distinguent observation locale, résultat confirmé et état inconnu ; conserver l'accès au détail technique sans en faire le titre principal.
- **Santé et intégrations :** séparer présence d'une configuration, disponibilité observée et dernière validation réussie ; relier chaque alerte à une action précise lorsqu'elle existe.

## Validation recommandée

Tester sur une copie isolée, sans perturber les services ni les sessions de l'utilisateur : fenêtre étroite et agrandie, navigation au clavier, VoiceOver, contraste et taille du texte. Parcourir lancement initial, restauration valide, projet disparu, checkpoint indisponible, service absent et annulation incertaine. Vérifier que les messages ne promettent ni dispatch, ni résultat, ni reprise qui n'ont pas été confirmés.
