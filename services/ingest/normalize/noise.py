"""Stage F — noise classification (spec 00 §10). High-precision, header-driven."""
from __future__ import annotations

import re

from ..providers.base import RawMessage

BULK_PRECEDENCE = {"bulk", "list", "junk"}
NOREPLY = re.compile(r"(no[-_.]?reply|donotreply|do[-_.]?not[-_.]?reply|notifications?|mailer)@")
ESP = ("mailchimp", "sendgrid", "marketo", "sparkpost", "amazonses", "constantcontact")


def is_noise(raw: RawMessage, sender_email: str) -> bool:
    h = {k.lower(): v for k, v in raw.headers.items()}
    if "list-unsubscribe" in h:
        return True
    if h.get("precedence", "").lower() in BULK_PRECEDENCE:
        return True
    if h.get("auto-submitted", "no").lower() != "no":
        return True
    if NOREPLY.search(sender_email):
        return True
    if any(e in h.get("x-mailer", "").lower() for e in ESP):
        return True
    return False
