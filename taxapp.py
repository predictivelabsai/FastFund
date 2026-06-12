"""TaxHub web app — the traceability viewer.

A read-only console for a fund back office: browse tracked jurisdictions and
documents, see each document's version timeline, read the plain-English AI
summary of every amendment with the underlying diff, and trace citations back
to primary law.

Run:  python3.12 -m uvicorn taxapp:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import os

from fasthtml.common import *

import taxstore as store
import taxrag

store.init_db()

LOGIN_REQUIRED = os.environ.get("TAXHUB_PUBLIC", "0") != "1"

# ──────────────────────────────────────────────────────────────────────────
# Styling
# ──────────────────────────────────────────────────────────────────────────

CSS = Style("""
:root{--navy:#0f2740;--navy2:#1b3a5b;--accent:#c8a24b;--bg:#f6f7f9;--line:#e3e6eb;
--green:#1c7c44;--amber:#b06b00;--text:#1d2430;--muted:#6b7686;}
*{box-sizing:border-box}
body{margin:0;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
color:var(--text);background:var(--bg);line-height:1.5}
a{color:var(--navy2);text-decoration:none}a:hover{text-decoration:underline}
header.top{background:var(--navy);color:#fff;padding:14px 26px;display:flex;
align-items:center;gap:16px}
header.top .brand{font-weight:700;font-size:19px;letter-spacing:.3px}
header.top .brand span{color:var(--accent)}
header.top nav{margin-left:auto;display:flex;gap:18px;font-size:14px}
header.top nav a{color:#cdd7e3}
.wrap{max-width:1080px;margin:0 auto;padding:26px}
h1{font-size:24px;margin:0 0 4px}h2{font-size:18px;margin:26px 0 10px}
.sub{color:var(--muted);margin:0 0 18px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:14px}
.card{background:#fff;border:1px solid var(--line);border-radius:10px;padding:16px}
.card .n{font-size:30px;font-weight:700;color:var(--navy)}
.card .l{color:var(--muted);font-size:13px;text-transform:uppercase;letter-spacing:.5px}
table{width:100%;border-collapse:collapse;background:#fff;border:1px solid var(--line);
border-radius:10px;overflow:hidden}
th,td{text-align:left;padding:10px 14px;border-bottom:1px solid var(--line);font-size:14px;vertical-align:top}
th{background:#fbfbfc;color:var(--muted);font-weight:600;text-transform:uppercase;font-size:11px;letter-spacing:.5px}
tr:last-child td{border-bottom:none}
.pill{display:inline-block;padding:2px 9px;border-radius:20px;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.4px}
.pill.legislation{background:#e7eef6;color:var(--navy2)}
.pill.guidance{background:#eaf5ee;color:var(--green)}
.pill.gazette{background:#fdf0e3;color:var(--amber)}
.pill.treaty{background:#f0e9f7;color:#6b3fa0}
.pill.new{background:#eaf5ee;color:var(--green)}
.pill.amended{background:#fdf0e3;color:var(--amber)}
.tag{display:inline-block;background:#eef1f5;color:var(--muted);border-radius:5px;
padding:1px 7px;font-size:11px;margin-right:5px}
.feed-item{background:#fff;border:1px solid var(--line);border-left:4px solid var(--accent);
border-radius:8px;padding:14px 16px;margin-bottom:12px}
.feed-item .meta{color:var(--muted);font-size:12px;margin-bottom:4px}
.feed-item .impact{margin-top:6px;font-size:13px;color:var(--navy2);background:#f3f6fa;padding:8px 10px;border-radius:6px}
.diff{background:#0f1620;color:#d6deeb;border-radius:8px;padding:14px;font-family:ui-monospace,Menlo,monospace;
font-size:12.5px;white-space:pre-wrap;overflow-x:auto;max-height:520px}
.diff .add{color:#7ee2a8}.diff .del{color:#ff9d9d}.diff .hdr{color:#7fb0ff}
.muted{color:var(--muted)}.right{text-align:right}
.btn{display:inline-block;background:var(--navy);color:#fff;padding:9px 16px;border-radius:7px;font-size:14px}
.timeline{list-style:none;padding:0;margin:0}
.timeline li{padding:8px 0;border-bottom:1px solid var(--line);font-size:14px;display:flex;gap:12px}
.form{background:#fff;border:1px solid var(--line);border-radius:10px;padding:24px;max-width:380px;margin:60px auto}
.form input{width:100%;padding:10px;border:1px solid var(--line);border-radius:7px;margin:6px 0 14px}
""")


def Shell(*content, title="TaxHub"):
    return Title(title), CSS, Header(
        Div("JTC ", Span("TaxHub"), cls="brand"),
        Nav(A("Dashboard", href="/"),
            A("Jurisdictions", href="/#jurisdictions"),
            A("Changes", href="/changes"),
            A("Ask", href="/ask")),
        cls="top",
    ), Div(*content, cls="wrap")


# ──────────────────────────────────────────────────────────────────────────
# Auth (lightweight)
# ──────────────────────────────────────────────────────────────────────────

def current_user(sess):
    return sess.get("uid") if sess else None


def require(sess):
    if LOGIN_REQUIRED and not current_user(sess):
        return RedirectResponse("/login", status_code=303)
    return None


app, rt = fast_app(hdrs=(), secret_key=os.environ.get("APP_SECRET", "taxhub-2026"),
                   pico=False)


@rt("/health")
def health():
    return JSONResponse({"status": "ok"})


@rt("/login", methods=["GET"])
def login_form(sess, error: str = ""):
    return Shell(
        Form(
            H1("Sign in"), P("JTC TaxHub", cls="sub"),
            (P(error, style="color:#c0392b") if error else ""),
            Input(name="email", placeholder="Email", type="email"),
            Input(name="password", placeholder="Password", type="password"),
            Button("Sign in", cls="btn", style="width:100%"),
            method="post", action="/login", cls="form",
        ), title="Sign in · TaxHub",
    )


@rt("/login", methods=["POST"])
def login_submit(sess, email: str = "", password: str = ""):
    import bcrypt
    user = store.get_user_by_email(email)
    if user and user["password_hash"] and bcrypt.checkpw(
            password.encode(), user["password_hash"].encode()):
        sess["uid"] = user["id"]
        return RedirectResponse("/", status_code=303)
    return RedirectResponse("/login?error=Invalid+credentials", status_code=303)


@rt("/logout")
def logout(sess):
    sess.pop("uid", None)
    return RedirectResponse("/login", status_code=303)


# ──────────────────────────────────────────────────────────────────────────
# Dashboard
# ──────────────────────────────────────────────────────────────────────────

def _pill(kind, label=None):
    return Span(label or kind, cls=f"pill {kind}")


@rt("/")
def home(sess):
    if (r := require(sess)):
        return r
    st = store.stats()
    jurs = store.list_jurisdictions_with_counts()
    cards = Div(
        Div(Div(str(st["jurisdictions"]), cls="n"), Div("Jurisdictions", cls="l"), cls="card"),
        Div(Div(str(st["documents"]), cls="n"), Div("Documents tracked", cls="l"), cls="card"),
        Div(Div(str(st["versions"]), cls="n"), Div("Versions captured", cls="l"), cls="card"),
        Div(Div(str(st["changes"]), cls="n"), Div("Changes detected", cls="l"), cls="card"),
        cls="cards",
    )
    rows = [Tr(
        Td(A(j["name"], href=f"/jurisdiction/{j['code']}"), Br(),
           Span(j["authority"] or "", cls="muted", style="font-size:12px")),
        Td(j["code"]),
        Td(str(j["docs"]), cls="right"),
    ) for j in jurs]
    feed = store.recent_changes(8)
    return Shell(
        H1("Tax law traceability"),
        P("Monitoring official tax documents across JTC fund jurisdictions — "
          "every version captured, every change explained.", cls="sub"),
        cards,
        H2("Jurisdictions", id="jurisdictions"),
        Table(Thead(Tr(Th("Jurisdiction"), Th("Code"), Th("Docs", cls="right"))), Tbody(*rows)),
        H2("Latest changes"),
        (Div(*[_feed_item(ch) for ch in feed]) if feed
         else P("No changes recorded yet — run the scraper.", cls="muted")),
        A("View all changes →", href="/changes"),
        title="TaxHub Dashboard",
    )


def _feed_item(ch):
    body = [Div(f"{ch['detected_at']} · {ch['jurisdiction_code']}", cls="meta"),
            _pill(ch["change_type"]), " ",
            A(ch["title"], href=f"/document/{ch['document_id']}",
              style="font-weight:600")]
    if ch.get("ai_summary"):
        body.append(P(ch["ai_summary"], style="margin:6px 0 0"))
    if ch.get("ai_impact"):
        body.append(Div("Impact: " + ch["ai_impact"], cls="impact"))
    if ch["change_type"] == "amended":
        body.append(P(A("See diff →", href=f"/change/{ch['id']}"), style="margin:6px 0 0;font-size:13px"))
    return Div(*body, cls="feed-item")


@rt("/changes")
def changes(sess, j: str = None):
    if (r := require(sess)):
        return r
    feed = store.recent_changes(100, jurisdiction_code=j)
    return Shell(
        H1("Change feed"),
        P(f"{len(feed)} most recent changes" + (f" · {j}" if j else ""), cls="sub"),
        (Div(*[_feed_item(ch) for ch in feed]) if feed
         else P("No changes recorded yet.", cls="muted")),
        title="Changes · TaxHub",
    )


@rt("/ask", methods=["GET"])
def ask_form(sess, q: str = ""):
    if (r := require(sess)):
        return r
    return Shell(*_ask_page(q, None), title="Ask · TaxHub")


@rt("/ask", methods=["POST"])
def ask_submit(sess, q: str = ""):
    if (r := require(sess)):
        return r
    result = taxrag.answer(q) if q.strip() else None
    return Shell(*_ask_page(q, result), title="Ask · TaxHub")


def _ask_page(q: str, result: dict | None):
    form = Form(
        Input(name="q", value=q or "", placeholder="e.g. What economic substance "
              "obligations apply to Jersey fund companies?",
              style="width:100%;padding:11px;border:1px solid var(--line);border-radius:7px"),
        Button("Ask", cls="btn", style="margin-top:10px"),
        method="post", action="/ask",
    )
    body = [
        H1("Ask the law"),
        P("Grounded answers across every tracked tax document — retrieval walks "
          "the citation and change graph, then cites its sources.", cls="sub"),
        form,
    ]
    if result is None:
        return body
    body.append(Div(
        Div("Answer", cls="l", style="margin-bottom:6px"),
        Div(result["answer"], style="white-space:pre-wrap"),
        cls="feed-item", style="border-left-color:var(--navy);margin-top:20px",
    ))
    if result["sources"]:
        body.append(H2("Sources"))
        body.append(Div(*[_source_item(i, s) for i, s in enumerate(result["sources"], 1)]))
    if result.get("model"):
        body.append(P(f"Synthesised by {result['model']}", cls="muted",
                      style="font-size:12px;margin-top:10px"))
    return body


def _source_item(n: int, s: dict):
    meta = f"{s['jurisdiction_code']}"
    if s.get("reference"):
        meta += f" · {s['reference']}"
    if s.get("cited_instruments"):
        meta += f" · cites {len(s['cited_instruments'])} instrument(s)"
    body = [
        Div(f"[{n}] {meta}", cls="meta"),
        A(s["title"], href=f"/document/{s['document_id']}", style="font-weight:600"),
    ]
    if s.get("latest_change"):
        body.append(Div("Recent change: " + s["latest_change"], cls="impact"))
    if s.get("snippet"):
        body.append(P(s["snippet"], cls="muted", style="font-size:13px;margin:6px 0 0"))
    return Div(*body, cls="feed-item")


@rt("/jurisdiction/{code}")
def jurisdiction(sess, code: str):
    if (r := require(sess)):
        return r
    jur = store.get_jurisdiction(code)
    docs = store.list_documents_for_jurisdiction(code)
    if not jur:
        return Shell(H1("Not found"), title="TaxHub")
    rows = [Tr(
        Td(A(d["title"], href=f"/document/{d['id']}"), Br(),
           Span(d["reference"] or "", cls="muted", style="font-size:12px")),
        Td(_pill(d["doc_type"])),
        Td(str(d["versions"]), cls="right"),
        Td(Span(d["status"], cls="muted")),
        Td((d["last_checked"] or "")[:10], cls="muted"),
    ) for d in docs]
    return Shell(
        H1(jur["name"]), P(jur["authority"] or "", cls="sub"),
        Table(Thead(Tr(Th("Document"), Th("Type"), Th("Versions", cls="right"),
                       Th("Status"), Th("Checked"))), Tbody(*rows)),
        title=f"{jur['name']} · TaxHub",
    )


@rt("/document/{doc_id}")
def document(sess, doc_id: int):
    if (r := require(sess)):
        return r
    d = store.get_document_by_id(doc_id)
    if not d:
        return Shell(H1("Not found"), title="TaxHub")
    versions = store.list_versions(doc_id)
    chgs = store.list_changes_for_document(doc_id)
    cites = store.list_citations(doc_id)
    tl = [Li(Span(f"v{v['version_no']}", style="font-weight:600;color:var(--navy)"),
             Span((v["fetched_at"] or "")[:19].replace("T", " "), cls="muted"),
             Span(f"{v['char_count'] or 0:,} chars", cls="muted")) for v in versions]
    cite_chips = [Span(f"{ct['cited_instrument']}" +
                       (f" · {ct['cited_locator']}" if ct["cited_locator"] else ""),
                       cls="tag", style="margin-bottom:5px;display:inline-block")
                  for ct in cites]
    return Shell(
        P(A("← " + d["jurisdiction_code"], href=f"/jurisdiction/{d['jurisdiction_code']}"), cls="muted"),
        H1(d["title"]),
        P(_pill(d["doc_type"]), " ", Span(d["reference"] or "", cls="muted"), " · ",
          A("Source ↗", href=d["url"], target="_blank"), cls="sub"),
        Div(*[Span(t, cls="tag") for t in _tags(d)]),
        H2("Version history"),
        Ul(*tl, cls="timeline") if tl else P("No versions captured.", cls="muted"),
        (Div(H2("Changes"), *[_feed_item(ch) for ch in chgs]) if chgs else ""),
        (Div(H2("Citations (traceability)"),
             P("Statutory references detected in this document:", cls="muted"),
             Div(*cite_chips)) if cites else ""),
        title=f"{d['title'][:40]} · TaxHub",
    )


def _tags(d):
    import json
    try:
        return json.loads(d["tags"] or "[]")
    except Exception:
        return []


@rt("/change/{change_id}")
def change(sess, change_id: int):
    if (r := require(sess)):
        return r
    ch = store.get_change(change_id)
    if not ch:
        return Shell(H1("Not found"), title="TaxHub")
    diff_lines = []
    for line in (ch["diff_text"] or "").splitlines():
        cls = ("add" if line.startswith("+") and not line.startswith("+++")
               else "del" if line.startswith("-") and not line.startswith("---")
               else "hdr" if line.startswith("@@") else "")
        diff_lines.append(Span(line + "\n", cls=cls))
    body = [
        P(A("← " + ch["title"], href=f"/document/{ch['document_id']}"), cls="muted"),
        H1("Change detail"),
        P(_pill(ch["change_type"]), " ",
          Span(f"{ch['detected_at']} · +{ch['added_chars']}/-{ch['removed_chars']} chars",
               cls="muted"), cls="sub"),
    ]
    if ch.get("ai_summary"):
        body += [H2("Summary"), P(ch["ai_summary"])]
    if ch.get("ai_impact"):
        body += [Div("Back-office impact: " + ch["ai_impact"], cls="impact",
                     style="font-size:14px")]
    body += [H2("Diff"), Div(*diff_lines, cls="diff") if diff_lines
             else P("No diff stored.", cls="muted")]
    if ch.get("ai_model"):
        body.append(P(f"AI summary by {ch['ai_model']}", cls="muted",
                      style="font-size:12px;margin-top:10px"))
    return Shell(*body, title="Change · TaxHub")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
