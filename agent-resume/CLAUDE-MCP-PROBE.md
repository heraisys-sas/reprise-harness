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
