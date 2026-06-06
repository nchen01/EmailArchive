# Noise Quality Profiles Note

Audience: future engineers, reviewers, and product discussions about spam/noise
handling.

Status: not implemented. This is a product/UX note to revisit after S6 real-mailbox
quality sampling.

## Context

The live Gmail hardening run proved the ingest path on a throwaway mailbox, but
it also showed that inboxes vary wildly:

- The validated throwaway mailbox had 2161 messages.
- 1427 / 2161 messages were flagged as noise.
- That rate may be correct for a spam-heavy test inbox, but it should not become
  a global product threshold.

Different users will have very different mailbox shapes. A founder, sales rep,
customer success lead, engineer, executive assistant, and old catch-all inbox
will all produce different ratios of useful project mail, automated notices,
newsletters, calendar mail, and vendor messages.

## Recommendation

Do not expose raw noise thresholds to users.

Instead, expose simple processing profiles:

| Profile | Product meaning | Internal behavior |
|---|---|---|
| Conservative | Keep more mail; hide less. Best for first ingest, small mailboxes, founders, sales, or messy project work. | Require stronger evidence before marking noise. Prefer `not_noise` on ambiguity. |
| Balanced | Default everyday behavior. | Current rules and reviewed S6 defaults. |
| Aggressive | Hide more obvious clutter. Best only after reviewing samples from an old/noisy inbox. | Allow weaker automated/newsletter signals to mark noise, but never weaken sensitivity recall. |

Default recommendation:

- First ingest: Conservative or Balanced.
- Product default after S6: Balanced.
- Aggressive: opt-in only after a user/admin reviews filtered samples.

## UX Shape

Show profiles in human language, not classifier jargon.

Example labels:

```text
Conservative — keep more context
Balanced — recommended
Aggressive — hide more automated mail
```

Avoid exposing:

- noise precision
- false-noise rate
- raw heuristic weights
- regex controls
- confidence sliders

Users should also have review actions:

```text
Not noise
Mark as noise
Sensitive
Not sensitive
Merge people
Split people
```

Those actions should become per-mailbox overrides and future labeled data.

## Backend Shape

Start with mailbox-level config, likely in `mailbox.config`, before adding new
tables.

Example:

```json
{
  "quality_profile": "balanced",
  "noise": {
    "mode": "balanced",
    "sender_overrides": {
      "newsletter@example.com": "noise",
      "vendor@example.com": "not_noise"
    },
    "domain_overrides": {
      "github.com": "not_noise"
    }
  },
  "sensitivity": {
    "mode": "conservative",
    "hr_senders": [],
    "legal_domains": [],
    "personal_domains": []
  }
}
```

Implementation notes:

- `noise` should be reversible. Do not delete noisy messages.
- Views and synthesis should exclude noise by default, but admin/review views
  should allow "include filtered mail".
- Sensitivity should remain conservative across all profiles. Aggressive noise
  filtering must not reduce HR/legal/privileged recall.
- Profile mapping should feed `IngestParams` / `EnrichParams`; avoid hardcoded
  profile checks scattered through logic.
- Add CLI support before UI support, for example:

```text
python scripts/gmail_smoke_ingest.py --quality-profile conservative ...
```

## S6 Connection

S6 should not implement these profiles immediately unless the quality pass proves
the need. Instead, S6 should produce the data that informs the preset mapping:

- Which real messages are false noise?
- Which spam/automation patterns are correctly filtered?
- Which domains/senders need overrides?
- Are project-relevant messages dropped by the current rules?
- Are sensitive messages over-tagged or under-tagged?

The soft metrics in S6 are reporting targets only. They are not global product
thresholds.

## Future Trigger

If the user asks again about spam, noise, inbox variation, false positives, or
how users should tune filtering, revisit this note before designing the feature.

The key product stance is:

> Presets for users, structured overrides for admins, raw thresholds for
> engineers only.

## Open Questions

- Should the first real customer ingest default to Conservative, then suggest
  Balanced after review?
- Should profile choice be per mailbox, per user, or per tenant?
- Should sender/domain overrides apply before or after global heuristics?
- How should override provenance be stored once there are multiple reviewers?
- Should "not noise" overrides automatically cause re-enrichment for affected
  threads?
