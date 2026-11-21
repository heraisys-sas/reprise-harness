# Preuves post-déploiement #241 (20/11/2026 ~04:30-04:50 Paris)

Release déployée : 4816d91eaa1729bdd3dec89d69190b9f16808f202b6aa7371ffafbf870e36ed0
Server info : reprise-codex-expert 0.1.20, source_sha256 69921a35727c39f6b146cb1db0e38a39bb06aecdc4fd342e3038e64b6870a9c3
Outils actifs : reprise_requests, reprise_reconcile, reprise_delegate, reprise_backends, reprise_notifications, reprise_followup

## 1. reprise_backends — CRITÈRE PARTIEL
codex.models = ['gpt-6.2-sol', 'gpt-6.1-sol', 'gpt-6-luna', 'gpt-6-astra'] ✔
(gpt-6.2-sol présent dans le registre déployé, provenant du constant 4816d91e/codex_expert_mcp.py:110)

## 2. Admission model=gpt-6.2-sol — REFUSÉE (limitation externe)
reprise_delegate model=gpt-6.2-sol → engine_unavailable, phase=starting,
"Requested model or reasoning effort unavailable; no substitution."
Cause vérifiée : model/list du daemon codex actif (app-server via proxy socket
app-server-control.sock) = gpt-6-luna, gpt-5.6-terra, gpt-5.6-luna — ni gpt-6.2-sol ni gpt-6.1-sol.
Le code déployé (codex_expert.py:153) refuse toute substitution : comportement conforme au contrat.
Tour de contrôle model=gpt-6-luna → finished, thread 01a1f701-d021-7770-b972-51de8fb56840.

## 3. Délégations parallèles — CRITÈRE OK
probe-par-20261120-p1 → finished, thread 01a1f702-2c6a-7ea2-86d5-e0739968489b ("La capitale de la France est Paris.")
probe-par-20261120-p2 → finished, thread 01a1f702-3d56-7842-b0fd-9eaea0729aaa ("Paris.")

## 4. reprise_notifications — CRITÈRE OK
3 événements turn_terminal (terminal_evidence=native:completed, work_completed=true,
result_sha256 stables) lus pour les 3 request_keys ci-dessus.
ack idempotent : 2e ack → acknowledged sans erreur (consumer_id asi1-probe-20261120).

## 5. Backend chatgpt — admission OK, capture dépendante du holder
probe-chatgpt-20261120-a1 → admission_only=true, delivery=attempted.
reconcile → next_action=collect, "No native thread was persisted before the client returned"
(canal v1 consultatif : dispatch at-most-once, capture via holder CDP Desktop — conforme #156).

## Notes
- wait_seconds/execution_seconds refusés pour backend≠codex ("budgets apply to the Codex backend only") — conformité au contrat.
- Suite de tests dev locale : 2095 OK / 5 skipped (20/11, après fetch origin/dev).
- Scripts de sonde : mcp-probe-241c (admission), 241d (backends+contrôle luna), 241e (parallèle), 241f (read), 241g (ack), 241i (chatgpt), 241j (reconcile).
