# Test du routage Harness — 14 octobre 2026

Plugin : dsh-subagent-router 0.6.0, source dfadfaf40f44017c5fa7bfa1f62f06cca265ae5e.

## Résultats

- Installation du paquet publié dans un profil headless isolé : réussie, scripts d’installation désactivés.
- Démarrage avec le Harness installé : réussi jusqu’à la première requête modèle.
- Première requête : MISSING_CREDENTIAL pour deepseek-official ; aucune réponse modèle obtenue. Aucun secret copié dans le profil temporaire.
- Compilation TypeScript du code hôte : réussie.
- Suite amont : 132 tests réussis.
- Montage Cordis, enregistrement des trois outils, catalogue et refus du RPC hors loopback : réussis sur environnement simulé.
- Trois tests diagnostiques supplémentaires : réussis ; ils documentent le comportement existant, pas une garantie de bonne classification.

## Limite française reproduite

Avec des routes explicitement configurées (tâches simples sur Flash, complexes sur Pro), « Évalue les garanties de cohérence de ce système distribué » est classé trivial et envoyé vers Flash. Son équivalent anglais est classé complex et envoyé vers Pro. Le mot architecture déclenche bien le niveau complexe même dans une demande française.

## Décision

Ne pas activer les heuristiques par défaut dans le profil desktop. Pour un essai opérationnel, employer des délégations explicitement qualifiées et des modèles vérifiés, sans outil de recommandation LLM ni changement automatique de fournisseur. L’escalade après échec d’outil métier doit rester pilotée par le diagnostic et les tests : ce plugin traite surtout les échecs d’exécution/fournisseur, pas la qualité du code.

Aucun plugin ajouté au profil quotidien ; aucun réglage de Reprise modifié. Les dépendances de développement et le contournement WebAssembly du lanceur de tests restent dans la copie de test. Aucune protection macOS de signature n’a été modifiée.

## Abonnement

Plus ne constitue pas un crédit API général. La connexion ChatGPT permet l’accès Codex soumis aux quotas du forfait ; certaines applications participantes peuvent utiliser le forfait via une intégration officielle dédiée. La route openai-codex déclarée dans Harness n’a pas été validée par ce test.

Sources :
- https://github.com/NinjaSln-labs/dsh-subagent-router
- https://learn.chatgpt.com/docs/auth
- https://developers.openai.com/siwc/quickstart
