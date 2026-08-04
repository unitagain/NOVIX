"""Single Writer model binding used by the agentic writing runtime."""

from __future__ import annotations

from typing import Any


class WriterAgent:
    """Keep the Writer identity and language without carrying a second workflow."""

    def __init__(
        self,
        gateway: Any,
        card_storage: Any,
        canon_storage: Any,
        draft_storage: Any,
        language: str = "zh",
    ) -> None:
        self.gateway = gateway
        self.card_storage = card_storage
        self.canon_storage = canon_storage
        self.draft_storage = draft_storage
        self.language = language

    def get_agent_name(self) -> str:
        return "writer"
