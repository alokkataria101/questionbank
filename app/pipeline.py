"""Engine selection: python | watsonx | hybrid. All return the same Reconstruction."""
from __future__ import annotations

from . import diagram as dg
from .processing import image as im
from .providers.base import ProviderUnavailable, Reconstruction
from .providers.python_only import NoAIProvider
from .providers.watsonx import WatsonxProvider

MODES = ("python", "watsonx", "hybrid")
_python = NoAIProvider()
_watsonx = WatsonxProvider()


def engine_status() -> dict:
    p_ok, p_why = _python.available()
    w_ok, w_why = _watsonx.available()
    return {"python": {"available": p_ok, "reason": p_why},
            "watsonx": {"available": w_ok, "reason": w_why}}


def prepare(data: bytes, regions_override: list[dict] | None = None):
    """Decode, clean and split an upload. Returns (original_png, gray, regions, figure_png, ocr_png)."""
    img = im.load_image(data)
    gray = im.preprocess(img)
    regions = regions_override if regions_override is not None else im.find_regions(gray)
    figure = next((r for r in regions if r["kind"] == "figure"), None)
    figure_png = im.to_png(im.crop(gray, figure)) if figure else None
    ocr_png = im.to_png(im.text_only(gray, regions))
    return im.to_png(img), gray, regions, figure_png, ocr_png


def reconstruct(data: bytes, mode: str, subject: str = "Physics",
                regions_override: list[dict] | None = None, allow_ai: bool = True):
    """Returns (Reconstruction, figure_png). Never raises for a missing engine in
    python/hybrid mode; a watsonx-only request that cannot run raises ProviderUnavailable."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    original_png, _gray, regions, figure_png, ocr_png = prepare(data, regions_override)
    page = original_png if data[:5] == b"%PDF-" else data
    args = (page, figure_png, ocr_png, regions, subject)

    use_ai = allow_ai and mode in ("watsonx", "hybrid")
    if mode == "watsonx" and not allow_ai:
        raise ProviderUnavailable("External AI is disabled for this school")

    result: Reconstruction | None = None
    fallback_note = ""
    if use_ai:
        try:
            result = _watsonx.reconstruct(*args)
        except ProviderUnavailable as exc:
            if mode == "watsonx":
                raise
            fallback_note = f"watsonx unavailable ({exc}); used Python-only processing."
    if result is None:
        try:
            result = _python.reconstruct(*args)
        except ProviderUnavailable as exc:
            result = Reconstruction(
                text="", diagram=dg.empty_diagram(), engine="python", regions=regions,
                warnings=[f"Automatic text recognition unavailable: {exc}. Type the question manually."])
    if fallback_note:
        result.warnings.insert(0, fallback_note)
    return result, figure_png, original_png
