import io
import os

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("QB_STORAGE_DIR", str(tmp_path))
    monkeypatch.setenv("QB_DATABASE_URL", f"sqlite:///{tmp_path / 't.db'}")
    import importlib

    from app import config
    importlib.reload(config)
    from app import db
    importlib.reload(db)
    from app import api, main
    importlib.reload(api)
    importlib.reload(main)
    with TestClient(main.app) as c:
        yield c


def sample_png() -> bytes:
    img = Image.new("RGB", (900, 700), "white")
    d = ImageDraw.Draw(img)
    d.text((40, 40), "A body of mass m is placed on an inclined plane. Find the acceleration.", fill="black")
    d.polygon([(100, 600), (700, 600), (700, 300)], outline="black")
    d.line([(100, 600), (700, 600)], fill="black", width=3)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


T = {"X-Role": "teacher", "X-User": "t1"}


def upload(client, mode="python", **form):
    r = client.post("/api/questions", headers=T, data={"mode": mode, "subject": "Physics", **form},
                    files={"file": ("q.png", sample_png(), "image/png")})
    assert r.status_code == 201, r.text
    return r.json()["question"]


def test_python_mode_creates_draft_without_any_ai(client):
    q = upload(client, "python", class_name="XI", marks="5")
    assert q["status"] == "draft" and q["current_version"] == 1
    assert client.get(f"/api/questions/{q['id']}/source", headers=T).status_code == 200


def test_full_workflow_and_no_publish_without_approval(client):
    q = upload(client)
    qid = q["id"]
    assert client.put(f"/api/questions/{qid}/draft", headers=T,
                      json={"text": "A body of mass $m$ on a plane of angle $\\theta$. Find $a$.",
                            "chapter": "Laws of Motion", "topic": "Inclined Plane"}).status_code == 200
    # publish straight from draft is refused
    assert client.post(f"/api/questions/{qid}/publish", headers=T).status_code == 409
    assert client.post(f"/api/questions/{qid}/approve", headers=T).status_code == 409
    assert client.post(f"/api/questions/{qid}/submit", headers=T).json()["status"] == "under_review"
    # typists may not approve
    assert client.post(f"/api/questions/{qid}/approve", headers={"X-Role": "typist"}).status_code == 403
    assert client.post(f"/api/questions/{qid}/approve", headers=T).json()["status"] == "approved"
    # approved questions are locked
    assert client.put(f"/api/questions/{qid}/draft", headers=T, json={"text": "x"}).status_code == 409
    assert client.post(f"/api/questions/{qid}/publish", headers=T).json()["status"] == "published"


def test_revisions_kept_and_compared(client):
    q = upload(client)
    qid = q["id"]
    client.put(f"/api/questions/{qid}/draft", headers=T, json={"text": "first"})
    client.put(f"/api/questions/{qid}/draft", headers=T, json={"text": "second"})
    full = client.get(f"/api/questions/{qid}", headers=T).json()
    assert [v["number"] for v in full["versions"]] == [1, 2, 3]
    diff = client.get(f"/api/questions/{qid}/compare?a=2&b=3", headers=T).json()["text_diff"]
    assert any(line.startswith("-first") for line in diff) and any(line.startswith("+second") for line in diff)


def test_school_isolation(client):
    q = upload(client)
    other = {"X-Role": "teacher", "X-School-Id": "2"}
    assert client.get(f"/api/questions/{q['id']}", headers=other).status_code == 404


def test_diagram_validation_and_render(client):
    q = upload(client)
    bad = {"objects": [{"id": "a", "type": "banana"}]}
    assert client.put(f"/api/questions/{q['id']}/draft", headers=T, json={"diagram": bad}).status_code == 422
    from app import diagram as dg
    d = dg.template("inclined_plane")
    assert client.put(f"/api/questions/{q['id']}/draft", headers=T, json={"diagram": d}).status_code == 200
    svg = client.get(f"/api/questions/{q['id']}/diagram.svg", headers=T)
    assert svg.status_code == 200 and "<svg" in svg.text and 'data-id="gravity"' in svg.text
    assert dg.to_png(d)[:4] == b"\x89PNG"


def _publish(client, text, **meta):
    q = upload(client, marks=str(meta.pop("marks", 5)), difficulty=meta.pop("difficulty", "Medium"),
               class_name="XI")
    qid = q["id"]
    client.put(f"/api/questions/{qid}/draft", headers=T, json={"text": text, **meta})
    for a in ("submit", "approve", "publish"):
        assert client.post(f"/api/questions/{qid}/{a}", headers=T).status_code == 200
    return qid


def test_concept_search_and_exports(client):
    from app import diagram as dg
    a = _publish(client, "Newton's second law: block on inclined plane, find $a$.",
                 chapter="Laws of Motion", topic="Inclined Plane", marks=5,
                 diagram=dg.template("inclined_plane"))
    _publish(client, "Define the SI unit of charge.", chapter="Electrostatics", marks=1, difficulty="Easy")
    hits = client.get("/api/questions", headers=T, params={"q": "newton + inclined", "difficulty": "Medium",
                                                           "marks": 5}).json()
    assert [h["id"] for h in hits["items"]] == [a]
    # Markdown with a diagram is a zip: the .md plus the diagram as an SVG asset.
    for fmt, magic in (("pdf", b"%PDF"), ("docx", b"PK"), ("md", b"PK")):
        r = client.get(f"/api/questions/{a}/export", headers=T, params={"format": fmt})
        assert r.status_code == 200 and r.content[:len(magic)] == magic, fmt
    import zipfile
    names = zipfile.ZipFile(io.BytesIO(client.get(f"/api/questions/{a}/export", headers=T, params={"format": "md"}).content)).namelist()
    assert any(n.endswith(".md") for n in names) and "assets/q1.svg" in names


def test_exam_paper_builder(client):
    ids = [_publish(client, f"Q about motion number {i}", chapter="Laws of Motion", marks=m,
                    difficulty=d) for i, (m, d) in enumerate([(5, "Medium"), (3, "Easy"), (2, "Hard"), (5, "Medium")])]
    r = client.post("/api/papers/auto", headers=T, json={"title": "Unit Test", "class_name": "XI",
                                                         "subject": "Physics", "total_marks": 10, "seed": 1})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    chosen = r.json()["question_ids"]
    assert sum(int(client.get(f"/api/questions/{i}", headers=T).json()["marks"]) for i in chosen) == 10
    assert client.put(f"/api/papers/{pid}/order", headers=T, json=chosen[::-1]).status_code == 200
    pdf = client.get(f"/api/papers/{pid}/export", headers=T, params={"format": "pdf"})
    assert pdf.content[:4] == b"%PDF"
    too_big = client.post("/api/papers/auto", headers=T, json={"title": "x", "class_name": "XI",
                                                               "subject": "Physics", "total_marks": 500})
    assert too_big.status_code == 422
    assert client.post("/api/papers", headers=T, json={"title": "bad", "question_ids": [9999]}).status_code == 404


def test_school_can_disable_external_ai(client):
    assert client.put("/api/school", headers={"X-Role": "admin"}, json={"allow_external_ai": False}).status_code == 200
    r = client.post("/api/questions", headers=T, data={"mode": "watsonx"},
                    files={"file": ("q.png", sample_png(), "image/png")})
    assert r.status_code == 503 and "disabled" in r.json()["detail"].lower()
    # hybrid quietly stays on Python
    q = upload(client, "hybrid")
    assert q["versions"][0]["engine"] == "python"


def test_upload_validation(client):
    r = client.post("/api/questions", headers=T, files={"file": ("q.png", b"not an image", "image/png")})
    assert r.status_code == 422
    r = client.post("/api/questions", headers=T, data={"subject": "Astrology"},
                    files={"file": ("q.png", sample_png(), "image/png")})
    assert r.status_code == 422


def test_latex_runs():
    from app.documents import latex
    assert latex.tex_runs(r"\theta_1^2") == [("θ", ""), ("1", "sub"), ("2", "sup")]
    assert latex.tex_runs(r"\mathrm{H_2SO_4}") == [("H", ""), ("2", "sub"), ("SO", ""), ("4", "sub")]
    assert "√" in "".join(t for t, _ in latex.tex_runs(r"\sqrt{2gh}"))


@pytest.mark.skipif(not os.getenv("QB_LIVE_WATSONX"), reason="set QB_LIVE_WATSONX=1 to call watsonx")
def test_live_watsonx_reads_image(client):
    img = Image.new("RGB", (900, 300), "white")
    ImageDraw.Draw(img).text((30, 100), "Q. Find the force on a 2 kg mass accelerating at 3 m/s^2.", fill="black")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    r = client.post("/api/questions", headers=T, data={"mode": "watsonx"},
                    files={"file": ("q.png", buf.getvalue(), "image/png")})
    assert r.status_code == 201, r.text
    assert "force" in r.json()["question"]["text"].lower()
    assert r.json()["question"]["versions"][0]["engine"] == "watsonx"


@pytest.mark.skipif(__import__("shutil").which("tesseract") is None, reason="tesseract not installed")
def test_python_mode_reads_printed_text_and_separates_figure(client):
    from PIL import ImageFont
    font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 30) if os.path.exists(
        "/System/Library/Fonts/Supplemental/Arial.ttf") else None
    img = Image.new("RGB", (1400, 900), "white")
    d = ImageDraw.Draw(img)
    d.text((40, 40), "A block of mass m rests on an inclined plane. Find the acceleration.", fill="black", font=font)
    d.polygon([(200, 780), (1000, 780), (1000, 320)], outline="black", width=4)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    r = client.post("/api/questions", headers=T, data={"mode": "python"}, files={"file": ("q.png", buf.getvalue(), "image/png")})
    q = r.json()["question"]
    assert "inclined plane" in q["text"].lower()
    assert any(x["kind"] == "figure" for x in q["regions"])
    assert [o["id"] for o in q["diagram"]["objects"]][:2] == ["plane", "block"]
