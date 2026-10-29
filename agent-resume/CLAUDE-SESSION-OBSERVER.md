# Observation des sessions Claude Desktop

Ce guide décrit le code local des lots 0–3. Il n'atteste pas qu'il soit installé ou chargé dans Claude Desktop. L'installation opérateur observée est la release 0.1.13 : elle expose `get_task` et `submit_result`, sans `await_task` ni `register_session`; l'état de chargement du client MCP est inconnu. Le code de ces lots est validé localement ; sa publication ne met pas à jour le connecteur installé.

## État et stockage

Le connecteur résout le projet et l'état Quick depuis son manifeste d'intégration géré, puis vérifie l'entrée MCP configurée et son empreinte. Un rattachement absent produit `not_configured`; un manifeste, chemin ou réglage incohérent produit `binding_mismatch`. Le JSON donne `binding.status`, `quick_state` et `state_sha256`; ce dernier empreinte le chemin, pas le contenu de la base. Les options `--project-root` et `--quick-state` servent aux fixtures/tests explicites, pas à contourner le manifeste d'une installation gérée.

Les routes d'observation, associations Claude et connaissances de quota sont stockées dans le `quick.sqlite` existant, sous `quick_state` et dans le scope du projet. C'est la même base SQLite que le `TaskStore` du connecteur MCP. Les tables d'association sont distinctes des tâches, reçus et résultats ; enregistrer une session ne crée ni ne réclame une tâche. L'interface publique renvoie un snapshot JSON versionné même quand le rattachement ou l'observation est indisponible.

## Commandes présentes dans le code local

Depuis `agent-resume/`, avec le `resume` correspondant au clone :

```sh
./resume claude-sessions-status
./resume claude-sessions-status --refresh
./resume claude-sessions-status --refresh-if-due
./resume claude-observer-config --enabled true --port 9333 --interval-seconds 60
./resume claude-observer-config --enabled false --interval-seconds 60
./resume claude-session-register --chat 00000000-0000-0000-0000-000000000001
./resume claude-session-remove --chat 00000000-0000-0000-0000-000000000001
```

Remplacer l'UUID d'exemple par celui d'un chat Claude Desktop existant, déjà confirmé par l'opérateur. `claude-session-register` déclare la provenance `operator_desktop_confirmed`; `claude-session-remove` retire l'association. Pour activer la route, un port local CDP explicite est requis. Réutiliser le navigateur dédié déjà utilisé pour la continuation (port `9333` dans cet exemple) et le même UUID de chat : aucune nouvelle fenêtre ni endpoint ne sont ouverts par ces commandes. L'intervalle est compris entre 15 et 3 600 secondes.

`claude-sessions-status` lit le cache sans ouvrir de connexion CDP. `--refresh` demande une observation explicite. `--refresh-if-due` observe seulement si la route est opt-in et si son échéance est atteinte ; sinon le statut demeure `disabled`, `not_due` ou `busy`. Le collecteur vérifie un budget de passe de 5 secondes entre opérations, limite chaque échange de métadonnées HTTP à une échéance totale de 0,5 seconde au maximum (en-têtes et corps compris), et applique un backoff après indisponibilité. Ce budget de passe ne constitue pas un plafond absolu du temps de la commande entière, qui inclut SQLite et la construction du snapshot. Les erreurs d'opération sont rendues en JSON avec code de sortie 0 ; les erreurs d'arguments du parseur restent des erreurs CLI.

Claude Desktop peut aussi déclarer une session par le nouvel outil MCP local `register_session(chat_id)`. Son origine est `desktop_mcp_declared`. Les deux voies ne constituent qu'une déclaration de provenance : l'UUID et la source ne sont pas attestés cryptographiquement, et cette déclaration n'autorise aucune action de reprise.

## Observation et lecture de quota

La route est désactivée par défaut. Après opt-in explicite, le collecteur contacte le port loopback configuré et inspecte seulement les cibles CDP des onglets ouverts. Il ne considère que les UUID déjà associés, sous `https://claude.ai/chat/<UUID>`. Il ne découvre pas la liste des chats privés, n'adopte pas les autres onglets et ne lit pas l'historique des conversations.

Dans l'app, le rafraîchissement normal affiche d'abord le snapshot cache, puis déclenche `--refresh-if-due` sur une tâche d'arrière-plan distincte. Le rafraîchissement périodique existant tourne pendant que Reprise est ouvert ; la fermeture de l'app arrête ce déclencheur. Le bouton d'actualisation explicite utilise `--refresh`. Une route désactivée ne fait pas de lecture CDP.

Le collecteur ne lit que les alertes/dialogues visibles correspondant à des textes de limite connus, une éventuelle balise `time[datetime]`, le fuseau navigateur et l'état du bouton d'envoi. Un texte de refus reconnu peut signaler `blocked: true`; un bouton désactivé seul reste un indice heuristique, sans preuve de quota épuisé. Une absence de bannière, un onglet absent/ambigu, une erreur ou un état non parsable ne signifient pas que le quota est disponible.

Un pourcentage non observé reste `null` et la connaissance peut rester `unknown`. Le compte et le pool restent non attribués. Une heure affichée seule, par exemple `14:40`, est conservée dans `displayed_reset_time` avec `reset_status: ambiguous` et sans date calculée. Une date absolue reconnue peut être fournie dans `pools[].windows[].resets_at`; les resets précédemment connus après indisponibilité sont séparés dans `historical_reset`. Une date passée ou historique n'établit pas la disponibilité courante.

## Limites fonctionnelles

- Le périmètre est `open_tabs_only` : un chat enregistré mais non exposé reste connu, non observable. Les chats non enregistrés ne sont pas découverts.
- L'observation CDP est passive et opt-in. Elle ne crée/navigue aucun chat, ne saisit rien, n'émet pas de clic et n'envoie pas de tour.
- `action_eligible`, `dispatch_supported`, `admission_confirmed`, `mcp_return_supported` et `automatic_resume_supported` restent faux. L'observation d'un bandeau ou le maintien du même chat dans Claude.ai ne prouve ni admission du message ni retour MCP `submit_result`.
- Il n'y a ni auto-retry du quota, ni réarmement MCP, ni promesse de reprise d'un tour interrompu. Le lot d'action durable et les POC de transport/retour restent conditionnels et ne sont pas implémentés par ces commandes.

## Validation du changement d'interface

Validation du 28 octobre 2026 : suite `ci_test.py`, 950 tests, 6 ignorés, code 0 ; 15 scénarios DOM synthétiques exécutés séparément, tous réussis ; parse et compilation avec édition de liens du module UI réussis. Les tests couvrent également le packaging des helpers. Un premier passage complet a rencontré un timeout de nettoyage de fixture Quick Apply ; ses 9 tests isolés puis le second passage complet ont réussi. Aucun lancement GUI, déploiement, test MCP réel ou inférence. Le bundle autonome complet n'a pas été reconstruit (archive runtime épinglée absente du clone). Voir [la revue](../REVIEW-CLAUDE-SESSION-QUOTA.md).
