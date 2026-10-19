# Claude Desktop MCP capability probe

Observed locally on 18 October 2026, Claude Desktop 2.42304.0, signed-in Free account. This probes the Chat/Desktop MCP client, not a Claude Code CLI session.

A temporary stdio MCP server was registered alongside the existing extension. It exposed no tools, resources or prompts, made no network calls, and never requested sampling. It saved only selected initialization metadata, not credentials, conversation content or tool inputs. The desktop was closed and reopened while idle to load the temporary configuration.

The actual client initialization reported:

```json
{
  "client": {
    "name": "claude-ai",
    "version": "0.1.0"
  },
  "protocolVersion": "2025-11-25",
  "capability_names": [
    "extensions"
  ],
  "sampling_declared": false,
  "sampling_features": []
}
```

Conclusion: this Desktop client did not advertise `sampling`. The advertised contract does not support sampling on this connection. No `sampling/createMessage` request was sent during this first capability-only probe. The matching strings previously found in the installed application are bundled protocol-library code, not evidence of an enabled feature.

Scope: one installed version and this account/session configuration. This does not establish that every future version lacks sampling, nor does it rule out another supported integration. No shared inference/session-control server equivalent to the Codex integration was established by this probe.

Cleanup: the temporary server entry was removed and the original configuration restored; Desktop was reopened to unload it. The existing Kubernetes MCP extension was left unchanged.

Protocol reference: [MCP sampling capability negotiation](https://modelcontextprotocol.io/specification/2025-06-18/client/sampling).

## Direct method test requested by the user

To test whether the implementation understands more than it advertises, a second temporary no-tool server sent one actual `sampling/createMessage` request after initialization:

```json
{"method":"sampling/createMessage","params":{"messages":[{"role":"user","content":{"type":"text","text":"Reply only OK."}}],"maxTokens":8,"includeContext":"none"}}
```

The real Desktop client replied:

```json
{"error":{"code":-32601,"message":"Method not found"}}
```

This is an explicit method rejection, beyond merely omitting `sampling` from its capabilities. No model content was returned. It establishes that this method is unavailable on the tested Desktop MCP connection; it does not establish the behavior of other transports or future builds. The second temporary server was also removed, with exact restoration of the initial configuration and Desktop relaunched to unload it.

## Single-job round trip: successful

On 18 October 2026, a third temporary local stdio server exposed only `get_task` and `submit_result`. Its fixed harmless task was to sort `[7, 2, 5]` and return comma-separated digits. It read no project files, used no model API or sampling, and provided no command execution, network or general file tools.

The existing test conversation was prompted once to claim, solve and submit exactly one job. Other connectors and web search were disabled for the test and restored afterwards. Desktop displayed one-time tool approval prompts; there was no unattended wake-up test and no permanent approval was requested by the test driver.

Independent receiver-side evidence (read from the local SQLite job state, not scraped from the chat answer):

```json
{
  "desktop_version": "2.42304.0",
  "account_ui": "Free",
  "model_ui": "Sonnet 5.5 Moyen",
  "job_id": "66647178-146f-4251-8767-45e4b27e4e04",
  "status": "completed",
  "answer": "2,5,7",
  "get_task_calls": 1,
  "accepted_results": 1,
  "trigger": "One visible chat prompt; tool approval prompts shown",
  "result_transport": "submit_result MCP -> local SQLite, read independently of the UI"
}
```

The result matched the fixed task. The receiver accepts only a claimed job with the matching ID and a bounded nonempty result, persists completion across restarts, and refuses duplicate submissions. Two fixture tests verify that contract, wrong IDs, early submissions and oversized results. This is an experimental one-job diagnostic (`desktop_job_probe.py`), not a general job queue, authenticated multi-client service or production integration. The task is trivial: no claim is made about review quality or routing performance.

This demonstrates a working **conversation-triggered MCP return channel on the signed-in Free Desktop**. It does not establish background operation, automatic generation triggering, controlled model selection or remaining quota. No MCP App widget was used or tested. The temporary server configuration was removed after the test, the saved configuration restored byte-for-byte, and Desktop reopened to unload the server.

## MCP Apps button experiment — 19 October 2026

Version provenance: the bundle version was not freshly captured during the button trial. A subsequent passive read found 2.42304.2. The previously recorded 2.42304.0 belongs to the 18 October tests. The behavior below is an observation of the actual running session, without asserting that all builds behave identically.

The next initialization was inspected at the extension value level, without inference or tools exposed in capability-only mode. Desktop advertised:

```json
{"io.modelcontextprotocol/ui":{"mimeTypes":["text/html;profile=mcp-app"]}}
```

An experimental `desktop_app_probe.py` server then exposed a small self-contained HTML widget, without network domains, external scripts, project file tools, timer-triggered messages or credentials. A visible chat prompt requested only `open_test_panel` and stopped. The panel completed the `ui/initialize` handshake and recorded `ready` through an app-only tool.

One button activation reserved the only dispatch persistently, sent one `ui/message`, and recorded its ACK. **On this Desktop, the ACK meant that the composer was prefilled with a caution notice, not that a model turn had started.** The driver then clicked Send once to complete the bounded return-channel test; tool permissions were granted for one use. This is a supervised flow and does not prove autonomous wake-up. No delayed/background inference was attempted, since even foreground delivery required explicit sending.

Receiver-side evidence:

```json
{
  "client_date": "2026-10-19",
  "ui_capability": {
    "mimeTypes": [
      "text/html;profile=mcp-app"
    ]
  },
  "ui_events": [
    "ready",
    "message_ack"
  ],
  "dispatch_reservations": 1,
  "job_id": "27c16bdf-7295-4746-ba93-cc3bac25487d",
  "attempt_id": "b21c480c-a754-40f8-9a80-69b5b0d0d309",
  "status": "completed",
  "answer": "2,5,7",
  "claims": 1,
  "host_message_behavior": "ui/message acknowledged and prefilled composer with warning; explicit Send was required",
  "automation_proven": false,
  "result_verified": "Local SQLite read, not chat text"
}
```

Before this live trial, the job prototype was strengthened: a pending job receives one atomic `attempt_id`; subsequent claim calls return no task; an uncertain claim remains `indeterminate` after reconnect, without automatic redistribution. A matching late result may still complete it. Repeating an identical result returns the same receipt; a conflicting result or wrong attempt is refused. This does not guarantee exactly one model inference, which is outside the SQLite transaction.

Six local tests cover capability-only operation, missing UI support, bounded resource access, a dispatch surviving widget reload, interrupted ownership and lost-ACK result idempotency. A JavaScript fixture also exercised the actual inline script with a simulated host: no message before a click, one message after two immediate clicks, and a permanently disabled button. The real Desktop trial confirmed the task result independently through SQLite.

The temporary connector was removed, its original config restored byte-for-byte, and the previously enabled conversation connectors/search restored. Desktop was reopened to unload the experimental tools. No persistent installation, startup setting or isolated profile was activated.

Sources: [MCP Apps protocol](https://github.com/modelcontextprotocol/ext-apps/blob/main/specification/2026-01-26/apps.mdx), [current protocol types](https://github.com/modelcontextprotocol/ext-apps/blob/main/src/spec.types.ts). Host behavior above is direct local evidence, not an inference from generic SDK documentation.

## Quick Entry trial — 19 October 2026

Installed bundle version was captured before and after the trial: **2.42304.2**. A fresh one-job connector (`reprise_quick_probe`) was registered temporarily. The existing test chat was active, and its unrelated connectors and search were disabled for the trial.

The automation driver could not emit a modifier-only double Option (`keyPressIncludedNoNonModifierKeys`) and could not obtain a screenshot. The user opened Quick Entry using the shortcut. After dismissing a native menu left open by the driver, accessibility inspection showed the actual Quick Entry system dialog: empty text field, screenshot controls, destination menu and Send button. No screenshot controls or attachments were used.

The destination menu listed the five recent conversations. Selecting **Test technique Reprise** changed the destination label accordingly. The driver entered one short instruction naming the expected job UUID and pressed **Return once**. Quick Entry dismissed itself and the existing chat URL `178dcceb-fcd3-4f2d-ac4f-116a8a153bc3` showed the new user message and a model turn in progress. No additional Send click in the main composer was needed. Ordinary MCP tools remained usable; approval UI appeared during the turn.

Independent local SQLite evidence:

```json
{
  "installed_version_before_test": "2.42304.2",
  "job_id": "619a69f2-03a9-44cd-9b81-f53028d763b5",
  "client_date": "2026-10-19",
  "installed_version_after_test": "2.42304.2",
  "conversation_id": "178dcceb-fcd3-4f2d-ac4f-116a8a153bc3",
  "conversation_selected_in_quick_entry": "Test technique Reprise",
  "quick_entry_opened_by": "User double-Option; automation tool refused modifier-only keypress",
  "driver_send_actions": "One Return in Quick Entry; no additional Send in main composer",
  "attachments_sent": false,
  "claim_count": 1,
  "attempt_id": "8f027aa8-ca00-48fa-bc50-fc0cb618a76a",
  "status": "completed",
  "answer": "2,5,7",
  "tool_permissions": "Approval UI appeared; driver attempted Allow once but user UI changes intervened, so exact choice not captured. No permanent approval action issued by driver.",
  "receiver_verification": "Local SQLite state, independent of chat response text",
  "fully_unattended_proven": false,
  "destination_user_confirmation": "User confirmed the destination was correct; menu click may have been redundant.",
  "menu_click_required": "Not established; inherited active chat is a hypothesis for a separate trial."
}
```

**User correction and remaining uncertainty:** the user reported that a menu click entered a phrase in the prompt, then explicitly confirmed that the destination was correct and suggested the click might be superfluous. The recorded snapshots establish the selected destination, submitted instruction and resulting chat; they do not establish that the menu interaction was necessary or free of side effects. Quick Entry may be able to reuse the already active chat. A separate minimal-path trial should omit the menu, verify the effective destination and use one fresh bounded job. No second inference was launched to resolve this point during the present trial.

Conclusion: supervised **Quick Entry → one Return → existing chat → MCP result** worked on this installation. Opening Quick Entry required user assistance, tool approval UI appeared, and the menu step remains an optimization question. This is not proof of unattended operation, reliability when another chat is active, or operation with a locked/sleeping Mac.

Cleanup: the original Desktop config was restored byte-for-byte and the unrelated connector/search selections restored in the test conversation. The two matching local stdio test processes were stopped without further UI interaction; their reason for multiplicity was not investigated. Claude was left open while the user was typing. No keyboard shortcut, startup setting, profile, credential or subscription was changed.
