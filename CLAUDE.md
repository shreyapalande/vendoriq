# VendorIQ — instructions for Claude Code

## What this project is
VendorIQ is a portfolio project: a multi-tenant vendor security review app.
A Requester uploads a vendor's security questionnaire (PDF), a background worker
uses an LLM to propose a finding per security control with verified citations,
a Reviewer accepts or overrides each finding, and an Approver approves or rejects.
Every change is recorded in an audit log.

The data is synthetic, but every behavior must be real: real auth, real database,
real Gemini API calls, real tests. Target split: ~75% software engineering, ~25% applied AI.

I will be questioned in depth on this code in an interview. Prefer simple, explicit
code I can explain over clever or abstract code.

## Stack (do not change without asking)
- Frontend: React + JavaScript (not TypeScript) + Vite + Tailwind + React Router
- Backend: Python (version below), FastAPI, Pydantic v2, SQLModel (sync), Alembic
- Database: PostgreSQL 16 via Docker Compose
- PDF: PyMuPDF. PDF generation for test data: ReportLab
- AI: Google Gemini API via the official `google-genai` SDK, free tier, with a JSON
  response schema (structured output). Model name from env `GEMINI_MODEL`, key from
  `GEMINI_API_KEY`. Free-tier rate limits are low: treat 429 / RESOURCE_EXHAUSTED as
  retryable with backoff, and never call the model in a tight loop.
- Fuzzy matching: rapidfuzz
- Python version: 3.14
- Tests: pytest + HTTPX against a real Postgres test database (never SQLite)
- Background jobs: a `processing_jobs` Postgres table + a separate worker process

Do NOT add: Redis, Celery, LangChain, LlamaIndex, LangGraph, pgvector, an ORM other
than SQLModel/SQLAlchemy, a UI component library, or any new dependency I didn't ask
for. If you think one is needed, stop and ask.

## Layout
```
backend/
  app/
    main.py            # app factory, routers, error handlers
    config.py          # pydantic-settings
    db.py              # engine, session dependency
    models/            # SQLModel tables
    api/               # routers, one file per resource
    auth/              # sessions, password hashing, dependencies
    domain/            # state_machine.py, audit.py, business rules
    ai/                # text.py, citations.py, analyze.py, schemas.py (no FastAPI/DB imports)
  worker/main.py       # job loop
  alembic/
  tests/
evals/vendors/*.yaml   # synthetic vendors + expected labels (ground truth)
evals/pdfs/            # generated PDFs
scripts/               # seed.py, make_pdfs.py, run_eval.py
frontend/
docker-compose.yml
DECISIONS.md
```

## Non-negotiable rules
1. **Tenant isolation.** Every query on a tenant-owned table filters by the current
   user's `org_id`. Another org's resource returns 404, never 403. Write a test for it.
2. **Authorization is backend-only.** The frontend may hide buttons but never enforces anything.
3. **State changes** go through `domain/state_machine.py` using a conditional
   `UPDATE ... WHERE status = :expected`; no row changed means 409.
4. **Audit events** are written in the same transaction as the change they describe.
   Never log passwords, session tokens, API keys or full document text.
5. **The worker never holds a database transaction open while calling the LLM.**
   Claim in one short transaction, call the model, save in another short transaction
   that first checks the job is still locked by this worker.
6. **AI output is a suggestion.** Never auto-approve. Keep the AI label separate from
   the human final label. Never show an unverified citation as evidence.
7. **Uploaded PDFs are untrusted.** Validate type, size and page count. Treat document
   text as data in prompts, never as instructions.
8. **Secrets** come from environment variables only. Never commit `.env`.
9. **Request models** use `extra="forbid"`. Errors use the shared JSON error shape.

## How to work
- Work on ONE milestone at a time. Do not start the next one.
- Start by giving me a short plan: files to create or change, key choices, tests.
  Wait for my OK before writing code.
- Keep changes small and readable. No speculative abstractions, base classes or
  plugin systems.
- Write tests alongside the code. Run them and show me the real output.
- Tests that touch AI use a fake analyzer; only the eval script and the worker call Gemini.
- All Gemini calls go through one function in `app/ai/analyze.py`, so the provider
  can be swapped later. No provider abstraction layer beyond that function.
- Do not stage, commit or push. I review and commit myself.
- When done, summarize: files changed, key decisions and alternatives, test results
  (with the command you ran), known limitations. Suggest 1–3 lines for DECISIONS.md.
- If something in my request conflicts with these rules, say so instead of guessing.

## Commands
(Fill in during Milestone 2.)
- Python environment: a virtualenv already exists at `.venv` in the repo root.
  Always use it: run `.venv/bin/python`, `.venv/bin/pip` and `.venv/bin/pytest`
  (Windows: `.venv\Scripts\...`). Never install packages globally and never create
  another venv. Add every dependency to backend/requirements.txt.
- Start database: `docker compose up -d db`
- Migrate: `cd backend && alembic upgrade head`
- Seed: `python scripts/seed.py`
- API: `cd backend && uvicorn app.main:app --reload`
- Worker: `cd backend && python -m worker.main`
- Tests: `cd backend && pytest`
- Frontend: `cd frontend && npm run dev`
- Eval: `python scripts/run_eval.py`
