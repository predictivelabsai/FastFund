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
JUR_NAMES = {"JE": "Jersey", "GG": "Guernsey", "LU": "Luxembourg",
             "IE": "Ireland", "KY": "Cayman Islands", "VG": "British Virgin Islands"}
FILING_LABELS = {"downloadable": "📄 Downloadable form", "online": "🌐 Online filing",
                 "reference": "📘 Reference / guidance"}
FORM_TYPE_LABELS = {"return": "Return", "notification": "Notification",
                    "declaration": "Declaration", "registration": "Registration",
                    "report": "Report", "guidance": "Guidance", "form": "Form"}

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
:root{--navy:#0f2740;--navy2:#1b3a5b;--accent:#c8a24b;--bg:#f6f7f9;--line:#e3e6eb;
--green:#1c7c44;--amber:#b06b00;--text:#1d2430;--muted:#6b7686;--panel:#fff;}
*{box-sizing:border-box}html,body{height:100%}
body{margin:0;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
color:var(--text);background:var(--bg);line-height:1.5}
a{color:var(--navy2);text-decoration:none}a:hover{text-decoration:underline}
.app{display:grid;grid-template-columns:280px 1fr 430px;height:100vh;overflow:hidden}
.pane{height:100vh;overflow-y:auto}
.left{background:var(--navy);color:#cdd7e3;padding:0}
.left .brand{font-weight:700;font-size:18px;color:#fff;padding:16px 18px;border-bottom:1px solid #1c3856}
.left .brand span{color:var(--accent)}
.left a{color:#cdd7e3;display:block}
.section{padding:12px 16px;border-bottom:1px solid #14304c}
.section .lbl{font-size:11px;text-transform:uppercase;letter-spacing:.6px;color:#7d92ab;margin-bottom:8px}
.navlink{padding:6px 8px;border-radius:6px;font-size:14px}.navlink:hover{background:#173451;text-decoration:none}
.newchat{display:block;background:var(--accent);color:#1a1300;text-align:center;font-weight:600;
padding:9px;border-radius:8px;margin:12px 16px}
.newchat:hover{text-decoration:none;filter:brightness(1.05)}
details.tree{margin:2px 0}details.tree>summary{cursor:pointer;font-size:13px;padding:3px 0;list-style:none}
details.tree>summary::-webkit-details-marker{display:none}
details.tree>summary:before{content:"▸ ";color:#7d92ab}details.tree[open]>summary:before{content:"▾ "}
.tree .jur{font-weight:600;color:#e7eef6}.tree .cat{margin-left:12px;color:#b9c7d8}
.tree .typ{margin-left:24px;color:#9fb2c7;font-size:12px}
.formlink{margin-left:30px;display:block;font-size:12.5px;color:#cdd7e3;padding:2px 0;cursor:pointer}
.formlink:hover{color:#fff}
.shortcut{font-size:12px;padding:4px 6px;border-radius:6px;cursor:pointer}
.shortcut:hover{background:#173451}.shortcut b{color:var(--accent);font-family:ui-monospace,monospace}
.sess{font-size:13px;padding:4px 8px;border-radius:6px;display:block;color:#cdd7e3}.sess:hover{background:#173451}
/* center */
.center{display:flex;flex-direction:column;background:#fbfcfd}
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
""")

MARKED = Script(src="https://cdn.jsdelivr.net/npm/marked/marked.min.js")


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
        Div(Div("Navigate", cls="lbl"),
            A("Dashboard", href="/dashboard", cls="navlink"),
            A("Jurisdictions", href="/jurisdictions", cls="navlink"),
            A("Documents", href="/documents", cls="navlink"),
            A("Changes", href="/changes", cls="navlink"),
            cls="section"),
        Div(Div("Recent chats", cls="lbl"),
            *[A(s.get("title") or "Chat", href=f"/?sid={s['id']}", cls="sess")
              for s in sessions] or [Div("No chats yet", cls="muted", style="font-size:12px")],
            cls="section"),
        Div(Div("Tax Forms Tree", cls="lbl"), tree_component(), cls="section"),
        Div(Div("Shortcuts", cls="lbl"),
            *[Div(B(p), " ", desc, cls="shortcut", onclick=f"fillChat({ex!r})")
              for p, desc, ex in SHORTCUTS],
            cls="section"),
        Div(A("Sign out", href="/logout", cls="navlink"), cls="section"),
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
  document.getElementById('rtitle').textContent='Form #'+id;document.getElementById('rclose').textContent='✕';
  fetch('/form/'+id).then(r=>r.text()).then(h=>{rp.innerHTML=h;});}
function openDoc(id){if(!id)return;document.getElementById('rtitle').textContent='Document #'+id;
  document.getElementById('rclose').textContent='✕';
  document.getElementById('rbody').innerHTML='<iframe class="pdf" src="/document/'+id+'?embed=1"></iframe>';}
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


# ── Secondary pages (traceability) ──────────────────────────────────────────
def Page(*content, title="TaxHub"):
    return (Title(title), CSS,
            Header(A("← Assistant", href="/", style="color:#cdd7e3"),
                   style="background:#0f2740;padding:12px 24px"),
            Div(*content, cls="wrap"))


@rt("/dashboard")
def dashboard(sess):
    if (r := require(sess)):
        return r
    st = store.stats()
    jurs = store.list_jurisdictions_with_counts()
    return Page(H1("Dashboard"),
        Table(Tr(Th("Metric"), Th("Count")),
              *[Tr(Td(k), Td(str(v))) for k, v in st.items() if not isinstance(v, list)]),
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
    return Page(H1("Jurisdictions"),
        Table(Tr(Th("Code"), Th("Name"), Th("Documents")),
              *[Tr(Td(j["code"]), Td(A(JUR_NAMES.get(j["code"], j["code"]),
                href=f"/jurisdiction/{j['code']}")), Td(str(j["docs"]))) for j in jurs]))


@rt("/jurisdiction/{code}")
def jurisdiction(sess, code: str):
    if (r := require(sess)):
        return r
    docs = store.list_documents_for_jurisdiction(code)
    return Page(H1(JUR_NAMES.get(code, code)),
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
    return Page(body, title=f"{d['title'][:40]} · TaxHub")


@rt("/documents")
def documents(sess, uploaded: str = ""):
    if (r := require(sess)):
        return r
    forms = store.list_forms(limit=2000)
    with_pdf = [f for f in forms if f.get("file_path")]
    cats = sorted({f.get("category") or "other" for f in forms})
    jurs = ["JE", "GG", "LU", "IE", "KY", "VG"]
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
    return Page(H1("Documents"),
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


@rt("/changes")
def changes_page(sess, j: str = None):
    if (r := require(sess)):
        return r
    chs = store.recent_changes(60, jurisdiction_code=j)
    return Page(H1("Recent changes"),
        *[Div(Div(Span(c.get("change_type", ""), cls=f"pill {c.get('change_type','')}"), " ",
                  f"{c['jurisdiction_code']} · {(c.get('detected_at') or '')[:10]}", cls="meta"),
              Div(A(c["title"], href=f"/document/{c.get('document_id')}"),
                  style="font-weight:600"),
              (Div(c["ai_summary"], style="margin-top:4px;color:#6b7686")
               if c.get("ai_summary") else ""), cls="feed-item")
          for c in chs])
