"""IBM watsonx.ai provider: a vision model reads the handwriting and returns structured JSON."""
from __future__ import annotations

import base64
import json
import re

from .. import config
from .. import diagram as dg
from .base import AIProvider, ProviderUnavailable, Reconstruction

PROMPT = """You are digitising a handwritten {subject} exam question for a school question bank.
Read the image and reply with ONE JSON object and nothing else:
{{
  "text": "the full question in Markdown; ALL maths as LaTeX inside $...$ (e.g. $F=ma$, $\\\\theta$); chemical formulas like $\\\\mathrm{{H_2SO_4}}$; keep options (a)(b)(c)(d) on separate lines",
  "diagram": null OR {{
    "diagram_type": "short name",
    "width": 400, "height": 300,
    "objects": [ ... ]
  }},
  "suggested": {{"chapter": "", "topic": "", "difficulty": "Easy|Medium|Hard", "marks": 0, "question_type": ""}}
}}
Diagram objects use a 400x300 canvas, y grows downward. Allowed objects (each needs a unique "id"):
 {{"id","type":"line","x1","y1","x2","y2"}}
 {{"id","type":"arrow","x1","y1","x2","y2","label":"optional"}}
 {{"id","type":"rect","x","y","w","h","rotate":degrees optional}}
 {{"id","type":"circle","cx","cy","r"}}
 {{"id","type":"polygon","points":[[x,y],...]}}
 {{"id","type":"arc","cx","cy","r","start","end"}}  (degrees, counter-clockwise from +x)
 {{"id","type":"text","x","y","value","size":16}}
Redraw the figure faithfully with these objects, with every label as its own text object or arrow label.
Rules for the figure: give objects descriptive ids ("plane", "block", "gravity", "normal", "angle_arc"); draw every
force, velocity, field line or ray as an "arrow" (never a plain line) starting at its point of action; keep the
same orientation and proportions as the picture (a right angle at the bottom-right stays at the bottom-right);
draw a tilted body with "rotate" so it lies flat on its surface; put angle marks as "arc" plus a "text" label;
text values are plain Unicode (θ, α, ₁), not LaTeX.
Do NOT solve the question. If you cannot read a part, write [illegible] there. Use "diagram": null if there is no figure."""


def _extract_json(raw: str) -> dict:
    raw = raw.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", raw, re.S)
    if m:
        raw = m.group(1)
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("model returned no JSON")
    return json.loads(raw[start:end + 1])


class WatsonxProvider(AIProvider):
    name = "watsonx"

    def __init__(self) -> None:
        self._model = None

    def available(self) -> tuple[bool, str]:
        if not config.ALLOW_EXTERNAL_AI:
            return False, "External AI is disabled by configuration (QB_ALLOW_EXTERNAL_AI=false)"
        if not config.watsonx_configured():
            return False, "WATSONX_API_KEY / WATSONX_PROJECT_ID are not set"
        return True, ""

    def _client(self):
        if self._model is None:
            try:
                from ibm_watsonx_ai import Credentials
                from ibm_watsonx_ai.foundation_models import ModelInference
            except ImportError as exc:
                raise ProviderUnavailable("ibm-watsonx-ai SDK not installed") from exc
            self._model = ModelInference(
                model_id=config.WATSONX_VISION_MODEL,
                credentials=Credentials(url=config.WATSONX_URL, api_key=config.WATSONX_API_KEY),
                project_id=config.WATSONX_PROJECT_ID,
                params={"max_tokens": 3000, "temperature": 0.1},
            )
        return self._model

    def reconstruct(self, image, figure_png, ocr_png, regions, subject) -> Reconstruction:
        ok, why = self.available()
        if not ok:
            raise ProviderUnavailable(why)
        # Vision models take images; for PDFs fall back to the preprocessed page.
        page = ocr_png if image[:5] == b"%PDF-" else image
        b64 = base64.b64encode(page).decode()
        messages = [{"role": "user", "content": [
            {"type": "text", "text": PROMPT.format(subject=subject)},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
        ]}]
        try:
            resp = self._client().chat(messages=messages)
        except ProviderUnavailable:
            raise
        except Exception as exc:
            msg = str(exc)
            if "consumption_limit_reached" in msg:
                raise ProviderUnavailable("watsonx token quota for this plan/project is used up") from exc
            if "429" in msg or "Too Many Requests" in msg:
                raise ProviderUnavailable("watsonx rate limit reached; try again shortly") from exc
            raise ProviderUnavailable(f"watsonx call failed: {msg[:300]}") from exc

        content = resp["choices"][0]["message"]["content"]
        warnings: list[str] = []
        try:
            data = _extract_json(content)
        except (ValueError, json.JSONDecodeError):
            return Reconstruction(text=content.strip(), diagram=dg.empty_diagram(), engine=self.name,
                                  warnings=["watsonx reply was not valid JSON; shown as plain text."],
                                  regions=regions)
        try:
            d = dg.validate_diagram(data["diagram"]) if data.get("diagram") else dg.empty_diagram()
        except ValueError as exc:
            d = dg.empty_diagram()
            warnings.append(f"Diagram from watsonx was invalid ({exc}); redraw it in the editor.")
        if "[illegible]" in data.get("text", ""):
            warnings.append("Some handwriting was marked [illegible]; please complete it.")
        return Reconstruction(text=data.get("text", "").strip(), diagram=d, engine=self.name,
                              warnings=warnings, regions=regions, suggested=data.get("suggested") or {})
