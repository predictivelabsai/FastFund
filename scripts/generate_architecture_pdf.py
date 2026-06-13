#!/usr/bin/env python3.12
"""Generate docs/architecture_readme.pdf from docs/architecture_readme.md.

markdown (tables + toc + fenced code) -> JTC-branded HTML -> WeasyPrint PDF.
The `[TOC]` marker becomes a navigable, anchor-linked table of contents, and
WeasyPrint also emits PDF bookmarks from the headings.

Mermaid code blocks don't render as diagrams in WeasyPrint — they fall back to
fenced code blocks, which is fine for a printable architecture reference.
"""
from __future__ import annotations

from pathlib import Path

import markdown
from weasyprint import HTML

ROOT = Path(__file__).resolve().parent.parent
MD = ROOT / "docs" / "architecture_readme.md"
PDF = ROOT / "docs" / "architecture_readme.pdf"

# JTC Group brand palette (jtcgroup.com)
CSS = """
@page { size: A4; margin: 22mm 18mm;
  @bottom-center { content: "JTC TaxHub — Architecture"; font-size: 8pt; color: #9a93a6; }
  @bottom-right  { content: counter(page); font-size: 8pt; color: #9a93a6; } }
body { font-family: -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
  color: #48484f; font-size: 10.5pt; line-height: 1.5; }
h1 { color: #550055; font-size: 24pt; border-bottom: 3px solid #ba2a84; padding-bottom: 6px; }
h2 { color: #6b1766; font-size: 15pt; margin-top: 22px; border-bottom: 1px solid #e6e3ec; padding-bottom: 3px; }
h3 { color: #6b1766; font-size: 12pt; }
a { color: #6b1766; text-decoration: none; }
strong { color: #48484f; }
hr { border: none; border-top: 1px solid #e6e3ec; margin: 18px 0; }
blockquote { border-left: 3px solid #ba2a84; margin: 12px 0; padding: 2px 14px;
  background: #f7f4f9; color: #48484f; }
table { border-collapse: collapse; width: 100%; margin: 12px 0; font-size: 9.5pt; }
th { background: #6b1766; color: #fff; text-align: left; padding: 7px 9px; }
td { border-bottom: 1px solid #e6e3ec; padding: 6px 9px; vertical-align: top; }
tr:nth-child(even) td { background: #f7f4f9; }
code { background: #efeaf3; color: #6b1766; padding: 1px 5px; border-radius: 4px; font-size: 9pt; }
pre { background: #f5f6f4; border: 1px solid #e6e3ec; border-radius: 6px; padding: 10px 12px;
  font-size: 8.5pt; white-space: pre-wrap; word-wrap: break-word; }
pre code { background: none; color: #48484f; padding: 0; }
img { max-width: 100%; border: 1px solid #e6e3ec; border-radius: 6px; margin: 10px 0; }
/* TOC (from the markdown toc extension) */
.toc { background: #f5f6f4; border: 1px solid #e6e3ec; border-radius: 8px; padding: 10px 18px; }
.toc ul { list-style: none; padding-left: 14px; margin: 4px 0; }
.toc > ul { padding-left: 0; }
.toc a { color: #6b1766; }
"""


def main() -> None:
    md_text = MD.read_text()
    html_body = markdown.markdown(
        md_text, extensions=["tables", "toc", "fenced_code", "attr_list"])
    html = (f"<html><head><meta charset='utf-8'><style>{CSS}</style></head>"
            f"<body>{html_body}</body></html>")
    HTML(string=html, base_url=str(MD.parent)).write_pdf(str(PDF))
    print(f"Wrote {PDF} ({PDF.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
