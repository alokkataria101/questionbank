"""Editable diagram model: structured objects -> SVG (editor/preview) and PNG (PDF/DOCX).

A diagram is never stored as a flat image. Each object keeps its own geometry,
so a teacher can move an arrow, edit a label or change an angle.

    {"diagram_type": "inclined_plane", "width": 400, "height": 300,
     "objects": [{"id": "plane", "type": "line", "x1": .., "y1": .., "x2": .., "y2": ..}, ...]}

Object types: line, arrow, rect, circle, polygon, arc, text.
"""
from __future__ import annotations

import io
import math
from html import escape

from PIL import Image, ImageDraw, ImageFont

OBJECT_TYPES = {"line", "arrow", "rect", "circle", "polygon", "arc", "text"}
_REQUIRED = {
    "line": ("x1", "y1", "x2", "y2"),
    "arrow": ("x1", "y1", "x2", "y2"),
    "rect": ("x", "y", "w", "h"),
    "circle": ("cx", "cy", "r"),
    "polygon": ("points",),
    "arc": ("cx", "cy", "r", "start", "end"),
    "text": ("x", "y", "value"),
}


def empty_diagram() -> dict:
    return {"diagram_type": "none", "width": 400, "height": 300, "objects": []}


_SUB = str.maketrans("0123456789+-=()", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎")
_SUP = str.maketrans("0123456789+-=()n", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿ")


def plain_label(s: str) -> str:
    """Diagram labels are drawn as plain text, so turn $\\theta_1$ into θ₁ (no raw LaTeX on the figure)."""
    from .documents.latex import tex_runs

    s = str(s).strip()
    if s.startswith("$") and s.endswith("$") and len(s) > 1:
        s = s.strip("$")
    elif "\\" not in s and "$" not in s:
        return s
    return "".join(t.translate(_SUB) if k == "sub" else t.translate(_SUP) if k == "sup" else t
                   for t, k in tex_runs(s.replace("$", "")))


def validate_diagram(d: dict) -> dict:
    """Return a cleaned diagram; raise ValueError on structurally invalid input."""
    if not isinstance(d, dict):
        raise ValueError("diagram must be an object")
    out = {
        "diagram_type": str(d.get("diagram_type", "custom")),
        "width": float(d.get("width", 400)),
        "height": float(d.get("height", 300)),
        "objects": [],
    }
    seen: set[str] = set()
    for i, o in enumerate(d.get("objects", [])):
        t = o.get("type")
        if t not in OBJECT_TYPES:
            raise ValueError(f"object {i}: unknown type {t!r}")
        missing = [k for k in _REQUIRED[t] if k not in o]
        if missing:
            raise ValueError(f"object {i} ({t}): missing {missing}")
        oid = str(o.get("id") or f"{t}{i + 1}")
        if oid.isdigit():                       # models sometimes number objects 1,2,3
            oid = f"{t}{oid}"
        if oid in seen:
            raise ValueError(f"duplicate object id {oid!r}")
        seen.add(oid)
        o = {**o, "id": oid, "editable": o.get("editable", True)}
        if t == "text":
            o["value"] = plain_label(o["value"])
        if o.get("label"):
            o["label"] = plain_label(o["label"])
        out["objects"].append(o)
    return out


# ── SVG ──────────────────────────────────────────────────────────────────────
def _arc_path(o: dict) -> str:
    cx, cy, r = o["cx"], o["cy"], o["r"]
    a0, a1 = math.radians(o["start"]), math.radians(o["end"])
    x0, y0 = cx + r * math.cos(a0), cy - r * math.sin(a0)
    x1, y1 = cx + r * math.cos(a1), cy - r * math.sin(a1)
    large = 1 if abs(o["end"] - o["start"]) % 360 > 180 else 0
    sweep = 0 if o["end"] > o["start"] else 1
    return f"M {x0:.1f} {y0:.1f} A {r} {r} 0 {large} {sweep} {x1:.1f} {y1:.1f}"


def to_svg(d: dict) -> str:
    w, h = d.get("width", 400), d.get("height", 300)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w:g} {h:g}" '
        f'width="{w:g}" height="{h:g}" font-family="serif">',
        '<defs><marker id="ah" markerWidth="10" markerHeight="8" refX="9" refY="4" '
        'orient="auto"><path d="M0,0 L10,4 L0,8 z" fill="#111"/></marker></defs>',
    ]
    for o in d.get("objects", []):
        oid, t = escape(str(o["id"])), o["type"]
        a = f'data-id="{oid}" data-type="{t}" stroke="#111" fill="none" stroke-width="{o.get("stroke", 2)}"'
        if t in ("line", "arrow"):
            m = ' marker-end="url(#ah)"' if t == "arrow" else ""
            parts.append(f'<line {a} x1="{o["x1"]}" y1="{o["y1"]}" x2="{o["x2"]}" y2="{o["y2"]}"{m}/>')
            if o.get("label"):
                lx, ly = o["x2"] + 6, o["y2"] - 6
                parts.append(f'<text data-id="{oid}-label" x="{lx}" y="{ly}" font-size="16" '
                             f'font-style="italic">{escape(str(o["label"]))}</text>')
        elif t == "rect":
            parts.append(f'<rect {a} x="{o["x"]}" y="{o["y"]}" width="{o["w"]}" height="{o["h"]}"'
                         + (f' transform="rotate({o["rotate"]} {o["x"] + o["w"] / 2} {o["y"] + o["h"] / 2})"'
                            if o.get("rotate") else "") + "/>")
        elif t == "circle":
            parts.append(f'<circle {a} cx="{o["cx"]}" cy="{o["cy"]}" r="{o["r"]}"/>')
        elif t == "polygon":
            pts = " ".join(f"{p[0]},{p[1]}" for p in o["points"])
            parts.append(f'<polygon {a} points="{pts}"/>')
        elif t == "arc":
            parts.append(f'<path {a} d="{_arc_path(o)}"/>')
        elif t == "text":
            parts.append(f'<text data-id="{oid}" data-type="text" x="{o["x"]}" y="{o["y"]}" '
                         f'font-size="{o.get("size", 16)}" font-style="italic">{escape(str(o["value"]))}</text>')
    parts.append("</svg>")
    return "\n".join(parts)


# ── PNG (for PDF / DOCX) ─────────────────────────────────────────────────────
def _font(size: int) -> ImageFont.ImageFont:
    for name in ("Times New Roman.ttf", "DejaVuSerif.ttf", "Arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def to_png(d: dict, scale: int = 2) -> bytes:
    w, h = int(d.get("width", 400)), int(d.get("height", 300))
    img = Image.new("RGB", (w * scale, h * scale), "white")
    g = ImageDraw.Draw(img)
    s = scale
    for o in d.get("objects", []):
        t, lw = o["type"], int(o.get("stroke", 2)) * s
        if t in ("line", "arrow"):
            x1, y1, x2, y2 = (o[k] * s for k in ("x1", "y1", "x2", "y2"))
            g.line([x1, y1, x2, y2], fill="black", width=lw)
            if t == "arrow":
                ang = math.atan2(y2 - y1, x2 - x1)
                for da in (2.6, -2.6):
                    g.line([x2, y2, x2 + 12 * s * math.cos(ang + da), y2 + 12 * s * math.sin(ang + da)],
                           fill="black", width=lw)
            if o.get("label"):
                g.text((x2 + 6 * s, y2 - 18 * s), str(o["label"]), fill="black", font=_font(16 * s))
        elif t == "rect":
            x, y, rw, rh = (o[k] * s for k in ("x", "y", "w", "h"))
            rot = math.radians(o.get("rotate", 0))
            cx, cy = x + rw / 2, y + rh / 2
            pts = [(x, y), (x + rw, y), (x + rw, y + rh), (x, y + rh)]
            pts = [(cx + (px - cx) * math.cos(rot) - (py - cy) * math.sin(rot),
                    cy + (px - cx) * math.sin(rot) + (py - cy) * math.cos(rot)) for px, py in pts]
            g.polygon(pts, outline="black", width=lw)
        elif t == "circle":
            cx, cy, r = o["cx"] * s, o["cy"] * s, o["r"] * s
            g.ellipse([cx - r, cy - r, cx + r, cy + r], outline="black", width=lw)
        elif t == "polygon":
            g.polygon([(p[0] * s, p[1] * s) for p in o["points"]], outline="black", width=lw)
        elif t == "arc":
            cx, cy, r = o["cx"] * s, o["cy"] * s, o["r"] * s
            # PIL angles run clockwise from +x; ours are counter-clockwise.
            g.arc([cx - r, cy - r, cx + r, cy + r], -o["end"], -o["start"], fill="black", width=lw)
        elif t == "text":
            g.text((o["x"] * s, (o["y"] - o.get("size", 16)) * s), str(o["value"]),
                   fill="black", font=_font(int(o.get("size", 16)) * s))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


# ── Templates (used by the Python-only engine; a teacher picks one and edits it) ──
def template(name: str) -> dict:
    if name == "inclined_plane":
        return validate_diagram({
            "diagram_type": "inclined_plane", "width": 400, "height": 300,
            "objects": [
                {"id": "plane", "type": "polygon", "points": [[40, 260], [360, 260], [360, 90]]},
                {"id": "block", "type": "rect", "x": 190, "y": 134, "w": 50, "h": 34, "rotate": -27.5},
                {"id": "gravity", "type": "arrow", "x1": 215, "y1": 151, "x2": 215, "y2": 235, "label": "mg"},
                {"id": "normal", "type": "arrow", "x1": 215, "y1": 151, "x2": 247, "y2": 92, "label": "N"},
                {"id": "friction", "type": "arrow", "x1": 215, "y1": 151, "x2": 165, "y2": 178, "label": "f"},
                {"id": "angle_arc", "type": "arc", "cx": 40, "cy": 260, "r": 40, "start": 0, "end": 27.5},
                {"id": "angle", "type": "text", "x": 85, "y": 252, "value": "θ"},
            ]})
    if name == "coordinate_axes":
        return validate_diagram({
            "diagram_type": "coordinate_axes", "width": 400, "height": 300,
            "objects": [
                {"id": "x_axis", "type": "arrow", "x1": 30, "y1": 150, "x2": 370, "y2": 150, "label": "x"},
                {"id": "y_axis", "type": "arrow", "x1": 200, "y1": 280, "x2": 200, "y2": 20, "label": "y"},
                {"id": "origin", "type": "text", "x": 182, "y": 168, "value": "O"},
            ]})
    if name == "circuit_battery_resistor":
        return validate_diagram({
            "diagram_type": "circuit", "width": 400, "height": 300,
            "objects": [
                {"id": "top", "type": "line", "x1": 80, "y1": 80, "x2": 320, "y2": 80},
                {"id": "right", "type": "line", "x1": 320, "y1": 80, "x2": 320, "y2": 220},
                {"id": "bottom", "type": "line", "x1": 320, "y1": 220, "x2": 80, "y2": 220},
                {"id": "left", "type": "line", "x1": 80, "y1": 220, "x2": 80, "y2": 80},
                {"id": "resistor", "type": "rect", "x": 170, "y": 68, "w": 60, "h": 24},
                {"id": "r_label", "type": "text", "x": 190, "y": 60, "value": "R"},
                {"id": "battery_plus", "type": "line", "x1": 160, "y1": 205, "x2": 160, "y2": 235, "stroke": 3},
                {"id": "battery_minus", "type": "line", "x1": 180, "y1": 212, "x2": 180, "y2": 228, "stroke": 3},
                {"id": "v_label", "type": "text", "x": 160, "y": 262, "value": "V"},
            ]})
    raise KeyError(name)


TEMPLATES = ["inclined_plane", "coordinate_axes", "circuit_battery_resistor"]
