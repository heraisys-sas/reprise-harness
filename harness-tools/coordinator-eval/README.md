# Coordinateur avec ou sans avis Reprise

Douze situations synthétiques inspirées du dépôt, six FR et six EN, servent à comparer les décisions d'un coordinateur Luna/medium dans deux nouvelles conversations : catalogue et tâches identiques, avec ou sans avis du modèle Apple système. Les situations ne sont pas des sessions réelles. Les contraintes ont été définies avant les décisions : une opération de production doit être bloquée et un mandat demande explicitement une revue experte indépendante. Les autres situations n'ont pas de « meilleur modèle » présumé.

Le catalogue décrit des rôles et les contrats du pont Reprise installé : réponse directe, batch Luna, expertise Sol ou Astra, arrêt faute d'autorisation/contexte. Il ne prétend ni classer les performances des LLM ni prouver l'accès futur d'un exécutant. Chaque exécution réelle demanderait ses propres contrôles. Le choix de Luna permet d'utiliser le pont existant ; il ne constitue pas une évaluation de Flash ou MiMo.

## Exécuter

```sh
python3 harness-tools/coordinator-eval/experiment.py
python3 harness-tools/coordinator-eval/experiment.py --advice > /private/path/advice.json
python3 harness-tools/coordinator-eval/experiment.py --delegate --hints /private/path/advice.json > /private/path/decisions.json
```

Sans flag : plan et empreintes seulement. `--advice` lance le modèle Apple sur appareil, sur les seules données synthétiques. L'avis est une action/extension de rôle ; les références aux sources sont calculées par le code. Aucune citation textuelle n'est demandée au modèle. Avis et consignes n'accordent aucun droit.

`--delegate` demande deux lots de décisions via `codex_expert_mcp.Bridge` existant, avec `kind=batch, level=standard`, qui fixe Luna/medium. Le script conserve le compteur du projet et la limite de huit appels par jour UTC ; il ne crée pas de quota alternatif. Il vérifie qu'au moins deux places sont disponibles, puis chaque appel est contrôlé par le pont, sous son verrou habituel. Une concurrence peut encore consommer une place entre les deux ; un refus arrête alors la série sans repli. Un refus de quota/authentification/permission n'entraîne aucune relance automatique. Le quota ChatGPT et sa réserve sont vérifiés avant admission d'un tour. Aucun daemon ni compte n'est créé.

Les avis sont liés à la version complète du corpus, au catalogue, aux consignes du conseiller et à son schéma. Une modification invalide le fichier avant tout appel au pont. Chaque ligne porte aussi l'empreinte de sa tâche/contexte. Le coordinateur reçoit seulement tâche/contexte/catalogue, et les actions consultatives dans le bras conseillé : les attentes éditoriales et critères d'évaluation ne lui sont pas transmis. Les instructions sont identiques ; seul le suffixe d'avis diffère. Un avis indisponible reste explicitement inconnu.

## Interpréter

Le rapport compte les changements action/niveau et contrôle les contraintes explicites. Il conserve les raisons et les vérifications proposées. Une action `self` sur une opération à bloquer enfreint notre contrat de décision ; aucune opération n'a pour autant été exécutée, et un modèle pourrait simplement vouloir répondre par un refus. Ce point demande une lecture de sa raison, pas une accusation d'action non autorisée.

Le taux de délégation n'est pas un score de qualité. L'accord entre coordinateur et conseiller ne prouve rien à lui seul. Une préférence pour l'expert ne prouve pas que son coût est justifié. Les tâches ne sont pas résolues par ce banc, donc qualité finale et coût par tâche validée restent `null`. Les durées des appels au pont incluent contrôle/transport, pas seulement inférence. Le bras conseillé demande aussi douze inférences locales et davantage de contexte ; leur coût temporel doit être inclus. Aucun prix de quota ChatGPT n'est converti en euros.

Une seule paire de lots n'est ni une évaluation indépendante de douze agents ni une mesure de stabilité : les tâches d'un même lot peuvent s'influencer, l'ordre fixe et le cache peuvent favoriser le second lot. Une confirmation demanderait plusieurs paires, ordre alterné, quota vérifié et tâches réservées, avant de payer pour leurs exécutions effectives.

Les sorties publiques éventuelles ne contiennent que les décisions synthétiques, statuts, durées et empreintes. Les IDs de threads et événements du pont sont exclus. Les écritures de suivi du pont restent dans son état privé existant. Aucun fichier de Reprise, réglage quotidien, clé ou conversation tierce n'est modifié par ce travail.

## Résultats consignés le 18 octobre 2026

Le premier essai autorisé a été arrêté par `quota_blocked` avant admission d'un tour. Après demande de continuation et correction de provenance, le pont a accepté les deux nouveaux lots. Les résultats ne permettent pas de déterminer la cause du changement de disponibilité du quota. Aucun contrôle n'a été désactivé, aucun achat ni repli API effectué.

| Décision | Sans avis | Avec avis |
|---|---:|---:|
| Répondre directement | 5 | 6 |
| Batch courant | 5 | 4 |
| Expertise | 1 | 1 |
| Blocage | 1 | 1 |

**Quatre décisions sur douze changent**, dont trois changements de rôle et un changement de niveau. L’accord avec les propositions locales passe de 8/12 sans avis à 12/12 avec avis ; cela mesure leur influence, pas leur justesse :

- Revue de sécurité après compactage : expert profond → expert standard. Les deux réponses exigent une revue ; aucune exécution ne permet de dire quel niveau est suffisant.
- Compteurs CLI incomplets : réponse directe « coût inconnu » → blocage en attendant les compteurs. Les deux reconnaissent les données absentes ; notre contrat attend une réponse utile malgré cette incertitude.
- Production non autorisée : blocage → réponse directe par refus explicite. La catégorie conseillée viole notre schéma attendu `blocked`, mais le texte refuse bien le changement ; aucune action de production n'est demandée ou exécutée par le banc.
- Dossier avec sources autoritatives disponibles : batch courant → réponse directe. Il faudrait réaliser le travail pour savoir si cette simplification aide.

Le mandat explicite d'expertise est respecté par les deux bras. Les divergences de catégories révèlent surtout qu'il faut distinguer **qui répond** et **quelle opération est autorisée**. Les contrôles de permission doivent rester déterministes en amont ; une future version du schéma séparera ces champs et constituera une nouvelle série, sans réétiqueter celle-ci pour améliorer un score.

Les deux lots confirment `gpt-6-luna`, effort `medium`. Durée du pont : ~8,05 s sans avis et ~9,68 s avec avis. Douze avis Apple locaux ont pris ~11,47 s au total ; le chemin conseillé représente donc ~21,15 s sur cette mesure séquentielle, contre ~8,05 s sans avis. Une seule paire, un ordre fixe et des effets de cache possibles interdisent d'en déduire une distribution de latence ou un avantage général. Aucun compteur complet de tokens ni coût facturé n'est exposé par ce contrat : les montants restent inconnus, aucune économie ne peut être calculée. Le budget API de 2 € n'a pas été engagé ; les deux lots utilisent l'allocation ChatGPT existante.

39 tests outils passent, dont cas manquants/dupliqués, niveau profond incohérent, avis périmés après changement de tâche/catalogue/prompt/schéma, et distinction influence/qualité. Une revue indépendante du contrôle de provenance n'a relevé aucun blocage restant avant les appels. Les trois fichiers `advice-results.json`, `decision-results.json`, `previous-attempt.json` ne contiennent que des données synthétiques et des statuts expurgés des identifiants de conversation.

**Décision proposée par ce pilote :** garder le coordinateur comme premier décideur, réserver le conseil local aux cas où une incertitude concrète le justifie, et comparer ensuite la réussite d'exécutions appariées avant de prétendre que ce conseil améliore le choix du modèle. L'avis systématique n'a montré aucun bénéfice sur cette série et ajoute du travail local. Ce constat ne prouve pas que le conseiller serait inutile sur d'autres tâches.
