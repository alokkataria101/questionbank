"""Deterministic image handling (no AI): preprocessing and text/figure separation."""
from __future__ import annotations

import io

import cv2
import numpy as np
from PIL import Image


def load_image(data: bytes) -> np.ndarray:
    """Decode bytes (image or first page of a PDF) into a BGR array."""
    if data[:5] == b"%PDF-":
        import fitz  # PyMuPDF

        with fitz.open(stream=data, filetype="pdf") as doc:
            pix = doc[0].get_pixmap(dpi=200)
            data = pix.tobytes("png")
    arr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if arr is None:
        raise ValueError("could not decode image")
    return arr


def _deskew(gray: np.ndarray) -> np.ndarray:
    inv = cv2.bitwise_not(gray)
    _, bw = cv2.threshold(inv, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    pts = cv2.findNonZero(bw)
    if pts is None or len(pts) < 50:
        return gray
    angle = cv2.minAreaRect(pts)[-1]
    angle = angle - 90 if angle > 45 else angle
    if abs(angle) < 0.3 or abs(angle) > 15:  # ignore noise and implausible tilts
        return gray
    h, w = gray.shape
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(gray, m, (w, h), flags=cv2.INTER_CUBIC, borderValue=255)


def preprocess(img: np.ndarray) -> np.ndarray:
    """Grayscale, denoise, deskew, and flatten uneven lighting. Returns uint8 gray."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    if max(gray.shape) < 1400:  # small phone crops OCR badly
        gray = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    gray = cv2.fastNlMeansDenoising(gray, h=10)
    bg = cv2.medianBlur(gray, 51)
    gray = cv2.divide(gray, bg, scale=255)  # remove shadows
    return _deskew(gray)


def to_png(img: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        raise ValueError("png encode failed")
    return buf.tobytes()


def find_regions(gray: np.ndarray) -> list[dict]:
    """Split the page into 'text' and 'figure' boxes.

    Text lines are short, wide, low-density components once dilated horizontally;
    figures are large blocks that are tall relative to a text line or have long strokes.
    Boxes are returned as {kind, x, y, w, h}; the teacher can correct them in the UI.
    """
    h, w = gray.shape
    bw = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 31, 15)
    # Long straight strokes (axes, planes, wires) are a figure signature.
    lines = cv2.HoughLinesP(bw, 1, np.pi / 180, threshold=80, minLineLength=max(40, w // 12), maxLineGap=5)
    line_mask = np.zeros_like(bw)
    # OpenCV 4 returns (N,1,4); OpenCV 5 returns (N,4).
    for ln in (np.asarray(lines).reshape(-1, 4) if lines is not None else []):
        x1, y1, x2, y2 = (int(v) for v in ln)
        cv2.line(line_mask, (x1, y1), (x2, y2), 255, 9)
    blocks = cv2.dilate(bw, cv2.getStructuringElement(cv2.MORPH_RECT, (max(25, w // 40), 9)))
    n, _, stats, _ = cv2.connectedComponentsWithStats(blocks)
    regions = []
    for i in range(1, n):
        x, y, bw_, bh, area = stats[i]
        if bw_ < 20 or bh < 12 or area < 200:
            continue
        has_lines = int(np.count_nonzero(line_mask[y:y + bh, x:x + bw_])) > 0.02 * bw_ * bh
        tall = bh > 0.12 * h and bw_ > 0.1 * w
        regions.append({"kind": "figure" if (has_lines and tall) or bh > 0.25 * h else "text",
                        "x": int(x), "y": int(y), "w": int(bw_), "h": int(bh)})
    return sorted(regions, key=lambda r: (r["y"], r["x"]))


def crop(gray: np.ndarray, r: dict, pad: int = 8) -> np.ndarray:
    h, w = gray.shape
    return gray[max(0, r["y"] - pad):min(h, r["y"] + r["h"] + pad),
                max(0, r["x"] - pad):min(w, r["x"] + r["w"] + pad)]


def text_only(gray: np.ndarray, regions: list[dict]) -> np.ndarray:
    """Whiten out figure regions so OCR does not read diagram strokes as characters."""
    out = gray.copy()
    for r in regions:
        if r["kind"] == "figure":
            out[r["y"]:r["y"] + r["h"], r["x"]:r["x"] + r["w"]] = 255
    return out


def pil(img: np.ndarray) -> Image.Image:
    return Image.open(io.BytesIO(to_png(img)))
