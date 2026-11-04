# Q-Paper Studio

Handwritten questions → scientific documents → question bank, for Physics, Chemistry and Mathematics (Biology selectable).

A handwritten page is reconstructed as typeset text (LaTeX maths) plus an **editable object diagram** (not a flat image).
A teacher edits, saves revisions and approves; only approved questions can be published to the bank, searched by concept,
and assembled into exam papers exported as **Markdown, PDF and DOCX**.

## Processing engines (same output, same workflow)

| Mode | What runs | Needs |
|---|---|---|
| `python` | OpenCV preprocessing and figure/text split, Tesseract OCR, template-based diagram start | Tesseract binary, no network |
| `watsonx` | IBM watsonx.ai vision model reads text, equations and redraws the figure as diagram objects | `WATSONX_*` credentials |
| `hybrid` | watsonx first; falls back to `python` if watsonx is unavailable | either |

Providers sit behind `AIProvider` (`app/providers/base.py`). Adding a local model or another vendor means adding one class.
A school can switch external AI off entirely (School tab, or `QB_ALLOW_EXTERNAL_AI=false`); `watsonx` mode is then refused and `hybrid` stays local.

Python-only mode cannot read real handwriting well. Tesseract is a printed-text engine, so expect to retype most
handwritten maths. The teacher review step exists for this reason.

## Run

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
brew install tesseract            # or: apt install tesseract-ocr   (only for Python-only mode)
cp /path/to/.env.secrets .env.secrets   # WATSONX_API_KEY, WATSONX_PROJECT_ID, WATSONX_URL (git-ignored)
.venv/bin/uvicorn app.main:app --port 8000
```

Open http://localhost:8000. API docs are at `/docs`.

Optional settings: `WATSONX_VISION_MODEL` (default `meta-llama/llama-4-maverick-17b-128e-instruct-fp8`; it must accept images),
`QB_STORAGE_DIR`, `QB_DATABASE_URL`, `QB_ALLOW_EXTERNAL_AI`.

## Workflow

`uploaded → processing → draft → under_review → approved → published`, with `revision_requested` as the loop back.
Teachers and admins approve and publish; typists can digitise, edit and submit. Approved and published questions are locked.
Every save and every reprocess creates a new version; none are overwritten, and the original scan is kept.

## Tests

```bash
.venv/bin/python -m pytest -q                     # offline
QB_LIVE_WATSONX=1 .venv/bin/python -m pytest -q   # also calls watsonx with your credentials
```

## Limits to know about

- **Identity is a placeholder.** The API trusts `X-User`, `X-Role`, `X-School-Id` headers. Questions are scoped per school
  in every query, but there is no login, so do not expose it to the internet before adding real authentication.
- AI-drawn diagrams are a first draft (positions and directions are often slightly wrong). That is what the diagram editor is for.
- Display equations (`$$...$$`) render as images in PDF and DOCX; inline maths becomes Unicode with sub/superscripts.
  Chemistry structure drawing (RDKit) and a stricter equation check (SymPy) are not built yet.
- SQLite by default; PostgreSQL via `QB_DATABASE_URL` is untested. No background job queue yet, so reconstruction runs inside the request.
- The browser editor's JavaScript passed a syntax check, but I have not clicked through it in a real browser. The API behind it is tested.
