#!/usr/bin/env python3.12
"""Generate docs/architecture_readme.html from docs/architecture_readme.md.

Mermaid code blocks are rendered to PNGs with headless Chromium (mermaid.js) and
inlined as base64, so the HTML is fully self-contained (works offline, no JS,
embeddable in the app's Technical Guide pane). The first diagram (the §2 Azure
architecture) is also written to docs/architecture_diagram.png as a standalone.

markdown (tables + toc + fenced code) -> FF-branded HTML with the diagrams
wired in.

Run:  python3.12 scripts/generate_architecture_html.py
"""
from __future__ import annotations

import base64
import json
import re
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parent.parent
MD = ROOT / "docs" / "architecture_readme.md"
HTML_OUT = ROOT / "docs" / "architecture_readme.html"
DIAGRAM_PNG = ROOT / "docs" / "architecture_diagram.png"

MERMAID_CDN = "https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs"

CSS = """
:root{--navy:#102a43;--navy2:#0b1f33;--accent:#0f766e;--text:#48484f;--muted:#7a7a85;--line:#e6e3ec;}
*{box-sizing:border-box}
body{font-family:-apple-system,"Segoe UI",Helvetica,Arial,sans-serif;color:var(--text);
  font-size:15px;line-height:1.6;max-width:900px;margin:0 auto;padding:32px 28px;}
h1{color:var(--navy2);font-size:30px;border-bottom:3px solid var(--accent);padding-bottom:8px}
h2{color:var(--navy);font-size:21px;margin-top:30px;border-bottom:1px solid var(--line);padding-bottom:4px}
h3{color:var(--navy);font-size:16px}
a{color:var(--navy);text-decoration:none}a:hover{text-decoration:underline}
hr{border:none;border-top:1px solid var(--line);margin:22px 0}
blockquote{border-left:3px solid var(--accent);margin:14px 0;padding:2px 16px;background:#f7f4f9}
table{border-collapse:collapse;width:100%;margin:14px 0;font-size:13.5px}
th{background:var(--navy);color:#fff;text-align:left;padding:8px 10px}
td{border-bottom:1px solid var(--line);padding:7px 10px;vertical-align:top}
tr:nth-child(even) td{background:#f7f4f9}
code{background:#e6f4f1;color:var(--navy);padding:1px 5px;border-radius:4px;font-size:13px}
pre{background:#f5f6f4;border:1px solid var(--line);border-radius:6px;padding:12px 14px;
  font-size:12.5px;overflow-x:auto}
pre code{background:none;color:var(--text);padding:0}
figure{margin:18px 0;text-align:center}
figure img{max-width:100%;border:1px solid var(--line);border-radius:8px;padding:10px;background:#fff}
figcaption{color:var(--muted);font-size:12.5px;margin-top:6px}
.toc{background:#f5f6f4;border:1px solid var(--line);border-radius:8px;padding:10px 18px}
.toc ul{list-style:none;padding-left:14px;margin:4px 0}.toc>ul{padding-left:0}
"""


def extract_mermaid(md_text: str) -> tuple[str, list[str]]:
    """Replace each ```mermaid block with a placeholder; return (text, sources)."""
    blocks: list[str] = []

    def repl(m: re.Match) -> str:
        blocks.append(m.group(1).strip())
        return f"\n\nMERMAIDIMG{len(blocks) - 1}\n\n"

    text = re.sub(r"```mermaid\n(.*?)```", repl, md_text, flags=re.DOTALL)
    return text, blocks


def render_diagrams(sources: list[str]) -> list[bytes]:
    """Render each mermaid source to a tight PNG with headless Chromium."""
    from playwright.sync_api import sync_playwright

    pngs: list[bytes] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(device_scale_factor=2)
        for i, src in enumerate(sources):
            html = (
                "<!doctype html><html><head><meta charset='utf-8'>"
                "<style>body{margin:0;background:#fff;display:inline-block;padding:14px}</style>"
                "</head><body><div id='d' class='mermaid'></div>"
                "<script type='module'>"
                f"import mermaid from '{MERMAID_CDN}';"
                "mermaid.initialize({startOnLoad:false,theme:'base',"
                "themeVariables:{primaryColor:'#f3e9f3',primaryBorderColor:'#102a43',"
                "primaryTextColor:'#48484f',lineColor:'#0f766e',fontFamily:'Segoe UI,Arial'}});"
                f"const src={json.dumps(src)};"
                "const {svg}=await mermaid.render('g','' + src);"
                "document.getElementById('d').innerHTML=svg;"
                "window.__done=true;"
                "</script></body></html>"
            )
            page.set_content(html, wait_until="load")
            page.wait_for_function("window.__done===true", timeout=20000)
            page.wait_for_timeout(250)
            png = page.locator("#d svg").screenshot()
            pngs.append(png)
            print(f"  rendered diagram {i} ({len(png) // 1024} KB)")
        browser.close()
    return pngs


def main() -> None:
    md_text = MD.read_text()
    text, sources = extract_mermaid(md_text)
    print(f"Found {len(sources)} mermaid diagram(s); rendering…")
    pngs = render_diagrams(sources)

    if pngs:
        DIAGRAM_PNG.write_bytes(pngs[0])
        print(f"Wrote {DIAGRAM_PNG} ({DIAGRAM_PNG.stat().st_size // 1024} KB)")

    html_body = markdown.markdown(
        text, extensions=["tables", "toc", "fenced_code", "attr_list"])

    captions = {0: "FastFund on Azure — target production architecture"}
    for i, png in enumerate(pngs):
        b64 = base64.b64encode(png).decode()
        cap = captions.get(i, "")
        fig = (f'<figure><img alt="diagram {i}" src="data:image/png;base64,{b64}">'
               + (f'<figcaption>{cap}</figcaption>' if cap else "") + "</figure>")
        html_body = html_body.replace(f"<p>MERMAIDIMG{i}</p>", fig)

    html = (f"<!doctype html><html><head><meta charset='utf-8'>"
            f"<title>FastFund — Architecture (Azure)</title><style>{CSS}</style></head>"
            f"<body>{html_body}</body></html>")
    HTML_OUT.write_text(html)
    print(f"Wrote {HTML_OUT} ({HTML_OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
