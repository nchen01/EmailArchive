# `fixtures/` — seed synthetic mailbox (S0)

A single deterministic, hand-labeled mailbox that every layer's acceptance gate tests against.
Closes the S0 fixture prerequisite. Regenerate any time:

```bash
python fixtures/generate.py   # writes mailbox.json + gold/*.json (byte-identical each run)
```

`generate.py` is the source of truth for the scenario; the JSON files are its committed output so
evals can run without executing the generator.

## Files

| File | What it is |
|---|---|
| `mailbox.json` | The raw mailbox L0 ingests: `{owner_email, internal_domains, messages[]}`. Each message has `provider_id`, `provider_thread_id`, RFC `headers`, `body_text`, `attachments[]`. |
| `gold/identities.json` | `address_to_person` map + `must_merge` / `must_not_merge` checks. |
| `gold/roles.json` | `person_id → role`. |
| `gold/threads.json` | true thread → member `provider_id`s; plus `shared_provider_thread_id_but_separate` and `synthetic_message_id`. |
| `gold/projects.json` | true thread → `[project labels]` (one thread is multi-label). |
| `gold/sensitivity.json` | `provider_id → [tags]`. |
| `gold/noise.json` | `provider_id → bool`. |
| `gold/events.json` | gold events (`actor`, `type`, `summary`, `source`). |
| `gold/clean_text_checks.json` | quote/signature-stripping assertions per message. |

## Edge cases deliberately encoded (and the gate each one feeds)

- **Identity merge** — `jenna@acme.com` + `j.park@acme.com` are one person (`p_jenna`). → spec 01 §3.
- **Name collision that must NOT merge** — `jenna@vertexlabs.com` is a different person (`p_jbrooks`). → spec 01 §3.
- **Subject reuse, no false merge** — two unrelated threads share subject *and* `provider_thread_id`
  `pt_dup` but have distinct lineage; reconstruction must keep them separate and set
  `lineage_conflict`. → spec 00 §7 / §19.
- **Missing `Message-ID`** — one message (`pmsg_008`) has none; expect a synthesized id. → spec 00 §6 / §19.
- **Quote + signature stripping** — `pmsg_001` body contains a quoted reply and a signature that
  `clean_text` must drop. → spec 00 §8 / §19.
- **Noise** — a newsletter with `List-Unsubscribe` + `Precedence: bulk`. → spec 00 §10 / §19.
- **Sensitivity** — an HR comp email (`hr`) and a legal email (`privileged`+`legal`). → spec 00 §11 / §19.
- **Roles** — internal / manager / account_exec / lead / vendor all present. → spec 01 §5.
- **Multi-project thread** — the weekly-sync thread (`T7`) belongs to both `atlas` and `borealis`;
  soft membership must assign it to both. → spec 03 §10 / §18.
- **Shared-attachment signal** — `T1` and `T3` share `cutover_plan.xlsx` (same sha256), a clustering
  link across threads with no shared rare participant. → spec 03 §6 / §7.

## Scaling up

The committed mailbox is intentionally small and fully inspectable for correctness gates. For
performance/scale testing (spec 03 §20), extend `SCENARIO` in `generate.py` or wrap it in a loop
that clones threads with fresh ids — keep it deterministic (no RNG) so eval results stay comparable.

## Note on `gold/projects.json` and the clustering eval

Spec 03 §18/§19 references a clustering fixture under the clustering module. Treat **this** mailbox
as canonical and point the clustering eval at `fixtures/gold/projects.json` (true thread → projects)
rather than maintaining a second copy.
