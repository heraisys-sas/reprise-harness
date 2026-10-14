# Routage local — conception et tests

## État vérifié

Le plugin externe [NinjaSln-labs/dsh-subagent-router](https://github.com/NinjaSln-labs/dsh-subagent-router), paquet npm `dsh-subagent-router@0.6.0`, a été installé dans un profil temporaire isolé. Source de test : `dfadfaf40f44017c5fa7bfa1f62f06cca265ae5e` (MIT). Son code n’est pas redistribué ici ; le patch diagnostique modifie uniquement ses tests. Aucun fork local n’est activé dans Harness.

- Compilation du code hôte et vérification TypeScript réussies.
- 132 tests amont réussis ; trois cas diagnostiques ajoutés réussis.
- Montage sur Cordis simulé : outils enregistrés, catalogue disponible, RPC hors loopback refusé.
- Démarrage sur le Harness installé jusqu’à la première requête modèle, interrompue par `MISSING_CREDENTIAL` dans le profil isolé.
- Pas de validation d’une génération réelle, du coût, de l’accès OpenAI ou de la qualité du code par ce test.

Les trois cas ajoutés **documentent le comportement existant** : le français « Évalue les garanties de cohérence de ce système distribué » est classé `trivial` ; l’équivalent anglais est `complex`. Une demande française contenant « architecture » est également `complex`. Le passage des tests ne signifie pas que ce classement est satisfaisant.

Pour reproduire sur une copie du commit indiqué : appliquer `acceptance-tests.patch`, installer les dépendances de développement, puis exécuter la suite du plugin. Les tests utilisent des modèles simulés et n’ont besoin d’aucun identifiant. Le Node signé livré avec Harness refuse certains modules natifs tiers ; le lanceur Vitest a été testé avec le binding WebAssembly de Rolldown (`@rolldown/binding-wasm32-wasi@1.2.13`, `NAPI_RS_FORCE_WASI=true`), sans modifier les protections du système.

## Prochaine étape, non implémentée

1. Compléter les règles françaises avec des tests de faux positifs.
2. Créer un helper Swift, entrée/sortie JSON via stdin/stdout, sans serveur réseau.
3. Comparer la demande à un catalogue français d’exemples annotés avec NLEmbedding ; ne pas comparer directement des espaces linguistiques différents.
4. En cas d’ambiguïté, utiliser uniquement le modèle Apple sur l’appareil, avec disponibilité vérifiée, délai borné et catégorie structurée. Aucun basculement cloud implicite.
5. Mettre en cache les décisions et revenir aux règles si le helper échoue. Une proximité vectorielle n’est pas une probabilité de réussite.
6. Laisser au plugin la décision fournisseur/modèle, selon une liste autorisée. Pas de LLM de recommandation supplémentaire, ni de failover libre entre fournisseurs.
7. Mesurer les erreurs de routage, la latence et le coût total des tâches validées avant activation.

Reprise pourra afficher les décisions ultérieurement ; il ne sera pas nécessaire au routage. Les analyseurs déterministes du projet restent la première validation. Les échecs fournisseur ne doivent pas être confondus avec des échecs de qualité du code ni provoquer des réexécutions aveugles d’actions ayant des effets.

## Abonnement OpenAI

Un abonnement ChatGPT Plus ne constitue pas un crédit API général. Codex connecté par ChatGPT utilise le forfait et ses quotas. Certaines intégrations officielles permettent aussi l’usage du forfait dans des applications compatibles ; l’authentification et l’accès effectifs de Harness restent à vérifier.

- https://learn.chatgpt.com/docs/auth
- https://developers.openai.com/siwc/quickstart
