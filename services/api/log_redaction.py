"""Access-log query-param redaction (S23 pre-merge fix).

uvicorn's access logger prints the full request target, e.g.
    GET /api/oauth/gmail/callback?code=<oauth_code>&state=<state> HTTP/1.1
Google's OAuth redirect puts the authorization ``code`` (and ``state``) in the
query string, so without redaction the code would leak into backend stdout/logs —
violating the S23 invariant that no raw OAuth code/token/secret appears in logs.

This installs a logging.Filter on the ``uvicorn.access`` logger that rewrites the
logged request target, replacing sensitive query-param VALUES with ``REDACTED``.
Access logging is otherwise unchanged (path, method, status all still logged), so
we keep observability without leaking secrets. Application code already never logs
the code/tokens; this closes the framework-level access-log path.
"""
from __future__ import annotations

import logging
import urllib.parse

# Query params whose values must never be logged (OAuth codes, tokens, secrets).
_SENSITIVE_QUERY_PARAMS = {
    "code",
    "state",
    "token",
    "access_token",
    "refresh_token",
    "id_token",
    "id_token_hint",
    "client_secret",
}
_REDACTED = "REDACTED"


def _redact_query_string(query_string: str) -> str:
    pairs = urllib.parse.parse_qsl(query_string, keep_blank_values=True)
    redacted = [
        (k, _REDACTED if k.lower() in _SENSITIVE_QUERY_PARAMS else v) for k, v in pairs
    ]
    return urllib.parse.urlencode(redacted)


def redact_target(target: str) -> str:
    """Redact sensitive query-param values in a request target (path?query)."""
    if "?" not in target:
        return target
    path, _, query = target.partition("?")
    red = _redact_query_string(query)
    return f"{path}?{red}" if red else path


class AccessLogQueryRedactionFilter(logging.Filter):
    """Redacts sensitive query params from uvicorn access-log records.

    uvicorn logs with ``record.args = (client_addr, method, full_path,
    http_version, status)``; ``full_path`` (index 2) carries the query string.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) == 5 and isinstance(args[2], str) and "?" in args[2]:
            record.args = (args[0], args[1], redact_target(args[2]), args[3], args[4])
        return True


def install_access_log_redaction() -> None:
    """Attach the redaction filter to the ``uvicorn.access`` logger (idempotent).

    Called from main.py at app import. A logger-level filter survives uvicorn's
    dictConfig (its access-logger config declares no ``filters`` key), so it stays
    installed regardless of launcher flags — a hosted deploy cannot silently
    re-enable raw query logging without removing this.
    """
    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, AccessLogQueryRedactionFilter) for f in logger.filters):
        logger.addFilter(AccessLogQueryRedactionFilter())


def is_redaction_installed() -> bool:
    """True iff the OAuth-callback access-log redaction filter is on the
    ``uvicorn.access`` logger. Read by the S27 hosted-readiness guard so a hosted
    process that lost redaction fails startup/readiness instead of serving."""
    logger = logging.getLogger("uvicorn.access")
    return any(isinstance(f, AccessLogQueryRedactionFilter) for f in logger.filters)
