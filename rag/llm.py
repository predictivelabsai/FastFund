"""LLM client + AI helpers for SFO Hub.

The conversational advisor and the AI half of the cross-sell scorer both talk to
an **OpenAI-compatible** chat endpoint. In dev that is xAI Grok; the production
target is **Azure AI Foundry** — same code, the endpoint/key come from env
(``LLM_PROVIDER=azure``). Without a key the app still runs: chat degrades to a
rules-only advisor and AI scoring is skipped, so the demo never hard-fails.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "xai").strip().lower()
GROK_MODEL = os.environ.get("GROK_MODEL", "grok-4-1-fast-reasoning")

# xAI (dev) — OpenAI-compatible.
_XAI_KEY = os.environ.get("XAI_API_KEY", "")
_XAI_BASE = os.environ.get("XAI_BASE_URL", "https://api.x.ai/v1")

# Azure AI Foundry (production) — also OpenAI-compatible.
_AZURE_ENDPOINT = os.environ.get("AZURE_AI_FOUNDRY_ENDPOINT", "")
_AZURE_KEY = os.environ.get("AZURE_AI_FOUNDRY_API_KEY", "")
_FOUNDRY_MODEL = os.environ.get("FOUNDRY_MODEL", "")

_llm = None


def _config() -> tuple[str, str, str]:
    """(api_key, base_url, model) for the active provider."""
    if LLM_PROVIDER == "azure":
        return _AZURE_KEY, _AZURE_ENDPOINT, (_FOUNDRY_MODEL or GROK_MODEL)
    return _XAI_KEY, _XAI_BASE, GROK_MODEL


def ai_available() -> bool:
    key, base, _ = _config()
    return bool(key and base)


def get_llm():
    """Lazily build an OpenAI-compatible chat client for the active provider."""
    global _llm
    if _llm is None:
        from langchain_openai import ChatOpenAI
        key, base, model = _config()
        _llm = ChatOpenAI(model=model, api_key=key, base_url=base,
                          temperature=0.2, timeout=120)
    return _llm


def complete(system: str, user: str, temperature: float = 0.2) -> str:
    """One-shot completion. Returns '' if AI is unavailable or errors."""
    if not ai_available():
        return ""
    try:
        from langchain_core.messages import SystemMessage, HumanMessage
        resp = get_llm().invoke([SystemMessage(content=system),
                                 HumanMessage(content=user)])
        return getattr(resp, "content", "") or ""
    except Exception as e:  # noqa: BLE001
        return f"[AI unavailable: {e}]"
