# ADR — Opérations locales déléguées et contrôles déterministes

**Statut :** conception proposée ; démonstrateur de décision pure, exécution non activée.
**Date :** 23 octobre 2026.
**Contributeurs :** avis Astra approfondi dans la même session que la critique du quorum ; contre-exemples d’une revue indépendante. Aucune revue Sonnet réalisée : route authentifiée et facturation compatible non vérifiées.
**Contexte :** #29 (recettes Git/GitHub), #24 (profil local et Auto-review), #6 (installation), contrat HARNESS-SUPERVISION-ARCHITECTURE.md (work), DELEGATION-PERSISTENCE.md (incertitude RPC).

## Décision

Reprise pourra fournir des opérations nommées à un harness qui ne dispose pas du canal technique approprié, uniquement dans une autorisation opérateur vérifiée et un exécuteur réellement contraint. Le défaut technique d’un harness ne vaut pas autorisation de contourner une interdiction. Un accord IA ou un quorum est consultatif ; il ne crée pas de droits et ne surmonte pas une règle de refus.

Première tranche : évaluateur pur de `git.fast_forward` sur observations typées synthétiques. Il ne collecte aucune observation, ne lance aucune commande et ne modifie aucun dépôt. Tout résultat porte `execution_enabled=false`. `eligible_for_review` signifie seulement que les déclarations fournies sont compatibles avec les préconditions, pas que l’action est autorisée, sûre ou exécutable. Le module n’est ni un outil MCP/CLI ni une dépendance du scheduler ; il n’est pas intégré au payload exécutable.

La première recette cible une branche locale de destination explicite et un OID source fixé ; les objets doivent déjà être disponibles en destination. Aucun réseau, stash, clean, reset, rebase, fusion de conflit, changement de branche, hook ou filtre implicite n’est inclus. `clone`, `fetch`, `push` et chaque mutation `gh` nécessitent d’autres contrats. `pull` ne sera pas un alias cachant fetch et intégration sous un seul reçu.

## Matrice de décision

| Situation | Décision | Preuve requise / récupération |
| --- | --- | --- |
| Autorisation absente, refusée, expirée, non fiable ou hors périmètre | blocked | Accord opérateur établi par une future frontière de confiance ; aucun prompt ni vote ne le remplace |
| Observation inconnue, incomplète, contradictoire ou périmée | needs_evidence | Collecte fiable et cohérente ; horodatage seul insuffisant pour atomicité |
| Même dépôt/common_dir sous deux alias | blocked | Identités canoniques distinctes pour cette recette |
| Destination au commit demandé | noop | Identités/OID et préconditions vérifiés ; aucune commande |
| Ancien OID ancêtre du nouveau, objets présents, profil simple entièrement observé | eligible_for_review | Revue éventuelle, puis nouveau contrôle de l’exécuteur futur |
| Divergence, recul ou HEAD détachée/ref inattendue | blocked | Résolution séparée, jamais reset ou rebase implicite |
| Ascendance non établie ou objets manquants | needs_evidence | Relation exacte et disponibilité des objets ; future recette fetch distincte |
| Index/worktree sales, fichiers non suivis/ignorés, opération Git en cours | blocked | Résolution autorisée séparée ; aucun stash/clean automatique |
| Configurations/attributs/extensions hors profil supporté | blocked | Support explicite à concevoir ; ne pas neutraliser silencieusement un filtre requis |
| Tentative précédente active ou incertaine | blocked | Réconciliation durable de l’effet réel, sans nouvelle clé de contournement |
| Ref, contenu, index ou configuration changés après observation/revue | plan invalide | Recollecter et recalculer ; une empreinte ne verrouille pas un worktree |

Les raisons ont des codes stables ; les refus d’autorisation restent visibles même si d’autres observations manquent. Le plan conserve les références des observations, les préconditions et une empreinte canonique, jamais une commande shell ni un jeton permettant l’exécution.

## Frontières de confiance à construire

- **Demandeur** : identité/projet/session et request_key durables. Une clé n’est pas une autorisation.
- **Autorisation** : opération, dépôts/refs, périmètre, validité et provenance liés à l’opérateur ; aucun booléen fourni par le LLM n’authentifie cet accord. Les données du prototype ne constituent pas cette frontière.
- **Collecteur** : observations fiables de tous les états pertinents et profil effectif Git. Une commande de consultation n’est pas présumée sans effets.
- **Reviewer** : avis sur le manifeste exact, séparé des droits et de l’exécuteur. Pas de répétition pour obtenir un vote favorable ; désaccord/abstention/timeout explicites.
- **Exécuteur** : vérifications indépendantes, isolation OS et maîtrise démontrée de la concurrence. Aucun verrou Reprise seul ne bloque un autre client Git. Un compare-and-swap de ref ne rend pas atomique la mise à jour du worktree.
- **Registre** : intention avant lancement, tentative active/incertaine, effets observés et résultat durable. Après crash, pas de rejeu d’un effet possible sans réconciliation. Aucun acquittement ne transforme l’incertitude en succès.

Profil initial non supporté tant que non traité : hooks, clean/smudge/process et LFS, fsmonitor, external diff/textconv, helpers externes, includes non résolus, sous-modules/récursion, sparse checkout, shallow/partial, alternates, replace refs et worktrees liés. Collecter uniquement `.git/config` serait insuffisant : configuration système/globale/locale/worktree/environnement et attributs effectifs sont concernés.

## Options et compromis

- **Shell universel approuvé par majorité IA : rejeté.** Surface large, erreurs corrélées, injection et autorité implicite ; le vote ne fournit pas une garantie quantifiable.
- **Recettes déterministes immédiatement exécutables : différées.** Utiles, mais nécessitent d’abord autorisation authentifiée, collecte fiable, isolation et réconciliation.
- **Planification pure puis composants séparés : retenue pour le démonstrateur.** Vérifie la logique sans prétendre résoudre ces frontières ; ne débloque pas encore les commandes réelles.

## Tranches et critères de passage

1. Contrat/matrice, évaluateur pur et tests synthétiques ; revue des cas adverses. Aucun accès réel requis.
2. Collecteur Git contrôlé et tests sur dépôts temporaires, dont configurations hostiles, alias de chemins, fichiers ignorés et objets manquants. Choisir/démontrer l’isolation avant toute qualification.
3. Autorisation opérateur, exécuteur local et protocole de récupération après crash/race ; validation de l’absence de double envoi malgré perte d’ACK. Toujours sur dépôts jetables.
4. Façade MCP/CLI avec découverte des capacités et identité d’appelant ; routage compatible (#24), recette/version demandées et effectives ; installation gérée sans élargissement silencieux.
5. Contrats distincts pour clone/fetch réseau, push avec cible/ref explicites et sans force implicite, puis sous-ensemble nommé de gh. Secrets, remote helpers, changement d’accès, fusion et publication traités séparément.

Chaque tranche retourne à l’expert du même sujet avec les diagnostics manqués. Une revue Sonnet indépendante pourra compléter les preuves lorsque sa route sera vérifiée ; elle n’est pas déclarée obtenue. Les tests comparent décisions et cas de refus, puis coût/latence/fausses acceptations et faux refus lors des futurs essais réels. Ni compilation ni suite verte ne prouvent la sécurité de l’exécution non livrée.

## Sources primaires et limites

- Git, [git-merge](https://git-scm.com/docs/git-merge) : sémantique de l’avance rapide et contrôles avant intégration ; ce n’est pas une garantie d’isolation.
- Git, [git-config](https://git-scm.com/docs/git-config) et [git-status](https://git-scm.com/docs/git-status#_background_refresh) : configuration à effets et rafraîchissement d’index ; justifient de séparer la collecte de la décision.
- MITRE, [CWE-441](https://cwe.mitre.org/data/definitions/441.html) : risque d’intermédiaire privilégié manipulé par le demandeur.
- OpenAI, [Auto-review](https://learn.chatgpt.com/docs/sandboxing/auto-review) : changement du reviewer, pas attribution de droits supplémentaires.

La matrice et la décomposition sont des décisions de conception issues de la revue, pas des garanties fournies par ces sources ni une preuve empirique de sûreté.

## Façade Reprise appelable depuis Codex (#30)

Exigence utilisateur : Codex CLI et Codex Desktop doivent pouvoir demander une expertise ou un travail à Claude et aux autres harness par Reprise, comme Harness le fait. Ne pas limiter cette intégration aux recettes Git. Une même façade logique doit exposer découverte des capacités, demande, clarification, suivi et récupération du résultat durable, puis les opérations locales quand leur exécuteur sera validé.

Préférer le client MCP natif de Codex, avec configuration partagée pour un même host quand applicable. Cela n’est pas l’ancien `codex mcp-server` : Codex est ici demandeur/client du service Reprise. Vérifier séparément le CODEX_HOME/host et le chargement Desktop réel, sans supposer qu’un démon ou un autre profil partage automatiquement les mêmes outils. Un plugin/connecteur distinct n’est justifié que par une capacité démontrée.

Pour Claude : discovery distingue les routes réellement disponibles et leur facturation. `reprise_delegate` vers Claude Desktop peut actuellement ne rendre qu’une admission (`response:null`) ; seul le résultat corrélé accepté via MCP constitue le retour. Ne pas annoncer une capacité question/réponse complète tant que get_task/submit_result, récupération, clarifications et redémarrages ne sont pas validés. Installation, découverte des outils et échange réel sont des preuves distinctes. Aucun credential copié ni repli API payant implicite. Les experts Codex isolés ne doivent pas recevoir automatiquement des outils de redélégation, afin d’éviter les cycles.

Source : [MCP pour Codex Desktop/CLI](https://learn.chatgpt.com/docs/extend/mcp). Le support client MCP documenté ne prouve pas encore l’installation Reprise spécifique.

## Validation de la première tranche

Une revue indépendante a reproduit puis fait corriger deux faux positifs du prototype : caractère DEL interdit dans une ref et identités de dépôt source/destination contradictoires. Les tests portent exclusivement sur des observations synthétiques ; ils n’établissent ni la confiance de leur provenance ni la sûreté d’un exécuteur. Les champs de provenance du prototype sont déclaratifs et ne doivent jamais être exposés comme preuve d’autorisation acceptée d’un LLM.

## Canal natif Codex distinct — 24/10

La délégation d’implémentation native Codex est décrite dans [CODEX-EXECUTION-CONTRACT.md](CODEX-EXECUTION-CONTRACT.md). Elle réutilise thread/start et turn/start avec sandbox workspace-write explicitement autorisée par projet ; elle ne passe pas par l’évaluateur Git pur de ce document et ne le rend pas exécutable. Ne pas confondre l’état désactivé du courtier avec une incapacité de Codex à modifier son workspace.
