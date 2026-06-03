"""Stage E — attachments & links (spec 00 §9)."""
from __future__ import annotations

import hashlib
import re

from ekc_schemas import AttachmentRef

from ..providers.base import RawMessage

URL = re.compile(r'https?://[^\s>)\]"\']+')


def process_attachments(raw: RawMessage) -> list[AttachmentRef]:
    """Return AttachmentRefs. Fixture path uses pre-computed sha256; real path hashes bytes."""
    if raw.precomputed_attachments:
        return [
            AttachmentRef(
                sha256=a["sha256"],
                filename=a.get("filename"),
                mimetype=a["mimetype"],
                size_bytes=a["size_bytes"],
            )
            for a in raw.precomputed_attachments
        ]
    refs: list[AttachmentRef] = []
    for part in raw.mime_parts:
        if part.filename and part.type not in ("text/plain", "text/html"):
            refs.append(
                AttachmentRef(
                    sha256=hashlib.sha256(part.bytes).hexdigest(),
                    filename=part.filename,
                    mimetype=part.type,
                    size_bytes=len(part.bytes),
                )
            )
    return refs


def extract_link_domains(text: str) -> list[str]:
    """Registrable (PSL) domains found in URLs within text."""
    import tldextract

    domains: set[str] = set()
    for url in URL.findall(text):
        ext = tldextract.extract(url)
        if ext.registered_domain:
            domains.add(ext.registered_domain)
    return sorted(domains)
