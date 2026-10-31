# Project registry (prototype port)

This registry gives a named project a stable UUID and records explicit, existing workspaces for Codex, Harness, and Claude. The SQLite database is private Reprise state (`projects.sqlite`); it is not stored in `~/.codex`.

## Decisions and boundaries

- Registration and association are explicit. Reprise validates existing Git worktree paths and common repository identity; it does not create, remove, or mutate worktrees.
- A harness slot is singular. Re-associating a different path is rejected; this UI has no reassignment/removal workflow yet.
- Harness and Claude connectors continue to use their own workspace paths and existing path-indexed delegation state. The project identity does not merge requests, results, or client configuration.
- Selecting a project scopes connector plans to the registered workspace. Verification accepts an optional selected path and rejects mismatch with the installed manifest.
- Project creation/association writes the local registry. It does not install or activate a connector, start services, reload a client, or invoke inference.
- A registered path may later become stale. Registration-time Git validation is not proof of ongoing availability; connector plan/apply performs its own checks.

## Port trace

Ported from the local registry prototype onto dev `f7d5ec1`: registry and CLI modules, package assets, project selector and associations, and optional workspace checks for Harness/Claude verification. The current UI exposes only an existing directory picker; changing an occupied association requires a future explicit workflow.
