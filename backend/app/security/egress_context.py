"""Request-local external egress policy propagation."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Iterator, Optional


@dataclass(frozen=True)
class EgressPolicy:
    corpus_id: str = ""
    data_classification: str = "project_private"
    authorized: bool = True
    redaction: str = "content_not_logged"


_current: ContextVar[Optional[EgressPolicy]] = ContextVar("wenshape_egress_policy", default=None)


def current_egress_policy() -> Optional[EgressPolicy]:
    return _current.get()


@contextmanager
def bind_egress_policy(policy: EgressPolicy) -> Iterator[EgressPolicy]:
    token: Token = _current.set(policy)
    try:
        yield policy
    finally:
        _current.reset(token)
