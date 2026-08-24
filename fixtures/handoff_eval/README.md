# Handoff quality eval corpus (S43)

Synthetic, deterministic scenarios for the handoff quality evaluation harness
(`scripts/eval_handoff_quality.py`, `services/handoff/eval/`). Each `*.json` file is
one scenario. See `docs/s43-handoff-quality-eval-plan.md`.

## Scenario schema

```
{
  "name": "unique_scenario_name",
  "description": "one line",
  "owner_email": "owner@acme.dev",
  "internal_domains": ["acme.dev"],
  "projects": [{"key": "nexus", "label": "Nexus Auth Platform"}],
  "threads": [
    {"subject": "...", "project": "nexus", "messages": [
      {"header": "id-1@acme.dev", "sender": "dana@acme.dev", "display": "Dana",
       "body": "...", "sensitivity": ["none"], "noise": false}
    ]}
  ],
  "events": [
    {"type": "did|proposed|outcome", "summary": "...", "project": "nexus",
     "headers": ["id-1@acme.dev"]}
  ],
  "scope": {"date_from": null, "date_to": null, "included_projects": []},
  "gold": {
    "project_labels": ["Nexus Auth Platform"],
    "decisions":  [{"contains": "lowercased substring", "cites": ["id-1@acme.dev"]}],
    "open_loops": [{"contains": "...", "cites": ["..."]}],
    "blockers":   [{"contains": "...", "cites": ["..."]}],
    "stakeholders": ["acme.dev"],
    "excluded_headers": ["sensitive-or-noise-header@..."],
    "stale_conflict": false
  }
}
```

Notes:
- Event `type` maps to a claim kind: `did`/`outcome` -> `decision`, `proposed` ->
  `open_loop`. There is no `blocker` kind (see the plan's Limitations).
- A whole thread is excluded if ANY of its messages has a `sensitivity` other than
  `["none"]`; a message with `"noise": true` is excluded individually.
- `gold.excluded_headers` must be headers that the sensitivity/noise gates drop;
  the harness asserts they never appear in evidence or citations.
- `gold.*.contains` is matched case-insensitively against claim text; `cites` must
  all be present in that claim's citations.
- Keep scenarios small and deterministic. No random data.
