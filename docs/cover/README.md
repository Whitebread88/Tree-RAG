# Cover infographic

A one-screen explainer of the retrieval pipeline, sized 1600×900 for use as a
website cover or social card.

| File | Use |
| --- | --- |
| `rag-cover-light.svg` | Primary asset. Self-contained: the Inter subset is embedded as a data URI, so it needs no webfont and no network request. |
| `rag-cover-dark.svg` | Same diagram on a dark ground. |
| `rag-cover-light.png` / `rag-cover-dark.png` | 3200×1800 raster fallback, for surfaces that will not take an SVG (Open Graph tags, most social previews). |

## Regenerating

`make_infographic.py` is the source of truth — the SVGs are build output, so edit
the script rather than the markup. Copy, wording and layout constants are all at
the top of the file.

```bash
python3 docs/cover/make_infographic.py docs/cover
```

To refresh the PNGs, open each SVG in a browser at a 1600×900 viewport and
screenshot at 2× device scale.

## Font

`inter-latin.woff2` is the Latin subset of Inter, used under the SIL Open Font
License 1.1. It is committed so the build stays reproducible offline.
