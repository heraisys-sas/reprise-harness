# Maquette interface — défauts appliqués + mode avancé (#118)

`maquette.html` — fichier autonome : ouvrir dans un navigateur, aucun serveur ni dépendance.
Trois écrans commutables en tête de page, données fictives uniquement :

- **A · Intégrations** — l'écran actuel (24 contrôles) réorganisé en 3 temps d'assistant
  (Projet détecté → Canaux détectés → Limites), ligne « réglages appliqués » toujours visible,
  les 24 réglages existants repliés sous `Avancé ▸`.
- **B · Test Apple Intelligence** — un bouton « Tester » exécutant la séquence canonique et
  rendant une frise de verdicts par étape (✓/⚠/✗, durée, preuve consultable), verdict d'ensemble agrégé.
- **C · Activité des harness pairs** — carte par paire : synthèse locale d'une ligne (fraîcheur +
  provenance), 3 événements saillants, anomalies candidates du triage sémantique
  (NLEmbedding → candidats loin des clusters connus → première analyse FM = hypothèse),
  budget et cadence dans `Avancé ▸`.

## Contrat appliqué par la maquette

- Défauts appliqués sauf avis contraire ; champ demandé seulement si détection en échec,
  avec la raison affichée.
- Mode avancé par carte (`Avancé ▸`), mémorisé par écran ; rien de supprimé, tout est replié.
- Preuves en badges avec détail au clic ; un verdict FM est une hypothèse, jamais une conclusion.
- Les choix automatiques sont des décisions : listés dans la ligne « réglages appliqués ».

## Itération

Commenter l'issue #118 (écran, élément, règle) ; la maquette est modifiée ici avant tout port
SwiftUI. Les écrans pilot/Apple Intelligence de la session goal adoptent ce contrat sans
collision de code (cette branche ne touche aucun `.swift`).
