# Optional stable role routing for Harness 0.2

The unmodified `thedeveloper256/dsh-model-router` 0.7.1 was rejected by Harness 0.2.0-rc.2's compatibility check. This small adaptation uses the current APIs. It does not replace model catalogs, attachment limits, browser behavior or authentication.

```sh
./role-routing/bootstrap
./role-routing/build
python3 role-routing/smoke.py
./role-routing/install desktop
```

The installed bundle defaults to disabled. Configure the `economy-role-router` Cordis entry's `planner` and `executor` objects with `provider`, `model`, and optional `reasoningEffort` (`inherit` by default); then enable it. Empty provider/model preserves the selected route. Both fields must be supplied together. Only registered catalog models are accepted; registration does not establish API access or quality.

Routing eligibility and routes are fixed at agent creation: enabling the plugin affects only new agents. Turning routing off remains live. A configured executor route applies to every descendant: a child requiring a stronger model must report back to its parent. Do not combine this with another plugin overriding the same request route. No automatic paid fallback, retry loop, or forced delegation is added. Configure only after checking the provider's credentials and billing separately. No credentials belong in this repository.

For simple work, a single Flash agent is usually sufficient. Use an expert for conceptual design, difficult diagnosis or targeted review. Reuse coherent child sessions instead of repeatedly paying for duplicated context. Prefix cache hits are provider/model dependent and must be measured in actual usage; routing alone proves no savings.

Validation: eight policy tests and an actual Harness process against a loopback fake provider. The smoke verifies planner → executor → planner and makes no external model requests. It does not measure real-model competence, billing, or cache hits.
