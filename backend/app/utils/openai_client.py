"""
OpenAI client factory with httpx compatibility guards.
"""

from __future__ import annotations

from typing import Optional

import httpx
from openai import AsyncOpenAI


def create_async_openai_client(api_key: str, base_url: Optional[str] = None) -> AsyncOpenAI:
    """
    Create AsyncOpenAI with an explicit httpx.AsyncClient.

    Why:
    - openai<newer and httpx>=0.28 can fail with:
      AsyncClient.__init__() got an unexpected keyword argument 'proxies'
    - passing our own http client avoids SDK-internal construction differences.
    """
    http_client = httpx.AsyncClient()
    return AsyncOpenAI(api_key=api_key, base_url=base_url, http_client=http_client)
