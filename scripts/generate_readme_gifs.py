#!/usr/bin/env python3
"""Build README GIFs from the reproducible Playwright product screenshots."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
SHOTS = ROOT / "docs" / "screenshots"
DOCS = ROOT / "docs"
SIZE = (960, 600)

DEMOS = {
    "fastfund-family-office.gif": [
        ("fastfund-family-advisor.png", "Ask the family-office advisor"),
        ("fastfund-families.png", "Review the family and outreach book"),
        ("fastfund-family-profile.png", "Connect portfolio, ownership and tax context"),
        ("fastfund-outreach-pipeline.png", "Move opportunities through the pipeline"),
    ],
    "fastfund-tax-filings.gif": [
        ("fastfund-assistant.png", "Ask tax, form and regulatory-change questions"),
        ("fastfund-entities.png", "Link legal entities to their family office"),
        ("fastfund-obligations.png", "Determine and track filing obligations"),
        ("fastfund-filing-calendar.png", "Work the multijurisdiction filing calendar"),
        ("fastfund-tax-dashboard.png", "Monitor the complete compliance book"),
    ],
}


def fit(path: Path, caption: str) -> Image.Image:
    image = Image.open(path).convert("RGB")
    image.thumbnail(SIZE, Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", SIZE, "#f7fafc")
    x = (SIZE[0] - image.width) // 2
    y = max(0, (SIZE[1] - image.height) // 2)
    canvas.paste(image, (x, y))
    draw = ImageDraw.Draw(canvas)
    box = (16, SIZE[1] - 55, SIZE[0] - 16, SIZE[1] - 14)
    draw.rounded_rectangle(box, radius=8, fill="#0b1f33")
    draw.text((32, SIZE[1] - 44), caption, fill="#ffffff")
    return canvas


def build(name: str, sequence: list[tuple[str, str]]) -> None:
    scenes = [fit(SHOTS / filename, caption) for filename, caption in sequence]
    frames: list[Image.Image] = []
    durations: list[int] = []
    for index, scene in enumerate(scenes):
        frames.append(scene)
        durations.append(1700)
        nxt = scenes[(index + 1) % len(scenes)]
        for step in range(1, 5):
            frames.append(Image.blend(scene, nxt, step / 5))
            durations.append(90)
    frames[0].save(
        DOCS / name,
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=0,
        optimize=True,
    )
    print(f"generated docs/{name} ({len(frames)} frames)")


def main() -> None:
    for name, sequence in DEMOS.items():
        build(name, sequence)


if __name__ == "__main__":
    main()
