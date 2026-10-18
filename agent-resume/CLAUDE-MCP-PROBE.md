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
