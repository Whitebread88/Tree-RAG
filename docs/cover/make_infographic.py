#!/usr/bin/env python3
"""Generate the 'How the RAG chat works' cover infographic as a self-contained SVG.

The Inter subset is embedded as a data URI so the file renders identically
without a network request or a locally installed font.
"""
import base64
import html
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FONT = os.path.join(HERE, "inter-latin.woff2")  # variable font: one file covers 100-900

W, H = 1600, 900
M = 100                      # side margin
CONTENT = W - 2 * M          # 1400
COLS = 4
GAP = 44
COLW = (CONTENT - (COLS - 1) * GAP) / COLS      # 317
COL_X = [M + i * (COLW + GAP) for i in range(COLS)]

RAIL_END = 1460              # where each rail stops / turns

# --- vertical rhythm -------------------------------------------------------
EYEBROW_Y = 86
TITLE_Y = 142
SUB_Y = 180

A_LANE_Y = 256               # "INDEXING · once per document"
A_ICON_Y = 280               # icon box top
A_LABEL_Y = 330
A_D1_Y = 356
A_D2_Y = 376
A_RAIL_Y = 408

STORE_Y = 452
STORE_H = 80
STORE_B = STORE_Y + STORE_H  # 516

B_LANE_Y = 572
B_RAIL_Y = 604
B_ICON_Y = 624
B_LABEL_Y = 674
B_D1_Y = 700
B_D2_Y = 720

RULE_Y = 778
FOOT_Y = 808

THEMES = {
    "light": dict(
        paper="#FAF9F6", ink="#15181C", muted="#6E747D", faint="#9AA0A8",
        rule="#E3DFD6", accent="#2E6B52", tint="#EAF1ED", tintline="#CBDCD2",
    ),
    "dark": dict(
        paper="#111316", ink="#F2F1ED", muted="#9BA2AB", faint="#757C85",
        rule="#2A2E34", accent="#74C49B", tint="#182620", tintline="#2E4739",
    ),
}

STACK = "Inter, 'Inter var', -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"


def esc(s):
    return html.escape(s, quote=False)


# --- icons (drawn in a 20x20 box, stroke uses currentColor) ----------------
ICON_SCALE = 1.32            # 20px source box -> ~26px rendered


def icon(name, x, y, color, scale=ICON_SCALE):
    sw = 1.7 / scale         # keep the rendered stroke weight constant
    g = [f'<g transform="translate({x},{y}) scale({scale})" fill="none" stroke="{color}" '
         f'stroke-width="{sw:.3f}" stroke-linecap="round" stroke-linejoin="round">']
    if name == "upload":
        g.append('<path d="M10 3.6V13.6"/>')
        g.append('<path d="M6 7.6 10 3.6 14 7.6"/>')
        g.append('<path d="M3.6 16.6H16.4"/>')
    elif name == "extract":
        g.append('<rect x="4.2" y="2.6" width="11.6" height="14.8" rx="2.2"/>')
        g.append('<path d="M7.2 6.9H12.8"/><path d="M7.2 10.2H12.8"/><path d="M7.2 13.5H10.6"/>')
    elif name == "chunk":
        # filled, so the bars stay legible instead of collapsing into outlines
        g.append(f'<g fill="{color}" stroke="none">')
        g.append('<rect x="3" y="3.9" width="14" height="2.8" rx="1.4"/>')
        g.append('<rect x="3" y="8.6" width="8.6" height="2.8" rx="1.4"/>')
        g.append('<rect x="3" y="13.3" width="11.8" height="2.8" rx="1.4"/>')
        g.append('</g>')
    elif name == "vector":
        g.append(f'<g fill="{color}" stroke="none">')
        for cy in (5, 10, 15):
            for cx in (5, 10, 15):
                g.append(f'<circle cx="{cx}" cy="{cy}" r="1.5"/>')
        g.append('</g>')
    elif name == "ask":
        g.append('<rect x="2.6" y="3.4" width="14.8" height="11" rx="3.4"/>')
        g.append('<path d="M6.6 14.4V18.2L10.6 14.4"/>')
    elif name == "search":
        g.append('<circle cx="8.9" cy="8.9" r="5.3"/>')
        g.append('<path d="M12.9 12.9 17 17"/>')
    elif name == "answer":
        g.append('<rect x="2.6" y="3.4" width="14.8" height="11" rx="3.4"/>')
        g.append('<path d="M13.4 14.4V18.2L9.4 14.4"/>')
        g.append('<path d="M6.9 9 9.1 11.2 13.3 6.7"/>')
    g.append('</g>')
    return "".join(g)


LANE_A = [
    ("upload", "Upload", "PDFs, Word, slides, sheets and scans", "land in cloud storage."),
    ("extract", "Extract", "Layout-aware parsing, with OCR for", "pages that are only images."),
    ("chunk", "Chunk", "Split into ~1,000-character passages,", "never across a page or section."),
    ("vector", "Embed", "Tagged folder › file › heading › page,", "then turned into a 768-number vector."),
]

LANE_B = [
    ("ask", "Ask", "A follow-up is rewritten into a", "standalone question first."),
    ("vector", "Embed", "The question becomes a vector in", "that same 768-number space."),
    ("search", "Search", "The 50 nearest passages come back;", "weak and repeated ones are dropped."),
    ("answer", "Answer", "Written from those passages only,", "with the source files named."),
]


def lane(steps, icon_y, label_y, d1_y, d2_y, t, hot_index):
    """One row of four steps. hot_index gets the accent icon + filled rail dot."""
    out = []
    for i, (ic, label, d1, d2) in enumerate(steps):
        x = COL_X[i]
        col = t["accent"] if i == hot_index else t["ink"]
        out.append(icon(ic, x, icon_y, col))
        out.append(
            f'<text x="{x}" y="{label_y}" font-size="21" font-weight="600" '
            f'letter-spacing="-.2" fill="{t["ink"]}">{esc(label)}</text>'
        )
        for yy, line in ((d1_y, d1), (d2_y, d2)):
            out.append(
                f'<text x="{x}" y="{yy}" font-size="14.5" fill="{t["muted"]}">{esc(line)}</text>'
            )
    return "".join(out)


def rail(y, t, hot_index, arrow_end=True, end=RAIL_END):
    out = [f'<path d="M{M} {y}H{end}" stroke="{t["rule"]}" stroke-width="1.4" fill="none"/>']
    if arrow_end:
        out.append(
            f'<path d="M{end - 7} {y - 4.5} {end} {y} {end - 7} {y + 4.5}" '
            f'fill="none" stroke="{t["rule"]}" stroke-width="1.4" '
            f'stroke-linecap="round" stroke-linejoin="round"/>'
        )
    for i, x in enumerate(COL_X):
        if i == hot_index:
            out.append(f'<circle cx="{x}" cy="{y}" r="5" fill="{t["accent"]}"/>')
        else:
            out.append(
                f'<circle cx="{x}" cy="{y}" r="4.2" fill="{t["paper"]}" '
                f'stroke="{t["ink"]}" stroke-width="1.5"/>'
            )
    return "".join(out)


def lane_tag(y, word, tail, t):
    return (
        f'<text x="{M}" y="{y}" font-size="11.5" font-weight="600" letter-spacing="1.7" '
        f'fill="{t["accent"]}">{esc(word.upper())}'
        f'<tspan fill="{t["faint"]}" font-weight="500">   {esc(tail)}</tspan></text>'
    )


def build(theme_name):
    t = THEMES[theme_name]
    font_b64 = base64.b64encode(open(FONT, "rb").read()).decode()

    s = []
    s.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}" role="img" '
        f'aria-label="How the RAG chat works: documents are extracted, chunked and embedded '
        f'into a pgvector store; each question is embedded, matched against it, and answered '
        f'only from the passages that come back.">'
    )
    s.append(
        '<style>'
        '@font-face{font-family:"Inter";font-style:normal;font-weight:100 900;'
        f'src:url(data:font/woff2;base64,{font_b64}) format("woff2");}}'
        f'text{{font-family:{STACK};}}'
        '</style>'
    )
    s.append(f'<rect width="{W}" height="{H}" fill="{t["paper"]}"/>')

    # ---- header ----
    s.append(
        f'<text x="{M}" y="{EYEBROW_Y}" font-size="11.5" font-weight="600" letter-spacing="2.2" '
        f'fill="{t["accent"]}">RETRIEVAL-AUGMENTED CHAT</text>'
    )
    s.append(
        f'<text x="{M}" y="{TITLE_Y}" font-size="44" font-weight="650" letter-spacing="-1.1" '
        f'fill="{t["ink"]}">How the RAG chat works</text>'
    )
    s.append(
        f'<text x="{M}" y="{SUB_Y}" font-size="18" fill="{t["muted"]}">'
        f'Your files become searchable meaning, and every answer is built only from what the '
        f'search brings back.</text>'
    )

    # decorative motif, top right: text lines dissolving into vectors
    mx, my = 1234, 68
    s.append(f'<g opacity="{0.9 if theme_name == "light" else 0.85}">')
    for r, wdt in enumerate((84, 66, 76)):
        s.append(
            f'<rect x="{mx}" y="{my + r * 16 - 0.5}" width="{wdt}" height="4" rx="2" '
            f'fill="{t["ink"]}" opacity="0.16"/>'
        )
    for r in range(3):
        for c in range(9):
            o = 0.14 + 0.62 * (1 - c / 8.0)
            s.append(
                f'<circle cx="{mx + 108 + c * 17}" cy="{my + r * 16 + 1.5}" r="2.6" '
                f'fill="{t["accent"]}" opacity="{o:.2f}"/>'
            )
    s.append('</g>')

    # ---- lane A ----
    s.append(lane_tag(A_LANE_Y, "Indexing", "·   once per document", t))
    s.append(lane(LANE_A, A_ICON_Y, A_LABEL_Y, A_D1_Y, A_D2_Y, t, hot_index=3))
    s.append(rail(A_RAIL_Y, t, hot_index=3, arrow_end=False, end=RAIL_END - 42))

    # elbow: end of rail A turns down into the store
    s.append(
        f'<path d="M{RAIL_END - 44} {A_RAIL_Y}H{RAIL_END - 14}'
        f'a14 14 0 0 1 14 14V{STORE_Y - 12}" fill="none" stroke="{t["accent"]}" '
        f'stroke-width="1.6"/>'
    )
    s.append(
        f'<path d="M{RAIL_END - 5.5} {STORE_Y - 13} {RAIL_END} {STORE_Y - 5} '
        f'{RAIL_END + 5.5} {STORE_Y - 13}" fill="none" stroke="{t["accent"]}" '
        f'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>'
    )

    # ---- store band ----
    s.append(
        f'<rect x="{M}" y="{STORE_Y}" width="{CONTENT}" height="{STORE_H}" rx="12" '
        f'fill="{t["tint"]}" stroke="{t["tintline"]}" stroke-width="1.2"/>'
    )
    s.append(icon("vector", M + 30, STORE_Y + 31, t["accent"], scale=1.1))
    s.append(
        f'<text x="{M + 68}" y="{STORE_Y + 47}" font-size="21" font-weight="600" '
        f'letter-spacing="-.2" fill="{t["ink"]}">Vector store'
        f'<tspan dx="18" font-size="14.5" font-weight="400" letter-spacing="0" '
        f'fill="{t["muted"]}">Postgres + pgvector · one row per passage · '
        f'ranked by cosine distance</tspan></text>'
    )
    # vector motif inside the band, right edge
    for r in range(3):
        for c in range(10):
            o = 0.10 + 0.5 * (c / 9.0)
            s.append(
                f'<circle cx="{1180 + c * 30}" cy="{STORE_Y + 24 + r * 16}" r="2.5" '
                f'fill="{t["accent"]}" opacity="{o:.2f}"/>'
            )

    # store -> search connector
    sx = COL_X[2]
    s.append(
        f'<path d="M{sx} {STORE_B}V{B_RAIL_Y - 10}" fill="none" stroke="{t["accent"]}" '
        f'stroke-width="1.6"/>'
    )
    s.append(
        f'<path d="M{sx - 5.5} {B_RAIL_Y - 11} {sx} {B_RAIL_Y - 3} {sx + 5.5} {B_RAIL_Y - 11}" '
        f'fill="none" stroke="{t["accent"]}" stroke-width="1.6" '
        f'stroke-linecap="round" stroke-linejoin="round"/>'
    )

    # ---- lane B ----
    s.append(lane_tag(B_LANE_Y, "Answering", "·   every question", t))
    s.append(rail(B_RAIL_Y, t, hot_index=2, arrow_end=True))
    s.append(lane(LANE_B, B_ICON_Y, B_LABEL_Y, B_D1_Y, B_D2_Y, t, hot_index=2))

    # ---- footer ----
    s.append(f'<path d="M{M} {RULE_Y}H{W - M}" stroke="{t["rule"]}" stroke-width="1"/>')
    s.append(
        f'<text x="{M}" y="{FOOT_Y}" font-size="13" fill="{t["faint"]}">'
        f'768-dimension vectors · 50 candidates per query · 0.50 similarity floor · '
        f'answers never leave the retrieved text</text>'
    )
    s.append(
        f'<text x="{W - M}" y="{FOOT_Y}" font-size="13" text-anchor="end" fill="{t["faint"]}">'
        f'Docling · Gemini · pgvector · FastAPI on Cloud Run</text>'
    )

    s.append('</svg>')
    return "".join(s)


if __name__ == "__main__":
    out_dir = sys.argv[1] if len(sys.argv) > 1 else HERE
    os.makedirs(out_dir, exist_ok=True)
    for name in THEMES:
        path = os.path.join(out_dir, f"rag-cover-{name}.svg")
        with open(path, "w") as f:
            f.write(build(name))
        print(f"{path}  {os.path.getsize(path):,} bytes")
