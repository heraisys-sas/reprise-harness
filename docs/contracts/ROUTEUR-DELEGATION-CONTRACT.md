# Spec commune — lot ① routeur : vetos, seuils, quality_index source unique

Branche cible : `autoclaw/routeur-vetos` (travail par codeur sur sa branche : `autoclaw/routeur-vetos-metaai`, `autoclaw/routeur-vetos-autocoder`, base `origin/dev` @ dafc6bc).

## Périmètre (lot ① de l'avis croisé du 25 nov. 2026)
Fichiers en périmètre : `reprise/routing_events.py`, `reprise/external_rankings.py`, `reprise/expert_router.py` (point d'entrée du choix uniquement), `reprise/tests/test_expert_router.py` et/ou un nouveau `reprise/tests/test_route_vetos.py`.
INTERDIT : toucher `reprise/expert_mcp.py` (logique de substitution — 4 constats de revue réservés à l'autre agent), `source/` (Swift), VERSION/CHANGELOG, `build/`.

## État actuel à connaître (code réel, base dafc6bc)
- `external_rankings.METRICS = ('price_per_mtok', 'latency_ms', 'ttft_ms', 'quality_index')` ; sources fermées : artificial-analysis, openrouter, lmarena.
- `route_metrics(db, channel)` : corroboration PAR MODÈLE exigée (≥ 2 sources pour une même métrique) ; divergence > 50 % → `conflict=True` et TOUTES les valeurs externes abandonnées (jamais de moyenne).
- `route_choice(db, channels, automatic=True)` : pré-filtre `routing_events.admissible` → garde les canaux avec `price_per_mtok` non None → prend le moins cher. Sinon None.
- `routing_events.admissible(db, channel, automatic)` : automatic=True → free/included seulement ; paid jamais ; unknown fail-closed.
- `populate_from_quota(db, quota_status)` : codex included seulement si compte connu ET usage ordinaire observé, sinon unknown.
- Routes locales codées : foundation-models=free, ironclaw=free (mais ironclaw jamais admissible en auto côté contrat).
- claude-desktop et claude-code sont DEUX canaux distincts et doivent le rester : aucun aliasing, aucune fusion, classes de facturation déclarées séparément, et au moins un test prouve que les deux apparaissent comme candidats séparés.

## Exigences du lot (à implémenter)
1. **quality_index source unique (directive Olivier)** : la corroboration ≥ 2 sources ne s'applique plus à quality_index. Une source unique suffit (même validation de format et mapping par modèle). Si ≥ 2 sources divergent > 50 % sur quality_index → quality_index seul est abandonné (None), les autres métriques survivent. La corroboration et la règle de divergence > 50 % sans moyenne RESTENT intangibles pour price_per_mtok, latency_ms, ttft_ms. `conflict=True` reste un drapeau d'affichage si au moins une métrique est en conflit. Tests exigés : qualité à 1 source utilisée ; qualité divergente jetée sans tuer le prix ; prix divergent jetant toujours tout (comportement historique inchangé).
2. **Vetos disponibilité** dans `routing_events` : nouvelle fonction `vetos(db, channel, ttl_s=None)` retournant la liste des raisons (vide = pas de veto) :
   - veto `paid`/`unknown` en mode auto (cohérent avec admissible, jamais plus large) ;
   - veto `quota-perime` : un `included` dont la dernière observation de quota date de plus de ttl_s redevient unknown → veto (jamais d'extrapolation) ; ttl_s paramètre, défaut conservateur (ex. 24 h) ;
   - nouveau registre de refus observés : table `route_refusals(channel, reason, ts)` + `record_refusal(db, channel, reason)` + veto `refus-recent` si un refus du canal date de moins d'une durée explicite (cooldown = durée, pas simple préférence d'ordre). AUCUN branchement automatique dans expert_mcp dans ce lot : l'API est exposée, le branchement sera un lot suivant.
   - `vetos` ne doit JAMAIS rendre admissible un canal que `admissible` refuse (vetos = filtre supplémentaire, jamais un passe-droit).
3. **Seuils par classe de tâche** dans `route_choice` : nouveau paramètre optionnel `task_class=None` (valeurs : None/`rapide`/`exigeant`/`interactif`). None = comportement actuel exact (aucune régression pour les appelants existants). `exigeant` : exige quality_index non None et ≥ seuil (seuil constant documenté). `interactif` : exige latency_ms et ttft_ms non None et sous seuils. `rapide` : pas de seuil, mais priorité au local free si corroboré disponible. Après seuils : moins cher parmi les survivants, sinon None (jamais de repli payant, jamais de moyenne).
4. **claude-desktop ≠ claude-code** : test de non-fusion exigé (les deux canaux distincts passent chacun la porte avec leurs propres classes).

## Contrats intangibles (à faire respecter par les tests)
- Jamais de repli payant implicite ; unknown fail-closed en auto ; jamais de moyenne sur données divergentes ; ironclaw jamais en auto ; refus jamais re-substitués ; aucune écriture hors des tables de routage ; libellés sans pluriels « (s) » ; commentaires en français (accents tolérés dans le code, commits FR sans accents).

## Livrables par codeur
- Un patch unifié (diff git) applicable depuis la racine du dépôt, + la liste exacte des tests ajoutés.
- Convention de test : `/Applications/Reprise.app/Contents/Resources/runtime/bin/python3 -m unittest reprise.tests.test_expert_router` puis la suite complète `ci_test.py --parallel 4` en juge de paix.

## Réalisation (25 nov. 2026)
- Implémenté et validé (101 tests OK, juge de paix `ci_test.py --parallel 4` vert) selon la méthode demandée : Meta AI et un sous-agent AutoCoder ont codé en parallèle, comparaison idées/qualité, puis synthèse v3.
- Branches : synthèse retenue `autoclaw/routeur-vetos` @ 58271c0 ; versions parallèles conservées pour revue `autoclaw/routeur-vetos-autocoder` @ 7b28438 et `autoclaw/routeur-vetos-metaai` @ f4078db.
- Apport décisif Meta AI repris en synthèse : la priorité `rapide` au local free s'applique SANS exiger de prix corroboré (l'énoncé « si corroboré disponible » rendait la priorité morte — foundation-models n'a aucune cote externe) ; retombée sur le moins cher si le local est vetôé.
- Le chemin `task_class=None` reste inchangé (verbatim historique) ; `expert_mcp.py` et le manifeste non touchés.
