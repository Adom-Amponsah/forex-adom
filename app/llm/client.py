"""
Provider-agnostic LLM client.

The engine only needs `complete(system, user) -> str`. Any OpenAI-compatible
chat-completions endpoint works out of the box; tests inject a fake client.
"""
from __future__ import annotations

import os
from typing import Optional, Protocol, runtime_checkable

import requests


@runtime_checkable
class LLMClient(Protocol):
    def complete(self, system: str, user: str) -> str:
        ...


class LLMError(Exception):
    pass


class OpenAICompatibleClient:
    """Minimal client for any OpenAI-compatible /chat/completions endpoint."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        temperature: float = 0.0,
        timeout: float = 60.0,
        json_mode: bool = True,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.timeout = timeout
        self.json_mode = json_mode

    def complete(self, system: str, user: str) -> str:
        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if self.json_mode:
            payload["response_format"] = {"type": "json_object"}

        try:
            resp = requests.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self.timeout,
            )
        except requests.RequestException as e:
            raise LLMError(f"LLM request failed: {e}") from e

        if resp.status_code != 200:
            raise LLMError(f"LLM returned {resp.status_code}: {resp.text[:300]}")

        data = resp.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as e:
            raise LLMError(f"Unexpected LLM response shape: {str(data)[:300]}") from e


def get_llm_client(
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
) -> LLMClient:
    """
    Build a client from arguments or environment:
      LLM_BASE_URL (default https://api.openai.com/v1)
      LLM_API_KEY  (required)
      LLM_MODEL    (required)
    """
    from app.config.settings import config

    cfg = config.llm
    base = base_url or os.environ.get("LLM_BASE_URL") or cfg.base_url
    key = (api_key or os.environ.get("LLM_API_KEY")
           or os.environ.get("OPENAI_API_KEY"))
    mdl = model or os.environ.get("LLM_MODEL") or cfg.model

    if not key:
        raise LLMError("LLM_API_KEY (or OPENAI_API_KEY) is not set")
    if not mdl:
        raise LLMError("LLM_MODEL is not set")

    return OpenAICompatibleClient(
        base_url=base, api_key=key, model=mdl,
        temperature=cfg.temperature, timeout=cfg.timeout,
    )
