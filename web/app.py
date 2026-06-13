"""TaxHub — 3-pane agentic web app.

Left: nav + Tax Forms Tree + Shortcuts. Center: AI Assistant (SSE chat over the
LangGraph orchestrator) with suggestion cards. Right: changes newsfeed, which
swaps to a PDF viewer when a form/document is opened.

Primary goal: find the correct TAX FORM. Traceability (versions, changes,
citations) is the secondary layer.

Run:  python3.12 -m uvicorn web.app:app --host 0.0.0.0 --port 5011
"""
from __future__ import annotations

import os

from fasthtml.common import *
from starlette.responses import StreamingResponse

import taxstore as store
from web import monitor
from agents import orchestrator
from agents.tools import document_agent, law_agent, metadata_agent, changes_agent
from ingest.forms import forms_tree

store.init_db()
LOGIN_REQUIRED = os.environ.get("TAXHUB_PUBLIC", "0") != "1"

CATEGORY_LABELS = {
    "corporate_tax": "Corporate tax", "economic_substance": "Economic substance",
    "aeoi": "AEOI (FATCA/CRS)", "beneficial_ownership": "Beneficial ownership",
    "partnership": "Partnerships", "personal_employer": "Personal / employer",
    "gst_vat": "GST / VAT", "fund": "Fund-specific", "other": "Other",
}
JUR_NAMES = {
    # Live MVP domiciles
    "JE": "Jersey", "GG": "Guernsey", "LU": "Luxembourg", "IE": "Ireland",
    "KY": "Cayman Islands", "VG": "British Virgin Islands",
    # Expansion — all JTC Group office jurisdictions
    "IM": "Isle of Man", "MU": "Mauritius", "MT": "Malta", "CY": "Cyprus",
    "NL": "Netherlands", "CH": "Switzerland", "GB": "United Kingdom", "DE": "Germany",
    "AT": "Austria", "PL": "Poland", "US": "United States", "HK": "Hong Kong",
    "SG": "Singapore", "MY": "Malaysia", "NZ": "New Zealand", "AE": "United Arab Emirates",
    "BS": "Bahamas", "BR": "Brazil", "ZA": "South Africa", "BM": "Bermuda",
}
FILING_LABELS = {"downloadable": "📄 Downloadable form", "online": "🌐 Online filing",
                 "reference": "📘 Reference / guidance"}
FORM_TYPE_LABELS = {"return": "Return", "notification": "Notification",
                    "declaration": "Declaration", "registration": "Registration",
                    "report": "Report", "guidance": "Guidance", "form": "Form"}
ENTITY_TYPES = ["fund", "spv", "gp", "trust", "company", "holdco"]
OB_STATUS = ["not_started", "in_progress", "prepared", "filed", "confirmed", "na"]
OB_STATUS_LABELS = {"not_started": "Not started", "in_progress": "In progress",
                    "prepared": "Prepared", "filed": "Filed", "confirmed": "Confirmed",
                    "na": "N/A"}
OB_STATUS_COLOR = {"not_started": "#7a7a85", "in_progress": "#b06b00",
                   "prepared": "#6b1766", "filed": "#1c7c44", "confirmed": "#1c7c44",
                   "na": "#9a93a6"}


def ob_status_badge(status):
    s = status or "not_started"
    col = OB_STATUS_COLOR.get(s, "#7a7a85")
    return Span(OB_STATUS_LABELS.get(s, s),
                style=f"display:inline-block;background:{col}22;color:{col};"
                      "border-radius:20px;padding:2px 10px;font-size:11px;font-weight:600")
ACTIVITY_OPTIONS = ["fund management", "holding", "finance & leasing",
                    "headquartering", "intellectual property", "distribution & service centre"]
# A few synthetic entities so the feature is demoable before real data is imported.
SAMPLE_ENTITIES = [
    {"name": "Aurora Global Fund SPC", "type": "fund", "domicile": "KY",
     "jurisdictions": ["KY", "US"], "fy_end": "31 December",
     "activities": ["fund management", "holding"], "client_ref": "JTC-0001"},
    {"name": "Helios Holdings (Jersey) Ltd", "type": "holdco", "domicile": "JE",
     "jurisdictions": ["JE"], "fy_end": "31 December",
     "activities": ["holding"], "client_ref": "JTC-0002"},
    {"name": "Lumen Private Equity GP Sàrl", "type": "gp", "domicile": "LU",
     "jurisdictions": ["LU"], "fy_end": "31 December",
     "activities": ["fund management"], "client_ref": "JTC-0003"},
    {"name": "Meridian Trust", "type": "trust", "domicile": "GG",
     "jurisdictions": ["GG"], "fy_end": "30 June",
     "activities": ["holding"], "client_ref": "JTC-0004"},
    {"name": "Atlas Infrastructure SCSp", "type": "fund", "domicile": "LU",
     "jurisdictions": ["LU", "IE"], "fy_end": "31 December",
     "activities": ["fund management", "finance & leasing"], "client_ref": "JTC-0005"},
    {"name": "Pioneer Ventures (Cayman) Ltd", "type": "spv", "domicile": "KY",
     "jurisdictions": ["KY"], "fy_end": "31 December",
     "activities": ["holding"], "client_ref": "JTC-0006"},
]

SUGGESTIONS = [
    "form: Cayman economic substance notification",
    "Which form do I file for a Jersey company tax return?",
    "law: what does CIGA mean?",
    "What FATCA/CRS forms apply in Guernsey?",
    "changes: recent changes in Jersey",
]
SHORTCUTS = [
    ("form:", "Find the right tax form", "form: Cayman economic substance"),
    ("law:", "Ask a tax-law question", "law: economic substance test"),
    ("forms:", "List forms for a jurisdiction", "forms: KY"),
    ("changes:", "Recent changes", "changes: JE"),
    ("find:", "Free search of the corpus", "find: holding company"),
]

CSS = Style("""
/* JTC Group brand palette (jtcgroup.com): purple #6B1766 / deep #550055 /
   magenta accent #BA2A84 / slate text #48484F / light bg #F5F6F4 */
:root{--navy:#6b1766;--navy2:#550055;--accent:#ba2a84;--bg:#f5f6f4;--line:#e6e3ec;
--green:#1c7c44;--amber:#b06b00;--text:#48484f;--muted:#7a7a85;--panel:#fff;}
*{box-sizing:border-box}html,body{height:100%}
body{margin:0;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
color:var(--text);background:var(--bg);line-height:1.5}
a{color:var(--navy2);text-decoration:none}a:hover{text-decoration:underline}
.app{display:grid;grid-template-columns:280px 1fr 430px;height:100vh;overflow:hidden}
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
details.tree{margin:2px 0}details.tree>summary{cursor:pointer;font-size:13px;padding:3px 0;list-style:none}
details.tree>summary::-webkit-details-marker{display:none}
details.tree>summary:before{content:"▸ ";color:#c9a3c6}details.tree[open]>summary:before{content:"▾ "}
.tree .jur{font-weight:600;color:#f3e9f3}.tree .cat{margin-left:12px;color:#d8c5d6}
.tree .typ{margin-left:24px;color:#c9a3c6;font-size:12px}
.formlink{margin-left:30px;display:block;font-size:12.5px;color:#ece3ee;padding:2px 0;cursor:pointer}
.formlink:hover{color:#fff}
.shortcut{font-size:12px;padding:4px 6px;border-radius:6px;cursor:pointer}
.shortcut:hover{background:#7a2474}.shortcut b{color:var(--accent);font-family:ui-monospace,monospace}
.sess{font-size:13px;padding:4px 8px;border-radius:6px;display:block;color:#ece3ee}.sess:hover{background:#7a2474}
/* center */
.center{display:flex;flex-direction:column;background:#fbfcfd}
/* centerdoc: scrollable document content (Navigate pages), not a chat column */
.pane.centerdoc{display:block;overflow-y:auto;background:#fbfcfd}
.centerdoc .wrap{max-width:920px;margin:0 auto;padding:26px 28px}
/* copilot pane reuses .right; tighten chat spacing for the narrower column */
.copilot-pane .msgs{padding:16px}
.copilot-pane .composer{padding:12px 14px}
.copilot-pane .scard{max-width:100%}
.center .chead{padding:14px 22px;border-bottom:1px solid var(--line);font-weight:600;color:var(--navy)}
.msgs{flex:1;overflow-y:auto;padding:22px;display:flex;flex-direction:column;gap:14px}
.bubble{max-width:760px;padding:12px 16px;border-radius:12px;font-size:14.5px;white-space:normal}
.bubble.user{align-self:flex-end;background:var(--navy);color:#fff;border-bottom-right-radius:3px}
.bubble.assistant{align-self:flex-start;background:#fff;border:1px solid var(--line);border-bottom-left-radius:3px}
.bubble.assistant pre{white-space:pre-wrap}
.toolchip{display:inline-block;font-size:11px;background:#eef4fb;color:var(--navy2);border:1px solid #d6e3f1;
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
.feed-item{border:1px solid var(--line);border-left:4px solid var(--accent);border-radius:8px;
padding:11px 13px;margin-bottom:11px;font-size:13px;cursor:pointer}
.feed-item:hover{background:#fafbfc}.feed-item .meta{color:var(--muted);font-size:11.5px;margin-bottom:3px}
.pill{display:inline-block;padding:1px 8px;border-radius:20px;font-size:10.5px;font-weight:600;text-transform:uppercase}
.pill.new{background:#eaf5ee;color:var(--green)}.pill.amended{background:#fdf0e3;color:var(--amber)}
iframe.pdf{width:100%;height:100%;border:none}
.formmeta{font-size:13px}.formmeta dt{color:var(--muted);font-size:11px;text-transform:uppercase;margin-top:8px}
.btn{display:inline-block;background:var(--navy);color:#fff;padding:9px 16px;border-radius:7px;font-size:14px}
.form{background:#fff;border:1px solid var(--line);border-radius:10px;padding:24px;max-width:380px;margin:60px auto}
.form input{width:100%;padding:10px;border:1px solid var(--line);border-radius:7px;margin:6px 0 14px}
.wrap{max-width:1000px;margin:0 auto;padding:24px}
table{width:100%;border-collapse:collapse;background:#fff;border:1px solid var(--line);border-radius:10px;overflow:hidden}
th,td{text-align:left;padding:9px 13px;border-bottom:1px solid var(--line);font-size:13.5px}
th{background:#fbfbfc;color:var(--muted);font-size:11px;text-transform:uppercase}
/* Copilot drawer (collapsible chat on Navigate pages) */
.cop-fab{position:fixed;right:20px;bottom:20px;z-index:50;background:var(--accent);color:#fff;
border:none;border-radius:30px;padding:12px 18px;font-weight:600;cursor:pointer;box-shadow:0 4px 16px rgba(0,0,0,.2)}
.cop-fab:hover{filter:brightness(1.05)}
.cop{position:fixed;top:0;right:0;height:100vh;width:400px;max-width:92vw;background:#fff;
border-left:1px solid var(--line);box-shadow:-6px 0 24px rgba(0,0,0,.10);z-index:60;
display:flex;flex-direction:column;transform:translateX(100%);transition:transform .22s ease}
.cop.open{transform:translateX(0)}
.cop .chead{padding:13px 16px;border-bottom:1px solid var(--line);font-weight:600;color:var(--navy);
display:flex;align-items:center;justify-content:space-between}
.cop .x{cursor:pointer;color:var(--muted);font-size:18px;line-height:1}
.cop .msgs{flex:1;overflow-y:auto;padding:14px;display:flex;flex-direction:column;gap:10px}
.cop .composer{padding:12px 14px;border-top:1px solid var(--line);display:flex;gap:8px}
.cop .composer textarea{flex:1;resize:none;border:1px solid var(--line);border-radius:9px;padding:9px;font:inherit;height:42px}
.cop .composer button{background:var(--navy);color:#fff;border:none;border-radius:9px;padding:0 16px;font-weight:600;cursor:pointer}
.cop .scard{background:#fff;border:1px solid var(--line);border-radius:9px;padding:7px 10px;font-size:12px;cursor:pointer;margin:3px 0}
.cop .scard:hover{border-color:var(--accent);color:var(--navy)}
""")

MARKED = Script(src="https://cdn.jsdelivr.net/npm/marked/marked.min.js")
PLOTLY = Script(src="https://cdn.plot.ly/plotly-2.35.2.min.js")
VISNET = Script(src="https://unpkg.com/vis-network@9.1.9/standalone/umd/vis-network.min.js")

# Colours for the Document Hierarchy tree (JTC palette).
_HIER_LEAF = {"downloadable": "#ba2a84", "online": "#6b1766", "reference": "#8a7d92"}
_HIER_BRANCH = {"jur": "#550055", "cat": "#9c5797", "typ": "#c9a3c6"}


def current_user(sess):
    return sess.get("uid") if sess else None


def user_email(sess):
    return sess.get("email", "") if sess else ""


def require(sess):
    if LOGIN_REQUIRED and not current_user(sess):
        return RedirectResponse("/login", status_code=303)
    return None


app, rt = fast_app(hdrs=(MARKED,), secret_key=os.environ.get("APP_SECRET", "taxhub-2026"),
                   pico=False)


@rt("/health")
def health():
    return JSONResponse({"status": "ok"})


@rt("/login", methods=["GET"])
def login_form(sess, error: str = ""):
    return Title("Sign in · TaxHub"), CSS, Form(
        H2("JTC TaxHub"), P("Sign in", style="color:#6b7686"),
        (P(error, style="color:#c0392b") if error else ""),
        Input(name="email", placeholder="Email", type="email"),
        Input(name="password", placeholder="Password", type="password"),
        Button("Sign in", cls="btn", style="width:100%"),
        method="post", action="/login", cls="form")


@rt("/login", methods=["POST"])
def login_submit(sess, email: str = "", password: str = ""):
    import bcrypt
    user = store.get_user_by_email(email)
    if user and user["password_hash"] and bcrypt.checkpw(
            password.encode(), user["password_hash"].encode()):
        sess["uid"] = user["id"]
        sess["email"] = user.get("email") or email
        return RedirectResponse("/", status_code=303)
    return RedirectResponse("/login?error=Invalid+credentials", status_code=303)


@rt("/logout")
def logout(sess):
    sess.clear()
    return RedirectResponse("/login", status_code=303)


# ── Admin: refresh the forms catalogue (scrape) from inside the container ──────
# Runs the same scrape as `python -m ingest.cli --forms`, but in-process so the
# downloaded PDFs land on the persistent /app/data volume and metadata is written
# to the live store. Gated by a session OR the APP_SECRET token (for curl/ops).
# Long-running, so it runs in a daemon thread; poll /admin/refresh-status.
_REFRESH = {"running": False, "done": False, "log": [], "summary": None}


def _admin_ok(sess, request) -> bool:
    """Allow a logged-in session, or an X-Admin-Token header matching APP_SECRET.
    The token is read from a header (not the URL) so it never lands in access logs."""
    if require(sess) is None:
        return True
    tok = request.headers.get("x-admin-token", "") if request is not None else ""
    return bool(tok) and tok == os.environ.get("APP_SECRET", "taxhub-2026")


def _run_refresh(jurisdiction):
    from ingest.forms import scrape_forms, load_catalogue
    _REFRESH.update(running=True, done=False, log=[], summary=None)
    try:
        codes = [jurisdiction] if jurisdiction else list(load_catalogue())
        agg = {"recorded": 0, "downloaded": 0, "portal": 0, "failed_downloads": 0}
        for c in codes:
            r = scrape_forms(c, download=True)
            for k in agg:
                agg[k] += r.get(k, 0)
            _REFRESH["log"].append(
                f"{c}: recorded={r['recorded']} downloaded={r['downloaded']} "
                f"portal={r['portal']} failed={r['failed_downloads']}")
        prov = store.build_provenance_edges()        # keep IMPLEMENTS edges fresh
        _REFRESH["log"].append(f"provenance: {prov}")
        _REFRESH["summary"] = agg
    except Exception as e:  # noqa: BLE001
        _REFRESH["log"].append(f"ERROR: {e!r}")
    finally:
        _REFRESH.update(running=False, done=True)


@rt("/admin/refresh-forms", methods=["POST"])
def refresh_forms(sess, request, jurisdiction: str = ""):
    import threading
    if not _admin_ok(sess, request):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    if _REFRESH["running"]:
        return JSONResponse({"status": "already running", **_REFRESH})
    threading.Thread(target=_run_refresh, args=(jurisdiction or None,),
                     daemon=True).start()
    return JSONResponse({"status": "started", "jurisdiction": jurisdiction or "ALL"})


@rt("/admin/refresh-status")
def refresh_status(sess, request):
    if not _admin_ok(sess, request):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    return JSONResponse(_REFRESH)


@rt("/admin/build-provenance", methods=["POST"])
def build_provenance(sess, request):
    """One-shot: persist Form.legislation_ref → (:Form)-[:IMPLEMENTS]->(:Legislation)
    edges (+ SOURCED_FROM to tracked Documents). Idempotent."""
    if not _admin_ok(sess, request):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    return JSONResponse(store.build_provenance_edges())


# ── 3-pane components ───────────────────────────────────────────────────────
def tree_component():
    nodes = []
    for j in forms_tree():
        cats = []
        for c in j["categories"]:
            types = []
            for t in c["types"]:
                forms = [Div(f["title"], cls="formlink",
                             onclick=f"openForm({f['id']})") for f in t["forms"]]
                types.append(Details(Summary(t["form_type"], cls="typ"), *forms, cls="tree"))
            cats.append(Details(Summary(CATEGORY_LABELS.get(c["category"], c["category"]),
                                        cls="cat"), *types, cls="tree"))
        nodes.append(Details(Summary(JUR_NAMES.get(j["code"], j["code"]), cls="jur"),
                             *cats, cls="tree"))
    return Div(*nodes)


def left_pane(sess):
    sessions = store.list_chat_sessions(user_email(sess), limit=15) if user_email(sess) else []
    return Div(
        Div("JTC ", Span("TaxHub"), cls="brand"),
        A("+ New chat", href="/", cls="newchat"),
        Div(Div("Recent chats", cls="lbl"),
            *[A(s.get("title") or "Chat", href=f"/?sid={s['id']}", cls="sess")
              for s in sessions] or [Div("No chats yet", cls="muted", style="font-size:12px")],
            cls="section"),
        Div(Div("Navigate", cls="lbl"),
            A("Dashboard", href="/dashboard", cls="navlink"),
            A("Entities", href="/entities", cls="navlink"),
            A("Obligations", href="/obligations", cls="navlink"),
            A("📅 Calendar", href="/calendar", cls="navlink"),
            A("🗺 Coverage", href="/coverage", cls="navlink"),
            A("Jurisdictions", href="/jurisdictions", cls="navlink"),
            A("Documents", href="/documents", cls="navlink"),
            A("Changes", href="/changes", cls="navlink"),
            cls="section"),
        Div(Div("Tax Forms Tree", cls="lbl"), tree_component(), cls="section"),
        Div(Div("Shortcuts", cls="lbl"),
            *[Div(B(p), " ", desc, cls="shortcut", onclick=f"fillChat({ex!r})")
              for p, desc, ex in SHORTCUTS],
            cls="section"),
        Div(Div("Help", cls="lbl"),
            A("📘 User Guide", href="/help", cls="navlink"),
            A("🛠 Technical Guide", href="/technical-guide", cls="navlink"),
            A("🌳 Document Hierarchy", href="/document-hierarchy", cls="navlink"),
            A("🕸 Ontology", href="/ontology", cls="navlink"),
            A("Sign out", href="/logout", cls="navlink"),
            cls="section"),
        cls="pane left")


def bubble(role, content):
    if role == "assistant":
        return Div(content, cls="bubble assistant", **{"data-md": "1"})
    return Div(content, cls="bubble user")


def center_pane(messages):
    msg_divs = [bubble(m["role"], m["content"]) for m in messages] or [
        Div(Div("TaxHub Assistant", style="font-weight:600;margin-bottom:4px"),
            "Ask me which tax form to file, or a tax-law question. I'll route to the "
            "right specialist agent and cite my sources.", cls="bubble assistant")]
    return Div(
        Div("AI Assistant", cls="chead"),
        Div(*msg_divs, id="msgs", cls="msgs"),
        Div(*[Div(s, cls="scard", onclick=f"fillChat({s!r})") for s in SUGGESTIONS],
            id="cards", cls="cards"),
        Form(Textarea(name="msg", placeholder="Ask anything, or type a shortcut like form: …",
                      id="inp"),
             Button("Send", type="submit"),
             id="composer", cls="composer", onsubmit="return sendMessage(event)"),
        cls="pane center")


def feed_item(ch):
    kind = ch.get("change_type", "new")
    return Div(
        Div(Span(kind, cls=f"pill {kind}"), " ",
            f"{ch['jurisdiction_code']} · {(ch.get('detected_at') or '')[:10]}", cls="meta"),
        Div(ch.get("title", ""), style="font-weight:600;font-size:13px"),
        (Div(ch["ai_summary"][:160], style="color:#6b7686;margin-top:3px")
         if ch.get("ai_summary") else ""),
        cls="feed-item", onclick=f"openDoc({ch.get('document_id')})")


def right_pane():
    changes = store.recent_changes(20)
    return Div(
        Div(Span("Recent changes", id="rtitle"),
            Span("", id="rclose", cls="x", onclick="closePdf()"), cls="rhead"),
        Div(Div(*[feed_item(c) for c in changes], id="feed"), id="rbody", cls="rbody"),
        cls="pane right", id="rightpane")


JS = Script("""
function fillChat(t){var i=document.getElementById('inp');i.value=t;i.focus();}
function renderMd(el){if(window.marked){el.innerHTML=linkMarkers(marked.parse(el.textContent));}}
function linkMarkers(h){
  h=h.replace(/\\[form:(\\d+)\\]/g,'<a href="#" onclick="openForm($1);return false">📄 open form</a>');
  h=h.replace(/\\[doc:(\\d+)\\]/g,'<a href="#" onclick="openDoc($1);return false">🔗 open doc</a>');
  return h;}
document.querySelectorAll('[data-md]').forEach(renderMd);
function addBubble(role,html){var m=document.getElementById('msgs');
  var d=document.createElement('div');d.className='bubble '+role;d.innerHTML=html;
  m.appendChild(d);m.scrollTop=m.scrollHeight;return d;}
let streaming=false;
async function sendMessage(e){if(e)e.preventDefault();if(streaming)return false;
  var i=document.getElementById('inp');var msg=i.value.trim();if(!msg)return false;
  streaming=true;addBubble('user',msg.replace(/</g,'&lt;'));i.value='';
  var sid=new URLSearchParams(location.search).get('sid')||'';
  var b=addBubble('assistant','<span style="color:#9aa">…</span>');var acc='';
  var resp=await fetch('/chat',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},
    body:new URLSearchParams({msg:msg,sid:sid})});
  var rd=resp.body.getReader(),dec=new TextDecoder(),buf='';
  while(true){var r=await rd.read();if(r.done)break;buf+=dec.decode(r.value,{stream:true});
    var idx;while((idx=buf.indexOf('\\n\\n'))>=0){var raw=buf.slice(0,idx);buf=buf.slice(idx+2);
      var ev=raw.match(/^event: (.*)$/m),da=raw.match(/^data: (.*)$/m);if(!ev||!da)continue;
      var type=ev[1],data=JSON.parse(da[1]);
      if(type==='token'){if(acc===''){b.innerHTML='';}acc+=data.text;
        b.innerHTML=linkMarkers(window.marked?marked.parse(acc):acc);}
      else if(type==='tool_start'){var c=document.createElement('span');c.className='toolchip';
        c.textContent='⚙ '+data.name;b.appendChild(c);}
      else if(type==='session'&&data.sid){history.replaceState(0,'','/?sid='+data.sid);}
    }
    document.getElementById('msgs').scrollTop=1e9;}
  streaming=false;return false;}
function openForm(id){var rp=document.getElementById('rbody');
  if(!rp){window.open('/form-pdf/'+id,'_blank');return;}
  document.getElementById('rtitle').textContent='Form #'+id;document.getElementById('rclose').textContent='✕';
  fetch('/form/'+id).then(r=>r.text()).then(h=>{rp.innerHTML=h;});}
function openDoc(id){if(!id)return;var rb=document.getElementById('rbody');
  if(!rb){window.open('/document/'+id,'_blank');return;}
  document.getElementById('rtitle').textContent='Document #'+id;
  document.getElementById('rclose').textContent='✕';
  rb.innerHTML='<iframe class="pdf" src="/document/'+id+'?embed=1"></iframe>';}
function closePdf(){location.reload();}
""")


@rt("/")
def home(sess, sid: int = 0):
    if (r := require(sess)):
        return r
    messages = store.get_chat_messages(sid) if sid else []
    return (Title("TaxHub Assistant"), CSS,
            Div(left_pane(sess), center_pane(messages), right_pane(), cls="app"), JS)


# ── Chat SSE ────────────────────────────────────────────────────────────────
def _shortcut(msg: str):
    low = msg.strip().lower()
    if low.startswith("form:"):
        return document_agent.invoke(msg.split(":", 1)[1].strip())
    if low.startswith("law:"):
        return law_agent.invoke(msg.split(":", 1)[1].strip())
    if low.startswith("forms:"):
        return metadata_agent.invoke({"jurisdiction_code": msg.split(":", 1)[1].strip().upper()})
    if low.startswith("changes:"):
        return changes_agent.invoke({"jurisdiction_code": msg.split(":", 1)[1].strip().upper()})
    if low.startswith("find:"):
        return document_agent.invoke(msg.split(":", 1)[1].strip())
    return None


@rt("/chat", methods=["POST"])
async def chat(sess, msg: str = "", sid: int = 0):
    if (require(sess)):
        return JSONResponse({"error": "auth"}, status_code=401)
    email = user_email(sess)
    if not sid:
        sid = store.create_chat_session(email, title=msg[:48])
    store.add_chat_message(sid, "user", msg)
    from agents import sse as S

    async def stream():
        yield S.event("session", {"sid": sid})
        sc = _shortcut(msg)
        if sc is not None:
            yield S.event(S.TOKEN, {"text": sc})
            store.add_chat_message(sid, "assistant", sc)
            yield S.event(S.DONE, {"tools": 1})
            return
        acc = []
        async for ev in orchestrator.astream(msg):
            # tee token text so we can persist the full answer
            if '"token"' in ev:
                import json as _j
                try:
                    acc.append(_j.loads(ev.split("data: ", 1)[1])["text"])
                except Exception:  # noqa: BLE001
                    pass
            yield ev
        store.add_chat_message(sid, "assistant", "".join(acc) or "(no response)")

    return StreamingResponse(stream(), media_type="text/event-stream")


@rt("/form/{form_id}")
def form_view(sess, form_id: int):
    if (r := require(sess)):
        return r
    f = store.get_form(form_id)
    if not f:
        return Div("Form not found")
    has_pdf = bool(f.get("file_path"))
    viewer = (Iframe(src=f"/form-pdf/{form_id}", cls="pdf", style="height:60vh")
              if has_pdf else
              Div(P("No local PDF stored yet.", cls="muted"),
                  A("Open official source ↗", href=f.get("url") or "#", target="_blank", cls="btn")
                  if f.get("url") else ""))
    filing = f.get("filing_type") or "downloadable"
    leg = f.get("legislation_ref")
    return Div(
        H3(f["title"], style="margin:0 0 6px"),
        Div(FILING_LABELS.get(filing, filing), style="display:inline-block;font-weight:600;"
            "font-size:12px;padding:3px 10px;border-radius:20px;background:#eef4fb;"
            "color:#1b3a5b;margin-bottom:6px"),
        Dl(Dt("Filing type"), Dd(FILING_LABELS.get(filing, filing)),
           Dt("Jurisdiction"), Dd(JUR_NAMES.get(f["jurisdiction_code"], f["jurisdiction_code"])),
           Dt("Category"), Dd(CATEGORY_LABELS.get(f.get("category"), f.get("category") or "—")),
           Dt("Document type"), Dd(FORM_TYPE_LABELS.get(f.get("form_type"), f.get("form_type") or "—")),
           Dt("Who files"), Dd(f.get("who_files") or "—"),
           Dt("Deadline"), Dd(f.get("deadline") or "—"),
           Dt("Frequency"), Dd(f.get("frequency") or "—"),
           Dt("Authority"), Dd(f.get("authority") or "—"),
           *([Dt("Underlying law"), Dd(A("View legislation ↗", href=leg, target="_blank"))]
             if leg else []), cls="formmeta"),
        Div(viewer, style="margin-top:12px"))


@rt("/form-pdf/{form_id}")
def form_pdf(sess, form_id: int):
    if (require(sess)):
        return RedirectResponse("/login", status_code=303)
    f = store.get_form(form_id)
    if not f or not f.get("file_path"):
        return RedirectResponse(f.get("url") or "/", status_code=303) if f else Response("Not found", status_code=404)
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    p = root / f["file_path"]
    if not p.exists():
        return RedirectResponse(f.get("url") or "/", status_code=303)
    return Response(p.read_bytes(), media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{f["form_key"]}.pdf"'})


@rt("/api/feed")
def api_feed(sess, j: str = ""):
    changes = store.recent_changes(20, jurisdiction_code=j or None)
    return Div(*[feed_item(c) for c in changes], id="feed")


# ── Copilot: persistent right-hand chat pane (same agents as the Assistant) ───
COPILOT_SUGGESTIONS = [
    "What does Aurora Global Fund owe this year?",
    "Which entities have economic-substance filings due before 30 September?",
    "Show me every overdue obligation",
    "form: Cayman economic substance notification",
]


def copilot():
    """Right-hand chat pane for the Navigate pages — query the database with the
    same orchestrator/agents while the page stays visible alongside it (the third
    pane of the 3-pane layout, in place of the changes feed)."""
    return Div(
        Div(Span("🤖 Copilot"),
            Span("chat with your data", style="font-weight:400;color:var(--muted);font-size:12px"),
            cls="rhead"),
        Div(Div("Ask about your entities, obligations, deadlines, forms or recent "
                "changes — I use the same agents as the Assistant.", cls="bubble assistant"),
            *[Div(s, cls="scard", onclick=f"cpFill({s!r})") for s in COPILOT_SUGGESTIONS],
            id="cpMsgs", cls="msgs"),
        Form(Textarea(name="msg", placeholder="Ask the database…", id="cpInp"),
             Button("Send", type="submit"),
             cls="composer", onsubmit="return cpSend(event)"),
        cls="pane right copilot-pane", id="copilotPane")


COPILOT_JS = Script("""
function cpFill(t){var i=document.getElementById('cpInp');i.value=t;i.focus();}
function cpToggle(){var c=document.getElementById('copilot');c.classList.toggle('open');
  if(c.classList.contains('open'))document.getElementById('cpInp').focus();}
function cpBubble(role,html){var m=document.getElementById('cpMsgs');
  var d=document.createElement('div');d.className='bubble '+role;d.innerHTML=html;
  m.appendChild(d);m.scrollTop=m.scrollHeight;return d;}
let cpStreaming=false,cpSid='';
async function cpSend(e){if(e)e.preventDefault();if(cpStreaming)return false;
  var i=document.getElementById('cpInp');var msg=i.value.trim();if(!msg)return false;
  cpStreaming=true;cpBubble('user',msg.replace(/</g,'&lt;'));i.value='';
  var b=cpBubble('assistant','<span style="color:#9aa">…</span>');var acc='';
  var resp=await fetch('/chat',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},
    body:new URLSearchParams({msg:msg,sid:cpSid})});
  var rd=resp.body.getReader(),dec=new TextDecoder(),buf='';
  while(true){var r=await rd.read();if(r.done)break;buf+=dec.decode(r.value,{stream:true});
    var idx;while((idx=buf.indexOf('\\n\\n'))>=0){var raw=buf.slice(0,idx);buf=buf.slice(idx+2);
      var ev=raw.match(/^event: (.*)$/m),da=raw.match(/^data: (.*)$/m);if(!ev||!da)continue;
      var type=ev[1],data=JSON.parse(da[1]);
      if(type==='token'){if(acc===''){b.innerHTML='';}acc+=data.text;
        b.innerHTML=linkMarkers(window.marked?marked.parse(acc):acc);}
      else if(type==='tool_start'){var c=document.createElement('span');c.className='toolchip';
        c.textContent='⚙ '+data.name;b.appendChild(c);}
      else if(type==='session'&&data.sid){cpSid=data.sid;}
    }
    document.getElementById('cpMsgs').scrollTop=1e9;}
  cpStreaming=false;return false;}
""")


# ── Secondary pages: same 3-pane shell as the Assistant ──────────────────────
# left = nav, center = the page content, right = the Copilot (in place of the
# changes feed). Pass sess so the left pane can show recent chats + sign-out.
def Page(sess, *content, title="TaxHub", with_copilot=True):
    center = Div(Div(*content, cls="wrap"), cls="pane center centerdoc")
    right = copilot() if with_copilot else Div(cls="pane right")
    return (Title(title), CSS,
            Div(left_pane(sess), center, right, cls="app"),
            MARKED, JS, COPILOT_JS)


@rt("/dashboard")
def dashboard(sess):
    if (r := require(sess)):
        return r
    st = store.stats()
    jurs = store.list_jurisdictions_with_counts()
    metrics = {"entities": store.count_entities(),
               **{k: v for k, v in st.items() if not isinstance(v, list)}}
    # Compliance roll-up across all obligations.
    from collections import Counter
    all_obs = store.list_obligations(limit=10000)
    by_status = Counter(o.get("status") or "not_started" for o in all_obs)
    total = len(all_obs)
    done = by_status.get("filed", 0) + by_status.get("confirmed", 0)
    pct = round(100 * done / total) if total else 0
    unver = sum(1 for o in all_obs if not o.get("verified"))
    compliance = Div(
        H2("Compliance"),
        (Div(
            Div(Span(f"{pct}%", style="font-size:30px;font-weight:700;color:var(--navy)"),
                Span(" filed / confirmed", style="color:var(--muted)"),
                style="margin-bottom:6px"),
            P(A(f"{total} obligations", href="/obligations"), " across ",
              A(f"{store.count_entities()} entities", href="/entities"), " · ",
              A(f"{unver} awaiting sign-off", href="/obligations"),
              style="color:var(--muted);font-size:13px"),
            Table(Tr(Th("Status"), Th("Count")),
                  *[Tr(Td(A(ob_status_badge(s), href=f"/obligations?status={s}")),
                       Td(str(by_status.get(s, 0)))) for s in OB_STATUS if by_status.get(s)]),
        ) if total else P("No obligations yet — open an entity and click "
                          "“Determine obligations”.", cls="muted")),
        style="margin:14px 0")
    # Upcoming deadlines (resolved dates) — the Monitor roll-up.
    from datetime import date
    dig = monitor.deadline_digest(store, date.today(), horizon_days=90)
    nxt = (dig["overdue"] + dig["upcoming"])[:6]
    deadlines = Div(
        H2("Deadlines"),
        P(A(f"{dig['counts']['overdue']} overdue", href="/calendar?urg=overdue",
            style="color:#c0392b;font-weight:600"), " · ",
          A(f"{dig['counts']['upcoming']} due within 90 days", href="/calendar?urg=due_soon"),
          " · ", A("open calendar →", href="/calendar"),
          style="color:var(--muted);font-size:13px"),
        (Table(Tr(Th("Due"), Th("Entity"), Th("Obligation"), Th("")),
               *[Tr(Td(r.get("due") or "—"),
                    Td(A(r.get("entity_name") or "—", href=f"/entity/{r['entity_id']}")),
                    Td((r.get("title") or "form")[:48]),
                    Td(urgency_badge(r["urgency"]))) for r in nxt])
         if nxt else P("Nothing overdue or due in the next 90 days.", cls="muted")),
        style="margin:14px 0") if total else ""
    return Page(sess, H1("Dashboard"),
        Table(Tr(Th("Metric"), Th("Count")),
              *[Tr(Td(A(k, href="/entities") if k == "entities" else k), Td(str(v)))
                for k, v in metrics.items()]),
        compliance,
        deadlines,
        H2("Jurisdictions"),
        Table(Tr(Th("Code"), Th("Documents")),
              *[Tr(Td(A(j["code"], href=f"/jurisdiction/{j['code']}")), Td(str(j["docs"])))
                for j in jurs]),
        title="Dashboard · TaxHub")


@rt("/jurisdictions")
def jurisdictions(sess):
    if (r := require(sess)):
        return r
    jurs = store.list_jurisdictions_with_counts()
    return Page(sess, H1("Jurisdictions"),
        Table(Tr(Th("Code"), Th("Name"), Th("Documents")),
              *[Tr(Td(j["code"]), Td(A(JUR_NAMES.get(j["code"], j["code"]),
                href=f"/jurisdiction/{j['code']}")), Td(str(j["docs"]))) for j in jurs]))


@rt("/jurisdiction/{code}")
def jurisdiction(sess, code: str):
    if (r := require(sess)):
        return r
    docs = store.list_documents_for_jurisdiction(code)
    return Page(sess, H1(JUR_NAMES.get(code, code)),
        Table(Tr(Th("Document"), Th("Type"), Th("Versions"), Th("Status")),
              *[Tr(Td(A(d["title"], href=f"/document/{d['id']}")), Td(d.get("doc_type", "")),
                   Td(str(d.get("versions", 0))), Td(d.get("status", ""))) for d in docs]),
        title=f"{code} · TaxHub")


@rt("/document/{doc_id}")
def document(sess, doc_id: int, embed: int = 0):
    if (require(sess)) and not embed:
        return RedirectResponse("/login", status_code=303)
    d = store.get_document_by_id(doc_id)
    if not d:
        return Div("Not found")
    versions = store.list_versions(doc_id)
    changes = store.list_changes_for_document(doc_id)
    cites = store.list_citations(doc_id)
    body = Div(
        H1(d["title"]), P(d.get("reference") or "", cls="muted"),
        (A("Open source ↗", href=d["url"], target="_blank", cls="btn") if d.get("url") else ""),
        H2("Versions"),
        Ul(*[Li(f"v{v['version_no']} · {v.get('content_hash','')[:10]} · {(v.get('fetched_at') or '')[:10]}")
             for v in versions]),
        H2("Changes"),
        Ul(*[Li(f"{c['change_type']} — {(c.get('ai_summary') or '')[:140]}") for c in changes]
            or [Li("No changes", cls="muted")]),
        H2("Citations"),
        Ul(*[Li(f"{c['cited_instrument']} {c.get('locator') or ''}") for c in cites[:30]]
            or [Li("None", cls="muted")]))
    if embed:
        return (CSS, Div(body, cls="wrap"))
    return Page(sess, body, title=f"{d['title'][:40]} · TaxHub")


# ── Document Hierarchy (Plotly tree: Jurisdiction ▸ Category ▸ Form type ▸ Form) ──
def _hierarchy_payload(jur: str | None = None) -> dict:
    """Hierarchy payload for Plotly icicle/treemap/sunburst, from forms_tree().
    Leaves are forms (value 1, coloured by filing type, carry the form id);
    branch values are leaf counts so branchvalues='total' is consistent."""
    ids, labels, parents, values, colors, meta = [], [], [], [], [], []

    def add(_id, label, parent, value, color, m=""):
        ids.append(_id); labels.append(label); parents.append(parent)
        values.append(value); colors.append(color); meta.append(m)

    for j in forms_tree(jur):
        jc = j["code"]; jcount = 0
        for c in j["categories"]:
            ccount = 0
            cid = f"{jc}|{c['category']}"
            for t in c["types"]:
                tid = f"{cid}|{t['form_type']}"
                for f in t["forms"]:
                    add(f"{tid}|{f['id']}", f.get("title") or "form", tid, 1,
                        _HIER_LEAF.get(f.get("filing_type") or "downloadable", "#cccccc"),
                        f["id"])
                add(tid, FORM_TYPE_LABELS.get(t["form_type"], t["form_type"]),
                    cid, len(t["forms"]), _HIER_BRANCH["typ"])
                ccount += len(t["forms"])
            add(cid, CATEGORY_LABELS.get(c["category"], c["category"]),
                jc, ccount, _HIER_BRANCH["cat"])
            jcount += ccount
        add(jc, JUR_NAMES.get(jc, jc), "", jcount, _HIER_BRANCH["jur"])
    return {"ids": ids, "labels": labels, "parents": parents,
            "values": values, "colors": colors, "meta": meta}


_HBTN = ("padding:5px 12px;border:1px solid var(--navy);border-radius:6px;"
         "background:#fff;color:#6b1766;cursor:pointer;font-size:13px")
_HLEGEND = [("downloadable", "📄 Downloadable"), ("online", "🌐 Online"),
            ("reference", "📘 Reference")]


@rt("/document-hierarchy")
def document_hierarchy(sess, jur: str = ""):
    if (r := require(sess)):
        return r
    import json as _json
    payload = _hierarchy_payload(jur or None)
    jurs = sorted({f["jurisdiction_code"] for f in store.list_forms(limit=5000)})
    nleaf = sum(1 for m in payload["meta"] if m)
    legend = Span(*[Span(Span("●", style=f"color:{_HIER_LEAF[k]}"), f" {lbl} ",
                         style="font-size:12px;margin-left:8px") for k, lbl in _HLEGEND],
                  style="color:var(--muted)")
    controls = Div(
        Select(Option("All jurisdictions", value="", selected=(jur == "")),
               *[Option(JUR_NAMES.get(c, c), value=c, selected=(c == jur)) for c in jurs],
               onchange="location='/document-hierarchy'+(this.value?('?jur='+this.value):'')",
               style="padding:7px;border:1px solid var(--line);border-radius:7px"),
        *[Button(lbl, onclick=f"setType('{t}')", id=f"bt_{t}", style=_HBTN)
          for t, lbl in [("icicle", "Icicle"), ("treemap", "Treemap"), ("sunburst", "Sunburst")]],
        legend,
        Span(f"{nleaf} forms", style="color:var(--muted);font-size:12px;margin-left:auto"),
        style="display:flex;gap:6px;align-items:center;padding:10px 22px;"
              "border-bottom:1px solid var(--line);flex-wrap:wrap")
    center = Div(
        Div("Document Hierarchy", cls="chead"),
        controls,
        Div(id="plot", style="flex:1;min-height:0"),
        cls="pane center")
    right = Div(
        Div(Span("Form detail", id="rtitle"),
            Span("", id="rclose", cls="x", onclick="closePdf()"), cls="rhead"),
        Div(Div("Click a form (a leaf of the tree) to view it here.", cls="muted"),
            id="rbody", cls="rbody"),
        cls="pane right", id="rightpane")
    data_script = Script(f"window.HDATA={_json.dumps(payload)};")
    plot_js = Script("""
let HTYPE='icicle';
function drawPlot(){
  var d=window.HDATA;
  var t={type:HTYPE,ids:d.ids,labels:d.labels,parents:d.parents,values:d.values,
    branchvalues:'total',customdata:d.meta,
    marker:{colors:d.colors,line:{width:1,color:'#fff'}},
    hovertemplate:'%{label}<br>%{value} form(s)<extra></extra>'};
  if(HTYPE!=='sunburst'){t.tiling={pad:1};}
  Plotly.react('plot',[t],{margin:{l:4,r:4,t:4,b:4}},{responsive:true,displayModeBar:false});
  var el=document.getElementById('plot');
  if(!el._wired){el.on('plotly_click',function(e){var p=e.points&&e.points[0];
    if(p&&p.customdata){openForm(p.customdata);}});el._wired=true;}
  ['icicle','treemap','sunburst'].forEach(function(x){var b=document.getElementById('bt_'+x);
    if(b){b.style.background=(x===HTYPE)?'#6b1766':'#fff';b.style.color=(x===HTYPE)?'#fff':'#6b1766';}});
}
function setType(x){HTYPE=x;drawPlot();}
window.addEventListener('resize',function(){if(window.Plotly)Plotly.Plots.resize('plot');});
drawPlot();
""")
    return (Title("Document Hierarchy · TaxHub"), CSS,
            Div(left_pane(sess), center, right, cls="app"),
            PLOTLY, JS, data_script, plot_js)


# ── Ontology (Help): provenance/network graph + schema meta-graph ─────────────
_ONTO_GROUPS = {  # vis-network group styling (JTC palette)
    "jurisdiction": {"color": "#550055", "shape": "dot"},
    "legislation": {"color": "#48484f", "shape": "diamond"},
    "document": {"color": "#9c5797", "shape": "square"},
    "form_downloadable": {"color": "#ba2a84", "shape": "dot"},
    "form_online": {"color": "#6b1766", "shape": "dot"},
    "form_reference": {"color": "#8a7d92", "shape": "dot"},
}


@rt("/ontology")
def ontology(sess, mode: str = "instance", jur: str = ""):
    if (r := require(sess)):
        return r
    import json as _json
    from web import ontology as onto
    mode = "schema" if mode == "schema" else "instance"
    data = (onto.build_schema(store) if mode == "schema"
            else onto.build_instance(store, jur or None))
    jurs = sorted({f["jurisdiction_code"] for f in store.list_forms(limit=5000)})

    def mbtn(m, lbl):
        on = (m == mode)
        return A(lbl, href=f"/ontology?mode={m}" + (f"&jur={jur}" if jur and m == "instance" else ""),
                 style=_HBTN + (";background:#6b1766;color:#fff" if on else ""))
    legend = Span(
        Span("● ", style="color:#550055"), "Jurisdiction  ",
        Span("● ", style="color:#ba2a84"), "Form  ",
        Span("◆ ", style="color:#48484f"), "Legislation",
        style="color:var(--muted);font-size:12px;margin-left:8px")
    controls = Div(
        mbtn("instance", "Provenance"), mbtn("schema", "Schema"),
        (Select(Option("All jurisdictions", value="", selected=(jur == "")),
                *[Option(JUR_NAMES.get(c, c), value=c, selected=(c == jur)) for c in jurs],
                onchange="location='/ontology?mode=instance'+(this.value?('&jur='+this.value):'')",
                style="padding:7px;border:1px solid var(--line);border-radius:7px")
         if mode == "instance" else Span()),
        legend,
        Span(data.get("stats", ""), style="color:var(--muted);font-size:12px;margin-left:auto"),
        style="display:flex;gap:6px;align-items:center;padding:10px 22px;"
              "border-bottom:1px solid var(--line);flex-wrap:wrap")
    center = Div(
        Div("Ontology — ", Span("how forms trace back to the law" if mode == "instance"
            else "how the graph database is organised", style="font-weight:400;color:var(--muted)"),
            cls="chead"),
        controls,
        Div(id="net", style="flex:1;min-height:0;background:#fbfcfd"),
        cls="pane center")
    right = Div(
        Div(Span("Detail", id="rtitle"),
            Span("", id="rclose", cls="x", onclick="closePdf()"), cls="rhead"),
        Div(Div("Click a form node to open it; click a legislation node to open the "
                "source law.", cls="muted"), id="rbody", cls="rbody"),
        cls="pane right", id="rightpane")
    data_script = Script(f"window.GDATA={_json.dumps(data)};"
                         f"window.GGROUPS={_json.dumps(_ONTO_GROUPS)};")
    net_js = Script("""
var d=window.GDATA;
var nodes=new vis.DataSet(d.nodes), edges=new vis.DataSet(d.edges);
var groups={};Object.keys(window.GGROUPS).forEach(function(k){var g=window.GGROUPS[k];
  groups[k]={shape:g.shape,color:{background:g.color,border:g.color,
    highlight:{background:g.color,border:'#000'}},font:{color:'#48484f',size:13}};});
var net=new vis.Network(document.getElementById('net'),{nodes:nodes,edges:edges},{
  groups:groups,
  nodes:{borderWidth:1,scaling:{min:8,max:48},font:{size:13}},
  edges:{color:{color:'#cdbcd0'},arrows:{to:{scaleFactor:0.5}},
    font:{size:9,color:'#9a93a6',align:'middle'},smooth:{type:'dynamic'}},
  physics:{stabilization:{iterations:160},barnesHut:{springLength:130,avoidOverlap:0.2}},
  interaction:{hover:true,tooltipDelay:120}});
net.on('click',function(p){if(!p.nodes.length)return;var n=nodes.get(p.nodes[0]);
  if(n.formid){openForm(n.formid);}
  else if(n.url){window.open(n.url,'_blank');}});
""")
    return (Title("Ontology · TaxHub"), CSS,
            Div(left_pane(sess), center, right, cls="app"),
            VISNET, JS, data_script, net_js)


# ── Entities (Phase 1: model + CRUD + CSV import) ─────────────────────────────
def _jur_badges(codes):
    return Span(*[Span(c, style="display:inline-block;background:#efeaf3;color:#6b1766;"
                       "border-radius:5px;padding:1px 7px;margin:1px;font-size:11px")
                  for c in (codes or [])]) or Span("—", cls="muted")


@rt("/entities")
def entities(sess, added: str = ""):
    if (r := require(sess)):
        return r
    ents = store.list_entities(limit=1000)
    from collections import Counter
    all_obs = store.list_obligations(limit=10000)
    n_by_ent = Counter(o["entity_id"] for o in all_obs)
    done_by_ent = Counter(o["entity_id"] for o in all_obs
                          if (o.get("status") in ("filed", "confirmed")))

    def ob_chip(eid):
        n = n_by_ent.get(eid, 0)
        if not n:
            return Span("—", cls="muted")
        return Span(f"{done_by_ent.get(eid, 0)}/{n} filed",
                    style="display:inline-block;background:#efeaf3;color:#6b1766;"
                          "border-radius:20px;padding:1px 9px;font-size:11px;font-weight:600")

    rows = [Tr(Td(A(e["name"], href=f"/entity/{e['id']}")),
               Td(e.get("type") or "—"), Td(e.get("domicile") or "—"),
               Td(_jur_badges(e.get("jurisdictions"))),
               Td(e.get("fy_end") or "—"), Td(ob_chip(e["id"])),
               Td(e.get("client_ref") or "—"))
            for e in ents]
    add_form = Form(
        H3("Add an entity"),
        (P("✓ Saved.", style="color:#1c7c44") if added else ""),
        Div(Input(name="name", placeholder="Entity name", required=True, style="width:40%"),
            Select(*[Option(t, value=t) for t in ENTITY_TYPES], name="type", style="width:18%;margin:0 1%"),
            Select(*[Option(JUR_NAMES.get(c, c), value=c) for c in sorted(JUR_NAMES)],
                   name="domicile", style="width:18%;margin-right:1%"),
            Input(name="client_ref", placeholder="Client ref", style="width:18%"),
            style="display:flex;gap:4px;margin:6px 0"),
        Div(Input(name="jurisdictions", placeholder="Jurisdictions (comma codes, e.g. KY,US)", style="width:40%"),
            Input(name="fy_end", placeholder="Financial year-end (e.g. 31 December)", style="width:30%;margin:0 1%"),
            Input(name="activities", placeholder="Activities (comma)", style="width:28%"),
            style="display:flex;gap:4px;margin:6px 0"),
        Button("Save entity", cls="btn", style="margin-top:8px"),
        method="post", action="/entity")
    import_form = Form(
        H3("Import from CSV"),
        P("Columns: name,type,domicile,jurisdictions,fy_end,activities,client_ref "
          "(jurisdictions & activities semicolon- or pipe-separated).", cls="muted",
          style="font-size:12px"),
        Input(type="file", name="csv_file", accept=".csv", required=True),
        Button("Import CSV", cls="btn", style="margin-left:8px"),
        method="post", action="/entities/import", enctype="multipart/form-data")
    return Page(sess, 
        H1("Entities"),
        P(f"{len(ents)} entities. The funds/SPVs you administer — obligations, "
          "deadlines and filing status build on these in the next phases.", cls="muted"),
        add_form, import_form,
        H2("Portfolio"),
        (Table(Tr(Th("Name"), Th("Type"), Th("Domicile"), Th("Jurisdictions"),
                  Th("FY end"), Th("Obligations"), Th("Client ref")), *rows) if rows
         else P("No entities yet — add one above or import a CSV.", cls="muted")),
        title="Entities · TaxHub")


@rt("/entity", methods=["POST"])
def entity_create(sess, name: str = "", type: str = "company", domicile: str = "",
                  jurisdictions: str = "", fy_end: str = "", activities: str = "",
                  client_ref: str = ""):
    if (require(sess)):
        return RedirectResponse("/login", status_code=303)
    if not name.strip():
        return RedirectResponse("/entities", status_code=303)
    store.upsert_entity({
        "name": name.strip(), "type": type, "domicile": domicile,
        "jurisdictions": [j.strip().upper() for j in re.split(r"[,;|]", jurisdictions) if j.strip()],
        "fy_end": fy_end.strip() or None,
        "activities": [a.strip() for a in re.split(r"[,;|]", activities) if a.strip()],
        "client_ref": client_ref.strip() or None, "status": "active"})
    return RedirectResponse("/entities?added=1", status_code=303)


@rt("/entities/import", methods=["POST"])
async def entities_import(sess, csv_file: UploadFile):
    if (require(sess)):
        return RedirectResponse("/login", status_code=303)
    import csv
    import io
    raw = (await csv_file.read()).decode("utf-8-sig", errors="ignore")
    n = 0
    for row in csv.DictReader(io.StringIO(raw)):
        name = (row.get("name") or "").strip()
        if not name:
            continue
        store.upsert_entity({
            "name": name, "type": (row.get("type") or "company").strip(),
            "domicile": (row.get("domicile") or "").strip(),
            "jurisdictions": [j.strip().upper() for j in re.split(r"[,;|]", row.get("jurisdictions") or "") if j.strip()],
            "fy_end": (row.get("fy_end") or "").strip() or None,
            "activities": [a.strip() for a in re.split(r"[,;|]", row.get("activities") or "") if a.strip()],
            "client_ref": (row.get("client_ref") or "").strip() or None, "status": "active"})
        n += 1
    return RedirectResponse("/entities?added=1", status_code=303)


@rt("/entity/{entity_id}")
def entity_view(sess, entity_id: int):
    if (r := require(sess)):
        return r
    e = store.get_entity(entity_id)
    if not e:
        return Page(sess, H1("Entity not found"))
    from datetime import date
    today = date.today()
    obs = [monitor.annotate(o, e, today) for o in store.list_obligations(entity_id=entity_id)]
    nver = sum(1 for o in obs if o.get("verified"))
    determine = Form(
        Button("⟳ Determine obligations", cls="btn"),
        method="post", action=f"/entity/{entity_id}/determine", style="display:inline")

    def ob_row(o):
        st = o.get("status") or "not_started"
        status_sel = Form(
            Select(*[Option(OB_STATUS_LABELS[s], value=s, selected=(s == st)) for s in OB_STATUS],
                   name="status", onchange="this.form.submit()",
                   style=f"border:1px solid var(--line);border-radius:6px;padding:3px 6px;"
                         f"font-weight:600;color:{OB_STATUS_COLOR.get(st, '#48484f')};cursor:pointer"),
            method="post", action=f"/obligation/{o['id']}/status", style="display:inline")
        ver = o.get("verified")
        ver_form = Form(
            Button("✓ Verified" if ver else "Mark verified",
                   style=(("background:#eaf5ee;color:#1c7c44;border:1px solid #b7e0c4;" if ver
                           else "background:#fff;color:#6b7686;border:1px solid #e6e3ec;") +
                          "border-radius:6px;padding:3px 9px;font-size:12px;cursor:pointer")),
            method="post", action=f"/obligation/{o['id']}/verify", style="display:inline")
        return Tr(Td(o.get("jurisdiction_code")),
                  Td(A(o.get("title") or "form", href=f"/form-pdf/{o['form_id']}", target="_blank")),
                  Td(CATEGORY_LABELS.get(o.get("category"), o.get("category") or "—")),
                  _due_cell(o), Td(urgency_badge(o["urgency"])),
                  Td(o.get("deadline") or "—"), Td(status_sel), Td(ver_form))

    ob_section = Div(
        Div(H2("Obligations", style="display:inline-block;margin:0 12px 0 0"), determine,
            (Span(f"  {len(obs)} obligations · {nver} verified",
                  style="color:var(--muted);font-size:12px;margin-left:10px") if obs else ""),
            style="display:flex;align-items:center;gap:8px;flex-wrap:wrap"),
        (Table(Tr(Th("Jur"), Th("Obligation"), Th("Category"), Th("Due"), Th(""),
                  Th("Filing deadline"), Th("Status"), Th("Verified")), *[ob_row(o) for o in obs])
         if obs else P("No obligations yet — click ", B("Determine obligations"),
                       " to generate them from this entity's jurisdictions and activities.",
                       cls="muted")),
        style="margin-top:14px;border-top:1px solid var(--line);padding-top:12px")
    return Page(sess, 
        H1(e["name"]),
        Dl(Dt("Type"), Dd(e.get("type") or "—"),
           Dt("Domicile"), Dd(JUR_NAMES.get(e.get("domicile"), e.get("domicile") or "—")),
           Dt("Operating jurisdictions"), Dd(_jur_badges(e.get("jurisdictions"))),
           Dt("Financial year-end"), Dd(e.get("fy_end") or "—"),
           Dt("Activities"), Dd(", ".join(e.get("activities") or []) or "—"),
           Dt("Client ref"), Dd(e.get("client_ref") or "—"),
           Dt("Status"), Dd(e.get("status") or "—"), cls="formmeta"),
        ob_section,
        Form(Button("Delete entity", cls="btn",
                    style="background:#b0353a", onclick="return confirm('Delete this entity?')"),
             method="post", action=f"/entity/{entity_id}/delete", style="margin-top:18px"),
        title=f"{e['name']} · TaxHub")


@rt("/entity/{entity_id}/determine", methods=["POST"])
def entity_determine(sess, entity_id: int):
    if (require(sess)):
        return RedirectResponse("/login", status_code=303)
    from web.obligations import run_determine
    run_determine(store, entity_id)
    return RedirectResponse(f"/entity/{entity_id}", status_code=303)


@rt("/obligation/{ob_id}/status", methods=["POST"])
def obligation_status(sess, ob_id: int, status: str = ""):
    if (require(sess)):
        return RedirectResponse("/login", status_code=303)
    o = store.get_obligation(ob_id)
    if o and status in OB_STATUS:
        store.set_obligation_status(ob_id, status)
    return RedirectResponse(f"/entity/{o['entity_id']}" if o else "/entities", status_code=303)


@rt("/obligation/{ob_id}/verify", methods=["POST"])
def obligation_verify(sess, ob_id: int):
    if (require(sess)):
        return RedirectResponse("/login", status_code=303)
    o = store.get_obligation(ob_id)
    if o:
        store.set_obligation_verified(ob_id, not o.get("verified"))
    return RedirectResponse(f"/entity/{o['entity_id']}" if o else "/entities", status_code=303)


@rt("/obligations")
def obligations_all(sess, status: str = "", jur: str = ""):
    if (r := require(sess)):
        return r
    obs = store.list_obligations(status=status or None, limit=10000)
    if jur:
        obs = [o for o in obs if o.get("jurisdiction_code") == jur]
    ents = {e["id"]: e["name"] for e in store.list_entities(limit=2000)}
    jurs = sorted({o.get("jurisdiction_code") for o in store.list_obligations(limit=10000) if o.get("jurisdiction_code")})
    def opt(v, label, cur):
        return Option(label, value=v, selected=(v == cur))
    filters = Div(
        Select(opt("", "All statuses", status),
               *[opt(s, OB_STATUS_LABELS[s], status) for s in OB_STATUS],
               onchange="location='/obligations?status='+this.value+'&jur='+'" + jur + "'",
               style="padding:7px;border:1px solid var(--line);border-radius:7px"),
        Select(opt("", "All jurisdictions", jur),
               *[opt(c, JUR_NAMES.get(c, c), jur) for c in jurs],
               onchange="location='/obligations?jur='+this.value+'&status='+'" + status + "'",
               style="padding:7px;border:1px solid var(--line);border-radius:7px;margin-left:6px"),
        Span(f"  {len(obs)} obligations", style="color:var(--muted);font-size:12px;margin-left:auto"),
        style="display:flex;align-items:center;margin:12px 0")
    rows = [Tr(Td(A(ents.get(o["entity_id"], "—"), href=f"/entity/{o['entity_id']}")),
               Td(o.get("jurisdiction_code")),
               Td(A(o.get("title") or "form", href=f"/form-pdf/{o['form_id']}", target="_blank")),
               Td(CATEGORY_LABELS.get(o.get("category"), o.get("category") or "—")),
               Td(o.get("deadline") or "—"),
               Td(ob_status_badge(o.get("status"))),
               Td("✓" if o.get("verified") else "—"))
            for o in obs]
    return Page(sess, 
        H1("Obligations"),
        P("Every filing obligation across the portfolio. Filter by status or "
          "jurisdiction; click an entity or open a form.", cls="muted"),
        filters,
        (Table(Tr(Th("Entity"), Th("Jur"), Th("Obligation"), Th("Category"),
                  Th("Filing deadline"), Th("Status"), Th("Verified")), *rows)
         if rows else P("No obligations match.", cls="muted")),
        title="Obligations · TaxHub")


def urgency_badge(urg):
    col = monitor.URGENCY_COLOR.get(urg, "#7a7a85")
    return Span(monitor.URGENCY_LABEL.get(urg, urg),
                style=f"display:inline-block;background:{col}22;color:{col};"
                      "border-radius:20px;padding:2px 10px;font-size:11px;font-weight:600")


def _due_cell(r):
    if not r.get("due"):
        return Td(Span("—", style="color:var(--muted)"),
                  title=r.get("due_basis") or "")
    days = r.get("days_out")
    rel = "today" if days == 0 else (f"{-days}d overdue" if days < 0 else f"in {days}d")
    mark = Span(" ~", title="Indicative — verify against the source rule",
                style="color:var(--amber);font-weight:700") if r.get("indicative") else ""
    return Td(r["due"], mark, Br(),
              Span(rel, style="color:var(--muted);font-size:11px"),
              title=r.get("due_basis") or "")


@rt("/calendar")
def calendar_view(sess, urg: str = "", jur: str = ""):
    if (r := require(sess)):
        return r
    from datetime import date
    today = date.today()
    cal = monitor.portfolio_calendar(store, today)
    counts = {u: sum(1 for r in cal if r["urgency"] == u) for u in monitor.URGENCY_LABEL}
    rows_all = cal
    if urg:
        cal = [r for r in cal if r["urgency"] == urg]
    if jur:
        cal = [r for r in cal if r.get("jurisdiction_code") == jur]
    jurs = sorted({r.get("jurisdiction_code") for r in rows_all if r.get("jurisdiction_code")})

    def chip(u):
        active = (u == urg)
        col = monitor.URGENCY_COLOR.get(u, "#7a7a85")
        return A(f"{monitor.URGENCY_LABEL[u]} {counts.get(u, 0)}",
                 href=f"/calendar?urg={'' if active else u}&jur={jur}",
                 style=f"display:inline-block;padding:5px 12px;border-radius:20px;"
                       f"font-size:12px;font-weight:600;text-decoration:none;margin-right:6px;"
                       f"border:1px solid {col};color:{'#fff' if active else col};"
                       f"background:{col if active else 'transparent'}")
    chips = Div(*[chip(u) for u in ("overdue", "due_soon", "upcoming", "scheduled", "undated", "done")],
                style="margin:12px 0")
    jurbar = Div(
        Select(Option("All jurisdictions", value="", selected=(not jur)),
               *[Option(JUR_NAMES.get(c, c), value=c, selected=(c == jur)) for c in jurs],
               onchange="location='/calendar?jur='+this.value+'&urg='+'" + urg + "'",
               style="padding:7px;border:1px solid var(--line);border-radius:7px"),
        Span(f"  {len(cal)} obligations", style="color:var(--muted);font-size:12px;margin-left:auto"),
        style="display:flex;align-items:center;margin:8px 0")
    rows = [Tr(_due_cell(r),
               Td(urgency_badge(r["urgency"])),
               Td(A(r.get("entity_name") or "—", href=f"/entity/{r['entity_id']}")),
               Td(r.get("jurisdiction_code")),
               Td(A(r.get("title") or "form", href=f"/form-pdf/{r['form_id']}", target="_blank")),
               Td(ob_status_badge(r.get("status"))),
               Td(Span(r.get("deadline") or "—", style="color:var(--muted);font-size:11px")))
            for r in cal]
    return Page(sess, 
        H1("Deadline calendar"),
        P("Filing deadlines across the portfolio, with concrete dates resolved from "
          "each rule and the entity's financial year-end. ", Span("~", style="color:var(--amber);font-weight:700"),
          " marks an indicative date — verify against the source rule. Closed "
          "obligations (filed/confirmed) are never flagged overdue.", cls="muted"),
        chips, jurbar,
        (Table(Tr(Th("Due"), Th("Urgency"), Th("Entity"), Th("Jur"), Th("Obligation"),
                  Th("Status"), Th("Rule")), *rows)
         if rows else P("No obligations match.", cls="muted")),
        title="Calendar · TaxHub")


@rt("/coverage")
def coverage_view(sess):
    if (r := require(sess)):
        return r
    import json as _json
    from web import coverage as cov
    pf = cov.portfolio_matrix(store, OB_STATUS)
    cat = cov.catalogue_matrix(store)

    # ── Portfolio lens payload (entity × status) ────────────────────────────
    p_rows = pf["rows"]
    p_y = [r["name"] for r in p_rows]
    p_x = [OB_STATUS_LABELS[s] for s in OB_STATUS]
    p_z = [[r["counts"].get(s, 0) for s in OB_STATUS] for r in p_rows]
    p_text = [[str(v) if v else "" for v in row] for row in p_z]
    p_payload = {"x": p_x, "y": p_y, "z": p_z, "text": p_text}

    # ── Catalogue lens payload (jurisdiction × category) ────────────────────
    c_y = [JUR_NAMES.get(j, j) for j in cat["jurs"]]
    c_x = [CATEGORY_LABELS.get(c, c) for c in cat["cats"]]
    c_z = cat["count"]
    c_text = [[str(v) if v else "" for v in row] for row in c_z]
    # Hover: filing-type breakdown per cell.
    c_hover = [["📄 %d · 🌐 %d · 📘 %d" % (cat["dl"][j][c], cat["on"][j][c], cat["ref"][j][c])
                for c in range(len(cat["cats"]))] for j in range(len(cat["jurs"]))]
    c_payload = {"x": c_x, "y": c_y, "z": c_z, "text": c_text, "hover": c_hover}

    t = cat["totals"]
    summary = Div(
        Div(Span(f"{pf['pct']}%", style="font-size:30px;font-weight:700;color:var(--navy)"),
            Span(" of the book filed / confirmed", style="color:var(--muted)"),
            Div(P(A(f"{pf['filed']} of {pf['total']} obligations", href="/obligations?status=filed"),
                  " across ", A(f"{len(p_rows)} entities", href="/entities"),
                  style="color:var(--muted);font-size:13px;margin:4px 0 0")),
            style="flex:1;min-width:240px"),
        Div(Span(f"{t['forms']}", style="font-size:30px;font-weight:700;color:var(--navy)"),
            Span(" forms in the catalogue", style="color:var(--muted)"),
            Div(P(f"{t['jurisdictions']} jurisdictions · 📄 {t['downloadable']} downloadable · "
                  f"🌐 {t['online']} online · 📘 {t['reference']} reference",
                  style="color:var(--muted);font-size:13px;margin:4px 0 0")),
            style="flex:1;min-width:240px"),
        style="display:flex;gap:24px;flex-wrap:wrap;margin:10px 0 4px")

    data_script = Script(f"window.PFCOV={_json.dumps(p_payload)};"
                         f"window.CATCOV={_json.dumps(c_payload)};")
    plot_js = Script("""
function heat(id, d, opts){
  var trace={type:'heatmap',x:d.x,y:d.y,z:d.z,xgap:2,ygap:2,
    text:d.text,texttemplate:'%{text}',textfont:{size:12,color:'#2b2b30'},
    colorscale:opts.scale,showscale:false,zmin:0,
    hoverongaps:false};
  if(d.hover){trace.customdata=d.hover;
    trace.hovertemplate='%{y} · %{x}<br>%{z} form(s)<br>%{customdata}<extra></extra>';}
  else{trace.hovertemplate='%{y} · %{x}<br>%{z} obligation(s)<extra></extra>';}
  var h=Math.max(160, d.y.length*30+90);
  Plotly.react(id,[trace],{margin:{l:170,r:10,t:10,b:70},height:h,
    xaxis:{side:'top',tickangle:-25,automargin:true,fixedrange:true},
    yaxis:{automargin:true,autorange:'reversed',fixedrange:true}},
    {responsive:true,displayModeBar:false});
}
var PUR=[[0,'#faf7fb'],[0.5,'#d3a9d0'],[1,'#6b1766']];
var GRN=[[0,'#f5faf6'],[0.5,'#9fcfb0'],[1,'#1c7c44']];
function drawCoverage(){
  if(!window.Plotly){return setTimeout(drawCoverage,60);}
  if(window.PFCOV&&document.getElementById('pfcov'))heat('pfcov',window.PFCOV,{scale:PUR});
  if(window.CATCOV&&document.getElementById('catcov'))heat('catcov',window.CATCOV,{scale:GRN});
}
drawCoverage();
window.addEventListener('resize',function(){if(window.Plotly){
  if(document.getElementById('pfcov'))Plotly.Plots.resize('pfcov');
  if(document.getElementById('catcov'))Plotly.Plots.resize('catcov');}});
""")
    return Page(sess, 
        H1("Coverage map"),
        P("Two lenses on coverage: how much of the client book is filed, and how "
          "thick our form catalogue is across jurisdictions and categories.", cls="muted"),
        summary,
        H2("Portfolio coverage", style="margin-top:18px"),
        P("Each entity's obligations by file-status — darker = more obligations in "
          "that state. Filed/Confirmed columns are the compliant book.", cls="muted"),
        (Div(id="pfcov") if p_rows else P("No entities yet.", cls="muted")),
        H2("Catalogue coverage", style="margin-top:22px"),
        P("Forms held per jurisdiction × category. Hover a cell for the filing-type "
          "breakdown (📄 downloadable · 🌐 online · 📘 reference).", cls="muted"),
        (Div(id="catcov") if cat["jurs"] else P("No forms yet.", cls="muted")),
        PLOTLY, data_script, plot_js,
        title="Coverage · TaxHub")


@rt("/admin/digest")
def admin_digest(sess, request, horizon: int = 90):
    """Machine-readable deadline digest (overdue + upcoming) — the foundation for
    email/Slack alerts. Gated by session or X-Admin-Token."""
    if require(sess) and not _admin_ok(sess, request):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    from datetime import date
    return JSONResponse(monitor.deadline_digest(store, date.today(), horizon_days=horizon))


@rt("/entity/{entity_id}/delete", methods=["POST"])
def entity_delete(sess, entity_id: int):
    if (require(sess)):
        return RedirectResponse("/login", status_code=303)
    store.delete_entity(entity_id)
    return RedirectResponse("/entities", status_code=303)


@rt("/admin/seed-entities", methods=["POST"])
def seed_entities(sess, request):
    if not _admin_ok(sess, request):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    ids = [store.upsert_entity(e) for e in SAMPLE_ENTITIES]
    return JSONResponse({"seeded": len(ids), "total": store.count_entities()})


@rt("/documents")
def documents(sess, uploaded: str = ""):
    if (r := require(sess)):
        return r
    forms = store.list_forms(limit=2000)
    with_pdf = [f for f in forms if f.get("file_path")]
    cats = sorted({f.get("category") or "other" for f in forms})
    jurs = sorted({f["jurisdiction_code"] for f in forms}) or list(JUR_NAMES)
    upload = Form(
        H2("Upload a document"),
        (P("✓ Uploaded.", style="color:#1c7c44") if uploaded else ""),
        Div(
            Input(type="file", name="doc_file", accept="application/pdf", required=True),
            style="margin:8px 0"),
        Div(Input(name="title", placeholder="Title (optional)", style="width:48%"),
            Select(*[Option(JUR_NAMES.get(j, j), value=j) for j in jurs], name="jurisdiction",
                   style="width:24%;margin:0 1%"),
            Select(*[Option(CATEGORY_LABELS.get(c, c), value=c) for c in
                     ["uploaded", "corporate_tax", "economic_substance", "aeoi",
                      "beneficial_ownership", "fund", "other"]], name="category",
                   style="width:24%"),
            style="display:flex;gap:6px;align-items:center"),
        Button("Upload PDF", cls="btn", style="margin-top:10px"),
        method="post", action="/upload", enctype="multipart/form-data")
    filterbar = Div(
        Input(id="docsearch", placeholder="Search documents…", oninput="filterDocs()",
              style="flex:1;padding:8px;border:1px solid #e3e6eb;border-radius:7px"),
        Select(Option("All jurisdictions", value=""),
               *[Option(JUR_NAMES.get(j, j), value=j) for j in jurs],
               id="docjur", onchange="filterDocs()", style="padding:8px"),
        Select(Option("All categories", value=""),
               *[Option(CATEGORY_LABELS.get(c, c), value=c) for c in cats],
               id="doccat", onchange="filterDocs()", style="padding:8px"),
        Select(Option("All filing types", value=""),
               *[Option(v, value=k) for k, v in FILING_LABELS.items()],
               id="docfiling", onchange="filterDocs()", style="padding:8px"),
        style="display:flex;gap:8px;margin:12px 0;flex-wrap:wrap")
    rows = [Tr(Td(f["jurisdiction_code"]),
               Td(FILING_LABELS.get(f.get("filing_type") or "downloadable", "")),
               Td(CATEGORY_LABELS.get(f.get("category"), f.get("category") or "")),
               Td(A(f["title"], href=f"/form/{f['id']}")),
               Td("📄" if f.get("file_path") else "link"),
               cls="docrow", **{"data-j": f["jurisdiction_code"], "data-c": f.get("category") or "",
                                "data-f": f.get("filing_type") or "downloadable",
                                "data-t": (f["title"] or "").lower()})
            for f in with_pdf]
    browser = Table(Tr(Th("Jur"), Th("Filing type"), Th("Category"), Th("Document"), Th("PDF")),
                    *rows, id="doctable")
    js = Script("""
function filterDocs(){var q=document.getElementById('docsearch').value.toLowerCase();
  var j=document.getElementById('docjur').value,c=document.getElementById('doccat').value,
      ff=document.getElementById('docfiling').value,n=0;
  document.querySelectorAll('#doctable tr.docrow').forEach(function(r){
    var ok=(!q||r.dataset.t.indexOf(q)>=0)&&(!j||r.dataset.j===j)&&(!c||r.dataset.c===c)
      &&(!ff||r.dataset.f===ff);
    r.style.display=ok?'':'none';if(ok)n++;});
  document.getElementById('doccount').textContent=n;}
""")
    return Page(sess, H1("Documents"),
                P(Span(str(len(with_pdf)), id="doccount"), f" of {len(forms)} documents shown · "
                  "search and filter below, or upload a new PDF (pushed to the server volume).",
                  cls="muted"),
                upload, H2("Stored documents"), filterbar, browser, js,
                title="Documents · TaxHub")


@rt("/upload", methods=["POST"])
async def upload(sess, doc_file: UploadFile, jurisdiction: str = "JE",
                 category: str = "uploaded", title: str = ""):
    if (require(sess)):
        return RedirectResponse("/login", status_code=303)
    from pathlib import Path
    from ingest.scrapers.base import slugify
    data = await doc_file.read()
    name = title.strip() or (doc_file.filename or "document").rsplit(".", 1)[0]
    key = "upload_" + (slugify(name) or "doc")
    root = Path(__file__).resolve().parent.parent
    dest = root / "data" / "forms" / jurisdiction / f"{key}.pdf"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    store.upsert_jurisdiction(jurisdiction, JUR_NAMES.get(jurisdiction, jurisdiction))
    store.upsert_form({
        "jurisdiction_code": jurisdiction, "category": category, "form_type": "form",
        "form_key": key, "title": name[:160], "authority": "Uploaded",
        "url": None, "file_path": str(dest.relative_to(root)), "filing_type": "downloadable"})
    return RedirectResponse("/documents?uploaded=1", status_code=303)


AUDIT = [
    ("Luxembourg (ACD)", "Complete & submit PDF", "126 real fillable forms (200/300/500/710…)", "downloadable"),
    ("Guernsey (gov.gg)", "PDF forms (main return online)", "71 real forms (registration, tax-cap returns…)", "downloadable"),
    ("Jersey (Revenue Jersey)", "Online portal", "No downloadable form", "online"),
    ("Ireland (Revenue)", "ROS e-file", "PDFs are TDM guidance, not forms", "online + reference"),
    ("Cayman (DITC)", "DITC portal", "PDFs are user guides", "online + reference"),
    ("BVI (ITA)", "BOSS portal (via agent)", "PDFs are guidance/methodology", "online + reference"),
]


@rt("/help")
def help_page(sess):
    if (r := require(sess)):
        return r
    legend = Div(*[Div(Span(v, style="font-weight:600"), cls="",
                       style="padding:6px 0") for v in FILING_LABELS.values()])
    audit = Table(
        Tr(Th("Jurisdiction"), Th("How you actually file"), Th("What the site offers"), Th("filing_type")),
        *[Tr(Td(j), Td(how), Td(offers), Td(ft)) for j, how, offers, ft in AUDIT])
    shortcuts = Table(Tr(Th("Shortcut"), Th("Does")),
                      *[Tr(Td(Code(p)), Td(desc)) for p, desc, _ in SHORTCUTS])
    return Page(sess, 
        H1("Help & User Guide"),
        P("TaxHub helps a fund back office find the correct tax form to file, with "
          "provenance back to the underlying law. Use the AI Assistant for free-text "
          "questions, the Forms Tree to browse, or the shortcuts below.", cls="muted"),
        P(A("⬇ Download the full User Guide (PDF)", href="/user-guide-pdf", cls="btn"), " ",
          A("🛠 Technical Guide (architecture & Azure deployment)", href="/technical-guide", cls="btn")),
        H2("Filing types"),
        P("Every form is labelled by how it is filed:"),
        legend,
        H2("Coverage audit — what each authority actually offers"),
        P("Not every jurisdiction publishes downloadable forms; several file online. "
          "We capture this honestly so you know what to expect:", cls="muted"),
        audit,
        H2("Assistant shortcuts"),
        shortcuts,
        H2("The Forms Tree"),
        P("Browse by Jurisdiction → category (corporate tax, economic substance, AEOI…) "
          "→ document type → form. Click any form to open its PDF (or the official portal "
          "link for online-filed forms) in the right pane."),
        title="Help · TaxHub")


@rt("/user-guide-pdf")
def user_guide_pdf(sess):
    if (require(sess)):
        return RedirectResponse("/login", status_code=303)
    from pathlib import Path
    p = Path(__file__).resolve().parent.parent / "docs" / "taxhub_user_guide.pdf"
    if not p.exists():
        return Page(sess, H1("User guide PDF not generated yet"),
                    P("Run scripts/generate_user_guide.py.", cls="muted"))
    return Response(p.read_bytes(), media_type="application/pdf",
                    headers={"Content-Disposition": 'inline; filename="taxhub_user_guide.pdf"'})


def _technical_guide_html():
    from pathlib import Path
    p = Path(__file__).resolve().parent.parent / "docs" / "architecture_readme.html"
    return p.read_text() if p.exists() else None


@rt("/technical-guide")
def technical_guide(sess):
    """Architecture & Azure-deployment guide — the generated self-contained HTML
    (with the rendered architecture diagram) shown inside the 3-pane shell."""
    if (r := require(sess)):
        return r
    html = _technical_guide_html()
    if not html:
        return Page(sess, H1("Technical Guide not generated yet"),
                    P("Run ", Code("python3.12 scripts/generate_architecture_html.py"),
                      " to build docs/architecture_readme.html.", cls="muted"),
                    title="Technical Guide · TaxHub")
    body = html.split("<body>", 1)[1].rsplit("</body>", 1)[0] if "<body>" in html else html
    return Page(sess,
        Div(A("⬇ Open standalone HTML", href="/technical-guide.html", target="_blank", cls="btn"),
            style="margin-bottom:14px"),
        # Scope the doc's figure/diagram styling so it doesn't fight the app CSS.
        Style(".techguide figure{margin:18px 0;text-align:center}"
              ".techguide figure img{max-width:100%;border:1px solid var(--line);"
              "border-radius:8px;padding:10px;background:#fff}"
              ".techguide figcaption{color:var(--muted);font-size:12.5px;margin-top:6px}"
              ".techguide pre{background:#f5f6f4;border:1px solid var(--line);border-radius:6px;"
              "padding:12px 14px;font-size:12.5px;overflow-x:auto}"
              ".techguide .toc{background:#f5f6f4;border:1px solid var(--line);border-radius:8px;padding:8px 16px}"
              ".techguide .toc ul{list-style:none;padding-left:14px}"),
        Div(NotStr(body), cls="techguide"),
        title="Technical Guide · TaxHub")


@rt("/technical-guide.html")
def technical_guide_raw(sess):
    if (require(sess)):
        return RedirectResponse("/login", status_code=303)
    html = _technical_guide_html()
    if not html:
        return Response("Not generated", status_code=404)
    return Response(html, media_type="text/html")


@rt("/changes")
def changes_page(sess, j: str = None):
    if (r := require(sess)):
        return r
    chs = store.recent_changes(60, jurisdiction_code=j)
    return Page(sess, H1("Recent changes"),
        *[Div(Div(Span(c.get("change_type", ""), cls=f"pill {c.get('change_type','')}"), " ",
                  f"{c['jurisdiction_code']} · {(c.get('detected_at') or '')[:10]}", cls="meta"),
              Div(A(c["title"], href=f"/document/{c.get('document_id')}"),
                  style="font-weight:600"),
              (Div(c["ai_summary"], style="margin-top:4px;color:#6b7686")
               if c.get("ai_summary") else ""), cls="feed-item")
          for c in chs])
