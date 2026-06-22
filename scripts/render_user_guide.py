#!/usr/bin/env python3
"""Build the SFO Hub user guide: a branded, landscape HTML slide deck →
docs/sfohub_user_guide.html, plus docs/sfohub_user_guide.md and the dated PDF.

The PDF is produced by headless Chrome's `--print-to-pdf` over a print-oriented
copy of the deck (each `.slide` is one 1280×720 page) — so the HTML/CSS controls
every slide and the brand footer stays intact. No screenshots needed for the deck
chrome itself; only the in-app screenshots in docs/screenshots/ are embedded.

The PDF is DATE-STAMPED — each regeneration writes docs/sfohub_user_guide_<YYYY-MM-DD>.pdf
(override with --date YYYY-MM-DD) and deletes the previous dated PDF. The app
serves the newest one at /user-guide-pdf.

Usage:
  python scripts/render_user_guide.py html          # write the .html deck + the .md
  python scripts/render_user_guide.py pdf [--date YYYY-MM-DD]   # → dated .pdf via Chrome
  python scripts/render_user_guide.py all [--date ...]         # html + md + pdf
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
SHOTS = "screenshots"  # relative to docs/
CHROME_CANDIDATES = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")


def _today():
    return date.today().isoformat()


def _pdf_name(stamp=None):
    return f"sfohub_user_guide_{stamp or _today()}.pdf"

# (id, title, image, [bullets])  — image None ⇒ text/section slide
SLIDES = [
    ("title", "SFO Hub", None, [
        "AI relationship-manager for JTC Private Office",
        "Cross-sell & upsell advisor for single family offices",
        "User Guide"]),
    ("journey", "A typical advisor journey", "JOURNEY", [
        "Open a family||Pick a family office from the client book.",
        "Understand them||Profile, portfolio and pain points at a glance.",
        "Ask the advisor||Plain-English questions; specialist agents answer.",
        "Review recommendations||Traceable cross-sell and upsell, ranked by fit.",
        "Work the pipeline||Advance cards: suggested → presented → accepted.",
        "Convert and book||Schedule, propose and book the engagement."]),
    ("advisor", "The advisor workspace", "ug-01-advisor.png", [
        "Three panes: client book + navigation (left), the AI advisor (centre), "
        "and the open family's profile + live recommendations (right).",
        "Ask in plain English — the advisor routes your question to specialist "
        "agents and answers with citations and open-in-panel links.",
        "Suggestion chips below the box get you started."]),
    ("clients", "Client book", "ug-02-clients.png", [
        "Every family office in one filterable table — AUM, domicile, stage, "
        "family, current services.",
        "Filter by lifecycle stage (lead · onboarding · client).",
        "Click a name to advise on them; ‘Open’ for the full profile; "
        "‘+ New family office’ to onboard a lead."]),
    ("profile", "Family profile", "ug-03-sfo-detail.png", [
        "AUM, generations, family size and the accepted/booked pipeline at a glance.",
        "Asset-allocation donut, current services, jurisdictions and pain points.",
        "Family members and full history in one place."]),
    ("portfolio", "Portfolio & transactions", "ug-03b-portfolio.png", [
        "Mock holdings across PE, real estate, public equity, luxury, cash and "
        "alternatives — each with a performance figure.",
        "Recent cash-flow transactions: capital calls, distributions, buys, fees.",
        "Drives the ‘personalised insights’ the advisor reasons over."]),
    ("pipeline", "Pipeline (kanban)", "ug-04-pipeline.png", [
        "Every recommendation as a card, grouped by funnel stage: suggested → "
        "presented → accepted → booked (or declined).",
        "Drag a card between columns to advance it — status persists instantly.",
        "Filter by cross-sell / upsell or by service category."]),
    ("calendar", "Pipeline calendar", "ug-05-calendar.png", [
        "Scheduled consultations, proposals and follow-ups across the book.",
        "Urgency-coded (overdue · due soon · upcoming) and filterable.",
        "Export to your own calendar with the iCal (.ics) button."]),
    ("coverage", "Coverage matrix", "ug-06-coverage.png", [
        "A family × service grid: held, recommended, or whitespace at a glance.",
        "Spot cross-sell opportunities across the whole book in one view.",
        "Click a family to jump to the advisor."]),
    ("graph", "Relationship graph", "ug-07-graph.png", [
        "Two modes: the cross-sell schema (how services bundle, with weights) and "
        "the client book (families ↔ the services they hold and are offered).",
        "Click a node to open the service or the family.",
        "The cross-sell graph is what the engine traverses to find bundles."]),
    ("dashboard", "Analytics dashboard", "ug-08-dashboard.png", [
        "Top line: family offices, recommendations, pipeline value, acceptance rate.",
        "Upsell funnel, pipeline value by category, average allocation, interest heatmap.",
        "Activity trends — conversations and recommendations over the last 12 weeks."]),
    ("documents", "Documents & insights", "ug-09-documents.png", [
        "Upload portfolio summaries, trust deeds and inventories (txt, csv, md, PDF).",
        "Attach to a family and the AI extracts a profile — asset mix, pain points, "
        "services in place — and refreshes their recommendations.",
        "Stored in Azure Blob Storage."]),
    ("onboard", "Onboard a new lead", "ug-10-onboard.png", [
        "Capture a prospect's profile through a guided intake form.",
        "We create the lead and immediately produce a tailored service roadmap.",
        "The new family appears in the client book, ready to advise."]),
    ("services", "Service catalogue", "ug-11-services.png", [
        "The JTC Private Office offerings the advisor cross-sells and upsells.",
        "Each service shows its category, tier and description.",
        "Open a service to see common bundles and which families hold it."]),
    ("how", "How recommendations are made", None, [
        "A transparent rule catalogue fires on the profile (services, asset mix, "
        "pain points, AUM, stage).",
        "The cross-sell graph expands the candidate set; an AI layer re-ranks and "
        "rewrites each rationale in a relationship-manager voice.",
        "Every recommendation is traceable to the rule or graph edge behind it.",
        "All family-office data is synthetic — no real client data is used."]),
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
/* journey / happy-flow ribbon */
.journey .flow{display:flex;align-items:stretch;flex:1;margin-top:8px}
.step{flex:1;background:#fff;border:1px solid #ecdce8;border-top:4px solid #ba2a84;
  border-radius:10px;padding:18px 15px;display:flex;flex-direction:column;
  box-shadow:0 6px 18px rgba(85,0,85,.08)}
.num{width:36px;height:36px;border-radius:50%;background:#6b1766;color:#fff;
  font-weight:800;font-size:18px;display:flex;align-items:center;justify-content:center;
  margin-bottom:14px}
.stt{color:#550055;font-weight:700;font-size:18px;margin-bottom:9px;line-height:1.2}
.std{color:#5a5a62;font-size:14px;line-height:1.45}
.arr{display:flex;align-items:center;color:#ba2a84;font-size:26px;font-weight:700;padding:0 5px}
.jnote{margin-top:22px;color:#7a7a85;font-size:16px;text-align:center}
"""


def _slide_html(s):
    sid, title, img, bullets = s
    foot = '<div class="foot"><span class="b">SFO <span>Hub</span></span>'\
           '&nbsp;&nbsp;·&nbsp;&nbsp;User Guide</div>'
    if sid == "title":
        return (f'<div class="slide title-slide" id="{sid}"><div class="bar"></div>'
                f'<div class="body"><div class="big">SFO <span>Hub</span></div>'
                f'<div class="sub">{bullets[0]}</div>'
                f'<div class="sub small">{bullets[1]}</div>'
                f'<div class="sub" style="margin-top:26px;color:#6b1766;font-weight:700">'
                f'{bullets[2]}</div></div>{foot}</div>')
    if img == "JOURNEY":
        steps = ""
        for i, b in enumerate(bullets, 1):
            t, d = b.split("||")
            steps += (f'<div class="step"><div class="num">{i}</div>'
                      f'<div class="stt">{t}</div><div class="std">{d}</div></div>')
            if i < len(bullets):
                steps += '<div class="arr">→</div>'
        return (f'<div class="slide journey" id="{sid}"><div class="bar"></div>'
                f'<div class="body"><h2>{title}</h2><div class="flow">{steps}</div>'
                f'<div class="jnote">From a cold lead to a booked engagement — the '
                f'advisor supports every step.</div></div>{foot}</div>')
    lis = "".join(f"<li>{b}</li>" for b in bullets)
    if img is None:
        return (f'<div class="slide section" id="{sid}"><div class="bar"></div>'
                f'<div class="body"><h2>{title}</h2><ul class="bul">{lis}</ul></div>{foot}</div>')
    return (f'<div class="slide" id="{sid}"><div class="bar"></div><div class="body">'
            f'<h2>{title}</h2><div class="split"><ul class="bul">{lis}</ul>'
            f'<div class="shot"><img src="{SHOTS}/{img}"></div></div></div>{foot}</div>')


def write_html():
    body = "\n".join(_slide_html(s) for s in SLIDES)
    html = (f"<!doctype html><html><head><meta charset='utf-8'>"
            f"<title>SFO Hub — User Guide</title><style>{CSS}</style></head>"
            f"<body>{body}</body></html>")
    (DOCS / "sfohub_user_guide.html").write_text(html)
    print(f"wrote docs/sfohub_user_guide.html ({len(SLIDES)} slides)")


def write_md(stamp=None):
    pdf = _pdf_name(stamp)
    out = ["# SFO Hub — User Guide\n",
           "AI relationship-manager for JTC Private Office: a cross-sell & upsell "
           f"advisor for single family offices. A slide version is at [`{pdf}`]({pdf}).\n"]
    for sid, title, img, bullets in SLIDES:
        if sid == "title":
            continue
        out.append(f"## {title}\n")
        if img == "JOURNEY":
            for i, b in enumerate(bullets, 1):
                t, d = b.split("||")
                out.append(f"{i}. **{t}** — {d}")
            out.append("")
            continue
        if img:
            out.append(f"![{title}]({SHOTS}/{img})\n")
        out += [f"- {b}" for b in bullets]
        out.append("")
    (DOCS / "sfohub_user_guide.md").write_text("\n".join(out))
    print("wrote docs/sfohub_user_guide.md")


def _print_deck_html():
    """A print-oriented copy of the deck: one .slide per 1280×720 page."""
    print_css = CSS + (
        "\n@page{size:1280px 720px;margin:0}\nhtml,body{background:#fff}\n"
        ".slide{margin:0 !important;page-break-after:always;break-after:page;box-shadow:none}\n"
        ".slide:last-child{page-break-after:auto;break-after:auto}\n")
    body = "\n".join(_slide_html(s) for s in SLIDES)
    return (f"<!doctype html><html><head><meta charset='utf-8'>"
            f"<title>SFO Hub — User Guide</title><style>{print_css}</style></head>"
            f"<body>{body}</body></html>")


def build_pdf(stamp=None):
    chrome = next((c for c in CHROME_CANDIDATES if shutil.which(c)), None)
    if not chrome:
        sys.exit(f"no Chrome/Chromium on PATH (tried {', '.join(CHROME_CANDIDATES)})")
    deck = DOCS / "_print_deck.html"
    deck.write_text(_print_deck_html())  # in docs/ so screenshots/ resolves relatively
    name = _pdf_name(stamp)
    # Keep only the latest dated PDF (and drop the legacy undated one).
    for old in DOCS.glob("sfohub_user_guide_*.pdf"):
        if old.name != name:
            old.unlink()
    (DOCS / "sfohub_user_guide.pdf").unlink(missing_ok=True)
    out = DOCS / name
    try:
        subprocess.run(
            [chrome, "--headless=new", "--no-sandbox", "--disable-gpu",
             "--no-pdf-header-footer", "--virtual-time-budget=4000",
             f"--print-to-pdf={out}", deck.resolve().as_uri()],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    finally:
        deck.unlink(missing_ok=True)
    print(f"wrote docs/{name} ({len(SLIDES)} slides)")


def _arg(flag, default=None):
    if flag in sys.argv:
        i = sys.argv.index(flag)
        return sys.argv[i + 1] if i + 1 < len(sys.argv) else default
    return default


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "html"
    stamp = _arg("--date")
    if cmd in ("html", "all"):
        write_html()
        write_md(stamp)
    if cmd in ("pdf", "all"):
        build_pdf(stamp)
    if cmd == "ids":
        print(" ".join(s[0] for s in SLIDES))
