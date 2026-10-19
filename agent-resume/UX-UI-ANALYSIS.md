# Analyse UX/UI du Dashboard Reprise

Analyse statique du code (`Dashboard.swift`, `Native.swift`, 929 lignes). Livrable pour l'issue #5 — à compléter par un analyste dédié (canal Claude/Codex, issues #3/#4) si un regard externe est souhaité.

## Ce qui est bien
- **Parité** entre les sections Claude Desktop et Codex dans l'onglet Santé (blocs de tailles proches, même structure de checks + anomalies).
- **Nomenclature uniforme** des boutons serveur/appli liée (« Démarrer l'appli liée / Démarrer le serveur / Arrêter le serveur »).
- **Heures en locale 24h** partout (helpers `localTime`/`localDateTime`).
- **Déterminisme affiché** : la santé reste factuelle, la synthèse Apple Intelligence est isolée dans « Analyse locale » et annoncée comme facultative.

## Problèmes UX observés (priorisés)

### 1. Accessibilité quasi absente — élevé
Seulement 3 mentions `accessibility*` pour 9 écrans. Sur macOS, VoiceOver doit annoncer :
- chaque bouton d'action (Tester/Enregistrer/Démarrer/Arrêter) ;
- les indicateurs ✓/✗ des checks de fonctionnalités (actuellement des `Image` sans label) ;
- les valeurs de quota (« Épuisé, renouvelle à 2h ») comme texte, pas seulement visuel.
→ Ajouter `accessibilityLabel` sur les `Image` d'état, les `ProgressView` et les boutons icon-only.

### 2. Densité de l'onglet Santé — moyen
9 onglets + des blocs Santé longs (Claude + Codex + anomalies). Risque de noyer l'info critique (« qu'est-ce qui bloque ? »).
→ Remonter un **résumé d'état** en tête : un bandeau « Sain / N action(s) requise(s) » avant le détail.

### 3. Cohérence des états vides — moyen
`empty(...)` existe mais n'est pas utilisé partout ; certains blocs affichent « indisponible » sans action de rafraîchissement à côté.
→ Uniformiser les états vides avec une action de retry.

### 4. Hiérarchie des titres — faible
`Text(...).font(.headline)` en vrac dans les `Surface` ; pas de distinction nette section/sous-section quand un bloc a plusieurs listes (features + anomalies).
→ Titres de section homogènes + séparation visuelle stable.

### 5. Feedback des actions longues — faible
`analyzeLocal()` (synthèse FM) peut prendre du temps ; le `ProgressView` est présent mais pas de libellé d'attente explicite ni d'estimation.
→ Ajouter un texte d'état pendant l'analyse (« Synthèse en cours… »).

## Recommandation
Traiter (1) Accessibilité et (2) Résumé d'état Santé en priorité — le plus de valeur perçue pour le moins de risque régression. (3)-(5) en second temps.

## Limites
Analyse statique du code, pas un test utilisateur ni un rendu visuel. Les préférences d'ergonomie (densité, couleurs) méritent un arbitrage humain.
