"""Render the integration's brand icons from the SVGs in artwork/.

Run from the repository root after changing artwork/*.svg:

    uv run --with cairosvg --with pillow python scripts/render_brand.py

Home Assistant 2026.3+ shows the PNGs in custom_components/dynamic_cost/brand/
(icon.png 256x256 and icon@2x.png 512x512, each with a dark_ variant for dark
themes). They must be square, transparent and trimmed to the shape, which the
SVGs' viewBox already is.
"""

from __future__ import annotations

import io
from pathlib import Path

import cairosvg
from PIL import Image

PROJECT = Path(__file__).resolve().parent.parent
ARTWORK = PROJECT / "artwork"
BRAND = PROJECT / "custom_components" / "dynamic_cost" / "brand"

# source SVG: (1x file, 2x file)
ICONS = {
    "icon.svg": ("icon.png", "icon@2x.png"),
    "dark_icon.svg": ("dark_icon.png", "dark_icon@2x.png"),
}


def render(svg: Path, size: int, target: Path) -> None:
    png = cairosvg.svg2png(url=str(svg), output_width=size, output_height=size)
    # Re-save through Pillow for a smaller, optimised file.
    Image.open(io.BytesIO(png)).save(target, optimize=True)
    print(f"{target.relative_to(PROJECT)}  {size}x{size}")
# end render


def main() -> None:
    BRAND.mkdir(exist_ok=True)
    for source, (normal, double) in ICONS.items():
        render(ARTWORK / source, 256, BRAND / normal)
        render(ARTWORK / source, 512, BRAND / double)
    # end for
# end main


if __name__ == "__main__":
    main()
# end if
