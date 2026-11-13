#!/usr/bin/env python3
"""The diagrams of Issue No. 8, the map of the podcast pipeline, as inline SVG.

Every figure is drawn in the page's own colour tokens (var(--ink), var(--paper-2),
var(--signal), ...), so it follows light and dark mode with the text around it,
and in the page's mono face. The map, the chains and the morning are laid out
twice, wide for a desk and tall for a phone, and the page shows one by a media
query (public/assets/diagrams.css); the two schematics are drawn once and scaled. The
brand marks are Simple Icons (CC0; the marks belong to their owners), reduced to
their path data below.

    python3 tools/diagrams.py public/issues/08-pipeline.html

replaces each `<!-- fig:NAME -->...<!-- /fig:NAME -->` block in the page with the
figure of that name, and says which it replaced.
"""

import html
import pathlib
import re
import sys
import textwrap

# Simple Icons, CC0 (simpleicons.org), each a 24-unit path; the marks belong to their owners.
ICONS = {
    "bluesky": "M5.202 2.857C7.954 4.922 10.913 9.11 12 11.358c1.087-2.247 4.046-6.436 6.798-8.501C20.783 1.366 24 .213 24 3.883c0 .732-.42 6.156-.667 7.037-.856 3.061-3.978 3.842-6.755 3.37 4.854.826 6.089 3.562 3.422 6.299-5.065 5.196-7.28-1.304-7.847-2.97-.104-.305-.152-.448-.153-.327 0-.121-.05.022-.153.327-.568 1.666-2.782 8.166-7.847 2.97-2.667-2.737-1.432-5.473 3.422-6.3-2.777.473-5.899-.308-6.755-3.369C.42 10.04 0 4.615 0 3.883c0-3.67 3.217-2.517 5.202-1.026",
    "cloudflare": "M16.5088 16.8447c.1475-.5068.0908-.9707-.1553-1.3154-.2246-.3164-.6045-.499-1.0615-.5205l-8.6592-.1123a.1559.1559 0 0 1-.1333-.0713c-.0283-.042-.0351-.0986-.021-.1553.0278-.084.1123-.1484.2036-.1562l8.7359-.1123c1.0351-.0489 2.1601-.8868 2.5537-1.9136l.499-1.3013c.0215-.0561.0293-.1128.0147-.168-.5625-2.5463-2.835-4.4453-5.5499-4.4453-2.5039 0-4.6284 1.6177-5.3876 3.8614-.4927-.3658-1.1187-.5625-1.794-.499-1.2026.119-2.1665 1.083-2.2861 2.2856-.0283.31-.0069.6128.0635.894C1.5683 13.171 0 14.7754 0 16.752c0 .1748.0142.3515.0352.5273.0141.083.0844.1475.1689.1475h15.9814c.0909 0 .1758-.0645.2032-.1553l.12-.4268zm2.7568-5.5634c-.0771 0-.1611 0-.2383.0112-.0566 0-.1054.0415-.127.0976l-.3378 1.1744c-.1475.5068-.0918.9707.1543 1.3164.2256.3164.6055.498 1.0625.5195l1.8437.1133c.0557 0 .1055.0263.1329.0703.0283.043.0351.1074.0214.1562-.0283.084-.1132.1485-.204.1553l-1.921.1123c-1.041.0488-2.1582.8867-2.5527 1.914l-.1406.3585c-.0283.0713.0215.1416.0986.1416h6.5977c.0771 0 .1474-.0489.169-.126.1122-.4082.1757-.837.1757-1.2803 0-2.6025-2.125-4.727-4.7344-4.727",
    "docker": "M13.983 11.078h2.119a.186.186 0 00.186-.185V9.006a.186.186 0 00-.186-.186h-2.119a.185.185 0 00-.185.185v1.888c0 .102.083.185.185.185m-2.954-5.43h2.118a.186.186 0 00.186-.186V3.574a.186.186 0 00-.186-.185h-2.118a.185.185 0 00-.185.185v1.888c0 .102.082.185.185.185m0 2.716h2.118a.187.187 0 00.186-.186V6.29a.186.186 0 00-.186-.185h-2.118a.185.185 0 00-.185.185v1.887c0 .102.082.185.185.186m-2.93 0h2.12a.186.186 0 00.184-.186V6.29a.185.185 0 00-.185-.185H8.1a.185.185 0 00-.185.185v1.887c0 .102.083.185.185.186m-2.964 0h2.119a.186.186 0 00.185-.186V6.29a.185.185 0 00-.185-.185H5.136a.186.186 0 00-.186.185v1.887c0 .102.084.185.186.186m5.893 2.715h2.118a.186.186 0 00.186-.185V9.006a.186.186 0 00-.186-.186h-2.118a.185.185 0 00-.185.185v1.888c0 .102.082.185.185.185m-2.93 0h2.12a.185.185 0 00.184-.185V9.006a.185.185 0 00-.184-.186h-2.12a.185.185 0 00-.184.185v1.888c0 .102.083.185.185.185m-2.964 0h2.119a.185.185 0 00.185-.185V9.006a.185.185 0 00-.184-.186h-2.12a.186.186 0 00-.186.186v1.887c0 .102.084.185.186.185m-2.92 0h2.12a.185.185 0 00.184-.185V9.006a.185.185 0 00-.184-.186h-2.12a.185.185 0 00-.184.185v1.888c0 .102.082.185.185.185M23.763 9.89c-.065-.051-.672-.51-1.954-.51-.338.001-.676.03-1.01.087-.248-1.7-1.653-2.53-1.716-2.566l-.344-.199-.226.327c-.284.438-.49.922-.612 1.43-.23.97-.09 1.882.403 2.661-.595.332-1.55.413-1.744.42H.751a.751.751 0 00-.75.748 11.376 11.376 0 00.692 4.062c.545 1.428 1.355 2.48 2.41 3.124 1.18.723 3.1 1.137 5.275 1.137.983.003 1.963-.086 2.93-.266a12.248 12.248 0 003.823-1.389c.98-.567 1.86-1.288 2.61-2.136 1.252-1.418 1.998-2.997 2.553-4.4h.221c1.372 0 2.215-.549 2.68-1.009.309-.293.55-.65.707-1.046l.098-.288Z",
    "instagram": "M7.0301.084c-1.2768.0602-2.1487.264-2.911.5634-.7888.3075-1.4575.72-2.1228 1.3877-.6652.6677-1.075 1.3368-1.3802 2.127-.2954.7638-.4956 1.6365-.552 2.914-.0564 1.2775-.0689 1.6882-.0626 4.947.0062 3.2586.0206 3.6671.0825 4.9473.061 1.2765.264 2.1482.5635 2.9107.308.7889.72 1.4573 1.388 2.1228.6679.6655 1.3365 1.0743 2.1285 1.38.7632.295 1.6361.4961 2.9134.552 1.2773.056 1.6884.069 4.9462.0627 3.2578-.0062 3.668-.0207 4.9478-.0814 1.28-.0607 2.147-.2652 2.9098-.5633.7889-.3086 1.4578-.72 2.1228-1.3881.665-.6682 1.0745-1.3378 1.3795-2.1284.2957-.7632.4966-1.636.552-2.9124.056-1.2809.0692-1.6898.063-4.948-.0063-3.2583-.021-3.6668-.0817-4.9465-.0607-1.2797-.264-2.1487-.5633-2.9117-.3084-.7889-.72-1.4568-1.3876-2.1228C21.2982 1.33 20.628.9208 19.8378.6165 19.074.321 18.2017.1197 16.9244.0645 15.6471.0093 15.236-.005 11.977.0014 8.718.0076 8.31.0215 7.0301.0839m.1402 21.6932c-1.17-.0509-1.8053-.2453-2.2287-.408-.5606-.216-.96-.4771-1.3819-.895-.422-.4178-.6811-.8186-.9-1.378-.1644-.4234-.3624-1.058-.4171-2.228-.0595-1.2645-.072-1.6442-.079-4.848-.007-3.2037.0053-3.583.0607-4.848.05-1.169.2456-1.805.408-2.2282.216-.5613.4762-.96.895-1.3816.4188-.4217.8184-.6814 1.3783-.9003.423-.1651 1.0575-.3614 2.227-.4171 1.2655-.06 1.6447-.072 4.848-.079 3.2033-.007 3.5835.005 4.8495.0608 1.169.0508 1.8053.2445 2.228.408.5608.216.96.4754 1.3816.895.4217.4194.6816.8176.9005 1.3787.1653.4217.3617 1.056.4169 2.2263.0602 1.2655.0739 1.645.0796 4.848.0058 3.203-.0055 3.5834-.061 4.848-.051 1.17-.245 1.8055-.408 2.2294-.216.5604-.4763.96-.8954 1.3814-.419.4215-.8181.6811-1.3783.9-.4224.1649-1.0577.3617-2.2262.4174-1.2656.0595-1.6448.072-4.8493.079-3.2045.007-3.5825-.006-4.848-.0608M16.953 5.5864A1.44 1.44 0 1 0 18.39 4.144a1.44 1.44 0 0 0-1.437 1.4424M5.8385 12.012c.0067 3.4032 2.7706 6.1557 6.173 6.1493 3.4026-.0065 6.157-2.7701 6.1506-6.1733-.0065-3.4032-2.771-6.1565-6.174-6.1498-3.403.0067-6.156 2.771-6.1496 6.1738M8 12.0077a4 4 0 1 1 4.008 3.9921A3.9996 3.9996 0 0 1 8 12.0077",
    "mastodon": "M23.268 5.313c-.35-2.578-2.617-4.61-5.304-5.004C17.51.242 15.792 0 11.813 0h-.03c-3.98 0-4.835.242-5.288.309C3.882.692 1.496 2.518.917 5.127.64 6.412.61 7.837.661 9.143c.074 1.874.088 3.745.26 5.611.118 1.24.325 2.47.62 3.68.55 2.237 2.777 4.098 4.96 4.857 2.336.792 4.849.923 7.256.38.265-.061.527-.132.786-.213.585-.184 1.27-.39 1.774-.753a.057.057 0 0 0 .023-.043v-1.809a.052.052 0 0 0-.02-.041.053.053 0 0 0-.046-.01 20.282 20.282 0 0 1-4.709.545c-2.73 0-3.463-1.284-3.674-1.818a5.593 5.593 0 0 1-.319-1.433.053.053 0 0 1 .066-.054c1.517.363 3.072.546 4.632.546.376 0 .75 0 1.125-.01 1.57-.044 3.224-.124 4.768-.422.038-.008.077-.015.11-.024 2.435-.464 4.753-1.92 4.989-5.604.008-.145.03-1.52.03-1.67.002-.512.167-3.63-.024-5.545zm-3.748 9.195h-2.561V8.29c0-1.309-.55-1.976-1.67-1.976-1.23 0-1.846.79-1.846 2.35v3.403h-2.546V8.663c0-1.56-.617-2.35-1.848-2.35-1.112 0-1.668.668-1.67 1.977v6.218H4.822V8.102c0-1.31.337-2.35 1.011-3.12.696-.77 1.608-1.164 2.74-1.164 1.311 0 2.302.5 2.962 1.498l.638 1.06.638-1.06c.66-.999 1.65-1.498 2.96-1.498 1.13 0 2.043.395 2.74 1.164.675.77 1.012 1.81 1.012 3.12z",
    "nvidia": "M8.948 8.798v-1.43a6.7 6.7 0 0 1 .424-.018c3.922-.124 6.493 3.374 6.493 3.374s-2.774 3.851-5.75 3.851c-.398 0-.787-.062-1.158-.185v-4.346c1.528.185 1.837.857 2.747 2.385l2.04-1.714s-1.492-1.952-4-1.952a6.016 6.016 0 0 0-.796.035m0-4.735v2.138l.424-.027c5.45-.185 9.01 4.47 9.01 4.47s-4.08 4.964-8.33 4.964c-.37 0-.733-.035-1.095-.097v1.325c.3.035.61.062.91.062 3.957 0 6.82-2.023 9.593-4.408.459.371 2.34 1.263 2.73 1.652-2.633 2.208-8.772 3.984-12.253 3.984-.335 0-.653-.018-.971-.053v1.864H24V4.063zm0 10.326v1.131c-3.657-.654-4.673-4.46-4.673-4.46s1.758-1.944 4.673-2.262v1.237H8.94c-1.528-.186-2.73 1.245-2.73 1.245s.68 2.412 2.739 3.11M2.456 10.9s2.164-3.197 6.5-3.533V6.201C4.153 6.59 0 10.653 0 10.653s2.35 6.802 8.948 7.42v-1.237c-4.84-.6-6.492-5.936-6.492-5.936z",
    "threads": "M12.186 24h-.007c-3.581-.024-6.334-1.205-8.184-3.509C2.35 18.44 1.5 15.586 1.472 12.01v-.017c.03-3.579.879-6.43 2.525-8.482C5.845 1.205 8.6.024 12.18 0h.014c2.746.02 5.043.725 6.826 2.098 1.677 1.29 2.858 3.13 3.509 5.467l-2.04.569c-1.104-3.96-3.898-5.984-8.304-6.015-2.91.022-5.11.936-6.54 2.717C4.307 6.504 3.616 8.914 3.589 12c.027 3.086.718 5.496 2.057 7.164 1.43 1.783 3.631 2.698 6.54 2.717 2.623-.02 4.358-.631 5.8-2.045 1.647-1.613 1.618-3.593 1.09-4.798-.31-.71-.873-1.3-1.634-1.75-.192 1.352-.622 2.446-1.284 3.272-.886 1.102-2.14 1.704-3.73 1.79-1.202.065-2.361-.218-3.259-.801-1.063-.689-1.685-1.74-1.752-2.964-.065-1.19.408-2.285 1.33-3.082.88-.76 2.119-1.207 3.583-1.291a13.853 13.853 0 0 1 3.02.142c-.126-.742-.375-1.332-.75-1.757-.513-.586-1.308-.883-2.359-.89h-.029c-.844 0-1.992.232-2.721 1.32L7.734 7.847c.98-1.454 2.568-2.256 4.478-2.256h.044c3.194.02 5.097 1.975 5.287 5.388.108.046.216.094.321.142 1.49.7 2.58 1.761 3.154 3.07.797 1.82.871 4.79-1.548 7.158-1.85 1.81-4.094 2.628-7.277 2.65Zm1.003-11.69c-.242 0-.487.007-.739.021-1.836.103-2.98.946-2.916 2.143.067 1.256 1.452 1.839 2.784 1.767 1.224-.065 2.818-.543 3.086-3.71a10.5 10.5 0 0 0-2.215-.221z",
    "x": "M14.234 10.162 22.977 0h-2.072l-7.591 8.824L7.251 0H.258l9.168 13.343L.258 24H2.33l8.016-9.318L16.749 24h6.993zm-2.837 3.299-.929-1.329L3.076 1.56h3.182l5.965 8.532.929 1.329 7.754 11.09h-3.182z",
    "youtube": "M23.498 6.186a3.016 3.016 0 0 0-2.122-2.136C19.505 3.545 12 3.545 12 3.545s-7.505 0-9.377.505A3.017 3.017 0 0 0 .502 6.186C0 8.07 0 12 0 12s0 3.93.502 5.814a3.016 3.016 0 0 0 2.122 2.136c1.871.505 9.376.505 9.376.505s7.505 0 9.377-.505a3.015 3.015 0 0 0 2.122-2.136C24 15.93 24 12 24 12s0-3.93-.502-5.814zM9.545 15.568V8.432L15.818 12l-6.273 3.568z",
}

EM = 0.61  # a mono character's width in em: DejaVu and Menlo are 0.60, with slack
TITLE, SUB, LH = 13, 11.5, 13  # the title size, the sub size and its line height
PAD = 10


def esc(s):
    return html.escape(s, quote=True)


def wrap(s, width_px, size=SUB):
    """A string into lines that fit width_px at `size`, by the mono em."""
    if isinstance(s, (list, tuple)):
        return list(s)
    n = max(8, int(width_px / (size * EM)))
    return textwrap.wrap(s, n) or []


class Canvas:
    """An SVG under construction: elements in order, a viewBox, a title for readers."""

    count = 0

    def __init__(self, w, h, title, cls=""):
        self.w, self.h, self.title, self.cls = w, h, title, cls
        self.parts = []
        Canvas.count += 1
        self.mid = f"dh{Canvas.count}"  # one arrowhead per drawing: url(#id) is looked up across the whole page

    def rect(self, x, y, w, h, cls, rx=2):
        self.parts.append(f'<rect x="{x:g}" y="{y:g}" width="{w:g}" height="{h:g}" rx="{rx}" class="{cls}"/>')

    def text(self, x, y, s, cls, anchor="start"):
        a = "" if anchor == "start" else f' text-anchor="{anchor}"'
        self.parts.append(f'<text x="{x:g}" y="{y:g}" class="{cls}"{a}>{esc(s)}</text>')

    def lines(self, x, y, lines, cls, lh=LH, anchor="start"):
        for i, s in enumerate(lines):
            self.text(x, y + i * lh, s, cls, anchor)

    def path(self, d, cls, head=False):
        m = f' marker-end="url(#{self.mid})"' if head else ""
        self.parts.append(f'<path d="{d}" class="{cls}"{m}/>')

    def line(self, x1, y1, x2, y2, cls, head=False):
        self.path(f"M{x1:g} {y1:g}L{x2:g} {y2:g}", cls, head)

    def icon(self, slug, x, y, size, cls="d-icon"):
        k = size / 24
        self.parts.append(f'<path transform="translate({x:g} {y:g}) scale({k:.4f})" d="{ICONS[slug]}" class="{cls}"/>')

    def render(self, orient):
        tid = f"t-{abs(hash(self.title)) % 10**8}-{orient}"
        return (
            f'<svg class="{orient} {self.cls}" viewBox="0 0 {self.w:g} {self.h:g}" role="img" '
            f'aria-labelledby="{tid}" xmlns="http://www.w3.org/2000/svg">'
            f'<title id="{tid}">{esc(self.title)}</title>'
            f'<defs><marker id="{self.mid}" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" '
            'orient="auto-start-reverse"><path d="M0 0.5L7.5 4L0 7.5z" class="d-head"/></marker></defs>'
            + "".join(self.parts)
            + "</svg>"
        )


# ─── Nodes and bands ─────────────────────────────────────────────────────────


class Node:
    def __init__(self, id, title, sub="", kind="auto", icon=None, chip=None, icons=()):
        self.id, self.title, self.sub, self.kind, self.icon, self.chip, self.icons = (
            id,
            title,
            sub,
            kind,
            icon,
            chip,
            list(icons),
        )
        self.x = self.y = self.w = self.h = 0
        self.lines = []
        self.band = None

    @property
    def cx(self):
        return self.x + self.w / 2

    @property
    def cy(self):
        return self.y + self.h / 2

    def fit(self, w):
        """Wrap the sub to a width and return the height the node wants."""
        self.w = w
        self.lines = wrap(self.sub, w - 2 * PAD)
        h = PAD + TITLE + PAD
        if self.lines:
            h += 4 + len(self.lines) * LH
        if self.icons:
            h += 18
        return h


class Band:
    def __init__(self, title, note, nodes, fan=False):
        self.title, self.note, self.nodes, self.fan = title, note, nodes, fan
        self.x = self.y = self.w = self.h = 0
        for n in nodes:
            n.band = self


def draw_node(c, n):
    cls = {"auto": "d-box", "hand": "d-box d-hand", "store": "d-box d-store", "future": "d-box d-future"}[n.kind]
    c.rect(n.x, n.y, n.w, n.h, cls, rx=3)
    tx = n.x + PAD
    ty = n.y + PAD + TITLE - 2
    if n.icon:
        c.icon(n.icon, n.x + PAD, n.y + PAD - 1, 14)
        tx += 20
    c.text(tx, ty, n.title, "d-title d-title-hand" if n.kind == "hand" else "d-title")
    if n.chip:
        c.text(n.x + n.w - PAD, ty, n.chip, "d-chip d-chip-gpu" if n.chip == "GPU" else "d-chip", anchor="end")
    c.lines(n.x + PAD, ty + 4 + LH, n.lines, "d-sub")
    if n.icons:
        xi = n.x + PAD
        for s in n.icons:
            c.icon(s, xi, n.y + n.h - PAD - 13, 12)
            xi += 20


def flow(bands, cross, width, cols, title, gap=14, row_gap=18, band_gap=20, margin=10, frame=True):
    """Bands top to bottom, each a titled frame with its nodes in rows of `cols`, arrows in reading order.

    A band with `fan` set has no arrows between its nodes: a bus from the band before feeds each one.
    """
    y = margin
    for b in bands:
        b.x, b.y, b.w = margin, y, width - 2 * margin
        inner = b.w - 2 * PAD
        ncols = min(cols, len(b.nodes))
        nw = (inner - (ncols - 1) * gap) / ncols
        head = 0
        if frame:
            notes = wrap(b.note, inner - len(b.title) * 11 * EM - 30, 10.5) if cols > 2 else wrap(b.note, inner, 10.5)
            b.notes = notes
            head = 20 if (cols > 2 and len(notes) <= 1) else 20 + len(notes) * 12
        if b.fan:
            head += 14
        rows = [b.nodes[i : i + ncols] for i in range(0, len(b.nodes), ncols)]
        ry = b.y + head + PAD
        for row in rows:
            hs = [n.fit(nw) for n in row]
            rh = max(hs) if cols > 1 else hs[0]
            for k, n in enumerate(row):
                n.x, n.y, n.h = b.x + PAD + k * (nw + gap), ry, rh if cols > 1 else n.fit(nw)
            ry += rh + row_gap
        b.h = ry - row_gap + PAD - b.y
        y += b.h + band_gap
    c = Canvas(width, y - band_gap + margin, title, "d-flow")
    for b in bands:
        if frame:
            c.rect(b.x, b.y, b.w, b.h, "d-band", rx=4)
            c.text(b.x + PAD, b.y + 14, b.title, "d-band-title")
            if b.notes:
                if cols > 2 and len(b.notes) <= 1:
                    c.text(b.x + b.w - PAD, b.y + 14, b.notes[0] if b.notes else "", "d-band-note", anchor="end")
                else:
                    c.lines(b.x + PAD, b.y + 14 + 12, b.notes, "d-band-note", 12)
        for n in b.nodes:
            draw_node(c, n)
        if b.fan:
            continue
        for a, z in zip(b.nodes, b.nodes[1:]):
            if abs(a.y - z.y) < 1:
                c.line(a.x + a.w, a.cy, z.x - 1, z.cy, "d-arrow", head=True)
            else:  # the carriage return: down, back, down
                gy = a.y + a.h + row_gap / 2
                c.path(f"M{a.cx:g} {a.y + a.h:g}V{gy:g}H{z.cx:g}V{z.y - 1:g}", "d-arrow", head=True)
    # between bands: the last node to the next band's first, or to the next band's bus
    for b, nb in zip(bands, bands[1:]):
        a = b.nodes[-1]
        gy = b.y + b.h + band_gap / 2
        if nb.fan:
            ncols = min(cols, len(nb.nodes))
            rows = [nb.nodes[i : i + ncols] for i in range(0, len(nb.nodes), ncols)]
            tx = nb.x + PAD / 2
            tops = [row[0].y - 10 for row in rows]
            c.path(f"M{a.cx:g} {a.y + a.h:g}V{gy:g}H{tx:g}V{tops[-1]:g}", "d-arrow")
            for row, by in zip(rows, tops):
                c.line(tx, by, row[-1].cx, by, "d-arrow")
                for n in row:
                    c.line(n.cx, by, n.cx, n.y - 1, "d-arrow", head=True)
            continue
        z = nb.nodes[0]
        if abs(a.cx - z.cx) < 1:
            c.line(a.cx, a.y + a.h, z.cx, z.y - 1, "d-arrow", head=True)
        else:
            c.path(f"M{a.cx:g} {a.y + a.h:g}V{gy:g}H{z.cx:g}V{z.y - 1:g}", "d-arrow", head=True)
    byid = {n.id: n for b in bands for n in b.nodes}
    for src, dst, label, dx in cross:
        a, z = byid[src], byid[dst]
        if z.y > a.y:
            gy = a.band.y + a.band.h + band_gap / 2
            d = (
                f"M{a.cx + dx:g} {a.y + a.h:g}V{gy:g}H{z.cx + dx:g}V{z.y - 1:g}"
                if abs(a.cx - z.cx) > 1
                else f"M{a.cx:g} {a.y + a.h:g}V{z.y - 1:g}"
            )
            lx, ly = (a.cx + z.cx) / 2 + dx, gy - 3
        else:
            gy = a.band.y - band_gap / 2
            d = (
                f"M{a.cx + dx:g} {a.y:g}V{gy:g}H{z.cx + dx:g}V{z.y + z.h + 1:g}"
                if abs(a.cx - z.cx) > 1
                else f"M{a.cx:g} {a.y:g}V{z.y + z.h + 1:g}"
            )
            lx, ly = (a.cx + z.cx) / 2 + dx, gy - 3
        c.path(d, "d-arrow d-cross", head=True)
        if label:
            c.text(lx + 6, ly, label, "d-edge-label")
    return c


def chain(nodes, title, orient, width):
    """A line of steps: left to right on a desk, top to bottom on a phone."""
    band = Band("", "", nodes)
    if orient == "wide":
        return flow([band], [], width, len(nodes), title, gap=20, margin=10, frame=False)
    return flow([band], [], width, 1, title, row_gap=22, margin=10, frame=False)


# ─── Figure 1: the map ───────────────────────────────────────────────────────


def fig_map(orient):
    wide = orient == "wide"
    N = Node
    studio = Band(
        "The studio",
        "Riverside, their cloud",
        [
            N("record", "Record", "each person recorded locally; 1080p set by hand before the button"),
            N(
                "download",
                "Download",
                "two clicks a person: the Aligned video and the Raw audio, to a folder",
                kind="hand",
            ),
        ],
    )
    words = Band(
        "The workstation, the words",
        "Hove: 32 threads, a 2080 Ti, Ubuntu; three pinned virtual environments",
        [
            N("ingest", "ingest", "each WAV placed inside its own video's audio by correlation; manifest.json"),
            N(
                "fillers",
                "fillers, erm",
                "Whisper asked for a verbatim transcript: every um its own token; seeded",
                chip="GPU",
            ),
            N(
                "align",
                "align",
                "wav2vec2 finds where each word is; ghosts dropped, skipped replies refilled",
                chip="GPU",
            ),
            N(
                "judge",
                "read the transcript",
                "chapters quoted, clips chosen, notes written: a session with the model",
                kind="hand",
            ),
            N("cut", "cut", "edl.json: fillers, stammers, pauses, each edge on a 2 ms envelope; six variants"),
        ],
    )
    picture = Band(
        "The workstation, the picture and the masters",
        "NVENC on the card; x264 the slow fallback",
        [
            N("cards", "cards", "title, chapter and end cards from the site's CSS, through Chrome"),
            N(
                "assemble",
                "assemble",
                "gate by words, match, -16 LUFS; the mp3 with chapters, the mp4, captions, qc.json",
            ),
            N("clips", "clips", "vertical, 4:5, captions lit word by word; a poster each, PNG and JPEG"),
            N("release", "release", "notes, chapters in three shapes, VTT, art, a hash per file, the day plan"),
            N(
                "approve",
                "approve",
                "the number minted; each guest's approval checked; refuses on a warning",
                kind="hand",
            ),
            N("stage", "stage", "masters to the archive, public files to the media host, by hash"),
        ],
    )
    cf = Band(
        "Cloudflare",
        "R2 in western Europe, two Workers, KV, Pages",
        [
            N(
                "media",
                "the media host",
                "media.agentsee.work: the mp3, the clips, the art, transcripts, chapters",
                kind="store",
                icon="cloudflare",
            ),
            N("mediaworker", "media Worker", "one row per request; a listen by the IAB's rule, no cookie"),
            N("links", "short links", "agentsee.work/p/<id>: a KV lookup, a row, a 302 to the moment"),
            N("site", "the site", "feed.xml for Apple and Spotify, the episode page; written by site.py on the day"),
            N(
                "archive",
                "the archive",
                "private: the tracks, the edit as data, every deliverable, the ledger nightly",
                kind="store",
                icon="cloudflare",
            ),
        ],
    )
    box = Band(
        "The box",
        "Infomaniak, 2 vCPU, 4 GB; two containers behind a Tunnel; deployed by a green tick",
        [
            N("eingest", "ingest", "release.json and staged.json from the archive into the ledger"),
            N("plan", "plan", "one post per asset per account; a short link minted for each"),
            N(
                "snapshot",
                "snapshot, 03:50",
                "the ledger copied to the archive; a rebuilt box restores from it",
                kind="store",
            ),
            N("collect", "collect, 05:30", "the edge's rows into listens and clicks; each platform's counts, dated"),
            N("publish", "publish", "a worker every minute: what is due, through one adapter per platform"),
        ],
    )
    platforms = Band(
        "The platforms",
        "the episode on its day, a clip a day after; Apple and Spotify read the feed",
        [
            N(
                "youtube",
                "YouTube",
                "06:00; uploaded private the day before, publishAt; captions, playlist",
                icon="youtube",
            ),
            N("x", "X", "09:00; the clip by chunked upload; a single-use refresh token", icon="x"),
            N("bluesky", "Bluesky", "09:00; the video service; links as facets; 300 graphemes", icon="bluesky"),
            N("mastodon", "Mastodon", "09:00; an idempotency key per attempt; 500 characters", icon="mastodon"),
            N("threads", "Threads", "09:00; Meta fetches the file; a sixty-day token, refreshed", icon="threads"),
            N("instagram", "Instagram", "09:00; a Reel with its poster; a system-user token", icon="instagram"),
            N("linkedin", "LinkedIn", "08:30; three cards; each post written by a person, by decision", kind="hand"),
        ],
        fan=True,
    )
    bands = [studio, words, picture, cf, box, platforms]
    cross = [("snapshot", "archive", "nightly", 0)]
    return flow(
        bands,
        cross,
        1152 if wide else 400,
        7 if wide else 2,
        "The map: from the studio to the platforms, by stage, and where each stage runs",
        gap=14 if wide else 12,
    )


# ─── Figure 2: the studio, and where a WAV belongs ───────────────────────────


def fig_studio(orient):
    w = 400
    c = Canvas(w, 10, "Two files a person, and the WAV placed by its own video's audio", "d-studio")
    x0, x1 = 112, w - 14
    secs = 60

    def sx(t):
        return x0 + (x1 - x0) * t / secs

    def bar(y, label, kind, start, pad_to=None, windows=False):
        c.text(x0 - 8, y + 12, label, "d-sub", anchor="end")
        if pad_to:
            c.rect(sx(0), y, sx(pad_to) - sx(0), 16, "d-pad", rx=2)
        c.rect(sx(start), y, sx(secs) - sx(start), 16, "d-track-video" if kind == "video" else "d-track-wav", rx=2)
        if windows:
            for t in (start + 8, start + 26, start + 44):
                c.rect(sx(t), y - 3, sx(min(t + 5, secs)) - sx(t), 22, "d-window", rx=1)

    ay = 22
    c.line(x0, ay, x1, ay, "d-axis")
    for t in (0, 20, 40, 60):
        c.line(sx(t), ay - 3, sx(t), ay + 3, "d-axis")
        c.text(sx(t), ay - 7, f"{t} s", "d-tiny", anchor="middle")
    c.text(x0 - 8, ay + 4, "one clock", "d-tiny", anchor="end")

    y = 52
    people = [
        (
            "Abrar",
            0,
            "found at +0.00 ms: the whole file at 4 kHz, then three one-minute windows at 16 kHz that have to agree",
        ),
        (
            "James",
            0,
            "found at +0.00 ms. The frame or two the old XML sometimes carried is inside the call's own delay",
        ),
        (
            "A guest who joined late",
            11,
            "the aligned video carries the wait, so the WAV lands where they spoke; a Raw video would start at 0 and lose it",
        ),
    ]
    for name, start, note in people:
        c.text(14, y, name, "d-title")
        bar(y + 8, "Aligned video", "video", start, pad_to=start or None)
        bar(y + 32, "Raw audio", "wav", start, windows=True)
        if start:
            c.text(sx(start / 2), y + 20, "the wait", "d-tiny", anchor="middle")
        lines = wrap(note, x1 - x0, 10)
        c.lines(x0, y + 64, lines, "d-tiny", 12)
        y += 64 + len(lines) * 12 + 18
    c.rect(14, y - 10, 12, 12, "d-track-video", rx=1)
    c.text(32, y, "video: constant frame rate, their own audio inside", "d-tiny")
    c.rect(14, y + 6, 12, 12, "d-track-wav", rx=1)
    c.text(32, y + 16, "WAV: recorded on their machine, 44.1 kHz, the master", "d-tiny")
    c.h = y + 28
    return c


# ─── Figure 3: the words ─────────────────────────────────────────────────────


def fig_words(orient):
    N = Node
    nodes = [
        N("wav", "Raw audio", "one WAV a person, 44.1 kHz, recorded on their own machine", kind="store"),
        N(
            "erm",
            "fillers.py, erm",
            "Whisper with a verbatim prompt: every um its own token, 68 against our 41; seeded, so three runs agree. 90 s for both tracks",
            chip="GPU",
        ),
        N(
            "align",
            "align.py",
            "wav2vec2, character CTC, one Viterbi pass over the whole track: where each word actually is. 20 s",
            chip="GPU",
        ),
        N(
            "ghosts",
            "ghosts, refills",
            "'Thank you.' written into silence dropped by its own doubt and level; a reply Whisper skipped transcribed again on its own",
            chip="CPU",
        ),
        N(
            "cut",
            "cut.py",
            "fillers, stammers, pauses over 0.7 s; each edge on the quietest moment of a 2 ms envelope; chapters by quoted words. 4 s",
            chip="CPU",
        ),
        N(
            "edl",
            "edl.json",
            "the edit as data: the stretches that survive, the cards placed; six variants to listen to",
            kind="store",
        ),
    ]
    return chain(
        nodes,
        "From the tracks to the edit: what each stage reads, writes and runs on",
        orient,
        1152 if orient == "wide" else 400,
    )


# ─── Figure 4: the masters ───────────────────────────────────────────────────


def fig_masters(orient):
    w = 400
    N = Node
    boxes = [
        N(
            "ws",
            "The workstation",
            "build/episodes/<slug>/: source/ holds the tracks, then transcript/, edl.json and out/ with every deliverable. Gitignored: the recipe is the repo, the episode is not. Until staging, the masters are here and nowhere else.",
            kind="store",
        ),
        N(
            "stage",
            "stage",
            "refuses files whose hash is not the one approved; every object carries its SHA-256, so a re-run puts only what changed. Episode 4: 77 objects, 8.6 GB, ninety minutes up a slow line.",
            kind="hand",
        ),
        N(
            "archive",
            "agentsee-podcast-archive",
            "private, R2 in western Europe: sources/<slug>/tracks/, the edit as data, out/, each review round, guests/<id>/, and the engine's ledger each night. Nothing serves it.",
            kind="store",
            icon="cloudflare",
        ),
        N(
            "media",
            "agentsee-media",
            "public, media.agentsee.work: the mp3, the clips, the art, transcripts and chapters. Nothing goes here that anyone may not download. The Worker in front counts.",
            kind="store",
            icon="cloudflare",
        ),
        N(
            "box",
            "The box: ledger.sqlite",
            "on the root disk, so a rebuild wipes it; copied to the archive at 03:50 and restored on a rebuilt box. The mail server beside it backs up nightly to a bucket of its own.",
            kind="store",
        ),
    ]
    chan = 16  # a channel each side: the public files go down the right one, the nightly copy up the left
    y = 12
    for n in boxes:
        n.h = n.fit(w - 2 * chan)
        n.x, n.y = chan, y
        y += n.h + 30
    foot = wrap(
        "The infrastructure's state sits in a fourth bucket, and each bucket has a token of its own.", w - 2 * chan, 10
    )
    c = Canvas(
        w,
        y - 30 + 20 + len(foot) * 12,
        "Where the bytes live: the workstation until staging, then the archive and the media host",
        "d-masters",
    )
    ws, st, ar, me, bx = boxes
    for n in boxes:
        draw_node(c, n)
    c.line(ws.cx, ws.y + ws.h, st.cx, st.y - 1, "d-arrow", head=True)
    c.line(st.x + 90, st.y + st.h, st.x + 90, ar.y - 1, "d-arrow", head=True)
    c.text(st.x + 96, ar.y - 10, "the masters", "d-edge-label")
    rx = w - chan / 2
    c.path(
        f"M{st.x + st.w - 40:g} {st.y + st.h:g}V{st.y + st.h + 10:g}H{rx:g}V{me.y - 10:g}H{me.x + me.w - 40:g}V{me.y - 1:g}",
        "d-arrow",
        head=True,
    )
    c.text(st.x + st.w - 46, st.y + st.h + 21, "the public files", "d-edge-label", anchor="end")
    lx = chan / 2
    c.path(
        f"M{bx.x + 40:g} {bx.y:g}V{bx.y - 12:g}H{lx:g}V{ar.y + ar.h + 10:g}H{ar.x + 40:g}V{ar.y + ar.h + 1:g}",
        "d-arrow d-cross",
        head=True,
    )
    c.text(bx.x + 46, bx.y - 16, "nightly", "d-edge-label")
    c.lines(chan, y - 30 + 22, foot, "d-tiny", 12)
    return c


# ─── Figure 5: the morning ───────────────────────────────────────────────────

MORNING = [
    (3 + 50 / 60, "03:50", "snapshot", "the ledger to the archive", None, "auto", ()),
    (
        5.5,
        "05:30",
        "collect",
        "the edge's rows into listens and clicks; every platform's counts, as dated rows",
        None,
        "auto",
        (),
    ),
    (
        6,
        "06:00",
        "YouTube, and the feed",
        "the episode and its clips go public, uploaded private the day before; the feed's date is 06:00, so Apple and Spotify follow",
        "youtube",
        "auto",
        (),
    ),
    (
        8.5,
        "08:30",
        "LinkedIn",
        "three cards: the short link and the file; the post written by a person",
        None,
        "hand",
        (),
    ),
    (
        9,
        "09:00",
        "the five at once",
        "the art and the episode's text, each in its platform's way; a clip a day after, same hours",
        None,
        "auto",
        ("x", "bluesky", "mastodon", "threads", "instagram"),
    ),
]


def fig_morning(orient):
    title = "Release day on the box: what runs when, London time"
    if orient == "wide":
        w = 1152
        c = Canvas(w, 10, title, "d-morning")
        x0, x1, ay = 30, w - 30, 30
        hx = lambda t: x0 + (x1 - x0) * (t - 3) / 7  # noqa: E731
        c.line(x0, ay, x1, ay, "d-axis")
        for t in range(3, 11):
            c.line(hx(t), ay - 4, hx(t), ay + 4, "d-axis")
            c.text(hx(t), ay - 10, f"{t:02d}:00", "d-tiny", anchor="middle")
        slots = [(40, 170), (225, 230), (470, 250), (735, 180), (930, 212)]
        nodes = []
        for (t, label, head, sub, icon, kind, icons), (sx, sw) in zip(MORNING, slots):
            n = Node(label, f"{label}  {head}", sub, kind=kind, icon=icon, icons=icons)
            n.h = n.fit(sw)
            n.x, n.y = sx, 92
            nodes.append((t, n))
        nh = max(n.h for _, n in nodes)
        for t, n in nodes:
            n.h = nh
            c.path(f"M{hx(t):g} {ay:g}V{ay + 24:g}H{n.cx:g}V{n.y - 1:g}", "d-arrow", head=True)
            draw_node(c, n)
        c.h = 92 + nh + 36
        c.text(
            x0,
            c.h - 12,
            "A post whose hour passed more than six hours ago waits for a person instead of going out in a bunch.",
            "d-sub",
        )
        return c
    w = 400
    c = Canvas(w, 10, title, "d-morning")
    ax = 20
    y = 14
    nodes = []
    for t, label, head, sub, icon, kind, icons in MORNING:
        n = Node(label, f"{label}  {head}", sub, kind=kind, icon=icon, icons=icons)
        n.h = n.fit(w - ax - 16 - 12)
        n.x, n.y = ax + 16, y
        nodes.append(n)
        y += n.h + 18
    c.line(ax, 14, ax, nodes[-1].cy, "d-axis")
    for n in nodes:
        c.line(ax, n.y + 14, n.x - 1, n.y + 14, "d-arrow", head=True)
        draw_node(c, n)
    c.lines(
        12,
        y + 4,
        wrap(
            "A post whose hour passed more than six hours ago waits for a person instead of going out in a bunch.",
            w - 24,
        ),
        "d-sub",
    )
    c.h = y + 4 + 2 * LH + 6
    return c


# ─── Figure 6: the plan ──────────────────────────────────────────────────────


def fig_plan(orient):
    N = Node
    nodes = [
        N(
            "engine",
            "the engine starts a run",
            "a Production screen: start, retry a stage, cancel, destroy; a deadline on every run and a monthly ceiling",
            kind="future",
        ),
        N(
            "gpu",
            "a GPU for the run",
            "Infomaniak, the box's own account: an L4 at about €0.29 an hour, destroyed on exit, never stopped",
            kind="future",
            icon="nvidia",
        ),
        N(
            "image",
            "one image",
            "CUDA, the three venvs, ffmpeg with NVENC, headless Chrome, the fonts and the model weights, pinned by digest",
            kind="future",
            icon="docker",
        ),
        N(
            "stages",
            "the stages, unchanged",
            "each reads the archive and writes back by hash, so a run resumes from the last complete stage",
            kind="future",
        ),
        N(
            "person",
            "a person, on a screen",
            "chapters, clips and notes proposed from the transcript; edited and approved before the render",
            kind="future",
        ),
    ]
    return chain(
        nodes,
        "The plan: the same stages on a rented GPU, marshalled by the engine",
        orient,
        1152 if orient == "wide" else 400,
    )


FIGS = {
    "map": fig_map,
    "studio": fig_studio,
    "words": fig_words,
    "masters": fig_masters,
    "morning": fig_morning,
    "plan": fig_plan,
}
ONE = {"studio", "masters"}


def figure(name):
    f = FIGS[name]
    if name in ONE:
        return f("one").render("one")
    return f("wide").render("wide") + "\n" + f("tall").render("tall")


def main():
    page = pathlib.Path(sys.argv[1])
    text = page.read_text()
    done = []
    for name in FIGS:
        pat = re.compile(rf"(<!-- fig:{name} -->)(.*?)(<!-- /fig:{name} -->)", re.S)
        if pat.search(text):
            text = pat.sub(lambda m, n=name: m.group(1) + "\n" + figure(n) + "\n" + m.group(3), text)
            done.append(name)
    page.write_text(text)
    print("figures:", ", ".join(done) or "none")


if __name__ == "__main__":
    main()
