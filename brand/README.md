# brand/

Drop your brand assets here. Nothing else in the repo hardcodes colors, fonts, or
logos — `brand/brand.json` is the single source of truth that the PDF renderer reads.

## What to drop in

```
brand/
  logo/          # SVG preferred (scales cleanly in PDF). PNG at 2x+ if no SVG.
                 # Include variants if you have them: primary, reversed/white,
                 # icon-only/mark, and horizontal vs stacked lockups.
  guidelines/    # Brand guidelines PDF, Word doc, or Figma export
  fonts/         # .ttf/.otf/.woff2 for your brand typefaces (see licensing note)
  brand.json     # Generated — do not hand-edit once the renderer depends on it
```

## How it gets used

1. You drop assets in the folders above.
2. Claude reads the guidelines and writes `brand.json` — hex codes, type scale,
   logo clear-space and minimum sizes, allowed logo variants per background.
3. `tools/render_report.py` reads `brand.json` + the HTML/CSS template. It never
   guesses a color; anything not in `brand.json` is a bug in `brand.json`.

## Font licensing

Embedding a font in a PDF requires an embedding-permitted license. If your brand
faces are licensed web-only (common with Adobe Fonts and some foundries), the PDF
falls back to the nearest system face. Flag it and we'll pick a licensed
substitute rather than shipping a report that silently renders wrong.
