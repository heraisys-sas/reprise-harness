# Claude Code expertise backend

Implemented 18 October 2026; opt-in, disabled by default. This adds explicit expertise delegation, not automatic resumption of existing Claude/Cowork sessions.

## Contract

`reprise_delegate` accepts `backend: "claude-code"` with the same `kind`, `level`, `question` and optional `paths`. Standard requests use the CLI's `sonnet` alias with medium effort; deep requests use `opus` with high effort. A successful result must identify exactly one concrete model in `modelUsage`, matching the requested family. Reprise pins that concrete model for every follow-up. Aliases may change for new sessions; access and quota are not guaranteed.

`reprise_followup` requires the returned `thread_id` and explicit `backend`. Only successful sessions created through this project bridge may resume, with the same configured executable, model and effort. Codex compatibility tools remain unchanged. No arbitrary historical UUID, `--continue`, automatic retry or cross-provider fallback is used.

Claude receives only the question and explicitly requested UTF-8 file snapshots, at most 30 files and 96,000 bytes combined. Traversal, symlinks, nonregular files and oversized context are refused. No global Reprise index or chat history is injected. CLI tools, inherited MCP servers, skills, customizations and Chrome integration are disabled through `--safe-mode --restricted --strict-mcp-config --mcp-config '{"mcpServers":{}}' --tools "" --permission-mode dontAsk --permission-prompts none --disable-slash-commands --no-chrome`. Managed policy still applies; this is not an OS sandbox or an override of administrator policies.

The exact child environment uses an allowlist, dropping API keys, provider/helper/runtime/IDE overrides. Cloud connectors, automatic updates/installations, fast mode, transport retries, model refusal fallback and nonessential traffic are disabled. One turn and 8,192 output tokens are requested. Each subprocess has bounded output (1 MiB stdout and stderr combined); inference has a 300-second deadline, with interrupted process groups terminated. An interruption may have consumed quota. A missing/malformed terminal result, denied permission, unsuccessful subtype, session mismatch or unexpected model cannot become a successful resumable route. Error details and credentials are not forwarded.

There is no local daily call cap. The one-request lock remains, with persistent request identity, uncertain-attempt barriers and ancestry checks; see [the durable delegation contract](DELEGATION-PERSISTENCE.md). Legacy counters/options do not block admission. Claude quota remaining stays unknown.

## Activation after account checks

Installed binary verified without inference: `/Users/olivierhz/.vscode/extensions/anthropic.claude-code-2.1.299-darwin-arm64/resources/native-binary/claude`, version 2.1.299. Its native authentication check currently reports no logged-in account. No login, credential change or inference was performed.

1. Sign in to Claude Code using its native subscription login (`claude auth login`, without `--console`) if desired. Do not copy credentials into Reprise.
2. In Claude Settings → Usage, disable paid usage credits/extra usage. Subscription authentication alone does **not** prove that paid overage is off. Reprise cannot verify this setting through the documented CLI.
3. Only after confirming both, add these arguments to the existing project-bound MCP command: `--claude-program /absolute/path/to/claude --claude-included-quota-confirmed`. The latter records an operator assertion, not a technical guarantee. Leave them absent until confirmed. No Desktop session needs to be interrupted for these preparations; tool discovery updates when its MCP process is reloaded.
4. Inspect `reprise_backends`, then make one small explicit request and follow-up. The backend inventory reports configuration only; authentication is checked again in the exact isolated child environment before each inference. Reprise accepts only `loggedIn: true`, `authMethod: "claude.ai"`, `apiProvider: "firstParty"`. API keys/helpers, ambiguous token auth and third-party routes are refused. No `--bare`, console login or silent billing fallback.

Unit tests cover terminal/session/model validation, billing/auth gates, context boundaries, environment filtering, deadlines/output caps, process descendants, backend ownership and persistent admission and recovery. Real subscription inference and model availability remain unvalidated until activation above.

Independent Astra/high review on 18 October used a focused context plus the adapter, bridge, tests and relevant dependencies. Two reproduced issues were corrected: padded questions now count their original length at both bridge and adapter boundaries, and MCP SIGTERM unwinds active subprocess cleanup before releasing the request lock. Regression tests reject oversized follow-ups before admission and terminate a real local fixture CLI on bridge shutdown. The review did not exercise authenticated Claude inference or establish the shutdown signal used by each MCP client.

Official references checked 18 October 2026: [CLI reference](https://code.claude.com/docs/en/cli-reference), [authentication](https://code.claude.com/docs/en/authentication), [environment variables](https://code.claude.com/docs/en/env-vars), [paid usage credits](https://support.claude.com/en/articles/12429409-manage-usage-credits-for-paid-claude-plans).

Desktop alternative investigated: a [real MCP initialization probe](CLAUDE-MCP-PROBE.md) on Desktop 2.42304.0 did not advertise `sampling`; bundled protocol strings alone do not establish a working inference transport. A subsequent explicitly requested direct `sampling/createMessage` test received `-32601 Method not found`, confirming that method is unavailable on the tested connection.

A subsequent [one-job Desktop test](CLAUDE-MCP-PROBE.md#single-job-round-trip-successful) succeeded through ordinary `get_task` / `submit_result` tools, with a visible chat trigger and tool approvals. This separate experimental return channel works without sampling; autonomous wake-up and a production queue remain unvalidated.

On 19 October, MCP Apps support was confirmed and a visible button round trip completed. However, `ui/message` only prefilled the Desktop composer with a warning; explicit Send was still required. See the [MCP Apps experiment](CLAUDE-MCP-PROBE.md#mcp-apps-button-experiment--19-october-2026). This is a supervised Desktop path, not autonomous inference control.

A [Quick Entry trial](CLAUDE-MCP-PROBE.md#quick-entry-trial--19-october-2026) on 2.42304.2 subsequently launched one turn with a single Return and returned its result by MCP in the existing test chat. The user opened Quick Entry; the destination was confirmed correct, while the necessity of the destination-menu click remains unproven. This is a supervised UI path, not a public inference API or validated unattended mode.


## Orientation actuelle — service Reprise unifié

La [cible multi-harness](HARNESS-SUPERVISION-ARCHITECTURE.md) regroupe dans le même service logique les outils de réception/retour et de délégation. Claude Desktop pourra recevoir une tâche et solliciter un autre harness avant de rendre son résultat. Priorité au parcours Desktop avec le compte disponible, même gratuit ; aucun repli API payant. Les prototypes restent séparés. Le CLI d’expertise restreint décrit ci-dessus conserve ses limitations : cette cible ne lui active pas implicitement des outils.

## Pause gratuite affichée — reprise native (#36)

Depuis 0.1.6, `ClaudeQuota` est livré dans le bundle macOS. L’option
**Continuer le chat après le reset gratuit** dans Intégrations autorise ce canal
seul ; elle est activée par défaut et reste subordonnée à la pause globale.
CLI équivalente : `resume claude-quota-config --enabled true` ; inspection :
`resume claude-quota-status`. Le scheduler installé doit pointer vers cette release.

L’observateur Accessibilité ne lit ni cookies ni API web privée. Il exige une
application Claude unique, une zone web avec URL `claude.ai/chat/<UUID>`, le
**dernier** message `Message N sur N`, la pause gratuite française et un bouton
natif Continuer unique. La date sans année est interprétée près de l’observation,
dans le fuseau explicite renvoyé par macOS ; dates ambiguës/inexistantes lors du
changement d’heure sont refusées. Une date récente déjà passée peut être traitée
si l’application expose toujours cette pause. La date et l’empreinte du dernier
message restent enregistrées dans `tasks.sqlite`, table `claude_quota_resumes`.

Après l’échéance, le scheduler revérifie deux fois l’identité, l’empreinte, le
bouton activé, l’absence de réponse en cours et la saisie vide. Il réserve
l’événement durablement **avant** AXPress. `submitted` prouve seulement la
soumission du clic ; `uncertain` interdit son rejeu automatique. Un refus prouvé
avant clic reste en attente. Un autre chat affiché diffère l’action sans naviguer
à la place de l’utilisateur ; une reprise manuelle ou un dernier message changé
empêche l’ancien clic. Aucun prompt, changement de modèle ni repli payant.

L’autorisation Accessibilité doit être accordée au composant Reprise qui agit ;
le helper ne la demande pas et ne l’accorde jamais automatiquement. Sans cette
permission ou si l’interface ne correspond plus au contrat, l’observation demeure
indisponible et aucune action n’est déduite. Reprise doit fonctionner dans la
session graphique connectée, avec le chat cible affiché. Écran verrouillé,
permissions et clic réel après reset restent des validations distinctes.

Un avis fourni par l’opérateur peut être conservé avec `register_notice` en état
`awaiting_observation` : date et chat sont mémorisés, mais aucune empreinte AX
n’est inventée et cet avis n’autorise jamais un clic. La première observation
native valide crée le véritable reçu d’interruption ; l’avis initial est conservé
comme remplacé. Cela permet de tracer une échéance même avant l’autorisation
Accessibilité, sans contourner la revalidation.


## Connecteur unifié livré (#31)

Voir [CLAUDE-CONNECTOR.md](CLAUDE-CONNECTOR.md) : installation native autonome,
réception/remise des tâches et délégation par un seul serveur MCP. Pour les
continuations, le planificateur privilégie maintenant claude-desktopd via le même
RPC que les harness. Sans capacité d’émission et de validation prouvée, il reste
bloqué sur `dispatch_driver_unavailable` ; le retour MCP ne remplace pas le pilote
Quick Apply encore absent. La voie AX de la section précédente est optionnelle.
