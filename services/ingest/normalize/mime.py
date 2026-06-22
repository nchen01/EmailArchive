"""RFC 2047 encoded-word MIME header decoding — canonical implementation.

All layers (ingest, retrieval, API) import decode_mime_words from here.
Do not define a competing implementation in address.py, threads.py, or
any other module.

Why this exists separately: the decoder is needed at ingest time (new data),
at retrieval time (existing DB rows), and at API response time (outbound
EvidenceMessage subjects). Centralising prevents drift between implementations.
"""
from __future__ import annotations

from email.header import decode_header as _decode_rfc2047


def decode_mime_words(value: str) -> str:
    """Decode an RFC 2047 encoded-word header value to a plain Unicode string.

    Handles B-encoding (base64) and Q-encoding (quoted-printable) with any
    charset, and mixed values like 'prefix =?utf-8?b?4oCU?= suffix'.
    Consecutive encoded words are concatenated without an inserted space
    (RFC 2047 §6.2 rule — whitespace between adjacent encoded words is
    folding whitespace, not part of the value). Falls back to the raw value
    on any decode error so callers never receive None or raise.

    Examples:
        '=?utf-8?b?4oCU?='                              -> '—'
        'INCIDENT P1: p99 =?utf-8?b?4oCU?= triaging'   -> 'INCIDENT P1: p99 — triaging'
        '=?US-ASCII?Q?View_Your_New_Benefit_Amount?='   -> 'View Your New Benefit Amount'
        '=?US-ASCII?Q?Usin?= =?US-ASCII?Q?g_It?='      -> 'Using It'
    """
    if not value or "=?" not in value:
        return value
    try:
        parts = _decode_rfc2047(value)
        out: list[str] = []
        for raw, charset in parts:
            if isinstance(raw, bytes):
                out.append(raw.decode(charset or "utf-8", errors="replace"))
            else:
                out.append(raw)
        return "".join(out)
    except Exception:
        return value
