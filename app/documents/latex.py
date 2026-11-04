"""Small LaTeX-to-runs converter used by the PDF and DOCX writers, plus display-math images.

Runs are (text, kind) with kind in {"", "sub", "sup", "i"}; this covers school-level
notation (indices, Greek, fractions, roots, chemical formulas). Equations that need
real layout (stacked fractions, matrices) are written as $$...$$ and rendered as images.
"""
from __future__ import annotations

import io
import re

SYMBOLS = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε", "varepsilon": "ε",
    "zeta": "ζ", "eta": "η", "theta": "θ", "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ",
    "pi": "π", "rho": "ρ", "sigma": "σ", "tau": "τ", "phi": "φ", "varphi": "φ", "chi": "χ",
    "psi": "ψ", "omega": "ω", "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ",
    "Pi": "Π", "Sigma": "Σ", "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω",
    "times": "×", "cdot": "·", "div": "÷", "pm": "±", "mp": "∓", "leq": "≤", "le": "≤",
    "geq": "≥", "ge": "≥", "neq": "≠", "ne": "≠", "approx": "≈", "sim": "∼", "equiv": "≡",
    "propto": "∝", "infty": "∞", "partial": "∂", "nabla": "∇", "int": "∫", "sum": "∑",
    "prod": "∏", "degree": "°", "circ": "°", "rightarrow": "→", "to": "→", "leftarrow": "←",
    "Rightarrow": "⇒", "Leftrightarrow": "⇔", "leftrightarrow": "↔", "rightleftharpoons": "⇌",
    "uparrow": "↑", "downarrow": "↓", "angle": "∠", "perp": "⊥", "parallel": "∥",
    "therefore": "∴", "because": "∵", "in": "∈", "subset": "⊂", "cup": "∪", "cap": "∩",
    "ldots": "…", "cdots": "⋯", "hbar": "ħ", "ell": "ℓ", "AA": "Å", "quad": " ", "qquad": "  ",
    ",": " ", ";": " ", " ": " ", "!": "", "left": "", "right": "", "mathrm": "", "text": "",
    "sin": "sin", "cos": "cos", "tan": "tan", "cot": "cot", "sec": "sec", "csc": "csc",
    "log": "log", "ln": "ln", "exp": "exp", "lim": "lim", "min": "min", "max": "max",
}
_INLINE_OR_DISPLAY = re.compile(r"(\$\$.+?\$\$|\$.+?\$)", re.S)


def split_math(text: str) -> list[tuple[str, str]]:
    """[(kind, content)] with kind in text|inline|display."""
    out: list[tuple[str, str]] = []
    for part in _INLINE_OR_DISPLAY.split(text):
        if not part:
            continue
        if part.startswith("$$") and part.endswith("$$") and len(part) > 4:
            out.append(("display", part[2:-2].strip()))
        elif part.startswith("$") and part.endswith("$") and len(part) > 2:
            out.append(("inline", part[1:-1].strip()))
        else:
            out.append(("text", part))
    return out


def _group(s: str, i: int) -> tuple[str, int]:
    """Read a {group} or single char starting at s[i]; return (content, next index)."""
    if i >= len(s):
        return "", i
    if s[i] == "{":
        depth, j = 1, i + 1
        while j < len(s) and depth:
            depth += (s[j] == "{") - (s[j] == "}")
            j += 1
        return s[i + 1:j - 1], j
    if s[i] == "\\":
        m = re.match(r"\\([A-Za-z]+|.)", s[i:])
        return m.group(0), i + m.end()
    return s[i], i + 1


def tex_runs(tex: str, kind: str = "") -> list[tuple[str, str]]:
    runs: list[tuple[str, str]] = []

    def add(t: str, k: str) -> None:
        if t:
            runs.append((t, k))

    i = 0
    while i < len(tex):
        c = tex[i]
        if c == "\\":
            m = re.match(r"\\([A-Za-z]+|.)", tex[i:])
            name = m.group(1)
            i += m.end()
            if name == "frac":
                a, i = _group(tex, i)
                b, i = _group(tex, i)
                add("(", kind); runs += tex_runs(a, kind); add(")/(", kind)
                runs += tex_runs(b, kind); add(")", kind)
            elif name == "sqrt":
                a, i = _group(tex, i)
                add("√(", kind); runs += tex_runs(a, kind); add(")", kind)
            elif name in ("mathrm", "text", "mathbf", "textbf", "operatorname", "mathit", "vec", "hat", "bar", "overline"):
                a, i = _group(tex, i)
                runs += tex_runs(a, kind)
            elif name in SYMBOLS:
                add(SYMBOLS[name], kind)
            else:
                add(name if name.isalpha() else name, kind)
        elif c in "^_":
            arg, i = _group(tex, i + 1)
            runs += tex_runs(arg, "sup" if c == "^" else "sub")
        elif c in "{}":
            i += 1
        else:
            add(c, kind)
            i += 1
    # merge neighbours of the same kind
    merged: list[tuple[str, str]] = []
    for t, k in runs:
        if merged and merged[-1][1] == k:
            merged[-1] = (merged[-1][0] + t, k)
        else:
            merged.append((t, k))
    return merged


def text_runs(text: str) -> list[tuple[str, str]]:
    """Plain text with inline $math$ -> runs (display math is handled by the caller)."""
    runs: list[tuple[str, str]] = []
    for kind, content in split_math(text):
        if kind == "text":
            runs.append((content, ""))
        else:
            runs += [(t, "i" if k == "" else k) for t, k in tex_runs(content)]
    return runs


def runs_to_markup(runs: list[tuple[str, str]]) -> str:
    """ReportLab Paragraph markup."""
    from xml.sax.saxutils import escape
    out = []
    for t, k in runs:
        t = escape(t)
        out.append({"sub": f"<sub>{t}</sub>", "sup": f"<super>{t}</super>", "i": f"<i>{t}</i>"}.get(k, t))
    return "".join(out)


def render_display(tex: str, fontsize: int = 15) -> bytes | None:
    """Render display math to PNG with matplotlib mathtext; None if it cannot parse."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig = plt.figure(figsize=(0.01, 0.01), dpi=200)
    try:
        fig.text(0, 0, f"${tex}$", fontsize=fontsize)
        buf = io.BytesIO()
        fig.savefig(buf, format="png", bbox_inches="tight", pad_inches=0.05, transparent=False,
                    facecolor="white")
        return buf.getvalue()
    except Exception:
        return None
    finally:
        plt.close(fig)
