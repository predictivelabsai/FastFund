"""AI proposal generation — turn a recommendation into a client-ready next step.

Given an SFO profile and a specific recommended service, draft a short, warm
proposal paragraph in a FastFund Family Office relationship-manager voice, plus a
concrete next step (a consultation). Degrades gracefully: with no LLM key it
returns a templated proposal so the feature still works in a demo.
"""
from __future__ import annotations

from rag import llm

_SYS = (
    "You are a FastFund Family Office relationship manager writing to the principal of "
    "a single family office. Draft a concise (3-5 sentence) proposal for ONE "
    "service: acknowledge their situation, explain specifically why this service "
    "fits THIS family, and close with a clear, low-pressure next step (a short "
    "consultation). Warm, precise, never salesy. Plain text, no preamble, no "
    "markdown headings."
)


def _template(profile: dict, service: dict, rationale: str) -> str:
    name = profile.get("family_name") or profile.get("name") or "the family"
    return (
        f"Dear {name},\n\n"
        f"Drawing on our understanding of your family office, we'd like to propose "
        f"FastFund's {service['name']}. {rationale} "
        f"We believe this is a natural next step alongside the services we already "
        f"provide, and would welcome a short introductory consultation to walk you "
        f"through how it would work for your circumstances.\n\n"
        f"With kind regards,\nYour FastFund Family Office team")


def draft(profile: dict, service: dict, rationale: str) -> str:
    """Return a proposal paragraph for (profile, service)."""
    if not llm.ai_available():
        return _template(profile, service, rationale)
    mix = ", ".join(f"{k.replace('_',' ')} {v}%" for k, v in (profile.get("asset_mix") or {}).items())
    user = (
        f"Family office: {profile.get('name')} (family {profile.get('family_name')}).\n"
        f"AUM ~${(profile.get('aum_usd') or 0)/1e6:,.0f}M, "
        f"{profile.get('generations','?')} generations, domicile {profile.get('domicile','?')}.\n"
        f"Current services: {', '.join(profile.get('current_services') or []) or 'none'}.\n"
        f"Asset mix: {mix or 'n/a'}.\n"
        f"Pain points: {', '.join(profile.get('pain_points') or []) or 'none stated'}.\n\n"
        f"Recommended service: {service['name']} — {service.get('description','')}.\n"
        f"Why it fits (internal note): {rationale}\n\n"
        f"Write the proposal.")
    out = llm.complete(_SYS, user, temperature=0.4)
    return out.strip() or _template(profile, service, rationale)
