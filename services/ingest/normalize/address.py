"""Stage B — address normalization (spec 00 §6)."""
from __future__ import annotations

import re
from email.utils import getaddresses

from ekc_schemas import Address

from .mime import decode_mime_words  # noqa: F401 — re-exported for callers

PLUS = re.compile(r"\+[^@]*@")


def norm_email(addr: str) -> str:
    """Lowercase + strip plus-addressing: ``a+x@co -> a@co``."""
    addr = addr.strip().lower()
    return PLUS.sub("@", addr)


def parse_addresses(header_value: str) -> list[Address]:
    """Parse a header value (To/Cc/From) into normalized Address objects."""
    out: list[Address] = []
    for name, email_addr in getaddresses([header_value or ""]):
        if not email_addr:
            continue
        display = decode_mime_words(name) if name else ""
        out.append(
            Address(
                raw=(f"{display} <{email_addr}>" if display else email_addr).strip(),
                email=norm_email(email_addr),
                display_names=[display] if display else [],
            )
        )
    return out
