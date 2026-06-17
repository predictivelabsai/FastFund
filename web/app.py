"""SFO Hub — 3-pane agentic web app (JTC Private Office advisor).

Left: nav + Client Book (family offices) + shortcuts. Center: AI Assistant (SSE
chat over the LangGraph orchestrator) with suggestion cards. Right: the open
family's profile + live cross/upsell recommendations, which swaps to a service
detail when a service is opened.

Run:  python3.12 -m uvicorn web.app:app --host 0.0.0.0 --port 5021
"""
from __future__ import annotations

import os

from fasthtml.common import *
from starlette.responses import StreamingResponse

import sfostore as store
from agents import orchestrator
from agents.context import set_active_sfo
from engine import crosssell

store.init_db()
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

SUGGESTIONS = [
    "Tell me about this family's governance setup",
    "What should we offer them next?",
    "How do family offices typically allocate capital?",
    "Explain JTC's luxury asset administration",
    "Where are the gaps in their current services?",
]
SHORTCUTS = [
    ("Profile", "Summarise the open family office", "Give me a profile summary of this family"),
    ("Recommend", "Ranked cross/upsell ideas", "What should we offer them next?"),
    ("Gaps", "Detect service gaps", "Where are the gaps in their current services?"),
    ("Benchmarks", "Industry context", "How do family offices typically allocate?"),
]

CSS = Style("""
/* JTC Group brand palette: purple #6B1766 / deep #550055 / magenta #BA2A84 /
   slate text #48484F / light bg #F5F6F4 */
:root{--navy:#6b1766;--navy2:#550055;--accent:#ba2a84;--bg:#f5f6f4;--line:#e6e3ec;
--green:#1c7c44;--amber:#b06b00;--text:#48484f;--muted:#7a7a85;--panel:#fff;}
*{box-sizing:border-box}html,body{height:100%}
body{margin:0;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
color:var(--text);background:var(--bg);line-height:1.5}
a{color:var(--navy2);text-decoration:none}a:hover{text-decoration:underline}
.app{display:grid;grid-template-columns:290px 1fr 430px;height:100vh;overflow:hidden}
.pane{height:100vh;overflow-y:auto}
.left{background:var(--navy);color:#ece3ee;padding:0}
.left .brand{font-weight:700;font-size:18px;color:#fff;padding:16px 18px;border-bottom:1px solid #45114a}
.left .brand span{color:var(--accent)}
.left a{color:#ece3ee;display:block}
.section{padding:12px 16px;border-bottom:1px solid #45114a}
.section .lbl{font-size:11px;text-transform:uppercase;letter-spacing:.6px;color:#c9a3c6;margin-bottom:8px}
.navlink{padding:6px 8px;border-radius:6px;font-size:14px}.navlink:hover{background:#7a2474;text-decoration:none}
.newchat{display:block;background:var(--accent);color:#fff;text-align:center;font-weight:600;
padding:9px;border-radius:8px;margin:12px 16px}
.newchat:hover{text-decoration:none;filter:brightness(1.05)}
.client{font-size:13px;padding:7px 9px;border-radius:7px;cursor:pointer;display:block;color:#ece3ee;border:1px solid transparent}
.client:hover{background:#7a2474;text-decoration:none}
.client.active{background:#7a2474;border-color:var(--accent)}
.client .meta{font-size:11px;color:#c9a3c6}
.stagebadge{display:inline-block;border-radius:20px;padding:1px 8px;font-size:10px;font-weight:600;color:#fff}
.shortcut{font-size:12px;padding:4px 6px;border-radius:6px;cursor:pointer}
.shortcut:hover{background:#7a2474}.shortcut b{color:var(--accent)}
/* center */
.center{display:flex;flex-direction:column;background:#fbfcfd}
.center .chead{padding:14px 22px;border-bottom:1px solid var(--line);font-weight:600;color:var(--navy);
display:flex;align-items:center;justify-content:space-between}
.msgs{flex:1;overflow-y:auto;padding:22px;display:flex;flex-direction:column;gap:14px}
.bubble{max-width:760px;padding:12px 16px;border-radius:12px;font-size:14.5px;white-space:normal}
.bubble.user{align-self:flex-end;background:var(--navy);color:#fff;border-bottom-right-radius:3px}
.bubble.assistant{align-self:flex-start;background:#fff;border:1px solid var(--line);border-bottom-left-radius:3px}
.bubble.assistant pre{white-space:pre-wrap}
.toolchip{display:inline-block;font-size:11px;background:#f3ecf3;color:var(--navy2);border:1px solid #e0cfe0;
border-radius:20px;padding:1px 9px;margin:2px 4px 2px 0}
.cards{display:flex;flex-wrap:wrap;gap:8px;padding:8px 22px}
.scard{background:#fff;border:1px solid var(--line);border-radius:10px;padding:8px 11px;font-size:12.5px;
cursor:pointer;max-width:340px}.scard:hover{border-color:var(--accent);color:var(--navy)}
.composer{padding:14px 22px;border-top:1px solid var(--line);background:#fff;display:flex;gap:10px}
.composer textarea{flex:1;resize:none;border:1px solid var(--line);border-radius:10px;padding:11px;font:inherit;height:48px}
.composer button{background:var(--navy);color:#fff;border:none;border-radius:10px;padding:0 20px;font-weight:600;cursor:pointer}
/* right */
.right{background:#fff;border-left:1px solid var(--line);display:flex;flex-direction:column}
.right .rhead{padding:13px 18px;border-bottom:1px solid var(--line);font-weight:600;color:var(--navy);
display:flex;align-items:center;justify-content:space-between}
.right .rhead .x{cursor:pointer;color:var(--muted)}
.rbody{flex:1;overflow-y:auto;padding:14px 18px}
.profilecard{border:1px solid var(--line);border-radius:10px;padding:13px;margin-bottom:14px}
.profilecard h3{margin:0 0 4px;color:var(--navy);font-size:16px}
.kv{font-size:12.5px;color:var(--muted);margin:2px 0}.kv b{color:var(--text)}
.chips{margin:6px 0}.chip{display:inline-block;background:#f3ecf3;color:var(--navy2);border-radius:20px;
padding:2px 9px;font-size:11px;margin:2px 4px 2px 0}
.bar{height:8px;border-radius:5px;background:#eee;overflow:hidden;display:flex;margin:6px 0}
.bar i{display:block;height:100%}
.rec{border:1px solid var(--line);border-left:4px solid var(--accent);border-radius:8px;
padding:11px 13px;margin-bottom:11px;font-size:13px}
.rec .top{display:flex;justify-content:space-between;align-items:center;margin-bottom:4px}
.rec .name{font-weight:600;color:var(--navy)}
.pill{display:inline-block;padding:1px 8px;border-radius:20px;font-size:10.5px;font-weight:600;text-transform:uppercase;color:#fff}
.rec .val{color:var(--green);font-weight:600;font-size:12px}
.rec .why{color:var(--muted);font-size:12px;margin-top:4px}
.btn{display:inline-block;background:var(--navy);color:#fff;padding:9px 16px;border-radius:7px;font-size:14px;border:none;cursor:pointer}
.btn.ghost{background:#fff;color:var(--navy);border:1px solid var(--line)}
.form{background:#fff;border:1px solid var(--line);border-radius:10px;padding:24px;max-width:380px;margin:60px auto}
.form input{width:100%;padding:10px;border:1px solid var(--line);border-radius:7px;margin:6px 0 14px}
.wrap{max-width:1100px;margin:0 auto;padding:24px}
table{width:100%;border-collapse:collapse;background:#fff;border:1px solid var(--line);border-radius:10px;overflow:hidden}
th,td{text-align:left;padding:9px 13px;border-bottom:1px solid var(--line);font-size:13.5px}
th{background:#fbfbfc;color:var(--muted);font-size:11px;text-transform:uppercase}
.statgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:14px;margin:18px 0}
.stat{background:#fff;border:1px solid var(--line);border-radius:10px;padding:16px}
.stat .n{font-size:28px;font-weight:700;color:var(--navy)}.stat .l{color:var(--muted);font-size:12px}
.heatrow{display:flex;align-items:center;gap:10px;margin:5px 0;font-size:13px}
.heatrow .track{flex:1;height:14px;background:#f0edf3;border-radius:7px;overflow:hidden}
.heatrow .track i{display:block;height:100%;background:var(--accent)}
""")

MARKED = Script(src="https://cdn.jsdelivr.net/npm/marked/marked.min.js")
FAVICON = Link(rel="icon", type="image/svg+xml",
               href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' "
                    "viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='6' "
                    "fill='%236b1766'/%3E%3Ctext x='16' y='22' font-family='Arial' "
                    "font-size='16' font-weight='700' fill='%23ba2a84' "
                    "text-anchor='middle'%3ES%3C/text%3E%3C/svg%3E")


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


@rt("/health")
def health():
    return JSONResponse({"status": "ok"})


# ── Auth ──────────────────────────────────────────────────────────────────────
@rt("/login", methods=["GET"])
def login_form(sess, error: str = ""):
    return Title("Sign in · SFO Hub"), CSS, Form(
        H2("SFO Hub"), P("JTC Private Office — AI advisor", style="color:#6b7686"),
        (P(error, style="color:#c0392b") if error else ""),
        Input(name="email", placeholder="Email", type="email"),
        Input(name="password", placeholder="Password", type="password"),
        Button("Sign in", cls="btn", style="width:100%"),
        method="post", action="/login", cls="form")


@rt("/login", methods=["POST"])
def login_submit(sess, email: str = "", password: str = ""):
    import bcrypt
    user = store.get_user_by_email(email)
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


# ── UI fragments ──────────────────────────────────────────────────────────────
def stage_badge(stage):
    col = STAGE_COLOR.get(stage, "#7a7a85")
    return Span(stage.title(), cls="stagebadge", style=f"background:{col}")


def left_pane(sess, active_id=None):
    sfos = store.list_sfos(limit=60)
    items = []
    for s in sfos:
        cls = "client active" if s["id"] == active_id else "client"
        items.append(A(
            Div(s["name"]),
            Div(f"${(s.get('aum_usd') or 0)/1e6:,.0f}M · {s.get('domicile','')} ",
                stage_badge(s.get("stage", "lead")), cls="meta"),
            cls=cls, href=f"/?sfo={s['id']}"))
    return Div(
        Div("SFO ", Span("Hub"), cls="brand"),
        A("+ New conversation", href="/", cls="newchat"),
        Div(Div("Navigate", cls="lbl"),
            A("📊 Analytics dashboard", href="/dashboard", cls="navlink"),
            A("🗂 Service catalogue", href="/services", cls="navlink"),
            A("❓ Help & guide", href="/help", cls="navlink"),
            A("↪ Sign out", href="/logout", cls="navlink"),
            cls="section"),
        Div(Div(f"Client book · {len(sfos)}", cls="lbl"), *items, cls="section"),
        Div(Div("Quick actions", cls="lbl"),
            *[Div(B(lbl), f" — {desc}", cls="shortcut",
                  onclick=f"sfoSet({prompt!r})") for lbl, desc, prompt in SHORTCUTS],
            cls="section"),
        cls="pane left")


def mix_bar(asset_mix):
    colors = {"private_equity": "#6b1766", "public_equity": "#ba2a84",
              "real_estate": "#9c5797", "luxury": "#b06b00",
              "cash": "#1c7c44", "alternatives": "#c9a3c6"}
    segs = [I(style=f"width:{v}%;background:{colors.get(k,'#ccc')}", title=f"{k} {v}%")
            for k, v in (asset_mix or {}).items() if v]
    legend = " · ".join(f"{k.replace('_',' ')} {v}%" for k, v in (asset_mix or {}).items() if v)
    return Div(Div(*segs, cls="bar"), Div(legend, cls="kv"))


def rec_card(r):
    kind = "Upsell" if r["kind"] == "upsell" else "Cross-sell"
    return Div(
        Div(Span(r["service_name"], cls="name"),
            Span(kind, cls="pill", style=f"background:{KIND_COLOR.get(r['kind'])}"),
            cls="top"),
        Div(f"Fit {r['score']:.0%}", Span(" · "),
            Span(f"~${r['est_value_usd']/1e3:,.0f}k/yr", cls="val"),
            Span(f" · {r.get('source','')}", cls="kv")),
        Div(r["rationale"], cls="why"),
        cls="rec")


def right_pane(sfo_id=None):
    if sfo_id is None:
        return Div(
            Div("Workspace", cls="rhead"),
            Div(P("Select a family office from the client book to see their "
                  "profile and live cross/upsell recommendations.", cls="kv"),
                cls="rbody"),
            cls="pane right", id="right")
    sfo = store.get_sfo(sfo_id)
    if not sfo:
        return right_pane(None)
    recs = store.list_recommendations(sfo_id=sfo_id, limit=20)
    if not recs:
        recs_rendered = [P("No recommendations yet.", cls="kv"),
                         Button("Generate recommendations", cls="btn",
                                hx_post=f"/recommend/{sfo_id}", hx_target="#right",
                                hx_swap="outerHTML")]
    else:
        recs_rendered = [rec_card(r) for r in recs]
    return Div(
        Div(sfo["name"], A("Dashboard ↗", href="/dashboard", style="font-size:12px"),
            cls="rhead"),
        Div(
            Div(H3(sfo["name"]),
                Div(f"AUM ", B(f"${(sfo.get('aum_usd') or 0)/1e6:,.0f}M"),
                    f" · {sfo.get('family_size','—')} members · "
                    f"{sfo.get('generations','—')} generations", cls="kv"),
                Div(f"Domicile {sfo.get('domicile','—')} · ", stage_badge(sfo.get("stage","lead")), cls="kv"),
                Div("Current services:", cls="kv", style="margin-top:8px"),
                Div(*[Span(s.replace("_", " "), cls="chip")
                      for s in (sfo.get("current_services") or [])] or [Span("none", cls="chip")],
                    cls="chips"),
                Div("Asset mix:", cls="kv", style="margin-top:6px"),
                mix_bar(sfo.get("asset_mix")),
                Div("Pain points:", cls="kv", style="margin-top:6px"),
                Div(*[Span(p, cls="chip") for p in (sfo.get("pain_points") or [])], cls="chips"),
                cls="profilecard"),
            Div(B("Recommendations"),
                Button("↻ Regenerate", cls="btn ghost", style="float:right;font-size:12px;padding:4px 10px",
                       hx_post=f"/recommend/{sfo_id}", hx_target="#right", hx_swap="outerHTML"),
                style="margin-bottom:8px;overflow:auto"),
            *recs_rendered,
            cls="rbody"),
        cls="pane right", id="right")


def chat_script(sfo_id=None):
    sfo_q = f"?sfo={sfo_id}" if sfo_id else ""
    return Script("""
function sfoSet(t){const b=document.getElementById('box');b.value=t;b.focus();}
function mdRender(el){ if(window.marked) el.innerHTML = marked.parse(el.dataset.raw||''); }
function openMarkers(html){
  return html.replace(/\\[service:(\\d+)\\]/g,'').replace(/\\[sfo:(\\d+)\\]/g,'').replace(/\\[rec:(\\d+)\\]/g,'');
}
async function sfoSend(){
  const box=document.getElementById('box'); const q=box.value.trim(); if(!q) return;
  box.value=''; const msgs=document.getElementById('msgs');
  const u=document.createElement('div'); u.className='bubble user'; u.textContent=q; msgs.appendChild(u);
  const a=document.createElement('div'); a.className='bubble assistant'; a.dataset.raw=''; msgs.appendChild(a);
  const tools=document.createElement('div'); a.appendChild(tools);
  const body=document.createElement('div'); a.appendChild(body);
  msgs.scrollTop=msgs.scrollHeight;
  const resp=await fetch('/chat__SFO__',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({q})});
  const reader=resp.body.getReader(); const dec=new TextDecoder(); let buf='';
  while(true){const{done,value}=await reader.read(); if(done)break; buf+=dec.decode(value,{stream:true});
    let parts=buf.split('\\n\\n'); buf=parts.pop();
    for(const p of parts){ const lines=p.split('\\n'); let ev='',data='';
      for(const l of lines){ if(l.startsWith('event: '))ev=l.slice(7); if(l.startsWith('data: '))data=l.slice(6);}
      if(!data)continue; let d={}; try{d=JSON.parse(data);}catch(e){continue;}
      if(ev==='token'){ a.dataset.raw+=d.text; body.innerHTML=openMarkers(marked.parse(a.dataset.raw)); }
      else if(ev==='tool_start'){ const c=document.createElement('span'); c.className='toolchip'; c.textContent='⚙ '+d.name; tools.appendChild(c);}
      else if(ev==='done'){ if(window.__sfoid){ htmx.ajax('GET','/panel/'+window.__sfoid,{target:'#right',swap:'outerHTML'}); } }
      else if(ev==='error'){ body.innerHTML='<i>'+d.message+'</i>'; }
      msgs.scrollTop=msgs.scrollHeight;
    }
  }
}
document.addEventListener('keydown',e=>{ if(e.target.id==='box'&&e.key==='Enter'&&!e.shiftKey){e.preventDefault();sfoSend();}});
""".replace("__SFO__", sfo_q))


@rt("/")
def index(sess, sfo: int = 0):
    r = require(sess)
    if r:
        return r
    sfo_id = sfo or None
    greeting = ("Hello — I'm your JTC Private Office advisor. Open a family office "
                "from the client book and ask me anything: their setup, where the "
                "gaps are, and what we should offer them next.")
    set_id = Script(f"window.__sfoid={sfo_id if sfo_id else 'null'};")
    return Title("SFO Hub"), CSS, Div(
        left_pane(sess, sfo_id),
        Div(
            Div("AI Assistant", Span(orchestrator and "", style="color:var(--muted);font-size:12px"),
                cls="chead"),
            Div(Div(greeting, cls="bubble assistant"), cls="msgs", id="msgs"),
            Div(*[Div(s, cls="scard", onclick=f"sfoSet({s!r})") for s in SUGGESTIONS], cls="cards"),
            Div(Textarea(placeholder="Message the advisor…", id="box", name="q"),
                Button("Send", onclick="sfoSend()"), cls="composer"),
            cls="pane center"),
        right_pane(sfo_id),
        cls="app"), set_id, chat_script(sfo_id)


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
        # Persist the exchange under a per-(user,sfo) conversation.
        convs = store.list_conversations(user_email=email, sfo_id=sfo or None, limit=1)
        cid = convs[0]["id"] if convs else store.create_conversation(
            email, sfo_id=sfo or None, title=question[:40])
        store.add_message(cid, "user", question)
        buf = []
        async for chunk in orchestrator.astream(question):
            if '"text"' in chunk and "event: token" in chunk:
                import json
                try:
                    buf.append(json.loads(chunk.split("data: ", 1)[1])["text"])
                except Exception:  # noqa: BLE001
                    pass
            yield chunk
        store.add_message(cid, "assistant", "".join(buf))

    return StreamingResponse(gen(), media_type="text/event-stream")


# ── Service catalogue ─────────────────────────────────────────────────────────
@rt("/services")
def services(sess):
    if require(sess):
        return RedirectResponse("/login", status_code=303)
    svcs = store.list_services(limit=200)
    rows = [Tr(Td(B(s["name"])), Td(CATEGORY_LABELS.get(s.get("category"), s.get("category",""))),
               Td(s.get("tier")), Td(s.get("description"), style="color:var(--muted)"))
            for s in svcs]
    return Title("Services · SFO Hub"), CSS, Div(
        H2("JTC Private Office — Service Catalogue", style="color:var(--navy)"),
        P(A("← Back to advisor", href="/")),
        Table(Thead(Tr(Th("Service"), Th("Category"), Th("Tier"), Th("Description"))),
              Tbody(*rows)),
        cls="wrap")


@rt("/service/{service_id}")
def service_detail(sess, service_id: int):
    if require(sess):
        return RedirectResponse("/login", status_code=303)
    s = store.get_service(service_id)
    if not s:
        return RedirectResponse("/services", status_code=303)
    partners = store.list_cross_sells(s["key"])
    return Title(f"{s['name']} · SFO Hub"), CSS, Div(
        P(A("← Back", href="/services")),
        H2(s["name"], style="color:var(--navy)"),
        P(f"{CATEGORY_LABELS.get(s.get('category'), s.get('category',''))} · {s.get('tier')}"),
        P(s.get("description")),
        H3("Commonly bundled with"),
        Ul(*[Li(A(p["name"], href=f"/service/{p['id']}"), f" (weight {p.get('weight',0):.0%})")
             for p in partners] or [Li("—")]),
        cls="wrap")


# ── Analytics dashboard ───────────────────────────────────────────────────────
@rt("/dashboard")
def dashboard(sess):
    if require(sess):
        return RedirectResponse("/login", status_code=303)
    st = store.stats()
    funnel = store.upsell_funnel()
    heat = store.service_interest_counts()
    maxc = max([h["count"] for h in heat] or [1]) or 1
    funnel_order = ["suggested", "presented", "accepted", "booked", "declined"]
    stat = lambda n, l: Div(Div(str(n), cls="n"), Div(l, cls="l"), cls="stat")  # noqa: E731
    heat_rows = [Div(Span(h["name"], style="width:200px"),
                     Div(I(style=f"width:{h['count']/maxc*100:.0f}%"), cls="track"),
                     Span(str(h["count"]), style="width:30px;text-align:right"),
                     cls="heatrow") for h in heat]
    funnel_rows = [Tr(Td(s.title()),
                      Td(str(funnel["by_status"].get(s, 0)),
                         style=f"color:{STATUS_COLOR.get(s)};font-weight:600"))
                   for s in funnel_order]
    return Title("Dashboard · SFO Hub"), CSS, Div(
        H2("Analytics — Cross/Upsell Pipeline", style="color:var(--navy)"),
        P(A("← Back to advisor", href="/")),
        Div(stat(st["sfos"], "Family offices"),
            stat(st["services"], "JTC services"),
            stat(st["recommendations"], "Recommendations"),
            stat(f"${funnel['pipeline_usd']/1e6:,.1f}M", "Pipeline value (acc/booked)"),
            cls="statgrid"),
        Div(
            Div(H3("Service interest heatmap"), *heat_rows,
                style="flex:1;min-width:340px"),
            Div(H3("Upsell funnel"),
                Table(Thead(Tr(Th("Status"), Th("Count"))), Tbody(*funnel_rows)),
                H3("Clients by stage", style="margin-top:18px"),
                Table(Tbody(*[Tr(Td(k.title()), Td(str(v)))
                              for k, v in st["by_stage"].items()])),
                style="flex:1;min-width:300px"),
            style="display:flex;gap:30px;flex-wrap:wrap;margin-top:20px"),
        cls="wrap")


# ── Help ──────────────────────────────────────────────────────────────────────
@rt("/help")
def help_page(sess):
    if require(sess):
        return RedirectResponse("/login", status_code=303)
    return Title("Help · SFO Hub"), CSS, Div(
        H2("SFO Hub — Help & Guide", style="color:var(--navy)"),
        P(A("← Back to advisor", href="/")),
        H3("What this is"),
        P("SFO Hub is an AI relationship-manager simulator for JTC Group's Private "
          "Office. It engages family-office principals in natural dialogue, analyses "
          "their profile, and surfaces personalised cross-sell and upsell "
          "recommendations — drawing on a transparent rule engine plus AI scoring."),
        H3("How to use it"),
        Ul(Li("Pick a family office from the ", B("client book"), " on the left."),
           Li("Chat with the advisor in the centre — ask about their setup, gaps, "
              "or what to offer next."),
           Li("The right panel shows the family's ", B("profile"), " and live ",
              B("recommendations"), "; click Regenerate to re-run the engine."),
           Li("The ", B("Analytics dashboard"), " shows the service-interest heatmap "
              "and the upsell funnel with simulated pipeline value.")),
        H3("How recommendations are made"),
        P("A deterministic rule catalogue fires on the profile (current services, "
          "asset mix, pain points, AUM, stage), the cross-sell graph expands the "
          "candidate set, and — when an LLM is configured — an AI layer re-ranks "
          "and rewrites each rationale in a relationship-manager voice. Every "
          "recommendation is traceable to the rule or graph edge that produced it."),
        P(I("Demo note: all family-office data is synthetic. No real client data is used.")),
        cls="wrap")
