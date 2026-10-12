"""Draw the Keyfit wordmark and favicon as SVG paths from the Sixtyfour font (SIL OFL 1.1).

    python3 site/scripts/make_logo_svgs.py

Needs fontTools (`pip install fonttools`) and `npm install` in site/ (for @fontsource/sixtyfour).
SVGs need outlines rather than text: favicons and <img> logos cannot load web fonts.
"""

from __future__ import annotations

from pathlib import Path

from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

SITE = Path(__file__).resolve().parents[1]
FONT = SITE / "node_modules/@fontsource/sixtyfour/files/sixtyfour-latin-400-normal.woff"
AMBER, INK, PAPER = "#f59e0b", "#111111", "#fafafa"
TRACKING = -0.02  # em, as on the logo options page


def outline(text: str, font: TTFont, size: float) -> tuple[str, tuple[float, float, float, float]]:
    """SVG path data for `text` at `size` px with the baseline at y=0, and its tight bounds (x0, y0, x1, y1)."""
    glyphs = font.getGlyphSet()
    cmap = font.getBestCmap()
    scale = size / font["head"].unitsPerEm
    pen = SVGPathPen(glyphs)
    x = 0.0
    bounds = [float("inf"), float("inf"), float("-inf"), float("-inf")]
    for i, char in enumerate(text):
        name = cmap[ord(char)]
        transform = (scale, 0, 0, -scale, x, 0)
        glyphs[name].draw(TransformPen(pen, transform))
        b = BoundsPen(glyphs)
        glyphs[name].draw(TransformPen(b, transform))
        if b.bounds:
            x0, y0, x1, y1 = b.bounds
            bounds = [min(bounds[0], x0), min(bounds[1], y0), max(bounds[2], x1), max(bounds[3], y1)]
        x += glyphs[name].width * scale + (TRACKING * size if i < len(text) - 1 else 0)
    return pen.getCommands(), tuple(bounds)


def wordmark(path_data: str, bounds, fill: str, pad: float = 2) -> str:
    x0, y0, x1, y1 = bounds
    w, h = x1 - x0 + 2 * pad, y1 - y0 + 2 * pad
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{x0 - pad:.2f} {y0 - pad:.2f} {w:.2f} {h:.2f}" '
            f'role="img" aria-label="Keyfit"><path fill="{fill}" d="{path_data}"/></svg>\n')


def tile(letter_data: str, bounds, background: str, fill: str, size: int = 32) -> str:
    x0, y0, x1, y1 = bounds
    dx, dy = (size - (x1 - x0)) / 2 - x0, (size - (y1 - y0)) / 2 - y0
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}">'
            f'<rect width="{size}" height="{size}" rx="{size * 0.22:.1f}" fill="{background}"/>'
            f'<path transform="translate({dx:.2f} {dy:.2f})" fill="{fill}" d="{letter_data}"/></svg>\n')


def main() -> None:
    font = TTFont(FONT)
    words, wbounds = outline("KEYFIT", font, 48)
    k, kbounds = outline("K", font, 19)
    out = {
        SITE / "src/assets/wordmark-on-dark.svg": wordmark(words, wbounds, PAPER),
        SITE / "src/assets/wordmark-on-light.svg": wordmark(words, wbounds, INK),
        SITE / "public/logo/keyfit-wordmark.svg": wordmark(words, wbounds, "currentColor"),
        SITE / "public/favicon.svg": tile(k, kbounds, AMBER, INK),
        SITE / "public/logo/keyfit-icon-dark.svg": tile(k, kbounds, INK, PAPER),
    }
    for path, svg in out.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(svg)
        print(f"wrote {path.relative_to(SITE)} ({len(svg)} bytes)")


if __name__ == "__main__":
    main()
