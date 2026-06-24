"""Postmark transactional email for TaxHub.

A thin wrapper over Postmark's HTTP API (no SDK — just ``requests``), modelled on
the liquidround integration. Used for team invites; deadline/alert digests are
deliberately NOT sent from here.

Env:
  POSTMARK_API_TOKEN  — Postmark server token (required to actually send)
  FROM_EMAIL          — verified sender address (default julian@predictivelabs.co.uk)
  FROM_NAME           — sender display name (default "TaxHub")

When no token is configured, send_email() is a no-op that returns
``{"skipped": True}`` so local/dev runs don't error.
"""
from __future__ import annotations

import json
import logging
import os

import requests

log = logging.getLogger(__name__)

POSTMARK_API_URL = "https://api.postmarkapp.com/email"
# Confirmed Sender Signature on the shared Postmark server. To send as
# julian@predictivelabs.co.uk, verify that address/domain in Postmark and set
# FROM_EMAIL accordingly.
DEFAULT_FROM = "info@liquidround.com"
DEFAULT_FROM_NAME = "TaxHub"


def send_email(*, to: str, subject: str, html_body: str, text_body: str = "",
               from_email: str | None = None, tag: str = "") -> dict:
    """Send one email via Postmark. Returns the API response dict, or
    ``{"skipped": True}`` when no token is set, or ``{"error": "..."}`` on failure."""
    token = os.getenv("POSTMARK_API_TOKEN")
    if not token:
        log.warning("POSTMARK_API_TOKEN not set — email to %s skipped", to)
        return {"skipped": True, "reason": "POSTMARK_API_TOKEN not set"}

    sender = from_email or os.getenv("FROM_EMAIL", DEFAULT_FROM)
    sender_name = os.getenv("FROM_NAME", DEFAULT_FROM_NAME)
    if sender_name and "<" not in sender:
        sender = f"{sender_name} <{sender}>"

    payload = {"From": sender, "To": to, "Subject": subject,
               "HtmlBody": html_body, "MessageStream": "outbound"}
    if text_body:
        payload["TextBody"] = text_body
    if tag:
        payload["Tag"] = tag

    try:
        resp = requests.post(POSTMARK_API_URL, timeout=15,
                             headers={"Accept": "application/json",
                                      "Content-Type": "application/json",
                                      "X-Postmark-Server-Token": token},
                             data=json.dumps(payload))
        result = resp.json()
        if resp.status_code == 200 and result.get("ErrorCode") == 0:
            log.info("Email sent to %s: %s", to, result.get("MessageID"))
        else:
            log.error("Postmark error to %s: %s", to, result)
        return result
    except Exception as e:  # noqa: BLE001
        log.exception("Postmark send failed")
        return {"error": str(e)}


def invite_email_html(*, invite_url: str, team_name: str, inviter: str,
                      role: str) -> str:
    """Branded HTML for a team invite — a clear call to accept and set a password."""
    return f"""\
<div style="font-family:-apple-system,'Segoe UI',Helvetica,Arial,sans-serif;max-width:520px;margin:0 auto;padding:40px 22px;color:#48484f">
  <div style="text-align:center;margin-bottom:28px">
    <div style="display:inline-block;width:46px;height:46px;border-radius:12px;
         background:linear-gradient(135deg,#ba2a84,#550055);color:#fff;font-weight:800;
         font-size:22px;line-height:46px">◆</div>
    <p style="font-size:19px;font-weight:700;color:#550055;margin:10px 0 0">TaxHub</p>
    <p style="font-size:12px;color:#7a7a85;margin:2px 0 0">Tax-law traceability &amp; filing intelligence</p>
  </div>
  <h2 style="font-size:20px;font-weight:600;color:#1E293B;margin:0 0 12px">
    You've been invited to {team_name}</h2>
  <p style="font-size:14px;line-height:1.65;color:#475569">
    <strong>{inviter}</strong> has invited you to join the <strong>{team_name}</strong>
    workspace on TaxHub as a <strong>{role}</strong>. TaxHub helps fund back-office teams
    find the right tax form, track filing obligations and deadlines, and validate
    FATCA/CRS readiness — every answer cited back to the source law.
  </p>
  <p style="font-size:14px;line-height:1.65;color:#475569">
    Click below to accept and set your password. You can also sign in with Google.
  </p>
  <div style="text-align:center;margin:30px 0">
    <a href="{invite_url}" style="display:inline-block;background:#6b1766;color:#fff;
       padding:13px 34px;border-radius:8px;font-weight:600;font-size:14px;
       text-decoration:none">Accept invitation</a>
  </div>
  <p style="font-size:12px;color:#94A3B8;line-height:1.5">
    Or paste this link into your browser:<br>
    <span style="color:#6b1766;word-break:break-all">{invite_url}</span>
  </p>
  <hr style="border:none;border-top:1px solid #E2E8F0;margin:24px 0">
  <p style="font-size:11px;color:#94A3B8">
    This invitation link expires in 7 days. If you weren't expecting it, you can ignore this email.<br>
    Predictive Labs · TaxHub
  </p>
</div>"""


def invite_email_text(*, invite_url: str, team_name: str, inviter: str, role: str) -> str:
    return (f"{inviter} has invited you to join the {team_name} workspace on TaxHub "
            f"as a {role}.\n\nAccept and set your password here (expires in 7 days):\n"
            f"{invite_url}\n\nYou can also sign in with Google.\n\n— TaxHub / Predictive Labs")
