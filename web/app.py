"""SFO Hub — the JTC Private Office AI advisor (FastHTML).

A multi-page agentic app mirroring TaxHub's structure:
- 3-pane home: client book · AI advisor (SSE) · profile + live recommendations.
- Multi-page views over a shared sidebar chrome: SFO detail, opportunities funnel,
  relationship graph (vis-network), pipeline calendar, coverage matrix, service
  catalogue, documents, an analytics dashboard (Plotly), help & technical guide.

Run:  python3.12 -m uvicorn web.app:app --host 0.0.0.0 --port 5021
"""
from __future__ import annotations

import json
import os
from datetime import date

from fasthtml.common import *
from starlette.responses import StreamingResponse, FileResponse

import sfostore as store
from agents import orchestrator
from agents.context import set_active_sfo
from engine import crosssell
from web import monitor, coverage as cov, graphdata

store.init_db()
# Auto-seed a fresh deployment so the demo always has data, even on an ephemeral
# volume (gated by env so local dev doesn't seed unexpectedly).
if os.environ.get("SFOHUB_AUTOSEED", "0") == "1":
    from data import synth
    synth.autoseed_if_empty(int(os.environ.get("SFOHUB_SEED_COUNT", "100")))
LOGIN_REQUIRED = os.environ.get("SFOHUB_PUBLIC", "0") != "1"

CATEGORY_LABELS = {
    "structuring": "Trusts & structuring", "fund": "Fund administration",
    "luxury": "Luxury assets", "reporting": "Reporting & Edge",
    "governance": "Family governance", "banking": "Banking & treasury",
    "compliance": "Regulatory & compliance", "advisory": "Advisory",
}
STAGE_COLOR = {"lead": "#7a7a85", "onboarding": "#b06b00", "client": "#1c7c44"}
KIND_COLOR = {"cross_sell": "#6b1766", "upsell": "#ba2a84"}
STATUS_COLOR = {"suggested": "#7a7a85", "presented": "#b06b00",
                "accepted": "#1c7c44", "booked": "#1c7c44", "declined": "#c0392b"}
STATUS_ORDER = ["suggested", "presented", "accepted", "booked", "declined"]
ACTION_LABELS = {"consultation": "Consultation", "proposal": "Send proposal",
                 "follow_up": "Follow up", "review": "Review"}

SUGGESTIONS = [
    "Tell me about this family's governance setup",
    "What should we offer them next?",
    "How do family offices typically allocate capital?",
    "Where are the gaps in their current services?",
    "Draft a proposal for the top opportunity",
]
SHORTCUTS = [
    ("Profile", "Summarise the open family", "Give me a profile summary of this family"),
    ("Recommend", "Ranked cross/upsell ideas", "What should we offer them next?"),
    ("Gaps", "Detect service gaps", "Where are the gaps in their current services?"),
    ("Benchmarks", "Industry context", "How do family offices typically allocate?"),
]

NAV = [
    ("/", "🏠 Advisor", "home"),
    ("/dashboard", "📊 Dashboard", "dashboard"),
    ("/clients", "👪 Client book", "clients"),
    ("/opportunities", "🎯 Pipeline", "opportunities"),
    ("/calendar", "🗓 Pipeline calendar", "calendar"),
    ("/coverage", "🧮 Coverage matrix", "coverage"),
    ("/graph", "🕸 Relationship graph", "graph"),
    ("/services", "🗂 Service catalogue", "services"),
    ("/documents", "📎 Documents", "documents"),
    ("/help", "❓ Help & guide", "help"),
]

CSS = Style("""
:root{--navy:#6b1766;--navy2:#550055;--accent:#ba2a84;--bg:#f5f6f4;--line:#e6e3ec;
--green:#1c7c44;--amber:#b06b00;--red:#c0392b;--text:#48484f;--muted:#7a7a85;--panel:#fff;}
*{box-sizing:border-box}html,body{height:100%}
body{margin:0;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
color:var(--text);background:var(--bg);line-height:1.5}
a{color:var(--navy2);text-decoration:none}a:hover{text-decoration:underline}
.app{display:grid;grid-template-columns:290px 1fr 430px;height:100vh;overflow:hidden}
.app-doc{display:grid;grid-template-columns:290px 1fr;height:100vh;overflow:hidden}
.pane{height:100vh;overflow-y:auto}
.left{background:var(--navy);color:#ece3ee;padding:0}
.left .brand{font-weight:700;font-size:18px;color:#fff;padding:16px 18px;border-bottom:1px solid #45114a}
.left .brand span{color:var(--accent)}
.left a{color:#ece3ee;display:block}
.section{padding:12px 16px;border-bottom:1px solid #45114a}
.section .lbl{font-size:11px;text-transform:uppercase;letter-spacing:.6px;color:#c9a3c6;margin-bottom:8px}
.navlink{padding:6px 8px;border-radius:6px;font-size:14px}.navlink:hover{background:#7a2474;text-decoration:none}
.navlink.on{background:#7a2474;font-weight:600}
.newchat{display:block;background:var(--accent);color:#fff;text-align:center;font-weight:600;
padding:9px;border-radius:8px;margin:12px 16px}
.newchat:hover{text-decoration:none;filter:brightness(1.05)}
.client{font-size:13px;padding:7px 9px;border-radius:7px;cursor:pointer;display:block;color:#ece3ee;border:1px solid transparent}
.client:hover{background:#7a2474;text-decoration:none}.client.active{background:#7a2474;border-color:var(--accent)}
.client .meta{font-size:11px;color:#c9a3c6}
.stagebadge{display:inline-block;border-radius:20px;padding:1px 8px;font-size:10px;font-weight:600;color:#fff}
.shortcut{font-size:12px;padding:4px 6px;border-radius:6px;cursor:pointer}
.shortcut:hover{background:#7a2474}.shortcut b{color:var(--accent)}
.center{display:flex;flex-direction:column;background:#fbfcfd}
.centerdoc{background:#fbfcfd}
.chead{padding:14px 22px;border-bottom:1px solid var(--line);font-weight:600;color:var(--navy);
display:flex;align-items:center;justify-content:space-between;gap:10px}
.msgs{flex:1;overflow-y:auto;padding:22px;display:flex;flex-direction:column;gap:14px}
.bubble{max-width:760px;padding:12px 16px;border-radius:12px;font-size:14.5px}
.bubble.user{align-self:flex-end;background:var(--navy);color:#fff;border-bottom-right-radius:3px}
.bubble.assistant{align-self:flex-start;background:#fff;border:1px solid var(--line);border-bottom-left-radius:3px}
.bubble.assistant pre{white-space:pre-wrap}
.toolchip{display:inline-block;font-size:11px;background:#f3ecf3;color:var(--navy2);border:1px solid #e0cfe0;
border-radius:20px;padding:1px 9px;margin:2px 4px 2px 0}
.cards{display:flex;flex-wrap:wrap;gap:8px;padding:8px 22px}
.cards-tray{border-top:1px solid var(--line);background:#fbfcfd;padding:6px 22px 12px}
.cards-tray .cards{padding:4px 0}
.cards-label{font-size:11px;text-transform:uppercase;letter-spacing:.6px;color:var(--muted);margin:2px 0}
.scard{background:#fff;border:1px solid var(--line);border-radius:10px;padding:8px 11px;font-size:12.5px;
cursor:pointer;max-width:340px}.scard:hover{border-color:var(--accent);color:var(--navy)}
.composer{padding:14px 22px;border-top:1px solid var(--line);background:#fff;display:flex;gap:10px}
.composer textarea{flex:1;resize:none;border:1px solid var(--line);border-radius:10px;padding:11px;font:inherit;height:48px}
.composer button{background:var(--navy);color:#fff;border:none;border-radius:10px;padding:0 20px;font-weight:600;cursor:pointer}
.right{background:#fff;border-left:1px solid var(--line);display:flex;flex-direction:column}
.right .rhead{padding:13px 18px;border-bottom:1px solid var(--line);font-weight:600;color:var(--navy);
display:flex;align-items:center;justify-content:space-between}
.rbody{flex:1;overflow-y:auto;padding:14px 18px}
.profilecard{border:1px solid var(--line);border-radius:10px;padding:13px;margin-bottom:14px;background:#fff}
.profilecard h3{margin:0 0 4px;color:var(--navy);font-size:16px}
.kv{font-size:12.5px;color:var(--muted);margin:2px 0}.kv b{color:var(--text)}
.chips{margin:6px 0}.chip{display:inline-block;background:#f3ecf3;color:var(--navy2);border-radius:20px;
padding:2px 9px;font-size:11px;margin:2px 4px 2px 0}
.bar{height:8px;border-radius:5px;background:#eee;overflow:hidden;display:flex;margin:6px 0}.bar i{display:block;height:100%}
.rec{border:1px solid var(--line);border-left:4px solid var(--accent);border-radius:8px;
padding:11px 13px;margin-bottom:11px;font-size:13px;background:#fff}
.rec .top{display:flex;justify-content:space-between;align-items:center;margin-bottom:4px}
.rec .name{font-weight:600;color:var(--navy)}.rec .val{color:var(--green);font-weight:600;font-size:12px}
.rec .why{color:var(--muted);font-size:12px;margin-top:4px}
.pill{display:inline-block;padding:1px 8px;border-radius:20px;font-size:10.5px;font-weight:600;text-transform:uppercase;color:#fff}
.btn{display:inline-block;background:var(--navy);color:#fff;padding:9px 16px;border-radius:7px;font-size:14px;border:none;cursor:pointer}
.btn.ghost{background:#fff;color:var(--navy);border:1px solid var(--line)}
.btn.sm{padding:4px 10px;font-size:12px}
.form{background:#fff;border:1px solid var(--line);border-radius:10px;padding:24px;max-width:380px;margin:60px auto}
.form input{width:100%;padding:10px;border:1px solid var(--line);border-radius:7px;margin:6px 0 14px}
.pwwrap{position:relative}.pwwrap input{padding-right:42px}
.pweye{position:absolute;right:6px;top:13px;background:none;border:none;cursor:pointer;
font-size:16px;line-height:1;padding:4px;color:var(--muted)}.pweye:hover{color:var(--navy)}
.wrap{max-width:1180px;margin:0 auto;padding:26px 30px}
h1{color:var(--navy);font-size:24px;margin:0 0 4px}h2{color:var(--navy);font-size:17px;margin:22px 0 8px}
table{width:100%;border-collapse:collapse;background:#fff;border:1px solid var(--line);border-radius:10px;overflow:hidden}
th,td{text-align:left;padding:9px 13px;border-bottom:1px solid var(--line);font-size:13.5px;vertical-align:top}
th{background:#fbfbfc;color:var(--muted);font-size:11px;text-transform:uppercase}
.statgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:14px;margin:16px 0}
.stat{background:#fff;border:1px solid var(--line);border-radius:10px;padding:16px}
.stat .n{font-size:26px;font-weight:700;color:var(--navy)}.stat .l{color:var(--muted);font-size:12px}
.cardrow{display:flex;gap:20px;flex-wrap:wrap}.card{background:#fff;border:1px solid var(--line);border-radius:10px;padding:16px;flex:1 1 340px;min-width:320px;max-width:100%;overflow:hidden}
.card .js-plotly-plot,.card .plot-container{max-width:100%}
.matrix td,.matrix th{padding:5px 7px;font-size:11.5px;text-align:center}
.matrix th.sfo,.matrix td.sfo{text-align:left;position:sticky;left:0;background:#fff;font-weight:600;max-width:200px}
.cell{display:inline-block;width:16px;height:16px;border-radius:4px}
.cell.held{background:#6b1766}.cell.rec{background:#ba2a84}.cell.gap{background:#efeaf3}
.filters{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:10px 0}
.filters select,.filters input{padding:7px;border:1px solid var(--line);border-radius:7px;font:inherit}
.urg{display:inline-block;border-radius:20px;padding:1px 9px;font-size:10.5px;font-weight:600;color:#fff}
/* Kanban pipeline */
.kanban{display:flex;gap:12px;overflow-x:auto;padding:8px 0 16px;align-items:flex-start}
.kcol{min-width:240px;max-width:300px;flex:1 0 240px;background:#fbfcfd;border:1px solid var(--line);
border-radius:10px;display:flex;flex-direction:column;max-height:calc(100vh - 190px)}
.kcol.drop{border-color:var(--accent);background:#faf2f8}
.kcol-head{display:flex;align-items:center;justify-content:space-between;padding:9px 12px;
border-bottom:3px solid var(--line);font-size:11.5px;font-weight:700;text-transform:uppercase;letter-spacing:.05em}
.kcol-count{color:var(--muted);font-weight:600;font-size:11px}
.kcol-body{padding:8px;overflow-y:auto;display:flex;flex-direction:column;gap:8px;flex:1;min-height:40px}
.kcard{background:#fff;border:1px solid var(--line);border-radius:8px;padding:9px 10px;font-size:12.5px;cursor:grab}
.kcard:hover{border-color:var(--accent)}.kcard.dragging{opacity:.4}
.kcard .kc-svc{font-weight:600;color:var(--navy)}.kcard .kc-sfo{font-size:11.5px}
.kcard .kc-meta{display:flex;justify-content:space-between;align-items:center;margin-top:6px;gap:6px}
.kcard .kc-val{color:var(--green);font-weight:600;font-size:11.5px}
.kcol-more{font-size:11px;color:var(--muted);text-align:center;padding:4px}
#net{height:calc(100vh - 56px);background:#fbfcfd}
.feed-item{border:1px solid var(--line);border-left:4px solid var(--accent);border-radius:8px;padding:10px 12px;margin-bottom:9px;font-size:13px}
.proposal{background:#faf7fb;border:1px solid var(--line);border-radius:8px;padding:12px;font-size:13px;white-space:pre-wrap;margin-top:6px}
""")

MARKED = Script(src="https://cdn.jsdelivr.net/npm/marked/marked.min.js")
PLOTLY = Script(src="https://cdn.plot.ly/plotly-2.35.2.min.js")
VISNET = Script(src="https://unpkg.com/vis-network@9.1.9/standalone/umd/vis-network.min.js")
# Faceted diamond gem in the JTC brand palette (navy pavilion, accent crown).
FAVICON = Link(rel="icon", type="image/svg+xml",
               href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' "
                    "viewBox='0 0 32 32'%3E"
                    "%3Cpolygon points='3,12 29,12 16,30' fill='%236b1766'/%3E"
                    "%3Cpolygon points='3,12 10,4 22,4 29,12' fill='%23ba2a84'/%3E"
                    "%3Cpolygon points='10,4 22,4 19,12 13,12' fill='%23d264a9'/%3E"
                    "%3Cpolygon points='13,12 19,12 16,30' fill='%23581460'/%3E"
                    "%3C/svg%3E")


def current_user(sess):
    return sess.get("uid") if sess else None


def user_email(sess):
    return sess.get("email", "") if sess else ""


def require(sess):
    if LOGIN_REQUIRED and not current_user(sess):
        return RedirectResponse("/login", status_code=303)
    return None


app, rt = fast_app(hdrs=(MARKED, FAVICON), secret_key=os.environ.get("APP_SECRET", "sfohub-2026"),
                   pico=False)


# ── Google OAuth (optional — enabled when client id + secret are set) ─────────
# Mirrors TaxHub: OIDC via Authlib, /auth/google → Google consent → /auth/callback.
# Set GOOGLE_ALLOWED_DOMAINS (comma-separated, e.g. "jtcgroup.com") to restrict
# sign-up to those email domains; existing accounts are always allowed. Unset ⇒
# any Google account may sign in.
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
GOOGLE_ALLOWED_DOMAINS = [
    d.strip().lower() for d in os.environ.get("GOOGLE_ALLOWED_DOMAINS", "").split(",") if d.strip()]
OAUTH_ENABLED = bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)

_oauth = None
if OAUTH_ENABLED:
    from authlib.integrations.starlette_client import OAuth as _AuthlibOAuth
    _oauth = _AuthlibOAuth()
    _oauth.register(
        name="google", client_id=GOOGLE_CLIENT_ID, client_secret=GOOGLE_CLIENT_SECRET,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"})

_GOOGLE_SVG = ('<svg width="18" height="18" viewBox="0 0 18 18" style="vertical-align:-4px;'
               'margin-right:9px"><path d="M17.64 9.2c0-.637-.057-1.251-.164-1.84H9v3.481h4.844'
               'c-.209 1.125-.843 2.078-1.796 2.717v2.258h2.908c1.702-1.567 2.684-3.874 '
               '2.684-6.615z" fill="#4285F4"/><path d="M9 18c2.43 0 4.467-.806 5.956-2.18l-2.908'
               '-2.259c-.806.54-1.837.86-3.048.86-2.344 0-4.328-1.584-5.036-3.711H.957v2.332A8.997 '
               '8.997 0 009 18z" fill="#34A853"/><path d="M3.964 10.71A5.41 5.41 0 013.682 9c0-.593'
               '.102-1.17.282-1.71V4.958H.957A8.996 8.996 0 000 9s.38 1.572.957 3.042l3.007-2.332z" '
               'fill="#FBBC05"/><path d="M9 3.58c1.321 0 2.508.454 3.44 1.345l2.582-2.58C13.463.891 '
               '11.426 0 9 0A8.997 8.997 0 00.957 4.958L3.964 7.29C4.672 5.163 6.656 3.58 9 3.58z" '
               'fill="#EA4335"/></svg>')


def _google_button():
    return Div(
        Div(Span(style="flex:1;height:1px;background:var(--line)"),
            Span("or", style="color:#9aa3ab;font-size:12px;padding:0 10px"),
            Span(style="flex:1;height:1px;background:var(--line)"),
            style="display:flex;align-items:center;margin:18px 0 14px"),
        A(NotStr(_GOOGLE_SVG), "Continue with Google", href="/auth/google",
          style="display:flex;align-items:center;justify-content:center;width:100%;"
                "padding:10px;border:1px solid var(--line);border-radius:7px;color:var(--text);"
                "font-size:14px;font-weight:600;text-decoration:none;background:#fff"))


@rt("/health")
def health():
    return JSONResponse({"status": "ok"})


# ── Formatting helpers ─────────────────────────────────────────────────────────
def money(n):
    n = float(n or 0)
    if n >= 1e9:
        return f"${n/1e9:,.1f}B"
    if n >= 1e6:
        return f"${n/1e6:,.0f}M"
    if n >= 1e3:
        return f"${n/1e3:,.0f}k"
    return f"${n:,.0f}"


def stage_badge(stage):
    return Span((stage or "lead").title(), cls="stagebadge",
                style=f"background:{STAGE_COLOR.get(stage, '#7a7a85')}")


def kind_badge(kind):
    return Span("Upsell" if kind == "upsell" else "Cross-sell", cls="pill",
                style=f"background:{KIND_COLOR.get(kind, '#6b1766')}")


def status_badge(status):
    return Span((status or "suggested").title(), cls="pill",
                style=f"background:{STATUS_COLOR.get(status, '#7a7a85')}")


def urgency_badge(urg):
    return Span(monitor.URGENCY_LABEL.get(urg, urg), cls="urg",
                style=f"background:{monitor.URGENCY_COLOR.get(urg, '#7a7a85')}")


def plotly(div_id, data, layout, height=300):
    # autosize lets Plotly size to the (flex-shrunk) container; we also resize
    # after layout settles so charts never render wider than their card.
    base = {"margin": {"t": 24, "r": 12, "b": 40, "l": 48}, "font": {"size": 12},
            "autosize": True, "paper_bgcolor": "rgba(0,0,0,0)", "plot_bgcolor": "rgba(0,0,0,0)",
            "colorway": ["#6b1766", "#ba2a84", "#9c5797", "#b06b00", "#1c7c44", "#c9a3c6"]}
    base.update(layout)
    base.pop("height", None)  # height comes from the div, not the layout (avoids overflow)
    return Div(Div(id=div_id, style=f"width:100%;height:{height}px"),
               Script(f"(function(){{var el=document.getElementById('{div_id}');"
                      f"Plotly.newPlot(el,{json.dumps(data)},{json.dumps(base)},"
                      f"{{displayModeBar:false,responsive:true}}).then(function(){{"
                      f"Plotly.Plots.resize(el);setTimeout(function(){{Plotly.Plots.resize(el);}},80);}});"
                      f"window.addEventListener('resize',function(){{Plotly.Plots.resize(el);}});}})();"))


# ── Auth ───────────────────────────────────────────────────────────────────────
@rt("/login", methods=["GET"])
def login_form(sess, error: str = ""):
    return Title("Sign in · SFO Hub"), CSS, Form(
        H2("SFO Hub"), P("Single-Family Office (SFO) AI Advisor", style="color:#6b7686"),
        (P(error, style="color:#c0392b") if error else ""),
        Input(name="email", placeholder="Email", type="email"),
        Div(Input(name="password", placeholder="Password", type="password", id="pw"),
            Button("👁", type="button", cls="pweye", id="pweye", onclick="togglePw(this)",
                   aria_label="Show password", aria_pressed="false"),
            cls="pwwrap"),
        Button("Sign in", cls="btn", style="width:100%"),
        Script("function togglePw(b){var i=document.getElementById('pw');"
               "var s=i.type==='password';i.type=s?'text':'password';"
               "b.textContent=s?'🙈':'👁';"
               "b.setAttribute('aria-label',s?'Hide password':'Show password');"
               "b.setAttribute('aria-pressed',s?'true':'false');i.focus();}"),
        (_google_button() if OAUTH_ENABLED else ""),
        method="post", action="/login", cls="form")


@rt("/login", methods=["POST"])
def login_submit(sess, email: str = "", password: str = ""):
    import bcrypt
    user = store.get_user_by_email((email or "").strip().lower())
    if user and user.get("password_hash") and bcrypt.checkpw(
            password.encode(), user["password_hash"].encode()):
        sess["uid"] = user["id"]
        sess["email"] = user.get("email") or email
        return RedirectResponse("/", status_code=303)
    return RedirectResponse("/login?error=Invalid+credentials", status_code=303)


@rt("/logout")
def logout(sess):
    sess.clear()
    return RedirectResponse("/login", status_code=303)


# ── Google OAuth: redirect to Google, then handle the callback ────────────────
if OAUTH_ENABLED:
    def _redirect_uri(request):
        # Honour the proxy (Coolify/Cloudflare terminate TLS) so the callback URL
        # matches the https:// URI registered in the GCP OAuth client.
        scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
        host = request.headers.get("host", request.url.netloc)
        return f"{scheme}://{host}/auth/callback"

    @rt("/auth/google", methods=["GET"])
    async def google_login(request):
        return await _oauth.google.authorize_redirect(request, _redirect_uri(request))

    @rt("/auth/callback", methods=["GET"])
    async def google_callback(request, sess):
        try:
            token = await _oauth.google.authorize_access_token(request)
        except Exception:  # noqa: BLE001
            return RedirectResponse("/login?error=Google+sign-in+failed", status_code=303)
        info = token.get("userinfo") or {}
        email = (info.get("email") or "").strip().lower()
        if not email or not info.get("email_verified", True):
            return RedirectResponse("/login?error=Google+did+not+return+a+verified+email",
                                    status_code=303)
        if (GOOGLE_ALLOWED_DOMAINS and email.split("@")[-1] not in GOOGLE_ALLOWED_DOMAINS
                and not store.get_user_by_email(email)):
            return RedirectResponse("/login?error=This+Google+account+is+not+permitted",
                                    status_code=303)
        user = store.get_or_create_oauth_user(email, info.get("name"))
        sess["uid"] = user["id"]
        sess["email"] = user["email"]
        return RedirectResponse("/", status_code=303)


# ── Shared chrome ──────────────────────────────────────────────────────────────
def left_pane(sess, active_id=None, ctx="home"):
    return Div(
        Div("SFO ", Span("Hub"), cls="brand"),
        A("+ New conversation", href="/", cls="newchat"),
        Div(Div("Navigate", cls="lbl"),
            *[A(lbl, href=href, cls="navlink on" if key == ctx else "navlink")
              for href, lbl, key in NAV],
            A("↪ Sign out", href="/logout", cls="navlink"),
            cls="section"),
        cls="pane left")


def Page(sess, *content, title="SFO Hub", ctx="home"):
    # PLOTLY is emitted before the content so the inline newPlot() scripts (which
    # run during parse) see a defined Plotly.
    return (Title(title), CSS, PLOTLY,
            Div(left_pane(sess, ctx=ctx),
                Div(Div(*content, cls="wrap"), cls="pane centerdoc"),
                cls="app-doc"))


# ── Recommendation / profile fragments ─────────────────────────────────────────
def mix_bar(asset_mix):
    colors = {"private_equity": "#6b1766", "public_equity": "#ba2a84",
              "real_estate": "#9c5797", "luxury": "#b06b00",
              "cash": "#1c7c44", "alternatives": "#c9a3c6"}
    segs = [I(style=f"width:{v}%;background:{colors.get(k,'#ccc')}", title=f"{k} {v}%")
            for k, v in (asset_mix or {}).items() if v]
    legend = " · ".join(f"{k.replace('_',' ')} {v}%" for k, v in (asset_mix or {}).items() if v)
    return Div(Div(*segs, cls="bar"), Div(legend, cls="kv"))


def rec_card(r, with_actions=False):
    body = [
        Div(Span(r["service_name"], cls="name"), kind_badge(r["kind"]), cls="top"),
        Div(f"Fit {r['score']:.0%}", Span(" · "),
            Span(f"~{money(r['est_value_usd'])}/yr", cls="val"),
            Span(f" · {r.get('status','suggested')}", cls="kv"), Span(" · "),
            Span(r.get("source", ""), cls="kv")),
        Div(r["rationale"], cls="why"),
    ]
    if r.get("proposal"):
        body.append(Div(r["proposal"], cls="proposal"))
    if with_actions:
        rid = r["id"]
        body.append(Div(
            Button("Draft proposal", cls="btn ghost sm",
                   hx_post=f"/rec/{rid}/proposal", hx_target=f"#rec-{rid}", hx_swap="outerHTML"),
            " ",
            Button("Book consultation", cls="btn ghost sm",
                   hx_post=f"/rec/{rid}/book", hx_target=f"#rec-{rid}", hx_swap="outerHTML"),
            " ",
            *[Button(lbl, cls="btn ghost sm",
                     hx_post=f"/rec/{rid}/status?status={st}", hx_target=f"#rec-{rid}",
                     hx_swap="outerHTML")
              for st, lbl in [("accepted", "Accept"), ("declined", "Decline")]],
            style="margin-top:8px"))
    return Div(*body, cls="rec", id=f"rec-{r['id']}")


def right_pane(sfo_id=None):
    if sfo_id is None:
        return Div(Div("Workspace", cls="rhead"),
                   Div(P("Select a family office from the client book to see their "
                         "profile and live cross/upsell recommendations.", cls="kv"),
                       cls="rbody"), cls="pane right", id="right")
    sfo = store.get_sfo(sfo_id)
    if not sfo:
        return right_pane(None)
    recs = store.list_recommendations(sfo_id=sfo_id, limit=20)
    recs_rendered = ([rec_card(r) for r in recs] if recs else
                     [P("No recommendations yet.", cls="kv"),
                      Button("Generate recommendations", cls="btn",
                             hx_post=f"/recommend/{sfo_id}", hx_target="#right", hx_swap="outerHTML")])
    return Div(
        Div(A(sfo["name"], href=f"/sfo/{sfo_id}", style="color:var(--navy)"),
            A("Open ↗", href=f"/sfo/{sfo_id}", style="font-size:12px"), cls="rhead"),
        Div(Div(H3(sfo["name"]),
                Div("AUM ", B(money(sfo.get("aum_usd"))),
                    f" · {sfo.get('family_size','—')} members · "
                    f"{sfo.get('generations','—')} generations", cls="kv"),
                Div(f"Domicile {sfo.get('domicile','—')} · ", stage_badge(sfo.get("stage", "lead")), cls="kv"),
                Div("Current services:", cls="kv", style="margin-top:8px"),
                Div(*[Span(s.replace("_", " "), cls="chip") for s in (sfo.get("current_services") or [])]
                    or [Span("none", cls="chip")], cls="chips"),
                Div("Asset mix:", cls="kv", style="margin-top:6px"), mix_bar(sfo.get("asset_mix")),
                Div("Pain points:", cls="kv", style="margin-top:6px"),
                Div(*[Span(p, cls="chip") for p in (sfo.get("pain_points") or [])], cls="chips"),
                cls="profilecard"),
            Div(B("Recommendations"),
                Button("↻ Regenerate", cls="btn ghost sm", style="float:right",
                       hx_post=f"/recommend/{sfo_id}", hx_target="#right", hx_swap="outerHTML"),
                style="margin-bottom:8px;overflow:auto"),
            *recs_rendered, cls="rbody"),
        cls="pane right", id="right")


# ── Home (3-pane) ──────────────────────────────────────────────────────────────
def chat_script(sfo_id=None):
    sfo_q = f"?sfo={sfo_id}" if sfo_id else ""
    return Script("""
function sfoSet(t){const b=document.getElementById('box');b.value=t;b.focus();}
function mk(html){return html.replace(/\\[service:(\\d+)\\]/g,(m,id)=>` <a href="/service/${id}">↗</a>`)
  .replace(/\\[sfo:(\\d+)\\]/g,(m,id)=>` <a href="/sfo/${id}">↗</a>`).replace(/\\[rec:(\\d+)\\]/g,'');}
async function sfoSend(){
  const box=document.getElementById('box'); const q=box.value.trim(); if(!q) return;
  box.value=''; const msgs=document.getElementById('msgs');
  const u=document.createElement('div'); u.className='bubble user'; u.textContent=q; msgs.appendChild(u);
  const a=document.createElement('div'); a.className='bubble assistant'; a.dataset.raw=''; msgs.appendChild(a);
  const tools=document.createElement('div'); a.appendChild(tools);
  const body=document.createElement('div'); a.appendChild(body); msgs.scrollTop=msgs.scrollHeight;
  const resp=await fetch('/chat__SFO__',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({q})});
  const reader=resp.body.getReader(); const dec=new TextDecoder(); let buf='';
  while(true){const{done,value}=await reader.read(); if(done)break; buf+=dec.decode(value,{stream:true});
    let parts=buf.split('\\n\\n'); buf=parts.pop();
    for(const p of parts){ const lines=p.split('\\n'); let ev='',data='';
      for(const l of lines){ if(l.startsWith('event: '))ev=l.slice(7); if(l.startsWith('data: '))data=l.slice(6);}
      if(!data)continue; let d={}; try{d=JSON.parse(data);}catch(e){continue;}
      if(ev==='token'){ a.dataset.raw+=d.text; body.innerHTML=mk(marked.parse(a.dataset.raw)); }
      else if(ev==='tool_start'){ const c=document.createElement('span'); c.className='toolchip'; c.textContent='⚙ '+d.name; tools.appendChild(c);}
      else if(ev==='done'){ if(window.__sfoid){ htmx.ajax('GET','/panel/'+window.__sfoid,{target:'#right',swap:'outerHTML'}); } }
      else if(ev==='error'){ body.innerHTML='<i>'+d.message+'</i>'; }
      msgs.scrollTop=msgs.scrollHeight;
    }
  }
}
document.addEventListener('keydown',e=>{ if(e.target.id==='box'&&e.key==='Enter'&&!e.shiftKey){e.preventDefault();sfoSend();}});
""".replace("__SFO__", sfo_q))


def _suggestions(sfo_id):
    """The standard prompts, plus a few that name real families from the book so
    the demo feels concrete."""
    base = list(SUGGESTIONS)
    sfos = store.list_sfos(limit=12)
    if sfo_id is None and sfos:
        picks = sfos[:3]
        templates = ["Summarise {n}", "What should we offer {n} next?",
                     "Where are the gaps for {n}?"]
        named = [t.format(n=p["name"]) for t, p in zip(templates, picks)]
        return base + named
    return base


@rt("/")
def index(sess, sfo: int = 0):
    if (r := require(sess)):
        return r
    sfo_id = sfo or None
    greeting = ("Hello — I'm your Single-Family Office advisor. Open a family from "
                "the client book and ask me anything: their setup, where the gaps "
                "are, and what we should offer them next.")
    cards = Div(Div("Try asking", cls="cards-label"),
                Div(*[Div(s, cls="scard", onclick=f"sfoSet({s!r})")
                      for s in _suggestions(sfo_id)], cls="cards"),
                cls="cards-tray")
    return (Title("SFO Hub"), CSS,
            Div(left_pane(sess, sfo_id, ctx="home"),
                Div(Div("AI Assistant", cls="chead"),
                    Div(Div(greeting, cls="bubble assistant"), cls="msgs", id="msgs"),
                    Div(Textarea(placeholder="Message the advisor…", id="box", name="q"),
                        Button("Send", onclick="sfoSend()"), cls="composer"),
                    cards,
                    cls="pane center"),
                right_pane(sfo_id), cls="app"),
            Script(f"window.__sfoid={sfo_id if sfo_id else 'null'};"), chat_script(sfo_id))


@rt("/panel/{sfo_id}")
def panel(sess, sfo_id: int):
    if require(sess):
        return RedirectResponse("/login", status_code=303)
    return right_pane(sfo_id)


@rt("/recommend/{sfo_id}", methods=["POST"])
def recommend(sess, sfo_id: int):
    if require(sess):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    set_active_sfo(sfo_id)
    crosssell.recommend(sfo_id, persist=True, use_ai=True)
    return right_pane(sfo_id)


@rt("/chat", methods=["POST"])
async def chat(sess, request, sfo: int = 0):
    if require(sess):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    data = await request.json()
    question = (data.get("q") or "").strip()
    set_active_sfo(sfo or None)
    email = user_email(sess)

    async def gen():
        convs = store.list_conversations(user_email=email, sfo_id=sfo or None, limit=1)
        cid = convs[0]["id"] if convs else store.create_conversation(
            email, sfo_id=sfo or None, title=question[:40])
        store.add_message(cid, "user", question)
        buf = []
        async for chunk in orchestrator.astream(question):
            if "event: token" in chunk and '"text"' in chunk:
                try:
                    buf.append(json.loads(chunk.split("data: ", 1)[1])["text"])
                except Exception:  # noqa: BLE001
                    pass
            yield chunk
        store.add_message(cid, "assistant", "".join(buf))

    return StreamingResponse(gen(), media_type="text/event-stream")


# ── Recommendation actions (HTMX) ──────────────────────────────────────────────
def _rec(rec_id):
    return next((r for r in store.list_recommendations(limit=10000) if r["id"] == rec_id), None)


@rt("/rec/{rec_id}/status", methods=["POST"])
def rec_status(sess, rec_id: int, status: str = ""):
    if require(sess):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    store.set_recommendation_status(rec_id, status or "suggested")
    return rec_card(_rec(rec_id), with_actions=True)


@rt("/rec/{rec_id}/proposal", methods=["POST"])
def rec_proposal(sess, rec_id: int):
    if require(sess):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    crosssell.generate_proposal(rec_id)
    store.set_recommendation_status(rec_id, "presented")
    return rec_card(_rec(rec_id), with_actions=True)


@rt("/rec/{rec_id}/book", methods=["POST"])
def rec_book(sess, rec_id: int):
    if require(sess):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    r = _rec(rec_id)
    if r:
        crosssell.schedule_action(r["sfo_id"], "consultation",
                                  f"Consultation — {r['service_name']}", 14,
                                  recommendation_id=rec_id)
        store.set_recommendation_status(rec_id, "accepted")
    return rec_card(_rec(rec_id), with_actions=True)


# ── SFO detail ─────────────────────────────────────────────────────────────────
@rt("/sfo/{sfo_id}")
def sfo_detail(sess, sfo_id: int):
    if (r := require(sess)):
        return r
    sfo = store.get_sfo(sfo_id)
    if not sfo:
        return RedirectResponse("/", status_code=303)
    recs = store.list_recommendations(sfo_id=sfo_id, limit=30)
    members = store.list_family_members(sfo_id)
    actions = [monitor.annotate(a, date.today()) for a in store.list_next_actions(sfo_id=sfo_id)]
    docs = store.list_documents(sfo_id=sfo_id)
    holdings = store.list_holdings(sfo_id)
    txns = store.list_transactions(sfo_id, limit=12)
    convs = store.list_conversations(sfo_id=sfo_id, limit=10)
    mix = sfo.get("asset_mix") or {}
    donut = plotly("mix", [{"type": "pie", "hole": 0.55,
                            "labels": [k.replace("_", " ") for k in mix],
                            "values": list(mix.values()), "textinfo": "label+percent"}],
                   {"showlegend": False, "margin": {"t": 10, "b": 10, "l": 10, "r": 10}})
    pipeline = sum(r["est_value_usd"] for r in recs if r["status"] in ("accepted", "booked"))
    return Page(sess,
        Div(A("← Advisor", href=f"/?sfo={sfo_id}"), style="margin-bottom:8px"),
        H1(sfo["name"]),
        P(f"Family {sfo.get('family_name','—')} · ", stage_badge(sfo.get("stage", "lead")),
          f" · client ref {sfo.get('client_ref','—')}", style="color:var(--muted)"),
        Div(Div(Div(money(sfo.get("aum_usd")), cls="n"), Div("AUM", cls="l"), cls="stat"),
            Div(Div(str(sfo.get("generations", "—")), cls="n"), Div("Generations", cls="l"), cls="stat"),
            Div(Div(str(sfo.get("family_size", "—")), cls="n"), Div("Members", cls="l"), cls="stat"),
            Div(Div(money(pipeline), cls="n"), Div("Pipeline (acc/booked)", cls="l"), cls="stat"),
            cls="statgrid"),
        Div(Div(H2("Asset allocation"), donut, cls="card"),
            Div(H2("Profile"),
                Div("Domicile: ", B(sfo.get("domicile", "—")),
                    f" · jurisdictions {', '.join(sfo.get('jurisdictions') or []) or '—'}", cls="kv"),
                Div("Current services:", cls="kv", style="margin-top:8px"),
                Div(*[Span(s.replace("_", " "), cls="chip") for s in (sfo.get("current_services") or [])]
                    or [Span("none", cls="chip")], cls="chips"),
                Div("Pain points:", cls="kv", style="margin-top:8px"),
                Div(*[Span(p, cls="chip") for p in (sfo.get("pain_points") or [])], cls="chips"),
                Div("Contact: ", B(sfo.get("contact_name", "—")),
                    f" ({sfo.get('contact_email','—')})", cls="kv", style="margin-top:8px"),
                cls="card"),
            cls="cardrow"),
        H2("Family members"),
        (Table(Tr(Th("Name"), Th("Role"), Th("Generation"), Th("Age")),
               *[Tr(Td(m["name"]), Td((m.get("role") or "").replace("_", " ").title()),
                    Td(str(m.get("generation") or "—")), Td(str(m.get("age") or "—")))
                 for m in members]) if members else P("No members recorded.", cls="kv")),
        H2("Portfolio holdings"),
        (Table(Tr(Th("Holding"), Th("Asset class"), Th("Value"), Th("Performance")),
               *[Tr(Td(h["name"]), Td((h.get("asset_class") or "").replace("_", " ")),
                    Td(money(h.get("value_usd"))),
                    Td(Span(f"{h.get('performance_pct',0):+.1f}%",
                            style=f"color:{'#1c7c44' if (h.get('performance_pct') or 0) >= 0 else '#c0392b'};font-weight:600")))
                 for h in holdings]) if holdings
         else P("No holdings recorded.", cls="kv")),
        H2("Recent transactions"),
        (Table(Tr(Th("Date"), Th("Type"), Th("Amount"), Th("Description")),
               *[Tr(Td((t.get("txn_date") or "")[:10]),
                    Td((t.get("kind") or "").replace("_", " ")),
                    Td(Span(money(abs(t.get("amount_usd") or 0)) if (t.get("amount_usd") or 0) >= 0
                            else f"-{money(abs(t.get('amount_usd') or 0))}",
                            style=f"color:{'#1c7c44' if (t.get('amount_usd') or 0) >= 0 else '#c0392b'}")),
                    Td(t.get("description"), style="color:var(--muted)"))
                 for t in txns]) if txns else P("No transactions recorded.", cls="kv")),
        Div(H2("Recommendations"),
            Button("↻ Regenerate", cls="btn ghost sm", style="float:right",
                   hx_post=f"/recommend/{sfo_id}/page", hx_target="#recs", hx_swap="innerHTML"),
            style="overflow:auto"),
        Div(*[rec_card(r, with_actions=True) for r in recs]
            or [P("No recommendations yet — click Regenerate.", cls="kv")], id="recs"),
        H2("Next actions"),
        (Table(Tr(Th("Due"), Th("Action"), Th("Title"), Th("Urgency"), Th("")),
               *[Tr(Td(a.get("due_date") or "—"),
                    Td(ACTION_LABELS.get(a.get("kind"), a.get("kind"))),
                    Td(a.get("title")), Td(urgency_badge(a["urgency"])),
                    Td(Button("Done", cls="btn ghost sm",
                              hx_post=f"/action/{a['id']}/done", hx_target="closest tr", hx_swap="outerHTML")))
                 for a in actions]) if actions else P("No scheduled actions.", cls="kv")),
        H2("Documents"),
        (Ul(*[Li(A(d["name"], href=f"/document/{d['id']}/file"),
                 f" · {d.get('doc_type','')} · {(d.get('byte_size') or 0)//1024}kB") for d in docs])
         if docs else P("No documents. Upload from the Documents page.", cls="kv")),
        H2("Conversation history"),
        (Ul(*[Li(A(c["title"] or "Conversation", href=f"/?sfo={sfo_id}"),
                 f" · {(c.get('updated_at') or '')[:10]}") for c in convs])
         if convs else P("No conversations yet.", cls="kv")),
        title=f"{sfo['name']} · SFO Hub", ctx="home")


@rt("/recommend/{sfo_id}/page", methods=["POST"])
def recommend_page(sess, sfo_id: int):
    if require(sess):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    set_active_sfo(sfo_id)
    crosssell.recommend(sfo_id, persist=True, use_ai=True)
    recs = store.list_recommendations(sfo_id=sfo_id, limit=30)
    return Div(*[rec_card(r, with_actions=True) for r in recs])


@rt("/action/{action_id}/done", methods=["POST"])
def action_done(sess, action_id: int):
    if require(sess):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    store.set_next_action_status(action_id, "done")
    return Tr(Td("✓ done", colspan="5", style="color:var(--green)"))


PAIN_OPTIONS = [
    "succession planning conflict between generations",
    "no formal governance framework",
    "consolidated reporting complexity across entities",
    "regulatory and AEOI (FATCA/CRS) reporting burden",
    "fragmented banking and cash-management relationships",
    "luxury asset (yacht/art) administration overhead",
    "limited visibility into private-equity holdings",
    "preparing the next generation for stewardship",
]
ALLOC_CLASSES = ["private_equity", "public_equity", "real_estate", "luxury", "cash", "alternatives"]


# ── New-lead onboarding wizard ───────────────────────────────────────────────────
@rt("/onboard", methods=["GET"])
def onboard_form(sess):
    if (r := require(sess)):
        return r
    svcs = store.list_services(limit=100)
    domiciles = ["JE", "GG", "LU", "IE", "KY", "VG", "GB", "CH", "SG", "US", "AE"]
    field = lambda label, inp: Div(Div(label, cls="kv", style="margin-top:10px"), inp)  # noqa: E731
    return Page(sess, H1("New family office — onboarding"),
        P("Capture a new lead's profile; we'll build a tailored service roadmap.",
          style="color:var(--muted)"),
        Form(
            Div(field("Family office name *", Input(name="name", required="required",
                      placeholder="e.g. Castellan Family Office", style="width:100%;padding:9px")),
                field("Family surname", Input(name="family_name", placeholder="Castellan",
                      style="width:100%;padding:9px")),
                style="display:flex;gap:16px"),
            Div(field("AUM (US$ millions) *", Input(name="aum_m", type="number", value="500",
                      style="width:100%;padding:9px")),
                field("Generations", Input(name="generations", type="number", value="2",
                      style="width:100%;padding:9px")),
                field("Family size", Input(name="family_size", type="number", value="6",
                      style="width:100%;padding:9px")),
                field("Domicile", Select(*[Option(d, value=d) for d in domiciles], name="domicile",
                      style="width:100%;padding:9px")),
                style="display:flex;gap:16px"),
            H2("Current JTC services"),
            Div(*[Label(Input(type="checkbox", name="services", value=s["key"]),
                        f" {s['name']}", style="display:inline-block;width:48%;font-size:13px;margin:3px 0")
                  for s in svcs]),
            H2("Asset allocation (%)"),
            Div(*[field(c.replace("_", " ").title(),
                        Input(name=f"alloc_{c}", type="number", value="0", style="width:100%;padding:9px"))
                  for c in ALLOC_CLASSES], style="display:flex;gap:12px;flex-wrap:wrap"),
            H2("Pain points"),
            Div(*[Label(Input(type="checkbox", name="pains", value=p), f" {p}",
                        style="display:block;font-size:13px;margin:3px 0") for p in PAIN_OPTIONS]),
            Div(Button("Build profile & roadmap", cls="btn", style="margin-top:16px"),
                style="margin-top:8px"),
            method="post", action="/onboard"),
        title="Onboarding · SFO Hub", ctx="clients")


@rt("/onboard", methods=["POST"])
async def onboard_submit(sess, request):
    if require(sess):
        return RedirectResponse("/login", status_code=303)
    form = await request.form()
    aum = float(form.get("aum_m") or 0) * 1e6
    mix = {c: int(form.get(f"alloc_{c}") or 0) for c in ALLOC_CLASSES
           if int(form.get(f"alloc_{c}") or 0) > 0}
    count = store.count_sfos()
    sfo_id = store.upsert_sfo({
        "client_ref": f"SFO-{count + 1000:04d}",
        "name": form.get("name") or "New Family Office",
        "family_name": form.get("family_name") or (form.get("name") or "").replace(" Family Office", ""),
        "aum_usd": aum, "generations": int(form.get("generations") or 1),
        "family_size": int(form.get("family_size") or 1), "domicile": form.get("domicile") or "JE",
        "jurisdictions": [form.get("domicile") or "JE"],
        "current_services": form.getlist("services"), "asset_mix": mix,
        "pain_points": form.getlist("pains"), "stage": "lead",
    })
    set_active_sfo(sfo_id)
    crosssell.recommend(sfo_id, persist=True, use_ai=True)
    return RedirectResponse(f"/sfo/{sfo_id}", status_code=303)


# ── Client book ────────────────────────────────────────────────────────────────
@rt("/clients")
def clients(sess, stage: str = ""):
    if (r := require(sess)):
        return r
    sfos = store.list_sfos(limit=500)
    if stage:
        sfos = [s for s in sfos if s.get("stage") == stage]
    chip = lambda lbl, q, on: A(lbl, href=q, cls="urg",  # noqa: E731
                                style=f"background:{'#6b1766' if on else '#9a93a6'};margin-right:6px")
    filters = Div(chip("All", "/clients", not stage),
                  *[chip(s.title(), f"/clients?stage={s}", stage == s)
                    for s in ("lead", "onboarding", "client")],
                  style="margin:10px 0")
    rows = [Tr(Td(A(B(s["name"]), href=f"/?sfo={s['id']}")),
               Td(money(s.get("aum_usd"))), Td(s.get("domicile", "—")),
               Td(stage_badge(s.get("stage", "lead"))),
               Td(f"{s.get('generations','—')}g · {s.get('family_size','—')} members",
                  style="color:var(--muted)"),
               Td(", ".join((s.get("current_services") or [])[:4]) or "—",
                  style="color:var(--muted)"),
               Td(A("Open ↗", href=f"/sfo/{s['id']}")))
            for s in sfos]
    return Page(sess,
        Div(H1("Client book", style="display:inline-block"),
            A("+ New family office", href="/onboard", cls="btn sm", style="float:right;margin-top:6px"),
            style="overflow:auto"),
        P(f"{len(sfos)} family offices · click a name to advise, or Open for the full profile",
          style="color:var(--muted)"),
        filters,
        Table(Tr(Th("Family office"), Th("AUM"), Th("Domicile"), Th("Stage"),
                 Th("Family"), Th("Current services"), Th("")), *rows),
        title="Client book · SFO Hub", ctx="clients")


# ── Pipeline (kanban) ──────────────────────────────────────────────────────────
_KANBAN_CAP = 80  # cards rendered per column


def _kcard(r):
    return Div(
        Div(r["service_name"], cls="kc-svc"),
        Div(A(r["sfo_name"], href=f"/sfo/{r['sfo_id']}", draggable="false"), cls="kc-sfo"),
        Div(kind_badge(r["kind"]),
            Span(f"{r['score']:.0%} · ", Span(money(r["est_value_usd"]), cls="kc-val")),
            cls="kc-meta"),
        cls="kcard", draggable="true",
        **{"data-recid": str(r["id"]), "data-status": r["status"]})


KANBAN_JS = Script("""
let dragId=null;
function kbInit(){
  document.querySelectorAll('.kcard').forEach(c=>{
    c.addEventListener('dragstart',e=>{dragId=c.dataset.recid;c.classList.add('dragging');e.dataTransfer.effectAllowed='move';});
    c.addEventListener('dragend',()=>c.classList.remove('dragging'));
  });
  document.querySelectorAll('.kcol').forEach(col=>{
    col.addEventListener('dragover',e=>{e.preventDefault();col.classList.add('drop');});
    col.addEventListener('dragleave',()=>col.classList.remove('drop'));
    col.addEventListener('drop',async e=>{
      e.preventDefault();col.classList.remove('drop');
      const card=document.querySelector('.kcard[data-recid="'+dragId+'"]');
      const status=col.dataset.status;
      if(card && card.dataset.status!==status){
        col.querySelector('.kcol-body').appendChild(card);
        card.dataset.status=status;
        kbCounts();
        try{await fetch('/rec/'+dragId+'/status?status='+status,{method:'POST'});}catch(e){}
      }
    });
  });
}
function kbCounts(){document.querySelectorAll('.kcol').forEach(c=>{
  const n=c.querySelectorAll('.kcard').length; const el=c.querySelector('.kcol-count'); if(el)el.textContent=n;});}
document.addEventListener('DOMContentLoaded',kbInit);
""")


@rt("/opportunities")
def opportunities(sess, kind: str = "", category: str = ""):
    if (r := require(sess)):
        return r
    recs = store.list_recommendations(limit=10000)
    if kind:
        recs = [r for r in recs if r["kind"] == kind]
    if category:
        recs = [r for r in recs if r.get("service_category") == category]
    total_val = sum(r["est_value_usd"] for r in recs)
    by_status = {s: [r for r in recs if r["status"] == s] for s in STATUS_ORDER}

    cols = []
    for s in STATUS_ORDER:
        items = by_status[s]
        body = [_kcard(r) for r in items[:_KANBAN_CAP]]
        if len(items) > _KANBAN_CAP:
            body.append(Div(f"+ {len(items) - _KANBAN_CAP} more", cls="kcol-more"))
        cols.append(Div(
            Div(Span(s.title()), Span(str(len(items)), cls="kcol-count"),
                cls="kcol-head", style=f"border-bottom-color:{STATUS_COLOR.get(s)}"),
            Div(*body, cls="kcol-body"),
            cls="kcol", **{"data-status": s}))

    chip = lambda lbl, q, on: A(lbl, href=q, cls="urg",  # noqa: E731
                                style=f"background:{'#6b1766' if on else '#9a93a6'};margin-right:6px")
    filters = Div(
        chip("All", "/opportunities", not kind),
        chip("Cross-sell", "/opportunities?kind=cross_sell", kind == "cross_sell"),
        chip("Upsell", "/opportunities?kind=upsell", kind == "upsell"),
        Span(" · ", style="color:var(--muted)"),
        *[chip(v, f"/opportunities?category={k}", category == k) for k, v in CATEGORY_LABELS.items()],
        style="margin:10px 0;display:flex;flex-wrap:wrap;align-items:center")

    return Page(sess,
        H1("Pipeline"),
        P(f"{len(recs)} recommendations · {money(total_val)} total estimated annual value · "
          "drag a card between stages to advance it", style="color:var(--muted)"),
        filters,
        Div(*cols, cls="kanban"),
        KANBAN_JS,
        title="Pipeline · SFO Hub", ctx="opportunities")


# ── Pipeline calendar ──────────────────────────────────────────────────────────
@rt("/calendar")
def calendar(sess, urg: str = ""):
    if (r := require(sess)):
        return r
    rows = monitor.pipeline_calendar(store, date.today())
    if urg:
        rows = [a for a in rows if a["urgency"] == urg]
    from collections import Counter
    counts = Counter(a["urgency"] for a in monitor.pipeline_calendar(store, date.today()))
    chips = [A(f"{monitor.URGENCY_LABEL[u]} ({counts.get(u,0)})", href=f"/calendar?urg={u}",
               cls="urg", style=f"background:{monitor.URGENCY_COLOR[u]};margin-right:6px")
             for u in ["overdue", "due_soon", "upcoming", "scheduled"]]
    table = (Table(Tr(Th("Due"), Th("In"), Th("Family"), Th("Action"), Th("Title"), Th("Urgency"), Th("")),
                   *[Tr(Td(a.get("due_date") or "—"),
                        Td("—" if a["days_out"] is None else
                           ("today" if a["days_out"] == 0 else
                            (f"{-a['days_out']}d ago" if a["days_out"] < 0 else f"{a['days_out']}d"))),
                        Td(A(a.get("sfo_name") or "—", href=f"/sfo/{a['sfo_id']}")),
                        Td(ACTION_LABELS.get(a.get("kind"), a.get("kind"))),
                        Td(a.get("title")), Td(urgency_badge(a["urgency"])),
                        Td(Button("Done", cls="btn ghost sm",
                                  hx_post=f"/action/{a['id']}/done", hx_target="closest tr", hx_swap="outerHTML")))
                     for a in rows]) if rows else P("Nothing scheduled.", cls="kv"))
    return Page(sess,
        Div(H1("Pipeline calendar", style="display:inline-block"),
            A("📅 Subscribe / export (iCal)", href="/calendar-ics", cls="btn sm",
              style="float:right;margin-top:6px"), style="overflow:auto"),
        P("Scheduled consultations, proposals and follow-ups across the book.", style="color:var(--muted)"),
        Div(A("All", href="/calendar", cls="urg", style="background:#6b1766;margin-right:6px"), *chips,
            style="margin:12px 0"),
        table, title="Calendar · SFO Hub", ctx="calendar")


def _ics(actions) -> str:
    """Build a VCALENDAR of open next-actions (all-day events)."""
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//SFO Hub//Pipeline//EN",
             "CALSCALE:GREGORIAN", "METHOD:PUBLISH", "X-WR-CALNAME:SFO Hub pipeline"]
    stamp = store.utcnow().replace("-", "").replace(":", "").split("+")[0] + "Z"
    for a in actions:
        d = (a.get("due_date") or "")[:10].replace("-", "")
        if not d:
            continue
        title = f"{ACTION_LABELS.get(a.get('kind'), a.get('kind') or 'Action')}: {a.get('title','')}"
        lines += ["BEGIN:VEVENT", f"UID:action-{a.get('id')}@sfohub.predictivelabs.ai",
                  f"DTSTAMP:{stamp}", f"DTSTART;VALUE=DATE:{d}",
                  f"SUMMARY:{title}", f"DESCRIPTION:Family: {a.get('sfo_name','')}", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines)


@rt("/calendar-ics")
def calendar_ics(sess, sfo: int = 0):
    if require(sess):
        return RedirectResponse("/login", status_code=303)
    actions = store.list_next_actions(sfo_id=sfo or None, status="open", limit=2000)
    from starlette.responses import Response
    return Response(content=_ics(actions), media_type="text/calendar",
                    headers={"Content-Disposition": "attachment; filename=sfohub-pipeline.ics"})


# ── Coverage matrix ────────────────────────────────────────────────────────────
@rt("/coverage")
def coverage_page(sess):
    if (r := require(sess)):
        return r
    m = cov.coverage_matrix(store, limit=60)
    head = Tr(Th("Family", cls="sfo"),
              *[Th(Span(s["name"][:16], title=s["name"]),
                   style="writing-mode:vertical-rl;transform:rotate(180deg);height:90px")
                for s in m["services"]])
    rows = []
    for row in m["rows"]:
        cells = [Td(Span(cls=f"cell {row['cells'][s['key']]}",
                         title=f"{s['name']}: {row['cells'][s['key']]}")) for s in m["services"]]
        rows.append(Tr(Td(A(row["sfo"]["name"], href=f"/sfo/{row['sfo']['id']}"), cls="sfo"), *cells))
    return Page(sess, H1("Service coverage matrix"),
        P(Span(cls="cell held"), " held  ", Span(cls="cell rec"), " recommended  ",
          Span(cls="cell gap"), " whitespace  ·  ",
          f"{m['totals']['held']} held · {m['totals']['rec']} recommended · {m['totals']['gap']} gaps",
          style="color:var(--muted)"),
        Div(Table(head, *rows, cls="matrix"), style="overflow-x:auto"),
        title="Coverage · SFO Hub", ctx="coverage")


# ── Relationship graph (vis-network) ───────────────────────────────────────────
@rt("/graph")
def graph(sess, mode: str = "book"):
    if (r := require(sess)):
        return r
    mode = "schema" if mode == "schema" else "book"
    data = graphdata.build_schema(store) if mode == "schema" else graphdata.build_book(store)
    mbtn = lambda m, lbl: A(lbl, href=f"/graph?mode={m}", cls="btn sm" + ("" if m == mode else " ghost"))  # noqa: E731
    legend = Span(Span("● ", style="color:#550055"), "Family  ",
                  Span("■ ", style="color:#ba2a84"), "Service",
                  style="color:var(--muted);font-size:12px;margin-left:10px")
    net_js = Script("""
var d=window.GDATA, g=window.GGROUPS;
var groups={};Object.keys(g).forEach(k=>{groups[k]={shape:g[k].shape,color:{background:g[k].color,border:g[k].color}}});
var net=new vis.Network(document.getElementById('net'),
  {nodes:new vis.DataSet(d.nodes),edges:new vis.DataSet(d.edges)},
  {groups:groups,nodes:{font:{size:13,color:'#48484f'},scaling:{min:8,max:46}},
   edges:{arrows:{to:{scaleFactor:0.4}},font:{size:9,color:'#9a93a6'},smooth:{type:'dynamic'}},
   // Super-slow, calm physics: heavy damping + a low velocity cap + a small
   // timestep so nodes drift gently instead of wobbling fast.
   physics:{stabilization:{iterations:200},maxVelocity:2.2,minVelocity:0.05,
     timestep:0.18,adaptiveTimestep:false,
     barnesHut:{springLength:150,avoidOverlap:0.2,damping:0.92,gravitationalConstant:-2200}},
   interaction:{hover:true}});
// Once the layout settles, freeze it so it stays perfectly still until dragged.
net.once('stabilizationIterationsDone',()=>net.setOptions({physics:{enabled:false}}));
net.on('click',p=>{if(!p.nodes.length)return;var n=net.body.data.nodes.get(p.nodes[0]);
  if(n.sfoid)location='/sfo/'+n.sfoid; else if(n.serviceid)location='/service/'+n.serviceid;});
""")
    return (Title("Relationship graph · SFO Hub"), CSS,
            Div(left_pane(sess, ctx="graph"),
                Div(Div(Span("Relationship graph — ",
                             Span("client book" if mode == "book" else "cross-sell schema",
                                  style="font-weight:400;color:var(--muted)")),
                        Span(mbtn("book", "Client book"), " ", mbtn("schema", "Cross-sell schema"),
                             legend, Span(data.get("stats", ""), style="color:var(--muted);font-size:12px;margin-left:auto")),
                        cls="chead"),
                    Div(id="net"), cls="pane center", style="display:flex;flex-direction:column"),
                cls="app-doc"),
            VISNET,
            Script(f"window.GDATA={json.dumps(data)};window.GGROUPS={json.dumps(graphdata.GROUPS)};"),
            net_js)


# ── Service catalogue ──────────────────────────────────────────────────────────
@rt("/services")
def services(sess):
    if (r := require(sess)):
        return r
    svcs = store.list_services(limit=200)
    rows = [Tr(Td(A(B(s["name"]), href=f"/service/{s['id']}")),
               Td(CATEGORY_LABELS.get(s.get("category"), s.get("category", ""))),
               Td(s.get("tier")), Td(s.get("description"), style="color:var(--muted)"))
            for s in svcs]
    return Page(sess, H1("JTC Private Office — Service catalogue"),
        Table(Tr(Th("Service"), Th("Category"), Th("Tier"), Th("Description")), *rows),
        title="Services · SFO Hub", ctx="services")


@rt("/service/{service_id}")
def service_detail(sess, service_id: int):
    if (r := require(sess)):
        return r
    s = store.get_service(service_id)
    if not s:
        return RedirectResponse("/services", status_code=303)
    partners = store.list_cross_sells(s["key"])
    clients = cov.service_clients(store, s["key"], limit=200)
    return Page(sess, Div(A("← Catalogue", href="/services"), style="margin-bottom:8px"),
        H1(s["name"]),
        P(f"{CATEGORY_LABELS.get(s.get('category'), s.get('category',''))} · {s.get('tier')}",
          style="color:var(--muted)"),
        P(s.get("description")),
        Div(Div(H2("Commonly bundled with"),
                Ul(*[Li(A(p["name"], href=f"/service/{p['id']}"), f" · {p.get('weight',0):.0%}")
                     for p in partners] or [Li("—")]), cls="card"),
            Div(H2(f"Held by {len(clients['holders'])} families"),
                Ul(*[Li(A(c["name"], href=f"/sfo/{c['id']}")) for c in clients["holders"][:12]]
                   or [Li("—")]),
                H2(f"Recommended to {len(clients['recommended'])}"),
                Ul(*[Li(A(c["name"], href=f"/sfo/{c['id']}")) for c in clients["recommended"][:12]]
                   or [Li("—")]), cls="card"),
            cls="cardrow"),
        title=f"{s['name']} · SFO Hub", ctx="services")


# ── Documents ──────────────────────────────────────────────────────────────────
@rt("/documents")
def documents(sess):
    if (r := require(sess)):
        return r
    docs = store.list_documents(limit=200)
    sfos = store.list_sfos(limit=200)
    rows = [Tr(Td(A(d["name"], href=f"/document/{d['id']}/file")),
               Td(d.get("doc_type", "")), Td(A(d.get("sfo_name") or "—", href=f"/sfo/{d['sfo_id']}") if d.get("sfo_id") else "—"),
               Td(f"{(d.get('byte_size') or 0)//1024} kB"), Td((d.get("created_at") or "")[:10]))
            for d in docs]
    return Page(sess, H1("Documents"),
        P("Upload portfolio summaries, trust deeds and luxury-asset inventories "
          "(txt, csv, md or PDF). Stored in Azure Blob Storage. Attach a file to a "
          "family and the AI extracts a profile (asset mix, pain points, services) "
          "and refreshes their recommendations.", style="color:var(--muted)"),
        Form(Select(Option("— attach to family —", value=""),
                    *[Option(s["name"], value=str(s["id"])) for s in sfos], name="sfo_id"),
             Select(*[Option(t, value=t) for t in
                      ["portfolio", "trust_deed", "asset_inventory", "report", "other"]], name="doc_type"),
             Input(type="file", name="file"),
             Button("Upload", cls="btn sm"),
             cls="filters", method="post", action="/upload", enctype="multipart/form-data"),
        (Table(Tr(Th("Name"), Th("Type"), Th("Family"), Th("Size"), Th("Uploaded")), *rows)
         if rows else P("No documents yet.", cls="kv")),
        title="Documents · SFO Hub", ctx="documents")


@rt("/upload", methods=["POST"])
async def upload(sess, request):
    if require(sess):
        return RedirectResponse("/login", status_code=303)
    from storage.docstore import get_docstore
    form = await request.form()
    f = form.get("file")
    if f is None or not getattr(f, "filename", ""):
        return RedirectResponse("/documents", status_code=303)
    data = await f.read()
    sfo_id = int(form.get("sfo_id")) if form.get("sfo_id") else None
    key = get_docstore().put(sfo_id, f.filename, data)
    from rag import extract
    text = extract.text_from_upload(f.filename, data)
    store.add_document({"sfo_id": sfo_id, "name": f.filename,
                        "doc_type": form.get("doc_type") or "other", "storage_key": key,
                        "byte_size": len(data), "content_text": text[:20000],
                        "uploaded_by": user_email(sess)})
    # Personalised insights: extract a profile from the document, update the family,
    # and regenerate recommendations from the new picture.
    if sfo_id and text:
        applied = extract.apply_to_sfo(store, sfo_id, extract.extract_profile(text))
        if applied:
            set_active_sfo(sfo_id)
            crosssell.recommend(sfo_id, persist=True, use_ai=True)
    return RedirectResponse(f"/sfo/{sfo_id}" if sfo_id else "/documents", status_code=303)


@rt("/document/{doc_id}/file")
def document_file(sess, doc_id: int):
    if require(sess):
        return RedirectResponse("/login", status_code=303)
    from storage.docstore import get_docstore
    d = store.get_document(doc_id)
    if not d:
        return RedirectResponse("/documents", status_code=303)
    data = get_docstore().get(d["storage_key"])
    if data is None:
        return JSONResponse({"error": "file bytes not found (volume reset?)"}, status_code=404)
    from starlette.responses import Response
    return Response(content=data, media_type="application/octet-stream",
                    headers={"Content-Disposition": f'inline; filename="{d["name"]}"'})


# ── Dashboard (Plotly) ─────────────────────────────────────────────────────────
@rt("/dashboard")
def dashboard(sess):
    if (r := require(sess)):
        return r
    st = store.stats()
    funnel = store.upsell_funnel()
    heat = store.service_interest_counts()
    dig = monitor.deadline_digest(store, date.today())
    sfos = store.list_sfos(limit=1000)
    # Aggregate allocation across the book.
    agg, n = {}, 0
    for s in sfos:
        for k, v in (s.get("asset_mix") or {}).items():
            agg[k] = agg.get(k, 0) + (v or 0)
        n += 1
    agg = {k: round(v / n, 1) for k, v in agg.items()} if n else {}
    # Pipeline value by service category.
    by_cat = {}
    for r in store.list_recommendations(limit=10000):
        if r["status"] in ("accepted", "booked"):
            by_cat[r.get("service_category") or "other"] = by_cat.get(r.get("service_category") or "other", 0) + r["est_value_usd"]
    funnel_counts = [funnel["by_status"].get(s, 0) for s in STATUS_ORDER]
    conv = funnel["by_status"].get("accepted", 0) + funnel["by_status"].get("booked", 0)
    total_recs = sum(funnel["by_status"].values()) or 1
    trends = store.activity_trends(12)
    stat = lambda n_, l: Div(Div(n_, cls="n"), Div(l, cls="l"), cls="stat")  # noqa: E731
    return Page(sess, H1("Analytics — cross/upsell pipeline"),
        Div(stat(str(st["sfos"]), "Family offices"),
            stat(str(st["recommendations"]), "Recommendations"),
            stat(money(funnel["pipeline_usd"]), "Pipeline value"),
            stat(f"{round(100*conv/total_recs)}%", "Acceptance rate"),
            stat(str(dig["counts"]["overdue"]), "Overdue actions"),
            cls="statgrid"),
        H2("Activity (last 12 weeks)"),
        plotly("trends", [
            {"type": "scatter", "mode": "lines+markers", "name": "Conversations",
             "x": [t["label"] for t in trends], "y": [t["conversations"] for t in trends],
             "line": {"color": "#6b1766"}},
            {"type": "scatter", "mode": "lines+markers", "name": "Recommendations",
             "x": [t["label"] for t in trends], "y": [t["recommendations"] for t in trends],
             "line": {"color": "#ba2a84"}},
        ], {"showlegend": True, "legend": {"orientation": "h"}, "margin": {"l": 40, "t": 10}},
           height=260),
        Div(Div(H2("Upsell funnel"),
                plotly("funnel", [{"type": "bar", "x": [s.title() for s in STATUS_ORDER],
                                   "y": funnel_counts, "marker": {"color": [STATUS_COLOR[s] for s in STATUS_ORDER]}}],
                       {"yaxis": {"title": "count"}}), cls="card"),
            Div(H2("Pipeline value by category"),
                plotly("bycat", [{"type": "bar", "orientation": "h",
                                  "y": [CATEGORY_LABELS.get(k, k) for k in by_cat],
                                  "x": list(by_cat.values())}],
                       {"xaxis": {"title": "USD"}, "margin": {"l": 140}}) if by_cat
                else P("No accepted/booked pipeline yet.", cls="kv"), cls="card"),
            cls="cardrow"),
        Div(Div(H2("Average asset allocation (book)"),
                plotly("agg", [{"type": "pie", "hole": 0.5,
                                "labels": [k.replace("_", " ") for k in agg], "values": list(agg.values())}],
                       {"showlegend": True, "margin": {"t": 10, "b": 10}}), cls="card"),
            Div(H2("Service interest heatmap"),
                plotly("heat", [{"type": "bar", "orientation": "h",
                                 "y": [h["name"] for h in reversed(heat)],
                                 "x": [h["count"] for h in reversed(heat)],
                                 "marker": {"color": "#ba2a84"}}],
                       {"margin": {"l": 210}}, height=360), cls="card"),
            cls="cardrow"),
        H2("Clients by stage"),
        Table(Tr(Th("Stage"), Th("Count")),
              *[Tr(Td(stage_badge(k)), Td(str(v))) for k, v in st["by_stage"].items()]),
        title="Dashboard · SFO Hub", ctx="dashboard")


# ── Help & technical guide ─────────────────────────────────────────────────────
# Walk-through sections rendered with the captured screenshots.
HELP_SECTIONS = [
    ("ug-01-advisor.png", "The advisor workspace",
     "Three panes: the client book + navigation on the left, the AI advisor in the "
     "centre, and the open family's profile + live recommendations on the right. Ask "
     "in plain English — the advisor routes to specialist agents and answers with "
     "citations and open-in-panel links."),
    ("ug-02-clients.png", "Client book",
     "Every family office in one filterable table — AUM, domicile, stage, family and "
     "current services. Filter by lifecycle stage; click a name to advise, ‘Open’ for "
     "the full profile, or ‘+ New family office’ to onboard a lead."),
    ("ug-03-sfo-detail.png", "Family profile",
     "AUM, generations, family size and accepted/booked pipeline at a glance, plus the "
     "asset-allocation donut, current services, jurisdictions, pain points and members."),
    ("ug-03b-portfolio.png", "Portfolio & transactions",
     "Mock holdings across asset classes — each with a performance figure — and recent "
     "cash-flow transactions (capital calls, distributions, buys, fees)."),
    ("ug-04-pipeline.png", "Pipeline (kanban)",
     "Every recommendation as a card, grouped by funnel stage. Drag a card between "
     "columns to advance it; filter by cross-sell/upsell or category."),
    ("ug-05-calendar.png", "Pipeline calendar",
     "Scheduled consultations, proposals and follow-ups across the book — urgency-coded "
     "and exportable to your own calendar with the iCal button."),
    ("ug-06-coverage.png", "Coverage matrix",
     "A family × service grid showing held, recommended, or whitespace — spot cross-sell "
     "opportunities across the whole book at a glance."),
    ("ug-07-graph.png", "Relationship graph",
     "Two modes: the cross-sell schema (how services bundle, with weights) and the client "
     "book (families ↔ services). Click a node to open it."),
    ("ug-08-dashboard.png", "Analytics dashboard",
     "Funnel, pipeline value by category, average allocation, service-interest heatmap and "
     "12-week activity trends — plus headline metrics and acceptance rate."),
    ("ug-09-documents.png", "Documents & insights",
     "Upload portfolio summaries, trust deeds and inventories (txt/csv/md/PDF). Attach to a "
     "family and the AI extracts a profile and refreshes their recommendations."),
    ("ug-10-onboard.png", "Onboard a new lead",
     "Capture a prospect through a guided intake; we create the lead and immediately produce "
     "a tailored service roadmap."),
    ("ug-11-services.png", "Service catalogue",
     "The JTC Private Office offerings the advisor cross-sells and upsells — open a service "
     "for common bundles and which families hold it."),
]


@rt("/user-guide-pdf")
def user_guide_pdf(sess):
    if require(sess):
        return RedirectResponse("/login", status_code=303)
    import glob
    # Serve the newest date-stamped guide (docs/sfohub_user_guide_<date>.pdf),
    # falling back to the legacy undated file.
    dated = sorted(glob.glob("docs/sfohub_user_guide_*.pdf"))
    path = dated[-1] if dated else "docs/sfohub_user_guide.pdf"
    if not os.path.exists(path):
        return JSONResponse({"error": "user guide not generated"}, status_code=404)
    return FileResponse(path, media_type="application/pdf",
                        headers={"Content-Disposition": "inline; filename=sfohub-user-guide.pdf"})


@rt("/ug/{name}")
def ug_asset(sess, name: str):
    # name has NO extension (e.g. ug-01-advisor) to avoid the static-file shadow.
    if require(sess):
        return RedirectResponse("/login", status_code=303)
    safe = os.path.basename(name) + ".png"
    path = os.path.join("docs", "screenshots", safe)
    if not os.path.exists(path):
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(path, media_type="image/png")


@rt("/help")
def help_page(sess):
    if (r := require(sess)):
        return r
    sections = []
    for img, title, desc in HELP_SECTIONS:
        sections += [
            Div(H2(title, style="margin-top:0"), P(desc, style="color:var(--muted)"),
                Img(src=f"/ug/{img[:-4]}", loading="lazy",
                    style="width:100%;border:1px solid var(--line);border-radius:10px;"
                          "box-shadow:0 6px 20px rgba(85,0,85,.10);margin-top:8px"),
                style="margin:22px 0;padding-bottom:18px;border-bottom:1px solid var(--line)")]
    return Page(sess,
        Div(H1("SFO Hub — User Guide", style="display:inline-block"),
            Span(A("📑 Slide deck (PDF)", href="/user-guide-pdf", cls="btn sm"),
                 " ", A("Technical guide →", href="/technical-guide", cls="btn ghost sm"),
                 style="float:right;margin-top:8px"), style="overflow:auto"),
        P("SFO Hub is an AI relationship-manager for JTC Group's Private Office. It "
          "engages family-office principals in natural dialogue, analyses their profile "
          "and portfolio, and surfaces personalised, traceable cross-sell and upsell "
          "recommendations — a transparent rule engine plus AI scoring.", style="font-size:15px"),
        Div(Div(B("Quick start "), "Open a family from the ", B("Client book"),
                ", chat with the advisor about their gaps, then work the ", B("Pipeline"),
                " and ", B("Calendar"), " to convert opportunities.",
                style="background:#faf2f8;border:1px solid var(--line);border-radius:10px;"
                      "padding:12px 14px;font-size:14px"), style="margin:10px 0 6px"),
        *sections,
        H2("How recommendations are made"),
        P("A deterministic rule catalogue fires on the profile (services, asset mix, pain "
          "points, AUM, stage); the cross-sell graph expands the candidate set; and an AI "
          "layer re-ranks and rewrites each rationale in a relationship-manager voice. "
          "Every recommendation is traceable to the rule or graph edge that produced it."),
        P(I("All family-office data is synthetic. No real client data is used."),
          style="color:var(--muted)"),
        title="User Guide · SFO Hub", ctx="help")


AGENTS_TABLE = [
    ("profile_agent", "Ground in who the client is — AUM, mix, services, pains"),
    ("needs_agent", "Detect gaps from a described setup → service categories"),
    ("services_agent", "Explain the JTC services for a topic"),
    ("recommend_agent", "Ranked cross/upsell with rationale + estimated value"),
    ("benchmark_agent", "Aggregate industry benchmarks to frame advice"),
    ("data_agent", "Quantitative book-wide questions via text-to-SQL"),
]

# Client-rendered Mermaid diagrams (kept in sync with docs/technical_architecture.md).
_MERMAID = {
    "System overview": """flowchart TB
  USER([SFO principal / advisor / sales team])
  subgraph APP[FastHTML app · Azure Container Apps]
    UI[3-pane UI + multi-page views]
    ORCH[LangGraph orchestrator · SSE]
    ENGINE[Hybrid cross/upsell engine]
    STORE[Storage interface]
  end
  LLM[[OpenAI-compatible LLM<br/>xAI Grok dev → Azure AI Foundry prod]]
  DB[(PostgreSQL)]
  BLOB[(Azure Blob Storage · documents)]
  USER -->|HTTPS| UI --> ORCH --> ENGINE --> STORE --> DB
  ORCH --> STORE
  ORCH --> LLM
  UI --> BLOB""",
    "Agent orchestration": """flowchart TB
  MSG([User message + open SFO context]) --> ORCH{{Orchestrator · LangGraph react-agent · Grok}}
  ORCH --> P[profile_agent]
  ORCH --> N[needs_agent]
  ORCH --> S[services_agent]
  ORCH --> R[recommend_agent]
  ORCH --> B[benchmark_agent]
  ORCH --> D[data_agent]
  P --> ST[(Storage)]
  S --> ST
  R --> EN[Hybrid engine] --> ST
  N --> KB[Services + benchmarks]
  B --> KB
  D --> SQL[(PostgreSQL)]
  ORCH -->|composed cited answer · SSE| OUT([Reply + markers])""",
    "Hybrid recommendation engine": """flowchart LR
  P([SFO profile]) --> RULES[1 Rule catalogue] --> GRAPH[2 Graph expansion]
  GRAPH --> AI[3 AI re-rank + rationale] --> VAL[4 Estimated value] --> DB[(5 Persist · PostgreSQL)]
  DB --> UI([Cards · proposals · kanban])
  AI -.no LLM key.-> DEG[Degrade: rule/graph scores]""",
    "Data agent — text-to-SQL + evals": """flowchart TB
  Q([How many family offices over $1bn?]) --> GEN[LLM generates SQL · SELECT-only]
  GEN --> EXEC[(Execute on PostgreSQL)] --> FMT[Format answer] --> A([41 family offices over $1bn])
  GT[(ground_truth.csv)] --> RUN[run via assistant or sql] --> JUDGE[deepeval GEval · Grok] --> SC([PASS/FAIL])
  A -.tested by.-> RUN""",
    "Deployment & CI/CD": """flowchart LR
  DEV[git push main] --> GH[GitHub Actions] -->|deploy webhook| HOST[Azure Container Apps]
  HOST --> BUILD[Docker build · port 5021] --> RUN[Rolling update · /health] --> LIVE([sfohub.predictivelabs.ai])
  LIVE --> BLOB[(Azure Blob Storage)]
  LIVE --> DB[(PostgreSQL)]
  LIVE --> GROK[[xAI Grok / Azure AI Foundry]]""",
}

MERMAID_INIT = Script(
    "import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs';"
    "mermaid.initialize({startOnLoad:true, theme:'neutral', "
    "themeVariables:{primaryColor:'#f3ecf3',primaryBorderColor:'#ba2a84',"
    "lineColor:'#9c5797',primaryTextColor:'#48484f'}});",
    type="module")


@rt("/technical-guide-pdf")
def technical_guide_pdf(sess):
    if require(sess):
        return RedirectResponse("/login", status_code=303)
    return FileResponse("docs/technical_architecture_slides.pdf",
                        media_type="application/pdf",
                        headers={"Content-Disposition": "inline; filename=sfohub-architecture.pdf"})


@rt("/technical-guide")
def technical_guide(sess):
    if (r := require(sess)):
        return r
    diagrams = []
    for title, src in _MERMAID.items():
        diagrams += [H2(title), Pre(src, cls="mermaid",
                                    style="background:#fff;border:1px solid var(--line);"
                                          "border-radius:10px;padding:14px;text-align:center")]
    return Page(sess, H1("SFO Hub — Technical guide"),
        Div(A("← Help", href="/help"),
            A("📑 Download slide deck (PDF)", href="/technical-guide-pdf",
              cls="btn sm", style="float:right"), style="overflow:auto"),
        P("Full agentic architecture — the LangGraph orchestrator, its six specialist "
          "agents, the hybrid recommendation engine, the text-to-SQL data agent, and "
          "the deployment pipeline. Diagrams render below; a slide deck and the source "
          "Markdown live in ", A("docs/technical_architecture.md", href="https://github.com/predictivelabsai/sfohub/blob/main/docs/technical_architecture.md"),
          ".", style="color:var(--muted)"),
        H2("Stack"),
        Ul(Li("FastHTML multi-page app (this UI), uvicorn, port 5021."),
           Li("LangGraph tool-calling advisor over 6 specialist agents; SSE streaming."),
           Li("Data store: PostgreSQL (backend-neutral Storage interface)."),
           Li("Documents: Azure Blob Storage."),
           Li("OpenAI-compatible LLM: xAI Grok (dev) → Azure AI Foundry (prod)."),
           Li("Deploy via GitHub Actions → Azure Container Apps.")),
        H2("The six specialist agents"),
        Table(Tr(Th("Agent"), Th("Job")),
              *[Tr(Td(B(n)), Td(d)) for n, d in AGENTS_TABLE]),
        *diagrams,
        MERMAID_INIT,
        title="Technical guide · SFO Hub", ctx="help")
