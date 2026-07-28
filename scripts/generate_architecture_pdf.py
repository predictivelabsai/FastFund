#!/usr/bin/env python3.12
"""Generate docs/architecture_readme.pdf from docs/architecture_readme.md.

markdown (tables + toc + fenced code) -> FF-branded HTML -> WeasyPrint PDF.
The `[TOC]` marker becomes a navigable, anchor-linked table of contents, and
WeasyPrint also emits PDF bookmarks from the headings.

Mermaid diagrams are pre-rendered to PNGs with headless Chromium (shared with
``generate_architecture_html.py``) and inlined as base64, so the printed PDF
shows the real architecture diagram — not a fenced code block.
"""
from __future__ import annotations

from pathlib import Path

import markdown
from weasyprint import HTML

ROOT = Path(__file__).resolve().parent.parent
MD = ROOT / "docs" / "architecture_readme.md"
PDF = ROOT / "docs" / "architecture_readme.pdf"

# FastFund brand palette (fastfund.org)
CSS = """
@page { size: A4; margin: 22mm 18mm;
  @bottom-center { content: "FastFund — Architecture"; font-size: 8pt; color: #9a93a6; }
  @bottom-right  { content: counter(page); font-size: 8pt; color: #9a93a6; } }
body { font-family: -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
  color: #48484f; font-size: 10.5pt; line-height: 1.5; }
h1 { color: #0b1f33; font-size: 24pt; border-bottom: 3px solid #0f766e; padding-bottom: 6px; }
h2 { color: #102a43; font-size: 15pt; margin-top: 22px; border-bottom: 1px solid #e6e3ec; padding-bottom: 3px; }
h3 { color: #102a43; font-size: 12pt; }
a { color: #102a43; text-decoration: none; }
strong { color: #48484f; }
hr { border: none; border-top: 1px solid #e6e3ec; margin: 18px 0; }
blockquote { border-left: 3px solid #0f766e; margin: 12px 0; padding: 2px 14px;
  background: #f7f4f9; color: #48484f; }
table { border-collapse: collapse; width: 100%; margin: 12px 0; font-size: 9.5pt; }
th { background: #102a43; color: #fff; text-align: left; padding: 7px 9px; }
td { border-bottom: 1px solid #e6e3ec; padding: 6px 9px; vertical-align: top; }
tr:nth-child(even) td { background: #f7f4f9; }
code { background: #e6f4f1; color: #102a43; padding: 1px 5px; border-radius: 4px; font-size: 9pt; }
pre { background: #f5f6f4; border: 1px solid #e6e3ec; border-radius: 6px; padding: 10px 12px;
  font-size: 8.5pt; white-space: pre-wrap; word-wrap: break-word; }
pre code { background: none; color: #48484f; padding: 0; }
img { max-width: 100%; border: 1px solid #e6e3ec; border-radius: 6px; margin: 10px 0; }
/* TOC (from the markdown toc extension) */
.toc { background: #f5f6f4; border: 1px solid #e6e3ec; border-radius: 8px; padding: 10px 18px; }
.toc ul { list-style: none; padding-left: 14px; margin: 4px 0; }
.toc > ul { padding-left: 0; }
.toc a { color: #102a43; }
figure { margin: 14px 0; text-align: center; page-break-inside: avoid; }
figure img { max-width: 100%; }
figcaption { color: #9a93a6; font-size: 8.5pt; margin-top: 5px; }
"""


def main() -> None:
    import base64
    from generate_architecture_html import extract_mermaid, render_diagrams

    md_text = MD.read_text()
    # Render the mermaid diagrams to PNGs (headless Chromium) and inline them, so
    # the PDF shows real diagrams instead of mermaid code blocks.
    text, sources = extract_mermaid(md_text)
    print(f"Found {len(sources)} mermaid diagram(s); rendering…")
    pngs = render_diagrams(sources) if sources else []

    html_body = markdown.markdown(
        text, extensions=["tables", "toc", "fenced_code", "attr_list"])

    captions = {0: "FastFund on Azure — target production architecture"}
    for i, png in enumerate(pngs):
        b64 = base64.b64encode(png).decode()
        cap = captions.get(i, "")
        fig = (f'<figure><img alt="diagram {i}" src="data:image/png;base64,{b64}">'
               + (f'<figcaption>{cap}</figcaption>' if cap else "") + "</figure>")
        html_body = html_body.replace(f"<p>MERMAIDIMG{i}</p>", fig)

    html = (f"<html><head><meta charset='utf-8'><style>{CSS}</style></head>"
            f"<body>{html_body}</body></html>")
    HTML(string=html, base_url=str(MD.parent)).write_pdf(str(PDF))
    print(f"Wrote {PDF} ({PDF.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
