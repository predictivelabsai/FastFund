#!/usr/bin/env python3.12
"""Build the TaxHub user guide as a branded, landscape HTML slide deck →
docs/taxhub_user_guide.html, docs/taxhub_user_guide.md, and docs/taxhub_user_guide.pdf.

Each screen of the app gets one 1280×720 slide: a short bullet summary on the
left and a real product screenshot on the right (docs/screenshots/ug-*.png,
captured by driving the live app with Playwright). The PDF is "nicely formatted
HTML" — every slide is rendered in headless Chromium and stitched into one
landscape page each with fpdf2, so HTML/CSS controls the whole layout.

Re-shoot the screenshots whenever the UI changes, then re-run this script.

Usage:
  python3.12 scripts/generate_user_guide.py          # html + md + pdf
  python3.12 scripts/generate_user_guide.py html     # html + md only (no Chromium)
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
SHOTS = "screenshots"  # relative to docs/

# (id, title, image, [bullets])  — image None ⇒ text/section slide
SLIDES = [
    ("title", "TaxHub", None, [
        "Tax-law traceability & filing intelligence for fund back-office",
        "Ask in plain English; every answer cites the source law",
        "User Guide"]),
    ("assistant", "The Assistant workspace", "ug-01-assistant.png", [
        "Three panes: navigation + forms tree + shortcuts (left), the AI "
        "assistant (centre), and a live regulatory-changes feed (right).",
        "Ask in plain English — it routes to the right specialist agent "
        "(form-finder, tax-law, changes, free search) and answers with citations.",
        "Suggestion chips and `shortcut:` prefixes get you started fast."]),
    ("dashboard", "Compliance dashboard", "ug-02-dashboard.png", [
        "The whole book at a glance: obligations across all entities, how many "
        "are filed, and how many await expert sign-off.",
        "An overdue / due-soon digest surfaces what needs attention now.",
        "Every figure links straight through to the underlying list."]),
    ("entities", "Entities portfolio", "ug-03-entities.png", [
        "Every fund, SPV, GP, trust and holdco you administer in one table — "
        "type, domicile, jurisdictions, financial year-end and filing ratio.",
        "Add an entity inline, or bulk-import the portfolio from CSV.",
        "Each entity's obligations and deadlines build on this record."]),
    ("entity", "Entity profile", "ug-04-entity-detail.png", [
        "A single entity's full picture — domicile, jurisdictions, year-end and "
        "client reference.",
        "Its complete obligation register with filing status and deadlines.",
        "The starting point the Assistant reasons over for entity questions."]),
    ("obligations", "Obligations register", "ug-05-obligations.png", [
        "Every filing obligation across the book — entity, jurisdiction, type, "
        "deadline and status.",
        "Track what's filed, confirmed, or still awaiting expert sign-off.",
        "Filter to economic-substance, CRS/FATCA, returns and more."]),
    ("calendar", "Filing calendar", "ug-06-calendar.png", [
        "All deadlines on one timeline, urgency-coded: overdue, due soon, upcoming.",
        "Filter by entity, jurisdiction or obligation type.",
        "Resolves real due-dates from each entity's year-end and the rules."]),
    ("coverage", "Coverage map", "ug-07-coverage.png", [
        "A jurisdiction × obligation grid showing filed / confirmed coverage "
        "at a glance.",
        "Spot gaps — where an obligation exists but nothing has been filed yet.",
        "Drill from any cell into the obligations behind it."]),
    ("jurisdictions", "Jurisdictions", "ug-08-jurisdictions.png", [
        "The jurisdictions you operate in, each with its filing requirements.",
        "Open one to see the obligations, forms and source law that apply.",
        "Backed by a live-scraped corpus across 26 jurisdictions."]),
    ("documents", "Document library", "ug-09-documents.png", [
        "Every source document — acts, guidance notes, circulars and forms.",
        "Full-text searchable; each is the provenance behind an answer.",
        "Open a document to read it with its version history."]),
    ("changes", "Regulatory changes", "ug-10-changes.png", [
        "A running feed of what changed in the law, newest first, by jurisdiction.",
        "Each change links to the document version that introduced it.",
        "The same feed powers the `changes:` shortcut in the Assistant."]),
    ("hierarchy", "Document hierarchy", "ug-11-document-hierarchy.png", [
        "A tree view of the corpus: jurisdiction → document → version.",
        "See how the law is structured before you dive into the text.",
        "Click through to any document in context."]),
    ("ontology", "Ontology graph", "ug-12-ontology.png", [
        "A force-directed graph of how forms trace back to the law "
        "(283 forms, 39 laws linked).",
        "Toggle between the Provenance view and the Schema view.",
        "Click a form to open it, or a legislation node to open the source law."]),
    ("law", "Cited tax-law answers", "ug-13-assistant-answer.png", [
        "Ask a tax-law question and the Assistant answers from the corpus — "
        "never from thin air.",
        "Every claim carries a numbered citation, with open-doc links to the source.",
        "Here: 'what does CIGA mean?' → Core Income Generating Activities, sourced."]),
    ("forms", "Form finder", "ug-14-form-finder.png", [
        "Describe what you need to file; the form-finder ranks the matching forms.",
        "Each result shows the filing type (downloadable PDF vs online portal), "
        "who files, the deadline and the frequency.",
        "Open any form straight from the answer."]),
    ("aeoi", "AEOI readiness · FATCA / CRS", "ug-15-aeoi.png", [
        "Every entity is classified under CRS (Financial Institution vs Active / "
        "Passive NFE) and FATCA (FFI / NFFE + GIIN) — derived from its own profile.",
        "Validated for the documentation a return needs: a valid W-8 / self-"
        "certification, a treaty TIN, a GIIN, and identified controlling persons.",
        "A filing-ready / needs-review / not-ready verdict per entity, with the "
        "exact blocking issue and how to fix it — CRS 3.0 checks built in."]),
    ("w8", "Agent auto-fills a W-8BEN-E", "ug-16-w8.png", [
        "Ask the assistant to prepare a W-8BEN-E for an entity; the form opens on "
        "the right and fills in live from everything TaxHub already knows.",
        "Each field is validated as it lands — here the missing GIIN for a "
        "Reporting Model 1 FFI is flagged in red and the form is marked Not ready.",
        "Edit any field, re-validate, then sign off — the human-in-the-loop "
        "guardrail. A conceptual demo of agent-assisted form completion."]),
    ("how", "How it works", None, [
        "A live-scraped corpus of tax law across 26 jurisdictions, stored as a "
        "graph (documents → versions → changes → citations) in Neo4j.",
        "Specialist agents retrieve over that graph (vector + hybrid) and an LLM "
        "writes the answer — always grounded in, and citing, the source.",
        "Entities, obligations and deadlines sit on top, so the same corpus "
        "answers both 'what's the law?' and 'what do I owe, and when?'."]),
]

CSS = """
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,'Segoe UI',Helvetica,Arial,sans-serif;background:#33203a}
.slide{width:1280px;height:720px;background:#fbfcfd;position:relative;overflow:hidden;
  margin:0 auto 28px;display:flex;flex-direction:column}
.bar{height:14px;background:linear-gradient(90deg,#550055,#ba2a84)}
.foot{position:absolute;bottom:0;left:0;right:0;height:42px;background:#550055;color:#fff;
  display:flex;align-items:center;padding:0 28px;font-size:13px;letter-spacing:.04em}
.foot .b{font-weight:700}.foot .b span{color:#ec9bd0}
.body{flex:1;padding:34px 44px 56px;display:flex;flex-direction:column}
h2{color:#6b1766;font-size:34px;margin-bottom:18px}
.split{display:flex;gap:32px;flex:1;align-items:center}
.bul{flex:0 0 38%}.bul li{list-style:none;color:#48484f;font-size:18px;line-height:1.5;
  margin:14px 0;padding-left:22px;position:relative}
.bul li:before{content:'';position:absolute;left:0;top:9px;width:10px;height:10px;
  border-radius:50%;background:#ba2a84}
.shot{flex:1;text-align:center}
.shot img{max-width:100%;max-height:560px;border:1px solid #e6e3ec;border-radius:10px;
  box-shadow:0 10px 30px rgba(85,0,85,.18)}
/* title + section slides */
.title-slide .body{justify-content:center;padding-left:80px}
.title-slide .big{font-size:96px;font-weight:800;color:#550055;line-height:1}
.title-slide .big span{color:#ba2a84}
.title-slide .sub{font-size:26px;color:#48484f;margin-top:18px}
.title-slide .sub.small{font-size:20px;color:#7a7a85;margin-top:8px}
.section .body{justify-content:center}
.section .bul{flex:0 0 100%}.section .bul li{font-size:22px;margin:18px 0}
"""

FOOT = ('<div class="foot"><span class="b">Tax<span>Hub</span></span>'
        '&nbsp;&nbsp;·&nbsp;&nbsp;User Guide</div>')


def _slide_html(s):
    sid, title, img, bullets = s
    if sid == "title":
        return (f'<div class="slide title-slide" id="{sid}"><div class="bar"></div>'
                f'<div class="body"><div class="big">Tax<span>Hub</span></div>'
                f'<div class="sub">{bullets[0]}</div>'
                f'<div class="sub small">{bullets[1]}</div>'
                f'<div class="sub" style="margin-top:26px;color:#6b1766;font-weight:700">'
                f'{bullets[2]}</div></div>{FOOT}</div>')
    lis = "".join(f"<li>{b}</li>" for b in bullets)
    if img is None:
        return (f'<div class="slide section" id="{sid}"><div class="bar"></div>'
                f'<div class="body"><h2>{title}</h2><ul class="bul">{lis}</ul></div>{FOOT}</div>')
    return (f'<div class="slide" id="{sid}"><div class="bar"></div><div class="body">'
            f'<h2>{title}</h2><div class="split"><ul class="bul">{lis}</ul>'
            f'<div class="shot"><img src="{SHOTS}/{img}"></div></div></div>{FOOT}</div>')


def write_html():
    body = "\n".join(_slide_html(s) for s in SLIDES)
    html = (f"<!doctype html><html><head><meta charset='utf-8'>"
            f"<title>TaxHub — User Guide</title><style>{CSS}</style></head>"
            f"<body>{body}</body></html>")
    (DOCS / "taxhub_user_guide.html").write_text(html)
    print(f"wrote docs/taxhub_user_guide.html ({len(SLIDES)} slides)")


def write_md():
    out = ["# TaxHub — User Guide\n",
           "Tax-law traceability & filing intelligence for fund back-office: ask "
           "in plain English and every answer cites the source law. A slide "
           "version is at [`taxhub_user_guide.pdf`](taxhub_user_guide.pdf).\n"]
    for sid, title, img, bullets in SLIDES:
        if sid == "title":
            continue
        out.append(f"## {title}\n")
        if img:
            out.append(f"![{title}]({SHOTS}/{img})\n")
        out += [f"- {b}" for b in bullets]
        out.append("")
    (DOCS / "taxhub_user_guide.md").write_text("\n".join(out))
    print("wrote docs/taxhub_user_guide.md")


def write_pdf():
    """Render each .slide in headless Chromium → PNG, stitch into a landscape PDF."""
    from fpdf import FPDF
    from playwright.sync_api import sync_playwright

    pngs: list[bytes] = []
    html_path = (DOCS / "taxhub_user_guide.html").as_uri()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 720},
                                device_scale_factor=2)
        page.goto(html_path, wait_until="networkidle")
        slides = page.locator(".slide")
        for i in range(slides.count()):
            pngs.append(slides.nth(i).screenshot())
            print(f"  rendered slide {i + 1}/{slides.count()}")
        browser.close()

    pw, ph = 338.667, 190.5  # 16:9 landscape mm (1280:720 @ 96dpi)
    pdf = FPDF(orientation="L", unit="mm", format=(ph, pw))
    pdf.set_auto_page_break(False)
    import io
    for png in pngs:
        pdf.add_page()
        pdf.image(io.BytesIO(png), x=0, y=0, w=pw, h=ph)
    pdf.output(str(DOCS / "taxhub_user_guide.pdf"))
    print(f"wrote docs/taxhub_user_guide.pdf ({len(pngs)} slides)")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    write_html()
    write_md()
    if cmd != "html":
        write_pdf()
