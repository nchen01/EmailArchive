"""Microsoft 365 / Graph provider — stubbed in S1 (decision D2)."""
from __future__ import annotations

from typing import Iterator

from .base import RawMessage

_MSG = "M365 provider not implemented in S1"


class MSGraphProvider:
    def authorize(self, grant: dict) -> None:
        raise NotImplementedError(_MSG)

    def list_ids(self, since_token: str | None) -> Iterator[str]:
        raise NotImplementedError(_MSG)

    def fetch(self, provider_id: str) -> RawMessage:
        raise NotImplementedError(_MSG)

    def fetch_all(self) -> Iterator[RawMessage]:
        raise NotImplementedError(_MSG)

    def sync_token(self) -> str:
        raise NotImplementedError(_MSG)
