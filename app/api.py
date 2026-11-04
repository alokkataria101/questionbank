"""HTTP API. Identity comes from X-User / X-Role / X-School-Id headers (see README: placeholder
for a real login system). Every query is scoped to the caller's school."""
from __future__ import annotations

import difflib
import json
import random
import re
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from . import config, pipeline
from . import diagram as dg
from . import workflow as wf
from .db import Paper, Question, School, Version, get_session
from .documents import render
from .providers.base import ProviderUnavailable

router = APIRouter(prefix="/api")
ROLES = {"typist", "teacher", "admin"}
MAX_UPLOAD = 15 * 1024 * 1024


# ── identity ─────────────────────────────────────────────────────────────────
class Ctx(BaseModel):
    user: str
    role: str
    school_id: int


def ctx(x_user: str = Header("teacher1"), x_role: str = Header("teacher"),
        x_school_id: int = Header(1)) -> Ctx:
    if x_role not in ROLES:
        raise HTTPException(400, f"X-Role must be one of {sorted(ROLES)}")
    return Ctx(user=x_user, role=x_role, school_id=x_school_id)


def _school(s: Session, c: Ctx) -> School:
    sc = s.get(School, c.school_id)
    if not sc:
        raise HTTPException(404, "school not found")
    return sc


def _q(s: Session, c: Ctx, qid: int) -> Question:
    q = s.get(Question, qid)
    if not q or q.school_id != c.school_id:  # same 404 for "other school" - no leakage
        raise HTTPException(404, "question not found")
    return q


def _version(q: Question, n: int | None = None) -> Version:
    n = q.current_version if n is None else n
    for v in q.versions:
        if v.number == n:
            return v
    raise HTTPException(404, f"version {n} not found")


def _store(q: Question, name: str, data: bytes) -> str:
    d = config.STORAGE_DIR / "questions" / str(q.id)
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_bytes(data)
    return str((d / name).relative_to(config.STORAGE_DIR))


def _add_version(s: Session, q: Question, text: str, diagram: dict, engine: str, note: str, author: str) -> Version:
    q.current_version += 1
    v = Version(question_id=q.id, number=q.current_version, text=text, diagram=diagram,
                engine=engine, note=note, author=author)
    q.versions.append(v)
    return v


def _view(q: Question, full: bool = False) -> dict:
    v = _version(q) if q.versions else None
    out = {
        "id": q.id, "code": q.code, "subject": q.subject, "class_name": q.class_name, "chapter": q.chapter,
        "topic": q.topic, "concepts": q.concepts, "difficulty": q.difficulty, "marks": q.marks,
        "question_type": q.question_type, "status": q.status, "current_version": q.current_version,
        "approved_version": q.approved_version, "approved_by": q.approved_by,
        "has_source_image": bool(q.source_image), "has_figure": bool(q.figure_image),
        "text": v.text if v else "", "diagram": v.diagram if v else {},
        "equations": re.findall(r"\$\$?(.+?)\$\$?", v.text, re.S) if v else [],
    }
    if full:
        out["regions"] = q.regions
        out["versions"] = [{"number": x.number, "engine": x.engine, "note": x.note, "author": x.author,
                            "created_at": x.created_at.isoformat()} for x in q.versions]
    return out


# ── system ───────────────────────────────────────────────────────────────────
@router.get("/health")
def health():
    return {"ok": True}


@router.get("/engines")
def engines(c: Ctx = Depends(ctx), s: Session = Depends(get_session)):
    st = pipeline.engine_status()
    st["watsonx"]["school_allows"] = _school(s, c).allow_external_ai and config.ALLOW_EXTERNAL_AI
    return st


class SchoolIn(BaseModel):
    name: str | None = None
    address: str | None = None
    allow_external_ai: bool | None = None


@router.get("/school")
def get_school(c: Ctx = Depends(ctx), s: Session = Depends(get_session)):
    sc = _school(s, c)
    return {"id": sc.id, "name": sc.name, "address": sc.address, "allow_external_ai": sc.allow_external_ai,
            "has_logo": bool(sc.logo_path)}


@router.put("/school")
def put_school(body: SchoolIn, c: Ctx = Depends(ctx), s: Session = Depends(get_session)):
    if c.role != "admin":
        raise HTTPException(403, "admin only")
    sc = _school(s, c)
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(sc, k, v)
    s.commit()
    return get_school(c, s)


@router.post("/school/logo")
async def upload_logo(file: UploadFile = File(...), c: Ctx = Depends(ctx), s: Session = Depends(get_session)):
    if c.role != "admin":
        raise HTTPException(403, "admin only")
    data = await file.read()
    if len(data) > 2 * 1024 * 1024:
        raise HTTPException(413, "logo must be under 2 MB")
    d = config.STORAGE_DIR / "schools"
    d.mkdir(parents=True, exist_ok=True)
    sc = _school(s, c)
    (d / f"{sc.id}.png").write_bytes(data)
    sc.logo_path = f"schools/{sc.id}.png"
    s.commit()
    return {"ok": True}


# ── upload / processing ──────────────────────────────────────────────────────
def _process(s: Session, q: Question, data: bytes, mode: str, c: Ctx, note: str,
             regions_override: list[dict] | None = None) -> dict:
    sc = _school(s, c)
    q.status = "processing"
    try:
        res, figure_png, original_png = pipeline.reconstruct(
            data, mode, q.subject, regions_override, allow_ai=sc.allow_external_ai)
    except ProviderUnavailable as exc:
        q.status = "draft" if q.versions else "uploaded"
        s.commit()
        raise HTTPException(503, str(exc))
    except ValueError as exc:
        q.status = "draft" if q.versions else "uploaded"
        s.commit()
        raise HTTPException(422, str(exc))
    q.regions = res.regions
    q.figure_image = _store(q, "figure.png", figure_png) if figure_png else ""
    _add_version(s, q, res.text, res.diagram, res.engine, note, c.user)
    for k in ("chapter", "topic", "question_type"):
        if res.suggested.get(k) and not getattr(q, k):
            setattr(q, k, str(res.suggested[k])[:120])
    if res.suggested.get("difficulty") in ("Easy", "Medium", "Hard") and q.current_version == 1:
        q.difficulty = res.suggested["difficulty"]
    q.status = "draft"
    s.commit()
    return {"question": _view(q, True), "warnings": res.warnings}


@router.post("/questions", status_code=201)
async def create_question(
    file: UploadFile = File(...), mode: str = Form("hybrid"), subject: str = Form("Physics"),
    class_name: str = Form(""), chapter: str = Form(""), marks: int = Form(1),
    difficulty: str = Form("Medium"), c: Ctx = Depends(ctx), s: Session = Depends(get_session),
):
    data = await file.read(MAX_UPLOAD + 1)
    if not data:
        raise HTTPException(422, "empty file")
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "file larger than 15 MB")
    if subject not in ("Physics", "Chemistry", "Mathematics", "Biology"):
        raise HTTPException(422, "unsupported subject")
    if difficulty not in ("Easy", "Medium", "Hard"):
        raise HTTPException(422, "difficulty must be Easy, Medium or Hard")
    if mode not in pipeline.MODES:
        raise HTTPException(422, f"mode must be one of {pipeline.MODES}")
    q = Question(school_id=c.school_id, subject=subject, class_name=class_name, chapter=chapter,
                 marks=max(0, marks), difficulty=difficulty, created_by=c.user)
    s.add(q)
    s.flush()
    q.source_image = _store(q, "source" + (".pdf" if data[:5] == b"%PDF-" else ".img"), data)
    return _process(s, q, data, mode, c, f"Initial reconstruction ({mode})")


def _source_bytes(q: Question) -> bytes:
    p = config.STORAGE_DIR / q.source_image
    if not q.source_image or not p.is_file():
        raise HTTPException(404, "original scan not available")
    return p.read_bytes()


class ReprocessIn(BaseModel):
    mode: str = "hybrid"
    regions: list[dict] | None = None   # teacher-corrected text/figure boxes


@router.post("/questions/{qid}/reprocess")
def reprocess(qid: int, body: ReprocessIn, c: Ctx = Depends(ctx), s: Session = Depends(get_session)):
    q = _q(s, c, qid)
    if q.status not in wf.EDITABLE:
        raise HTTPException(409, f"cannot reprocess a question that is {q.status!r}")
    if body.mode not in pipeline.MODES:
        raise HTTPException(422, f"mode must be one of {pipeline.MODES}")
    for r in body.regions or []:
        if r.get("kind") not in ("text", "figure") or not all(isinstance(r.get(k), int) for k in "xywh"):
            raise HTTPException(422, "regions need kind (text|figure) and integer x,y,w,h")
    return _process(s, q, _source_bytes(q), body.mode, c, f"Reprocessed ({body.mode})", body.regions)


# ── reading ──────────────────────────────────────────────────────────────────
@router.get("/questions")
def search(
    q: str = "", subject: str = "", class_name: str = "", chapter: str = "", topic: str = "",
    concept: str = "", difficulty: str = "", marks: int | None = None, status: str = "published",
    limit: int = Query(50, le=200), offset: int = 0,
    c: Ctx = Depends(ctx), s: Session = Depends(get_session),
):
    """Concept search: every whitespace/'+'-separated term must appear in the text, topic, chapter or concepts."""
    qs = s.query(Question).filter(Question.school_id == c.school_id)
    if status and status != "any":
        qs = qs.filter(Question.status == status)
    for col, val in ((Question.subject, subject), (Question.class_name, class_name),
                     (Question.difficulty, difficulty)):
        if val:
            qs = qs.filter(col == val)
    if chapter:
        qs = qs.filter(Question.chapter.ilike(f"%{chapter}%"))
    if topic:
        qs = qs.filter(Question.topic.ilike(f"%{topic}%"))
    if marks is not None:
        qs = qs.filter(Question.marks == marks)
    terms = [t.strip().lower() for t in re.split(r"[+\n]", q) if t.strip()]
    terms += [t.strip().lower() for t in re.split(r"[+\n]", concept) if t.strip()]
    rows = qs.order_by(Question.id.desc()).all()
    out = []
    for r in rows:
        v = _version(r, r.approved_version) if r.approved_version else (_version(r) if r.versions else None)
        hay = " ".join([r.chapter, r.topic, " ".join(r.concepts or []), v.text if v else ""]).lower()
        if all(t in hay for t in terms):
            item = _view(r)
            if v:
                item["text"], item["diagram"] = v.text, v.diagram
            out.append(item)
    return {"total": len(out), "items": out[offset:offset + limit]}


@router.get("/questions/{qid}")
def get_question(qid: int, c: Ctx = Depends(ctx), s: Session = Depends(get_session)):
    return _view(_q(s, c, qid), True)


@router.get("/questions/{qid}/versions/{n}")
def get_version(qid: int, n: int, c: Ctx = Depends(ctx), s: Session = Depends(get_session)):
    v = _version(_q(s, c, qid), n)
    return {"number": v.number, "text": v.text, "diagram": v.diagram, "engine": v.engine,
            "note": v.note, "author": v.author, "created_at": v.created_at.isoformat()}


@router.get("/questions/{qid}/compare")
def compare(qid: int, a: int, b: int, c: Ctx = Depends(ctx), s: Session = Depends(get_session)):
    q = _q(s, c, qid)
    va, vb = _version(q, a), _version(q, b)
    diff = list(difflib.unified_diff(va.text.splitlines(), vb.text.splitlines(),
                                     f"v{a}", f"v{b}", lineterm=""))
    ids_a = {o["id"]: o for o in va.diagram.get("objects", [])}
    ids_b = {o["id"]: o for o in vb.diagram.get("objects", [])}
    return {"text_diff": diff,
            "diagram": {"added": sorted(ids_b.keys() - ids_a.keys()),
                        "removed": sorted(ids_a.keys() - ids_b.keys()),
                        "changed": sorted(k for k in ids_a.keys() & ids_b.keys() if ids_a[k] != ids_b[k])}}


def _file(path: str) -> Response:
    p = config.STORAGE_DIR / path
    if not path or not p.is_file():
        raise HTTPException(404, "not found")
    return Response(p.read_bytes(), media_type="application/pdf" if p.suffix == ".pdf" else "image/png")


@router.get("/questions/{qid}/source")
def source(qid: int, c: Ctx = Depends(ctx), s: Session = Depends(get_session)):
    return _file(_q(s, c, qid).source_image)


@router.get("/questions/{qid}/figure")
def figure(qid: int, c: Ctx = Depends(ctx), s: Session = Depends(get_session)):
    return _file(_q(s, c, qid).figure_image)


@router.get("/questions/{qid}/diagram.svg")
def diagram_svg(qid: int, version: int | None = None, c: Ctx = Depends(ctx), s: Session = Depends(get_session)):
    return Response(dg.to_svg(_version(_q(s, c, qid), version).diagram), media_type="image/svg+xml")


# ── editing ──────────────────────────────────────────────────────────────────
class DraftIn(BaseModel):
    text: str | None = None
    diagram: dict | None = None
    note: str = ""
    subject: str | None = None
    class_name: str | None = None
    chapter: str | None = None
    topic: str | None = None
    concepts: list[str] | None = None
    difficulty: str | None = None
    marks: int | None = Field(None, ge=0)
    question_type: str | None = None


@router.put("/questions/{qid}/draft")
def save_draft(qid: int, body: DraftIn, c: Ctx = Depends(ctx), s: Session = Depends(get_session)):
    q = _q(s, c, qid)
    if q.status not in wf.EDITABLE:
        raise HTTPException(409, f"a {q.status!r} question is locked; ask for a revision first")
    if body.difficulty and body.difficulty not in ("Easy", "Medium", "Hard"):
        raise HTTPException(422, "difficulty must be Easy, Medium or Hard")
    cur = _version(q)
    try:
        diagram = dg.validate_diagram(body.diagram) if body.diagram is not None else cur.diagram
    except ValueError as exc:
        raise HTTPException(422, f"invalid diagram: {exc}")
    for k in ("subject", "class_name", "chapter", "topic", "concepts", "difficulty", "marks", "question_type"):
        val = getattr(body, k)
        if val is not None:
            setattr(q, k, val)
    text = cur.text if body.text is None else body.text
    if text != cur.text or diagram != cur.diagram:
        _add_version(s, q, text, diagram, "manual", body.note or "Teacher edit", c.user)
    s.commit()
    return _view(q, True)


class ActionIn(BaseModel):
    note: str = ""


@router.post("/questions/{qid}/{action}")
def act(qid: int, action: str, body: ActionIn | None = None, c: Ctx = Depends(ctx),
        s: Session = Depends(get_session)):
    if action not in wf.ACTIONS:
        raise HTTPException(404, f"unknown action {action!r}")
    q = _q(s, c, qid)
    try:
        new = wf.next_status(action, q.status, c.role)
    except wf.WorkflowError as exc:
        raise HTTPException(409 if "cannot" in str(exc) else 403, str(exc))
    if action == "approve":
        if not _version(q).text.strip():
            raise HTTPException(422, "cannot approve an empty question")
        q.approved_version, q.approved_by, q.approved_at = q.current_version, c.user, datetime.now(timezone.utc)
    if action == "unpublish":
        q.approved_version = None
    if action == "request_revision" and body and body.note:
        q.versions[-1].note = (q.versions[-1].note + " | Reviewer: " + body.note)[:400]
    q.status = new
    s.commit()
    return _view(q, True)


# ── diagram helpers ──────────────────────────────────────────────────────────
@router.get("/diagram/templates")
def templates():
    return {n: dg.template(n) for n in dg.TEMPLATES}


@router.post("/diagram/render")
def render_diagram(d: dict):
    try:
        return Response(dg.to_svg(dg.validate_diagram(d)), media_type="image/svg+xml")
    except ValueError as exc:
        raise HTTPException(422, str(exc))


# ── export ───────────────────────────────────────────────────────────────────
def _header(s: Session, c: Ctx, **kw) -> tuple[dict, bytes | None]:
    sc = _school(s, c)
    logo = (config.STORAGE_DIR / sc.logo_path).read_bytes() if sc.logo_path and (config.STORAGE_DIR / sc.logo_path).is_file() else None
    return {"school": sc.name, **kw}, logo


def _item(q: Question, number: int) -> dict:
    v = _version(q, q.approved_version) if q.approved_version else _version(q)
    return {"number": number, "text": v.text, "marks": q.marks, "diagram": v.diagram, "code": q.code}


def _emit(fmt: str, header: dict, items: list[dict], logo: bytes | None, name: str) -> Response:
    if fmt == "md":
        md, assets = render.to_markdown(header, items)
        if assets:  # Markdown + diagram assets -> zip
            import io, zipfile
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as z:
                z.writestr(f"{name}.md", md)
                for p, b in assets.items():
                    z.writestr(p, b)
            return Response(buf.getvalue(), media_type="application/zip",
                            headers={"Content-Disposition": f'attachment; filename="{name}.zip"'})
        return Response(md, media_type="text/markdown",
                        headers={"Content-Disposition": f'attachment; filename="{name}.md"'})
    if fmt == "pdf":
        return Response(render.to_pdf(header, items, logo), media_type="application/pdf",
                        headers={"Content-Disposition": f'attachment; filename="{name}.pdf"'})
    if fmt == "docx":
        return Response(render.to_docx(header, items, logo),
                        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        headers={"Content-Disposition": f'attachment; filename="{name}.docx"'})
    raise HTTPException(422, "format must be md, pdf or docx")


@router.get("/questions/{qid}/export")
def export_question(qid: int, format: str = "pdf", c: Ctx = Depends(ctx), s: Session = Depends(get_session)):
    q = _q(s, c, qid)
    header, logo = _header(s, c, class_name=q.class_name, subject=q.subject, total_marks=q.marks,
                           title=f"Question {q.code}")
    return _emit(format, header, [_item(q, 1)], logo, q.code)


# ── exam papers ──────────────────────────────────────────────────────────────
class PaperIn(BaseModel):
    title: str
    class_name: str = ""
    subject: str = ""
    duration: str = "3 Hours"
    question_ids: list[int] = []


class AutoPaperIn(BaseModel):
    title: str
    class_name: str
    subject: str
    duration: str = "3 Hours"
    total_marks: int = Field(gt=0)
    chapters: list[str] = []
    difficulty_mix: dict[str, float] = {"Easy": 0.3, "Medium": 0.5, "Hard": 0.2}
    seed: int | None = None


def _paper_questions(s: Session, c: Ctx, ids: list[int]) -> list[Question]:
    out = []
    for i in ids:
        q = _q(s, c, i)
        if q.status != "published":
            raise HTTPException(409, f"question {q.code} is not published")
        out.append(q)
    return out


@router.post("/papers", status_code=201)
def create_paper(body: PaperIn, c: Ctx = Depends(ctx), s: Session = Depends(get_session)):
    if c.role == "typist":
        raise HTTPException(403, "typists cannot build papers")
    if len(set(body.question_ids)) != len(body.question_ids):
        raise HTTPException(422, "duplicate question ids")
    _paper_questions(s, c, body.question_ids)
    p = Paper(school_id=c.school_id, **body.model_dump())
    s.add(p)
    s.commit()
    return {"id": p.id, **body.model_dump()}


@router.post("/papers/auto", status_code=201)
def auto_paper(body: AutoPaperIn, c: Ctx = Depends(ctx), s: Session = Depends(get_session)):
    """Pick published questions to reach `total_marks` following the difficulty mix, chapter by chapter."""
    if c.role == "typist":
        raise HTTPException(403, "typists cannot build papers")
    if not abs(sum(body.difficulty_mix.values()) - 1) < 1e-6:
        raise HTTPException(422, "difficulty_mix must sum to 1")
    pool = s.query(Question).filter(Question.school_id == c.school_id, Question.status == "published",
                                    Question.subject == body.subject, Question.class_name == body.class_name).all()
    if body.chapters:
        want = {x.lower() for x in body.chapters}
        pool = [q for q in pool if q.chapter.lower() in want]
    rng = random.Random(body.seed)
    rng.shuffle(pool)
    chosen: list[Question] = []
    remaining = body.total_marks
    for diff, share in sorted(body.difficulty_mix.items(), key=lambda kv: -kv[1]):
        budget = round(body.total_marks * share)
        for q in [x for x in pool if x.difficulty == diff]:
            if budget >= q.marks and remaining >= q.marks and q not in chosen:
                chosen.append(q); budget -= q.marks; remaining -= q.marks
    for q in pool:  # top up from anything left
        if remaining >= q.marks and q not in chosen and q.marks > 0:
            chosen.append(q); remaining -= q.marks
    if remaining:
        raise HTTPException(422, f"question bank cannot reach {body.total_marks} marks "
                                 f"(short by {remaining}); add more published questions")
    p = Paper(school_id=c.school_id, title=body.title, class_name=body.class_name, subject=body.subject,
              duration=body.duration, question_ids=[q.id for q in chosen])
    s.add(p)
    s.commit()
    return {"id": p.id, "question_ids": p.question_ids, "total_marks": body.total_marks}


@router.put("/papers/{pid}/order")
def reorder(pid: int, ids: list[int], c: Ctx = Depends(ctx), s: Session = Depends(get_session)):
    p = s.get(Paper, pid)
    if not p or p.school_id != c.school_id:
        raise HTTPException(404, "paper not found")
    if sorted(ids) != sorted(p.question_ids):
        raise HTTPException(422, "ids must be a permutation of the paper's questions")
    p.question_ids = ids
    s.commit()
    return {"id": p.id, "question_ids": p.question_ids}


@router.get("/papers/{pid}/export")
def export_paper(pid: int, format: str = "pdf", c: Ctx = Depends(ctx), s: Session = Depends(get_session)):
    p = s.get(Paper, pid)
    if not p or p.school_id != c.school_id:
        raise HTTPException(404, "paper not found")
    qs = _paper_questions(s, c, p.question_ids)
    items = [_item(q, n) for n, q in enumerate(qs, 1)]
    header, logo = _header(s, c, title=p.title, class_name=p.class_name, subject=p.subject,
                           duration=p.duration, total_marks=sum(q.marks for q in qs))
    return _emit(format, header, items, logo, re.sub(r"\W+", "_", p.title) or f"paper_{p.id}")
