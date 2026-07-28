#!/usr/bin/env python3
"""Render the agentic-architecture diagrams + a slide deck.

Pipeline:
  1. Extract ```mermaid blocks (and their nearest ## heading) from
     docs/technical_architecture.md.
  2. Render each to a PNG in docs/diagrams/ via mermaid-cli (mmdc, --no-sandbox).
  3. Compose a landscape slide deck (title + agents + one slide per diagram) and
     save it as docs/technical_architecture_slides.pdf (via Pillow).

Prereqs: Node/npx (mmdc fetched on demand), Pillow, fpdf2. Re-run after editing the md.
  pip install Pillow fpdf2

Usage:  python scripts/render_architecture.py
"""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
MD = ROOT / "docs" / "technical_architecture.md"
DIAGRAMS = ROOT / "docs" / "diagrams"
PDF = ROOT / "docs" / "technical_architecture_slides.pdf"

# JTC palette
NAVY = (85, 0, 85)
PURPLE = (107, 23, 102)
ACCENT = (186, 42, 132)
INK = (72, 72, 79)
BG = (251, 252, 253)
WHITE = (255, 255, 255)

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_B = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

SLIDE_W, SLIDE_H = 1600, 900


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40]


def extract_blocks(md_text: str):
    """Return [(title, mermaid_src)] in document order."""
    blocks = []
    heading = "Diagram"
    lines = md_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        h = re.match(r"^##+\s+(.*)", line)
        if h:
            heading = re.sub(r"^\d+\.\s*", "", h.group(1)).strip()
        if line.strip().startswith("```mermaid"):
            j = i + 1
            buf = []
            while j < len(lines) and not lines[j].strip().startswith("```"):
                buf.append(lines[j])
                j += 1
            blocks.append((heading, "\n".join(buf)))
            i = j
        i += 1
    return blocks


def render_pngs(blocks):
    DIAGRAMS.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        cfg = Path(td) / "pptr.json"
        cfg.write_text(json.dumps({"args": ["--no-sandbox", "--disable-setuid-sandbox"]}))
        out = []
        for n, (title, src) in enumerate(blocks, 1):
            name = f"{n:02d}-{slug(title)}"
            mmd = Path(td) / f"{name}.mmd"
            mmd.write_text(src)
            png = DIAGRAMS / f"{name}.png"
            print(f"  rendering {png.name} …")
            subprocess.run(
                ["npx", "-y", "@mermaid-js/mermaid-cli@11", "-i", str(mmd),
                 "-o", str(png), "-b", "white", "-s", "2", "-p", str(cfg)],
                check=True, capture_output=True)
            out.append((title, png))
        return out


def _font(sz, bold=False):
    return ImageFont.truetype(FONT_B if bold else FONT, sz)


def _slide():
    img = Image.new("RGB", (SLIDE_W, SLIDE_H), WHITE)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, SLIDE_W, 12], fill=ACCENT)          # top accent
    d.rectangle([0, SLIDE_H - 48, SLIDE_W, SLIDE_H], fill=NAVY)  # footer
    d.text((40, SLIDE_H - 38), "SFO Hub — Agentic Architecture", font=_font(18), fill=WHITE)
    return img, d


def title_slide():
    img, d = _slide()
    d.text((80, 320), "SFO Hub", font=_font(96, True), fill=NAVY)
    d.text((84, 430), "Agentic Architecture", font=_font(54, True), fill=ACCENT)
    d.text((86, 520), "AI advisor for cross-selling & upselling to single family offices",
           font=_font(28), fill=INK)
    d.text((86, 570), "FastHTML · LangGraph orchestrator · 6 specialist agents · hybrid engine · text-to-SQL",
           font=_font(20), fill=INK)
    return img


def agents_slide():
    img, d = _slide()
    d.text((80, 70), "The agents", font=_font(52, True), fill=NAVY)
    agents = [
        ("profile_agent", "Ground in who the client is — AUM, mix, services, pains"),
        ("needs_agent", "Detect gaps from a described setup → service categories"),
        ("services_agent", "Explain the JTC services for a topic"),
        ("recommend_agent", "Ranked cross/upsell with rationale + estimated value"),
        ("benchmark_agent", "Aggregate industry benchmarks to frame advice"),
        ("data_agent", "Quantitative book-wide questions via text-to-SQL"),
    ]
    y = 210
    for name, desc in agents:
        d.ellipse([90, y + 8, 110, y + 28], fill=ACCENT)
        d.text((130, y), name, font=_font(30, True), fill=PURPLE)
        d.text((500, y + 4), desc, font=_font(24), fill=INK)
        y += 100
    return img


def diagram_slide(title, png_path):
    img, d = _slide()
    d.text((80, 60), title, font=_font(44, True), fill=NAVY)
    dia = Image.open(png_path).convert("RGBA")
    max_w, max_h = SLIDE_W - 160, SLIDE_H - 240
    scale = min(max_w / dia.width, max_h / dia.height, 1.0)
    dia = dia.resize((int(dia.width * scale), int(dia.height * scale)))
    x = (SLIDE_W - dia.width) // 2
    y = 170 + (max_h - dia.height) // 2
    bg = Image.new("RGB", img.size, WHITE)
    bg.paste(img, (0, 0))
    bg.paste(dia, (x, y), dia)
    return bg


def build_pdf(rendered):
    from fpdf import FPDF
    slides = [title_slide()]
    for title, png in rendered:
        slides.append(diagram_slide(title, png))
        if "orchestration" in title.lower():
            slides.append(agents_slide())
    # 16:9 landscape pages, one composed slide image per page (fpdf2 — no JPEG dep).
    pw, ph = 254.0, 142.875  # mm (≈10in × 5.625in)
    pdf = FPDF(orientation="L", unit="mm", format=(ph, pw))
    pdf.set_auto_page_break(False)
    with tempfile.TemporaryDirectory() as td:
        for i, sl in enumerate(slides):
            p = Path(td) / f"s{i:02d}.png"
            sl.save(p)
            pdf.add_page()
            pdf.image(str(p), x=0, y=0, w=pw, h=ph)
        pdf.output(str(PDF))
    print(f"  wrote {PDF.relative_to(ROOT)} ({len(slides)} slides)")


def main():
    blocks = extract_blocks(MD.read_text())
    print(f"Found {len(blocks)} mermaid diagrams in {MD.name}")
    rendered = render_pngs(blocks)
    build_pdf(rendered)
    print("Done.")


if __name__ == "__main__":
    main()
