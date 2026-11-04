"""AI provider abstraction. The rest of the app only sees `Reconstruction`.

    AIProvider
      +-- NoAIProvider        Python only: OpenCV + Tesseract, no network
      +-- WatsonxProvider     IBM watsonx.ai vision model
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class Reconstruction:
    text: str = ""                      # Markdown with $...$ LaTeX
    diagram: dict = field(default_factory=dict)
    engine: str = ""
    warnings: list[str] = field(default_factory=list)
    regions: list[dict] = field(default_factory=list)
    suggested: dict = field(default_factory=dict)  # chapter/topic/difficulty/marks hints


class ProviderUnavailable(Exception):
    """Provider cannot run (missing credentials, SDK, binary, quota...)."""


class AIProvider(ABC):
    name: str

    @abstractmethod
    def available(self) -> tuple[bool, str]:
        """(usable, reason-if-not)."""

    @abstractmethod
    def reconstruct(self, image: bytes, figure_png: bytes | None, ocr_png: bytes,
                    regions: list[dict], subject: str) -> Reconstruction:
        """image: original upload; figure_png: cropped figure if one was found;
        ocr_png: preprocessed page with figures blanked."""
