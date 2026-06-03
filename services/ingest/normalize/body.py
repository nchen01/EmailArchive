"""Stage D — body normalization → clean_text (spec 00 §8).

Uses talon when available (quote + signature stripping). On Windows talon often
won't install, so a robust regex fallback covers the same cases. HTML-only bodies
are run through html2text first.
"""
from __future__ import annotations

import re

try:  # pragma: no cover - depends on platform
    import talon

    talon.init()
    from talon import quotations, signature

    _TALON = True
except Exception:
    _TALON = False


# "On <date>, <name> wrote:" header that introduces a Gmail-style quoted block,
# followed by one or more quoted ("> ...") lines.
_ON_WROTE = re.compile(
    r"\n?On .*?wrote:\s*\n(?:>.*(?:\n|$))+",
    re.IGNORECASE | re.DOTALL,
)
# Fallback: a run of quoted lines at the end with no "On ... wrote:" header.
_QUOTED_LINES = re.compile(r"(?:^>.*(?:\n|$))+", re.MULTILINE)
# Signature delimiter: a line consisting of "--" or "-- " (RFC 3676).
_SIG_DELIM = re.compile(r"\n-{2} ?\n")


def _decode(part) -> str:
    """Charset-normalize bytes from a MIME part into text."""
    try:
        from charset_normalizer import from_bytes

        best = from_bytes(part.bytes).best()
        if best is not None:
            return str(best)
    except Exception:
        pass
    charset = part.charset or "utf-8"
    try:
        return part.bytes.decode(charset, "replace")
    except (LookupError, UnicodeDecodeError):
        return part.bytes.decode("utf-8", "replace")


def _strip_quotes_and_sig_fallback(text: str, sender_name: str | None) -> str:
    # 1. Remove "On ... wrote:" quoted block.
    text = _ON_WROTE.sub("\n", text)
    # 2. Remove signature: split on the "-- " delimiter, keep the first part.
    parts = _SIG_DELIM.split(text, maxsplit=1)
    text = parts[0]
    # 3. Remove any remaining trailing quoted-line block.
    text = _QUOTED_LINES.sub("", text)
    return text


def clean_body(text_or_part, kind: str = "text/plain", sender_name: str | None = None) -> str:
    """Normalize a body into clean_text.

    Accepts either a raw string (with ``kind`` indicating mime type) or a
    MimePart-like object exposing ``.type``/``.bytes``/``.charset``.
    """
    # Resolve input → (text, kind)
    if isinstance(text_or_part, str):
        text = text_or_part
        mime_type = kind
    else:
        text = _decode(text_or_part)
        mime_type = getattr(text_or_part, "type", kind)

    if not text:
        return ""

    if mime_type == "text/html":
        import html2text

        h = html2text.HTML2Text()
        h.ignore_links = False
        h.body_width = 0
        text = h.handle(text)

    if _TALON:  # pragma: no cover - platform dependent
        try:
            text = quotations.extract_from_plain(text)
            text, _sig = signature.extract(text, sender=sender_name or "")
        except Exception:
            text = _strip_quotes_and_sig_fallback(text, sender_name)
    else:
        text = _strip_quotes_and_sig_fallback(text, sender_name)

    return " ".join(text.split())


def clean_body_from_raw(raw, sender_name: str | None, max_chars: int) -> str:
    """Pick the best part of a RawMessage, clean it, and truncate."""
    plains = [p for p in raw.mime_parts if p.type == "text/plain"]
    htmls = [p for p in raw.mime_parts if p.type == "text/html"]
    if plains:
        part = plains[0]
    elif htmls:
        part = htmls[0]
    else:
        return ""
    cleaned = clean_body(part, part.type, sender_name)
    return cleaned[:max_chars]
