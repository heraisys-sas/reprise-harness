# Project registry (prototype port)

This registry gives a named project a stable UUID and records explicit, existing workspaces for Codex, Harness, and Claude. The SQLite database is private Reprise state (`projects.sqlite`); it is not stored in `~/.codex`.

## Decisions and boundaries

- Registration and association are explicit. Reprise validates canonical Git worktree roots and rejects path/harness collisions. An associated workspace may share the source's Git common-dir **or** be an independently cloned repository whose local `remote.origin.url` matches the source's local `remote.origin.url` exactly and is nonempty. No `git fetch`, network call, clone, mutation or silent workspace reassignment occurs.
- Matching `origin` is **not cryptographic proof of common history or trusted contents**; Git remote names are configurable locally. The operator must verify the clone before explicitly associating it. If either origin is missing or differs for independent clones, registration fails closed. An upstream expressed using different SSH/HTTPS URL spellings must be normalized by the operator, never inferred by the registry. Runtime code still validates its own worktree identity and project state.
- A harness slot is singular. Re-associating a different path is rejected; this UI has no reassignment/removal workflow yet.
- Harness and Claude connectors continue to use their own workspace paths and existing path-indexed delegation state. The project identity does not merge requests, results, or client configuration.
- Selecting a project scopes connector plans to the registered workspace. Verification accepts an optional selected path and rejects mismatch with the installed manifest.
- Project creation/association writes the local registry. It does not install or activate a connector, start services, reload a client, or invoke inference.
- A registered path may later become stale. Registration-time Git validation is not proof of ongoing availability; connector plan/apply performs its own checks.

## Port trace

Ported from the local registry prototype onto dev `f7d5ec1`: registry and CLI modules, package assets, project selector and associations, and optional workspace checks for Harness/Claude verification. The current UI exposes only an existing directory picker; changing an occupied association requires a future explicit workflow.
