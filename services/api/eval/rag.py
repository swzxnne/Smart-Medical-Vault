"""RAG eval: ingest the golden set into an isolated database, ask questions with known answers.

Uses a separate Postgres database (`<db>_eval`) and bucket (`<bucket>-eval`), so real data is never
touched. Documents go through the real pipeline tasks (parse -> embed -> summarize); questions go
through the real `/ask` endpoint. Gemini and Voyage responses are cached in eval/.cache, and the
eval waits out rate limits instead of degrading to keyword search.

  docker compose run --rm worker python -m eval.rag              # ingest if needed, then ask
  ... --reingest        # wipe the eval database's documents and ingest again
  ... --only q03        # one question
  ... --no-cache / --save-baseline   (as in eval.extraction)

Scores per question: retrieval (an expected document+page is among the sources), answer (contains
every expected value), citations (cites an expected document), and for "not found" questions,
that the answer says so and cites nothing.
"""

import argparse
import json
import logging
import os
import uuid

from sqlalchemy import create_engine, make_url, text


def _isolate_environment() -> None:
    """Point the app at the eval database and bucket. Must run before importing app modules."""
    url = make_url(os.environ["DATABASE_URL"])
    if not url.database.endswith("_eval"):
        os.environ["DATABASE_URL"] = url.set(database=f"{url.database}_eval").render_as_string(hide_password=False)
    bucket = os.environ.get("S3_BUCKET", "medical-vault")
    if not bucket.endswith("-eval"):
        os.environ["S3_BUCKET"] = f"{bucket}-eval"


_isolate_environment()

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi import Depends  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.auth.clerk import get_current_user  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import base  # noqa: E402
from app.db.models import Document, Profile, User  # noqa: E402
from app.main import app  # noqa: E402
from app.pipeline import tasks  # noqa: E402
from app.pipeline.providers.embedder import get_embedder  # noqa: E402
from app.pipeline.providers.llm import get_ask_llm, get_llm  # noqa: E402
from app.routers.search import get_answer_llm, get_query_embedder  # noqa: E402
from app.search.embedding import QueryEmbedder  # noqa: E402
from app.storage.s3 import get_storage  # noqa: E402
from eval.common import DOCS, ROOT, CachingEmbedder, CachingLLM, delta, load_cases, pct, ratio, write_results  # noqa: E402
from eval.scoring import NOT_FOUND, contains_all  # noqa: E402

log = logging.getLogger("eval")
EVAL_USER = "eval:harness"


def ensure_database() -> None:
    url = make_url(get_settings().database_url)
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        if not conn.scalar(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": url.database}):
            conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    admin.dispose()
    command.upgrade(Config(str(ROOT.parent / "alembic.ini")), "head")
    get_storage().ensure_bucket()


class NoPipeline:
    """Skip the debounced profile-summary task; the eval doesn't score it."""

    def refresh_profile(self, *args, **kwargs) -> None:
        pass


def ingest(reingest: bool, llm: CachingLLM, embedder: CachingEmbedder) -> uuid.UUID:
    tasks.get_llm = lambda: llm
    tasks.get_embedder = lambda: embedder
    tasks.get_pipeline = lambda: NoPipeline()
    storage = get_storage()

    with base.SessionLocal() as db:
        user = db.scalar(select(User).where(User.clerk_id == EVAL_USER))
        if user is not None and reingest:
            storage.delete_prefix(f"{user.id}/")
            db.delete(user)
            db.commit()
            user = None
        if user is None:
            user = User(clerk_id=EVAL_USER, email="eval@example.invalid")
            db.add(user)
            db.commit()
        user_id = user.id
        done = set(db.scalars(select(Document.original_name).where(Document.user_id == user_id, Document.status == "ready")))

    cases = [c for c in load_cases() if not c["expect_failure"]]
    todo = [c for c in cases if c["file"] not in done]
    print(f"Ingest: {len(cases) - len(todo)} already ready, {len(todo)} to process", flush=True)
    for case in todo:
        with base.SessionLocal() as db:
            profile = db.scalar(select(Profile).where(Profile.user_id == user_id, Profile.name == case["profile"]))
            if profile is None:
                profile = Profile(user_id=user_id, name=case["profile"])
                db.add(profile)
                db.flush()
            for stale in db.scalars(select(Document).where(Document.user_id == user_id, Document.original_name == case["file"])):
                db.delete(stale)  # e.g. left "failed" by an earlier interrupted run
            key = f"{user_id}/{profile.id}/{uuid.uuid4()}/{case['file']}"
            storage._client.upload_file(str(DOCS / case["file"]), storage.bucket, key, ExtraArgs={"ContentType": case["mime"]})
            doc = Document(user_id=user_id, profile_id=profile.id, storage_key=key, original_name=case["file"],
                           mime=case["mime"], size_bytes=(DOCS / case["file"]).stat().st_size, status="queued")
            db.add(doc)
            db.commit()
            doc_id = str(doc.id)
        # Same functions the Celery worker runs; called directly, so failures surface here.
        tasks.parse_document(doc_id)
        tasks.embed_document(doc_id)
        tasks.summarize_document(doc_id)
        print(f"  ingested {case['file']}", flush=True)
    return user_id


def parse_sse(body: str) -> list[tuple[str, dict]]:
    events = []
    for block in body.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        events.append((fields.get("event", ""), json.loads(fields.get("data", "{}"))))
    return events


def ask(client: TestClient, question: str) -> dict:
    events = parse_sse(client.post("/ask", json={"question": question}).text)
    sources = next((d["sources"] for e, d in events if e == "sources"), [])
    answer = "".join(d["text"] for e, d in events if e == "delta")
    end = events[-1] if events else ("error", {"message": "no events"})
    return dict(sources=sources, answer=answer, status=end[0], cited=end[1].get("cited", []),
                model=end[1].get("model"), error=end[1].get("message"))


def score_question(q: dict, result: dict) -> dict:
    sources = {s["number"]: (s["document_name"], s["page_no"]) for s in result["sources"]}
    expected = {(e["file"], e["page"]) for e in q["expected_sources"]}
    expected_files = {f for f, _ in expected}
    cited = [sources[n] for n in result["cited"] if n in sources]
    row = dict(id=q["id"], kind=q["kind"], question=q["question"], answer=result["answer"], status=result["status"],
               model=result["model"], error=result["error"], cited=cited)
    if result["status"] != "done":
        row.update(answered=False)
        return row
    if q["kind"] == "not_found":
        says_so = bool(NOT_FOUND.search(result["answer"]))
        row.update(answered=True, not_found_ok=says_so and not cited)
        return row
    ok, missing = contains_all(result["answer"], q["must_include"])
    row.update(
        answered=True,
        retrieval_hit=bool(expected & set(sources.values())),
        retrieval_doc_hit=bool(expected_files & {f for f, _ in sources.values()}),
        answer_ok=ok,
        missing=missing,
        citation_ok=bool(expected_files & {f for f, _ in cited}),
    )
    return row


def summarize(rows: list[dict]) -> dict:
    facts = [r for r in rows if r["kind"] != "not_found" and r.get("answered")]
    negatives = [r for r in rows if r["kind"] == "not_found" and r.get("answered")]
    by_kind = {}
    for kind in sorted({r["kind"] for r in rows}):
        rs = [r for r in rows if r["kind"] == kind and r.get("answered")]
        by_kind[kind] = dict(
            questions=len([r for r in rows if r["kind"] == kind]),
            answer=ratio(sum(r.get("answer_ok", r.get("not_found_ok", False)) for r in rs), len(rs)),
        )
    return dict(
        questions=len(rows),
        unanswered=sum(not r.get("answered") for r in rows),
        retrieval_page=ratio(sum(r["retrieval_hit"] for r in facts), len(facts)),
        retrieval_document=ratio(sum(r["retrieval_doc_hit"] for r in facts), len(facts)),
        answer_correct=ratio(sum(r["answer_ok"] for r in facts), len(facts)),
        citation_correct=ratio(sum(r["citation_ok"] for r in facts), len(facts)),
        not_found_correct=ratio(sum(r["not_found_ok"] for r in negatives), len(negatives)),
        by_kind=by_kind,
    )


def print_report(summary: dict, rows: list[dict], baseline: dict | None) -> None:
    b = (baseline or {}).get("summary", {})
    print("\n## RAG eval\n")
    print(f"{summary['questions']} questions ({summary['unanswered']} unanswered: AI unavailable)\n")
    print("| metric | score |\n|---|---|")
    for key, label in [("retrieval_page", "Retrieval: expected page among sources"),
                       ("retrieval_document", "Retrieval: expected document among sources"),
                       ("answer_correct", "Answer contains the expected values"),
                       ("citation_correct", "Answer cites an expected document"),
                       ("not_found_correct", '"Not found" handled (says so, cites nothing)')]:
        print(f"| {label} | {pct(summary[key])}{delta(summary[key], b.get(key))} |")
    print("\n| kind | questions | correct |\n|---|---|---|")
    for kind, s in summary["by_kind"].items():
        print(f"| {kind} | {s['questions']} | {pct(s['answer'])} |")
    failures = [r for r in rows if not r.get("answered") or not r.get("answer_ok", r.get("not_found_ok", True))
                or (r["kind"] != "not_found" and not (r.get("retrieval_hit") and r.get("citation_ok")))]
    if failures:
        print("\n### Questions to look at\n")
        for r in failures:
            why = []
            if not r.get("answered"):
                why.append(f"no answer ({r['error']})")
            elif r["kind"] == "not_found":
                why.append("should have said not found" + (f", but cited {r['cited']}" if r["cited"] else ""))
            else:
                if not r["retrieval_hit"]:
                    why.append("expected page not retrieved" + ("" if r["retrieval_doc_hit"] else " (nor the document)"))
                if r["missing"]:
                    why.append(f"missing {r['missing']}")
                if not r["citation_ok"]:
                    why.append(f"cited {r['cited'] or 'nothing'}")
            answer = " ".join(r["answer"].split())[:160]
            print(f"- **{r['id']}** {r['question']}: {'; '.join(why)}\n  > {answer}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reingest", action="store_true")
    parser.add_argument("--only", help="question id, e.g. q03")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--save-baseline", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(message)s")

    ensure_database()
    use_cache = not args.no_cache
    llm, embedder = CachingLLM(get_llm(), use_cache), CachingEmbedder(get_embedder(), use_cache)
    user_id = ingest(args.reingest, llm, embedder)

    ask_llm = CachingLLM(get_ask_llm(), use_cache)
    query_embedder = QueryEmbedder(embedder)

    def eval_user(db=Depends(base.get_db)):
        return db.get(User, user_id)

    app.dependency_overrides[get_current_user] = eval_user
    app.dependency_overrides[get_query_embedder] = lambda: query_embedder
    app.dependency_overrides[get_answer_llm] = lambda: ask_llm
    client = TestClient(app)

    questions = json.loads((ROOT / "questions.json").read_text())
    rows = []
    for q in questions:
        if args.only and q["id"] != args.only:
            continue
        row = score_question(q, ask(client, q["question"]))
        rows.append(row)
        verdict = "—" if not row.get("answered") else ("ok" if row.get("answer_ok", row.get("not_found_ok")) else "WRONG")
        print(f"{q['id']} {verdict:5} {q['question']}", flush=True)

    summary = summarize(rows)
    payload = dict(summary=summary, questions=rows,
                   llm=dict(calls=llm.calls + ask_llm.calls, cache_hits=llm.hits + ask_llm.hits),
                   voyage=dict(calls=embedder.calls, cache_hits=embedder.hits))
    baseline = write_results("rag", payload, args.save_baseline and not args.only)
    print_report(summary, rows, baseline)
    print(f"\nGemini calls: {payload['llm']['calls']} (cache hits {payload['llm']['cache_hits']}), "
          f"Voyage calls: {embedder.calls} (cache hits {embedder.hits}). Full results: eval/results/rag-latest.json")


if __name__ == "__main__":
    main()
