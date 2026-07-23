"""Static, self-contained HTML export of a published handoff package (S17.11).

A read-only SNAPSHOT of an already-frozen package for demo portability, offline
handoff, and compliance archive. It creates no new facts and reads nothing from
the live mailbox: the caller passes package-local ``handoff_*`` rows only, and
this module renders them into one standalone HTML document.

Privacy parity with the recipient view (S17.2 §8): NO mailbox id, NO exclusion
counts, NO Gmail/source/open_url link, NO raw capability code or session token —
only package metadata, the constant privacy posture, claims, and snapshotted
evidence. Every piece of package/claim/evidence text is HTML-escaped, so a
hostile subject/body/claim cannot inject markup or script into the document.
The output requires no JavaScript and makes no network calls to read.
"""
from __future__ import annotations

import html
from datetime import datetime

from services.api.schemas.handoff import PrivacyPosture

_KIND_LABEL = {
    "briefing": "Briefing",
    "project_state": "Project state",
    "open_loop": "Open loops",
    "decision": "Decisions",
    "blocker": "Blockers",
    "person_note": "People notes",
}
_KIND_ORDER = ["briefing", "project_state", "open_loop", "decision", "blocker", "person_note"]

# Per-status banner shown at the top of the document. Superseded/revoked are
# clearly marked so a reader never mistakes an archived export for a live grant.
_STATUS_BANNER = {
    "published": ("Published", "This is a static snapshot of a published handoff package."),
    "superseded": (
        "Superseded",
        "This version has been replaced by a newer handoff version; the recipient's "
        "access to this version is blocked.",
    ),
    "revoked": (
        "Revoked",
        "This handoff was revoked; the recipient's access is blocked. This export is "
        "retained for the creator's / compliance archive.",
    ),
}


def _esc(value: str | None) -> str:
    """HTML-escape a value (quotes included). None -> empty string."""
    return html.escape(value or "", quote=True)


def _fmt_date(value: datetime | None) -> str:
    return value.strftime("%b %d, %Y") if value else "—"


def _claims_section(claims: list) -> str:
    if not claims:
        return '<p class="muted">This handoff has no summary points.</p>'
    by_kind: dict[str, list] = {}
    for c in claims:
        by_kind.setdefault(c.kind, []).append(c)
    ordered = [k for k in _KIND_ORDER if k in by_kind]
    ordered += [k for k in by_kind if k not in _KIND_ORDER]  # unknown kinds still render

    blocks: list[str] = []
    for kind in ordered:
        rows = "".join(
            f'<li class="claim">{_esc(c.text)}</li>' for c in by_kind[kind]
        )
        label = _esc(_KIND_LABEL.get(kind, kind))
        blocks.append(f'<div class="kind"><div class="kind-label">{label}</div><ul>{rows}</ul></div>')
    return "".join(blocks)


def _evidence_section(evidence: list) -> str:
    if not evidence:
        return '<p class="muted">No supporting messages were included.</p>'
    cards: list[str] = []
    for e in evidence:
        meta_bits = [b for b in (_esc(e.sender_display) or "Unknown sender",
                                 _esc(e.sender_domain),
                                 _fmt_date(e.ts)) if b and b != ""]
        meta = " · ".join(meta_bits)
        body = (
            f'<p class="body">{_esc(e.body_snapshot)}</p>' if e.body_snapshot else ""
        )
        cards.append(
            '<li class="evidence">'
            f'<div class="subject">{_esc(e.subject) or "(no subject)"}</div>'
            f'<div class="ev-meta">{meta}</div>'
            f"{body}"
            "</li>"
        )
    return f'<ul class="evidence-list">{"".join(cards)}</ul>'


def render_package_html(
    pkg,
    claims: list,
    evidence: list,
    *,
    recipient_email: str | None = None,
) -> str:
    """Render a frozen package into one self-contained HTML document.

    ``pkg`` is a HandoffPackage; ``claims``/``evidence`` are its package-local
    rows. ``recipient_email`` is the (safe, creator-chosen) coverage address, if
    available. No live-mailbox data is read here.
    """
    posture = PrivacyPosture()
    status_label, status_note = _STATUS_BANNER.get(
        pkg.status, (pkg.status, "This is a static snapshot of a handoff package.")
    )

    meta_rows = [
        ("Status", f"{_esc(status_label)} · v{int(pkg.version)}"),
        ("Reason", _esc(pkg.reason)),
        ("Prepared by", _esc(pkg.creator_email)),
    ]
    if recipient_email:
        meta_rows.append(("Recipient", _esc(recipient_email)))
    meta_rows.append(("Published", _fmt_date(pkg.published_at)))
    if pkg.expires_at:
        meta_rows.append(("Access expires", _fmt_date(pkg.expires_at)))
    if pkg.revoked_at:
        meta_rows.append(("Revoked", _fmt_date(pkg.revoked_at)))
    meta_html = "".join(
        f'<div class="meta-row"><span class="meta-k">{k}</span>'
        f'<span class="meta-v">{v}</span></div>'
        for k, v in meta_rows
    )

    title = _esc(pkg.title) or "Coverage handoff"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Handoff package — {title}</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: #f1f5f9; color: #0f172a;
    font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; line-height: 1.5; }}
  .doc {{ max-width: 760px; margin: 40px auto; background: #fff; border-radius: 10px;
    box-shadow: 0 1px 3px rgba(0,0,0,.1); overflow: hidden; }}
  header {{ background: #312e81; color: #e0e7ff; padding: 28px 32px; }}
  header .eyebrow {{ font-size: 11px; font-weight: 600; letter-spacing: .12em;
    text-transform: uppercase; color: #a5b4fc; }}
  header h1 {{ margin: 8px 0 0; font-size: 24px; line-height: 1.2; color: #fff; }}
  header .status {{ margin-top: 10px; font-size: 13px; color: #c7d2fe; }}
  .inner {{ padding: 24px 32px 28px; }}
  .banner {{ border: 1px solid #cbd5e1; background: #f8fafc; border-radius: 8px;
    padding: 10px 14px; font-size: 13px; color: #334155; }}
  .posture {{ margin-top: 16px; border: 1px solid #a7f3d0; background: #ecfdf5;
    border-radius: 8px; padding: 12px 16px; font-size: 14px; color: #065f46; }}
  .posture .title {{ font-weight: 600; }}
  .meta {{ margin-top: 20px; border-top: 1px solid #e2e8f0; }}
  .meta-row {{ display: flex; gap: 12px; padding: 6px 0; border-bottom: 1px solid #f1f5f9;
    font-size: 14px; }}
  .meta-k {{ width: 140px; color: #64748b; flex-shrink: 0; }}
  .meta-v {{ color: #0f172a; }}
  h2 {{ font-size: 13px; text-transform: uppercase; letter-spacing: .06em; color: #64748b;
    margin: 28px 0 8px; }}
  .kind {{ margin-top: 14px; }}
  .kind-label {{ font-size: 12px; font-weight: 600; text-transform: uppercase;
    letter-spacing: .05em; color: #6366f1; }}
  ul {{ list-style: none; margin: 6px 0 0; padding: 0; }}
  .claim {{ border: 1px solid #eef2f7; border-radius: 6px; padding: 8px 12px; margin-top: 8px;
    font-size: 14px; }}
  .evidence-list {{ margin-top: 10px; }}
  .evidence {{ border: 1px solid #e2e8f0; background: #f8fafc; border-radius: 8px;
    padding: 14px 16px; margin-top: 12px; }}
  .subject {{ font-weight: 600; font-size: 14px; }}
  .ev-meta {{ font-size: 12px; color: #64748b; margin-top: 2px; }}
  .body {{ margin: 10px 0 0; white-space: pre-wrap; font-size: 14px; color: #334155; }}
  .muted {{ color: #94a3b8; font-size: 14px; }}
  footer {{ margin-top: 28px; border-top: 1px solid #e2e8f0; padding-top: 14px;
    font-size: 12px; color: #94a3b8; }}
</style>
</head>
<body>
<div class="doc">
  <header>
    <div class="eyebrow">Handoff package · Static export</div>
    <h1>{title}</h1>
    <div class="status">{_esc(status_label)} · version {int(pkg.version)}</div>
  </header>
  <div class="inner">
    <div class="banner">{_esc(status_note)}</div>

    <div class="posture">
      <div class="title">Scope-limited · Sensitive content excluded</div>
      <div>{_esc(posture.note)}</div>
    </div>

    <div class="meta">{meta_html}</div>

    <h2>What you need to know</h2>
    {_claims_section(claims)}

    <h2>Supporting messages ({len(evidence)})</h2>
    {_evidence_section(evidence)}

    <footer>
      Static snapshot of a published handoff package. It contains only the
      messages the sender chose to include; the underlying mailbox is not
      accessible, and sensitive or out-of-scope content is excluded. Generated
      offline — no live data, no network calls, no tracking.
    </footer>
  </div>
</div>
</body>
</html>
"""
