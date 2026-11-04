# Tests de fumée FoundationModels (bras A) — résultats

Date : 2026-10-28. Environnement : macOS 27.0.1 (build 26A434), Xcode 27.1, SDK 27.0, Swift 6.4.
Scripts : `smoke.swift` (disponibilité + guided generation brute), `smoke2.swift` (routage contraint par registre).
Exécution : `swift -module-cache-path /private/tmp/agent-resume-swift-cache <fichier>` — **hors sandbox requise** (les macros `@Generable` passent par `swift-plugin-server`, bloqué par `sandbox-exec`).

## Résultats mesurés

**Disponibilité** : `SystemLanguageModel.default.availability = available` — modèle on-device prêt, aucune configuration.

**Test 1 — traduction FR→EN** : 1000 ms, sortie correcte (« Semantic classification routing remains optional. »).

**Test 2 — guided generation brute** (sans registre) : 796 ms, structure `RouteDecision{best_model, confidence}` respectée, mais identifiants inventés (`semantic_routing`) — le modèle n'a pas de registre → un registre contraint dans l'instruction est nécessaire (confirmé par test 3).

**Test 3 — routage contraint par registre** (flash/pro/codex/decline, 5 cas) :

| Cas | Attendu | Sortie | Conf. | Latence | Verdict |
|---|---|---|---|---|---|
| D1 typo+test | flash | flash | 0.95 | 2096 ms | ✅ |
| D1 badge UI | flash | flash | 0.92 | 1427 ms | ✅ |
| D2 refonte state machine | pro/codex | pro | 0.98 | 1437 ms | ✅ |
| D2 races + retries | codex | codex | 0.96 | 1659 ms | ✅ |
| D5 résumé conversation | decline | flash | 0.91 | 1631 ms | ❌ (conf. haute sur erreur) |

## Ce que le test établit

1. **Le bras A est viable techniquement** : guided generation native, structure contractuelle (équivalent `choice` Jev), **scores de confiance exposés** — répond en partie à la question ouverte n°2 de `ANNOTATED-SET-V1.md` : l'ECE du bras A est mesurable sans échantillonnage multiple.
2. **Latence < 1–2 s** par décision, gratuit, offline, privé (anonymisation P0 moins critique pour A).
3. **Échec observé** : cas `decline` routé en `flash` avec confiance 0.91 — exactement le mode d'erreur que la classe D5 et le critère ECE ≤ 0.15 de `ANNOTATED-SET-V1.md` sont conçus pour détecter. La calibration sur D5 est suspecte dès 5 cas.
4. **Limite de ce test** : 5 cas non annotés, prompt artisanal, aucun contre-réglage — ce ne sont pas des données du holdout. Il établit la faisabilité, pas la performance.

## Conséquence pour la démarche

L'intuition « le résultat peut être évident dès à présent » est confirmée sur la faisabilité : le bras A fonctionne nativement, gratuitement, avec la bonne forme de sortie. La question qui reste ouverte — et que seul le jeu annoté tranche — est la **calibration** (le 0.91 sur un cas `decline` raté) et le taux d'erreur sur D2 vs C. La phase 0 d'accumulation de `ANNOTATED-SET-V1.md` reste requise avant toute activation, mais la comparaison haute qualité est maintenant ciblée : elle porte sur la calibration et D5, pas sur la faisabilité.

## Complément (28/10/2026) — fenêtre de contexte mesurée

Script : `context_limit.swift`. Fenêtre **réelle mesurée : 8192 tokens** (erreur native : « exceeds the maximum allowed context size of 8192 ») — le double du ~4096 documenté (WWDC25).

| Taille injectée | Résultat |
|---|---|
| 1 000 – 8 000 tokens | OK, réponse correcte |
| 12 000 tokens | ERREUR « May contain unsafe content » — les **guardrails** ont refusé avant la limite de contexte (padding répétitif interprété comme suspect) |
| 16 000 tokens | ERREUR native : dépasse la limite de 8192 |

Conséquences : (1) la limite dure est 8192, mais **les guardrails peuvent refuser bien avant** sur des contenus volumineux non naturels — la limite effective est variable, entre ~8k et moins ; (2) le verdict structurel reste inchangé : 8192 tokens ≈ 30-60 KB de texte, très en dessous d'un contexte de coding réel (fichiers + historique + outils) — la fenêtre reste **déterminante** pour écarter le coding de fond, la marge est simplement plus large qu'estimé.
