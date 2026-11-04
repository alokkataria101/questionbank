"""Python-only engine: no network, no AI service. OCR via Tesseract when installed."""
from __future__ import annotations

import io
import re
import shutil

from .. import diagram as dg
from .base import AIProvider, ProviderUnavailable, Reconstruction

# Handwritten math is beyond Tesseract; common symbols it does emit are normalised
# so the teacher starts closer to correct text. Everything remains editable.
_FIXES = [(r"\s{2,}", " "), (r"\bTheta\b", "θ"), (r"(?<=\d)\s*x\s*(?=\d)", r"\\times "),
          (r"(\d)\s*\^\s*(\d)", r"\1^\2")]

_KEYWORDS = {
    "inclined_plane": ("inclined", "incline", "slope", "wedge"),
    "circuit_battery_resistor": ("circuit", "resistor", "battery", "ammeter", "emf"),
    "coordinate_axes": ("graph", "plot", "axis", "axes", "coordinate", "curve"),
}


def guess_template(text: str) -> str | None:
    low = text.lower()
    for name, words in _KEYWORDS.items():
        if any(w in low for w in words):
            return name
    return None


class NoAIProvider(AIProvider):
    name = "python"

    def available(self) -> tuple[bool, str]:
        if shutil.which("tesseract") is None:
            return False, "Tesseract binary not installed (brew install tesseract / apt install tesseract-ocr)"
        return True, ""

    def reconstruct(self, image, figure_png, ocr_png, regions, subject) -> Reconstruction:
        ok, why = self.available()
        if not ok:
            raise ProviderUnavailable(why)
        import pytesseract
        from PIL import Image

        raw = pytesseract.image_to_string(Image.open(io.BytesIO(ocr_png)), config="--psm 6").strip()
        for pat, rep in _FIXES:
            raw = re.sub(pat, rep, raw)
        text = "\n\n".join(p.strip() for p in re.split(r"\n\s*\n", raw) if p.strip())

        warnings = ["Python-only mode: handwriting and equations need teacher review."]
        d = dg.empty_diagram()
        if figure_png:
            tpl = guess_template(text)
            if tpl:
                d = dg.template(tpl)
                warnings.append(f"Diagram started from the '{tpl}' template; adjust it against the scan.")
            else:
                warnings.append("A figure was detected but no template matches; draw it in the diagram editor.")
        return Reconstruction(text=text, diagram=d, engine=self.name, warnings=warnings, regions=regions)
