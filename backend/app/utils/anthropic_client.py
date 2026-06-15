"""
Anthropic client factory with httpx compatibility guards.
"""

from __future__ import annotations

from typing import Optional

import httpx
from anthropic import AsyncAnthropic


def create_async_anthropic_client(api_key: str, base_url: Optional[str] = None) -> AsyncAnthropic:
    """
    Create AsyncAnthropic with an explicit httpx.AsyncClient.

    This avoids SDK-internal client construction differences across httpx versions.
    """
    http_client = httpx.AsyncClient()
    if base_url:
        return AsyncAnthropic(api_key=api_key, base_url=base_url, http_client=http_client)
    return AsyncAnthropic(api_key=api_key, http_client=http_client)
