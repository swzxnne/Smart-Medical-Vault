# Smart Medical Vault

A secure, self-hosted vault for a family's medical records. Upload lab reports, prescriptions,
discharge summaries and scanned OPD cards. The system reads them (OCR), extracts structured health
data, charts it over time, and answers questions with citations to the exact source page.

> **Privacy notice:** The free tier of the Gemini API may use prompts to improve Google's products.
> Only upload synthetic documents or your own records, never anyone else's real medical data.

---

## Table of Contents
- [Features](#features)
- [Architecture](#architecture)
- [Tech Stack](#tech-stack)
- [Getting Started](#getting-started)
- [How It Works](#how-it-works)
  - [Processing Pipeline](#processing-pipeline)
  - [Search and Ask AI](#search-and-ask-ai)
  - [Structured Health Data](#structured-health-data)
- [Evaluation](#evaluation)
- [Testing](#testing)
- [Project Structure](#project-structure)

---

## Features
- **Family profiles:** keep records for each family member separately, under one account.
- **Document ingestion:** PDFs (born-digital and scanned), DOCX files and photos.
- **Robust OCR:** Docling for digital documents and Tesseract for scans, with Gemini Vision as a fallback for low-confidence pages.
- **AI summaries:** document type, date, provider and patient name are detected automatically, and summaries carry page-level citations.
- **Structured health data:** measurements, medications and conditions are extracted, grounded in the source text, normalized to canonical units and flagged against the reference ranges printed on the report.
- **Health charts:** see trends for each metric (blood pressure, glucose, HbA1c, weight, and more) across all documents.
- **Hybrid search:** semantic search (pgvector) is combined with Postgres full-text search using Reciprocal Rank Fusion.
- **Ask AI:** streamed answers grounded in your documents, with clickable `[S#]` source citations.
- **Human in the loop:** uncertain values are marked *Needs review* so users can confirm, correct or remove them.
- **Evaluation harness:** a synthetic golden set measures extraction and retrieval quality.

## Architecture

```
┌──────────────┐     ┌──────────────┐     ┌─────────────────────────────────────┐
│  Next.js Web │────▶│  FastAPI API │────▶│  Celery Workers                     │
│  (Clerk auth)│     │              │     │  parse (ocr) → embed → summarize    │
└──────────────┘     └──────┬───────┘     └──────┬───────────────┬──────────────┘
                            │                    │               │
                ┌───────────┼────────────────────┤               │
                ▼           ▼                    ▼               ▼
         ┌────────────┐ ┌────────┐      ┌────────────────┐ ┌──────────────┐
         │ PostgreSQL │ │ Redis  │      │ S3 (SeaweedFS) │ │ Gemini/Voyage│
         │ + pgvector │ │ broker │      │ document store │ │   AI APIs    │
         └────────────┘ └────────┘      └────────────────┘ └──────────────┘
```

## Tech Stack

| Layer | Technology |
|---|---|
| Frontend | Next.js, TypeScript, Tailwind CSS, Recharts, Clerk |
| API | FastAPI, SQLAlchemy, Alembic |
| Background jobs | Celery, Redis, Flower |
| Database | PostgreSQL + pgvector |
| Object storage | S3-compatible (SeaweedFS) |
| Document parsing | Docling, Tesseract OCR, Gemini Vision |
| AI | Google Gemini (LLM), Voyage AI (embeddings) |
| Infrastructure | Docker Compose |

## Getting Started

### Prerequisites
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (or OrbStack)
- API keys (all have free tiers):
  - **Clerk:** [dashboard.clerk.com](https://dashboard.clerk.com) → API Keys
  - **Google Gemini:** [aistudio.google.com/apikey](https://aistudio.google.com/apikey)
  - **Voyage AI:** [dashboard.voyageai.com](https://dashboard.voyageai.com)

### Installation

```sh
git clone https://github.com/swzxnne/AImedicalVault.git
cd AImedicalVault
cp .env.example .env        # add your API keys
docker compose up --build
```

### Services

| Service | URL |
|---|---|
| Web app | http://localhost:3000 |
| API docs (Swagger) | http://localhost:8000/docs |
| Celery monitor (Flower) | http://localhost:5555 |
| S3 storage (SeaweedFS) | http://localhost:8333 |

The API runs database migrations and creates the storage bucket automatically on startup.
The first document processed downloads Docling's layout models (about 1–2 minutes, cached afterwards).

### Sample Documents
Generate synthetic reports for a fictional patient:

```sh
docker compose run --rm -v ./samples:/samples worker python scripts/make_samples.py /samples
```

## How It Works

### Processing Pipeline
Each upload runs a Celery chain: **parse** (queue `ocr`) → **embed** (queue `embed`) → **summarize** (queue `llm`).

| Stage | Description |
|---|---|
| **Parse** | Born-digital PDF pages and DOCX files go through Docling, which preserves tables. Images and scanned pages go through Tesseract. Pages read with under 75% confidence (`VISION_OCR_THRESHOLD`) are re-read by Gemini Vision. Blank pages are detected first and never sent to the model, because vision models tend to invent content for them. Set `VISION_OCR_ENABLED=false` to turn vision off. |
| **Embed** | Text is split into page-bounded chunks, embedded with Voyage AI and stored in pgvector with a full-text index. |
| **Summarize** | A single Gemini call classifies the document (type, date, provider, patient), writes a summary with `[p.N]` page citations and extracts structured health data. A warning appears if the patient name doesn't match the profile. |

Rate-limit errors are retried with exponential backoff. Failed documents show their error and can be
retried with **Reprocess**. The worker doesn't hot-reload: run `docker compose restart worker` after changing pipeline code.

### Search and Ask AI
- **Search** (header bar or `/search`): hybrid retrieval combines vector similarity and full-text search, fused with
  Reciprocal Rank Fusion. Results are always scoped to the signed-in user (and optionally to one profile), grouped by
  document, and linked to the exact page.
- **Ask AI:** the top passages are sent to Gemini as numbered sources, and the answer streams back with `[S#]`
  citations. If nothing relevant is found, the system says so without calling the LLM.
- **Measurement questions** (for example, *"BP in 2024"* or *"Ravi's sugar since 2023"*) are detected by rules
  (`app/search/metric_query.py`) and answered from the structured data first. Follow-up questions are rewritten into
  standalone queries before searching.
- **Graceful degradation:** when a query can't be embedded (for example, because of Voyage free-tier rate limits),
  search falls back to keyword-only and the UI shows a notice.

### Structured Health Data
The code decides what gets stored, not the LLM:

- **Grounding:** a value is kept only if it appears in the document text on the cited page. Medicine and diagnosis
  names must appear on the page.
- **Normalization** (`app/health/catalog.py`, `normalize.py`): about 35 known metrics have canonical units and
  conversions (mmol/L → mg/dL, lb → kg, and so on). A blood pressure reading like `138/88` is split into systolic
  and diastolic. Implausible values and unknown units are dropped as likely misreads.
- **Flags:** high, low and in-range flags come from the reference range printed on the report and are never guessed.
- **Review workflow:** a value is marked *Needs review* when its label isn't near the value or when its page was
  re-read by AI. Users can confirm, correct or remove it, and their changes are kept if the document is re-analysed.
- **Profile summary:** a debounced background task writes a profile overview from the structured facts and
  document summaries, citing documents as `[D#]`.

## Evaluation
`services/api/eval/` contains a synthetic golden set and scorers, so changes to prompts, models and code
can be measured instead of guessed. It never touches real data.

```sh
docker compose run --rm worker python -m eval.generate      # build documents, answer keys and questions
docker compose run --rm worker python -m eval.extraction    # score extraction against the answer keys
docker compose run --rm worker python -m eval.rag           # score retrieval and answers in an isolated DB
```

- **Golden set:** 34 documents for three fictional family members. It includes typed and scanned labs,
  prescriptions, discharge summaries, OPD cards, simulated handwriting, edge cases (a blank scan and a
  wrong-patient report) and hard scans (blur, fading, camera angle). In total: 156 expected values and 19 questions.
- **Extraction metrics:** value precision and recall, page accuracy, document metadata, medications,
  conditions and refusal of unreadable documents.
- **RAG metrics:** retrieval hit rate, answer correctness, citation accuracy and "not found" behaviour.
- **Caching:** AI responses are cached by prompt, model and text, so re-scoring is free. `--no-cache` forces fresh calls.
- **Baselines:** `--save-baseline` saves a reference run, and later runs report deltas against it.
- **Vision comparison:** `--no-vision` disables the Gemini Vision fallback.

> The golden set is generated, so it's cleaner than real-world reports. High scores are necessary but not sufficient.

## Testing

```sh
docker compose run --rm worker pytest   # full suite, including the OCR/Docling parser test
docker compose exec api pytest          # same suite; the parser test is skipped (no Tesseract in the API image)
```

Tests run against a separate `vault_test` database, which is created automatically.

## Project Structure

```
apps/web/                   Next.js + Clerk frontend
services/api/
├── app/                    FastAPI app, models, auth, storage
│   ├── search/             hybrid retrieval, grounded answers, metric-question parser
│   ├── health/             metric catalog, normalization, fact storage, profile summary
│   └── pipeline/           Celery tasks, chunking, providers (Docling, Tesseract, Voyage, Gemini)
├── alembic/                database migrations
├── eval/                   golden set, extraction and RAG scorers
├── scripts/                synthetic sample document generator
├── tests/                  pytest suite
├── Dockerfile              API image (lightweight)
└── Dockerfile.worker       worker image (Docling, PyTorch CPU, Tesseract)
docker-compose.yml          postgres (pgvector), redis, storage, api, worker, flower, web
```
