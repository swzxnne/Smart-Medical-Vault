import uuid
from pathlib import Path

import pytest
from sqlalchemy import select, text

from app.db.models import Chunk, Document, DocumentPage, EMBEDDING_DIM, Profile, Summary, User
from app.pipeline import analysis, tasks
from app.pipeline.chunking import chunk_pages
from app.pipeline.providers.embedder import batches
from app.pipeline.providers.errors import PipelineError, TransientError
from app.pipeline.providers.parser import ParsedPage, tsv_to_text
from tests.conftest import TestingSession

# ---------- chunking ----------


def test_chunks_stay_on_their_page():
    chunks = chunk_pages([(1, "alpha\n\nbeta"), (2, "gamma")])
    assert [(c.page_no, c.chunk_index, c.text) for c in chunks] == [
        (1, 0, "alpha\n\nbeta"),
        (2, 1, "gamma"),
    ]


def test_large_table_split_by_rows_with_header():
    header = "| Test | Result |\n|---|---|"
    rows = [f"| Test number {i} | {i} mg/dL |" for i in range(40)]
    chunks = chunk_pages([(1, "\n".join([header, *rows]))], max_chars=300)
    assert len(chunks) > 1
    for c in chunks:
        assert c.text.startswith(header)
        assert len(c.text) <= 300
    all_rows = [line for c in chunks for line in c.text.splitlines()[2:]]
    assert all_rows == rows


def test_table_not_merged_into_paragraph_lines():
    page = "Intro line\n| a | b |\n|---|---|\n| 1 | 2 |\nOutro line"
    chunks = chunk_pages([(1, page)], max_chars=30)
    assert [c.text for c in chunks] == ["Intro line", "| a | b |\n|---|---|\n| 1 | 2 |", "Outro line"]


def test_oversized_table_row_is_kept_whole():
    table = "| Test | Result |\n|---|---|\n| " + "x" * 50 + " | 1 |"
    chunks = chunk_pages([(1, table)], max_chars=30)
    assert "".join(c.text for c in chunks).count("x" * 50) == 1


def test_long_paragraph_split_on_words():
    chunks = chunk_pages([(1, " ".join(["word"] * 500))], max_chars=100)
    assert all(len(c.text) <= 100 for c in chunks)
    assert len(chunks) > 1
    assert sum(len(c.text.split()) for c in chunks) == 500


def test_embedding_batches_respect_token_budget():
    texts = ["x" * 300] * 10  # ~101 tokens each
    groups = list(batches(texts, max_tokens=250))
    assert [len(g) for g in groups] == [2, 2, 2, 2, 2]
    assert list(batches(["tiny"] * 5, max_tokens=1000, max_items=2)) == [["tiny"] * 2, ["tiny"] * 2, ["tiny"]]


# ---------- OCR text reconstruction ----------


def test_tsv_to_text_rebuilds_lines_and_confidence():
    header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
    rows = [
        "5\t1\t1\t1\t1\t1\t0\t0\t0\t0\t90\tBlood",
        "5\t1\t1\t1\t1\t2\t0\t0\t0\t0\t80\tPressure:",
        "5\t1\t1\t1\t1\t3\t0\t0\t0\t0\t70\t146/92",
        "5\t1\t1\t1\t2\t1\t0\t0\t0\t0\t100\tPulse",
        "4\t1\t2\t1\t1\t0\t0\t0\t0\t0\t-1\t",
        "5\t1\t2\t1\t1\t1\t0\t0\t0\t0\t60\tPlan",
    ]
    text_out, conf = tsv_to_text("\n".join([header, *rows]))
    assert text_out == "Blood Pressure: 146/92\nPulse\n\nPlan"
    assert conf == 0.8


# ---------- analysis helpers ----------


def test_name_mismatch():
    assert analysis.name_mismatch("Asha Verma", "Mrs. ASHA VERMA") is None
    assert analysis.name_mismatch("Asha", "Asha Verma") is None
    assert analysis.name_mismatch("Asha Verma", None) is None
    assert analysis.name_mismatch("Mom", "Asha Verma") == 'Report is for "Asha Verma" but the profile is "Mom"'
    assert analysis.name_mismatch("Ravi Kumar", "Dr. Asha Verma") is not None


def test_tidy_markdown():
    bad = "### At a glance\nText [D1]. ### Conditions\n- A [D1]\n- B [D1] ### Medications\n- M [D1]"
    assert analysis.tidy_markdown(bad) == (
        "### At a glance\nText [D1].\n\n### Conditions\n- A [D1]\n- B [D1]\n\n### Medications\n- M [D1]"
    )
    good = "### Overview\nText [p.1].\n\n### Key findings\n- a [p.1]\n- b"
    assert analysis.tidy_markdown(good) == good
    escaped = "### Overview\\nText [p.1].\\n\\n### Key findings\\n- a [p.1]\\n- b"
    assert analysis.tidy_markdown(escaped) == good


def test_parse_date():
    assert str(analysis.parse_date("2024-03-12")) == "2024-03-12"
    assert analysis.parse_date("12/03/2024") is None
    assert analysis.parse_date(None) is None


def test_prompt_marks_pages_and_truncates():
    prompt = analysis.build_prompt("r.pdf", [(1, "a" * 50), (2, "b" * 50)], max_chars=80)
    assert '<page n="1">' in prompt
    assert '<page n="2">' not in prompt
    assert "remaining pages omitted" in prompt


# ---------- tasks end-to-end (fake parser/embedder/LLM, real Postgres) ----------


class FakeEmbedder:
    model = "fake-embed"

    def __init__(self):
        self.inputs: list[str] = []

    def embed_documents(self, texts):
        self.inputs.extend(texts)
        return [[float(i + 1)] + [0.0] * (EMBEDDING_DIM - 1) for i in range(len(texts))]


class FakeLLM:
    model = "fake-llm"

    def __init__(self, patient="Asha Verma"):
        self.patient = patient
        self.prompts: list[str] = []

    def generate_json(self, *, system, prompt, schema):
        self.prompts.append(prompt)
        return self._result(schema), self.model

    def _result(self, schema):
        return schema(
            doc_type="lab_report",
            doc_date="2024-03-12",
            provider="City Diagnostics",
            patient_name=self.patient,
            summary_md="### Key findings\n- HbA1c 6.4 % [p.1]",
        )


@pytest.fixture
def doc_id(storage) -> str:
    with TestingSession() as db:
        user = User(clerk_id="user_pipe")
        db.add(user)
        db.flush()
        profile = Profile(user_id=user.id, name="Asha Verma")
        db.add(profile)
        db.flush()
        doc = Document(
            user_id=user.id,
            profile_id=profile.id,
            storage_key=f"{user.id}/{profile.id}/x/report.pdf",
            original_name="report.pdf",
            mime="application/pdf",
            size_bytes=10,
            status="queued",
        )
        db.add(doc)
        db.commit()
        return str(doc.id)


@pytest.fixture
def fakes(monkeypatch, storage, pipeline):
    monkeypatch.setattr(tasks, "get_pipeline", lambda: pipeline)
    embedder, llm = FakeEmbedder(), FakeLLM()
    pages = [
        ParsedPage(1, "Fasting Blood Glucose 118 mg/dL\n\nHbA1c 6.4 %", "docling"),
        ParsedPage(2, "Blood pressure 138/88 mmHg", "tesseract", 0.93),
    ]
    monkeypatch.setattr(tasks, "get_parser", lambda: lambda path, mime: pages)
    monkeypatch.setattr(tasks, "get_embedder", lambda: embedder)
    monkeypatch.setattr(tasks, "get_llm", lambda: llm)
    monkeypatch.setattr(tasks, "get_storage", lambda: storage)
    return embedder, llm


def run_all(doc_id: str) -> None:
    tasks.parse_document(doc_id)
    tasks.embed_document(doc_id)
    tasks.summarize_document(doc_id)


def test_full_pipeline(doc_id, fakes):
    embedder, llm = fakes
    run_all(doc_id)

    with TestingSession() as db:
        doc = db.get(Document, uuid.UUID(doc_id))
        assert doc.status == "ready"
        assert doc.page_count == 2
        assert doc.doc_type == "lab_report"
        assert str(doc.doc_date) == "2024-03-12"
        assert doc.provider == "City Diagnostics"
        assert doc.review_reason is None
        assert doc.pipeline_version == tasks.PIPELINE_VERSION
        assert doc.processed_at is not None

        pages = db.scalars(select(DocumentPage).order_by(DocumentPage.page_no)).all()
        assert [(p.page_no, p.engine, p.ocr_confidence) for p in pages] == [(1, "docling", None), (2, "tesseract", 0.93)]

        chunks = db.scalars(select(Chunk).order_by(Chunk.chunk_index)).all()
        assert [c.page_no for c in chunks] == [1, 2]
        assert all(c.profile_id == doc.profile_id and c.user_id == doc.user_id for c in chunks)

        # Generated full-text column and vector column are both queryable.
        hit = db.scalar(select(Chunk.page_no).where(text("tsv @@ plainto_tsquery('english', 'glucose')")))
        assert hit == 1
        nearest = db.scalar(
            select(Chunk.page_no).order_by(Chunk.embedding.cosine_distance([2.0] + [0.0] * (EMBEDDING_DIM - 1)))
        )
        assert nearest in (1, 2)

        summary = db.scalar(select(Summary).where(Summary.document_id == doc.id))
        assert summary.model == "fake-llm"
        assert "[p.1]" in summary.content_md

    assert embedder.inputs[0].startswith("Document: report.pdf\nPage 1\n\n")
    assert '<page n="2">' in llm.prompts[0]


def test_rerun_replaces_outputs(doc_id, fakes):
    run_all(doc_id)
    run_all(doc_id)
    with TestingSession() as db:
        assert db.scalar(select(text("count(*)")).select_from(Chunk)) == 2
        assert db.scalar(select(text("count(*)")).select_from(DocumentPage)) == 2
        assert db.scalar(select(text("count(*)")).select_from(Summary)) == 1


def test_name_mismatch_flagged(doc_id, fakes, monkeypatch):
    monkeypatch.setattr(tasks, "get_llm", lambda: FakeLLM(patient="Ravi Kumar"))
    run_all(doc_id)
    with TestingSession() as db:
        doc = db.get(Document, uuid.UUID(doc_id))
        assert doc.status == "ready"
        assert "Ravi Kumar" in doc.review_reason


def test_blank_document_fails(doc_id, fakes, monkeypatch):
    monkeypatch.setattr(tasks, "get_parser", lambda: lambda path, mime: [ParsedPage(1, "  ", "tesseract", 0.1)])
    with pytest.raises(PipelineError):
        tasks.parse_document(doc_id)


def test_on_failure_marks_document_failed(doc_id):
    tasks.parse_document.on_failure(PipelineError("No readable text"), "task-id", (doc_id,), {}, None)
    with TestingSession() as db:
        doc = db.get(Document, uuid.UUID(doc_id))
        assert doc.status == "failed"
        assert doc.error == "parse: No readable text"


def test_deleted_document_is_skipped(doc_id, fakes):
    with TestingSession() as db:
        db.delete(db.get(Document, uuid.UUID(doc_id)))
        db.commit()
    run_all(doc_id)  # no exception, nothing written
    with TestingSession() as db:
        assert db.scalar(select(text("count(*)")).select_from(DocumentPage)) == 0


def test_transient_errors_are_retried():
    for task in (tasks.parse_document, tasks.embed_document, tasks.summarize_document):
        assert TransientError in task.autoretry_for
        assert task.max_retries >= 3


# ---------- Gemini model fallback ----------


class FakeAPIError(Exception):
    def __init__(self, code, message="boom"):
        self.code, self.message = code, message


class FakeModels:
    def __init__(self, outcomes):
        self.outcomes = outcomes  # model -> exception to raise, or JSON text to return
        self.calls: list[str] = []

    def generate_content(self, *, model, contents, config):
        self.calls.append(model)
        outcome = self.outcomes[model]
        if isinstance(outcome, Exception):
            raise outcome

        class Resp:
            parsed = None
            text = outcome

        return Resp()


@pytest.fixture
def gemini(monkeypatch):
    genai_errors = pytest.importorskip("google.genai.errors")
    monkeypatch.setattr(genai_errors, "APIError", FakeAPIError)
    from app.pipeline.providers.llm import GeminiLLM

    def make(outcomes, models):
        llm = GeminiLLM.__new__(GeminiLLM)
        llm.models = models
        llm._client = type("C", (), {"models": FakeModels(outcomes)})()
        return llm

    return make


ANALYSIS_JSON = '{"doc_type": "other", "doc_date": null, "provider": null, "patient_name": null, "summary_md": "x"}'


def test_falls_back_when_main_model_overloaded(gemini):
    llm = gemini({"main": FakeAPIError(503), "backup": ANALYSIS_JSON}, ["main", "backup"])
    result, used = llm.generate_json(system="s", prompt="p", schema=analysis.DocumentAnalysis)
    assert used == "backup"
    assert result.summary_md == "x"


def test_all_models_overloaded_is_transient(gemini):
    llm = gemini({"main": FakeAPIError(503), "backup": FakeAPIError(429)}, ["main", "backup"])
    with pytest.raises(TransientError, match="main: 503.*backup: 429"):
        llm.generate_json(system="s", prompt="p", schema=analysis.DocumentAnalysis)


def test_retired_main_model_is_permanent(gemini):
    llm = gemini({"main": FakeAPIError(404, "no longer available"), "backup": ANALYSIS_JSON}, ["main", "backup"])
    with pytest.raises(PipelineError, match="404"):
        llm.generate_json(system="s", prompt="p", schema=analysis.DocumentAnalysis)
    assert llm._client.models.calls == ["main"]


def test_broken_fallback_is_skipped(gemini):
    llm = gemini(
        {"main": FakeAPIError(503), "retired": FakeAPIError(404), "backup": ANALYSIS_JSON},
        ["main", "retired", "backup"],
    )
    _, used = llm.generate_json(system="s", prompt="p", schema=analysis.DocumentAnalysis)
    assert used == "backup"


# ---------- real parser on generated samples (worker image only) ----------


@pytest.mark.skipif(not Path("/usr/bin/tesseract").exists(), reason="needs the worker image (tesseract + docling)")
def test_parser_on_generated_samples(tmp_path):
    from app.pipeline.providers.parser import DOCX, parse_file
    from scripts.make_samples import lab_report_pdf, prescription_docx, scanned_bp_log

    lab_report_pdf(tmp_path / "lab.pdf")
    prescription_docx(tmp_path / "rx.docx")
    scanned_bp_log(tmp_path / "opd.png", tmp_path / "opd.pdf")

    lab = parse_file(tmp_path / "lab.pdf", "application/pdf")
    assert [p.engine for p in lab] == ["docling", "docling"]
    assert "| HbA1c" in lab[0].text and "6.4" in lab[0].text
    assert "&amp;" not in lab[1].text

    rx = parse_file(tmp_path / "rx.docx", DOCX)
    assert len(rx) == 1 and "Metformin 500 mg" in rx[0].text

    for path, mime in ((tmp_path / "opd.png", "image/png"), (tmp_path / "opd.pdf", "application/pdf")):
        [page] = parse_file(path, mime)
        assert page.engine == "tesseract"
        assert page.ocr_confidence and page.ocr_confidence > 0.8
        assert "Blood Pressure: 146/92 mmHg" in page.text
        # Labels stay on the same line as their values.
        assert "Random Blood Sugar: 162 mg/dL" in page.text


# ---------- vision fallback ----------


def _page_image(text_lines=True):
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (1240, 1754), "white")
    if text_lines:
        draw = ImageDraw.Draw(img)
        for i in range(12):
            draw.text((80, 100 + i * 60), "Hemoglobin 11.9 g/dL ref 12.0 - 15.5", fill="black", font=ImageFont.load_default(size=30))
    return img


def test_vision_rereads_low_confidence_pages(monkeypatch):
    from app.pipeline.providers import parser

    calls = []
    monkeypatch.setattr(parser, "get_vision_transcriber", lambda: lambda png: calls.append(png) or ("Hemoglobin: 11.9 g/dL", "m"))
    low = parser.ParsedPage(1, "Hemog1obin 1l.9", "tesseract", 0.42)
    high = parser.ParsedPage(1, "Hemoglobin 11.9", "tesseract", 0.93)
    none = parser.ParsedPage(1, "", "tesseract", None)  # Tesseract read nothing at all

    assert parser._maybe_vision(high, _page_image()) is high and not calls
    reread = parser._maybe_vision(low, _page_image())
    assert (reread.engine, reread.text, reread.ocr_confidence) == ("gemini-vision", "Hemoglobin: 11.9 g/dL", 0.42)
    assert parser._maybe_vision(none, _page_image()).engine == "gemini-vision"


def test_vision_never_sees_blank_pages_and_respects_no_text(monkeypatch):
    from app.pipeline.providers import parser

    calls = []
    monkeypatch.setattr(parser, "get_vision_transcriber", lambda: lambda png: calls.append(png) or ("NO_TEXT", "m"))
    blank = parser.ParsedPage(1, "", "tesseract", None)
    assert parser.looks_blank(_page_image(text_lines=False))
    assert not parser.looks_blank(_page_image())
    assert parser._maybe_vision(blank, _page_image(text_lines=False)) is blank and not calls  # never asked
    low = parser.ParsedPage(1, "x", "tesseract", 0.3)
    assert parser._maybe_vision(low, _page_image()) is low and len(calls) == 1  # model said NO_TEXT


def test_vision_failure_keeps_tesseract_text(monkeypatch):
    from app.pipeline.providers import parser

    def busy(png):
        raise TransientError("503")

    monkeypatch.setattr(parser, "get_vision_transcriber", lambda: busy)
    low = parser.ParsedPage(1, "Hemog1obin 1l.9", "tesseract", 0.42)
    assert parser._maybe_vision(low, _page_image()) is low
