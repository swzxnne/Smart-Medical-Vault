import json
import uuid
from datetime import date

import pytest

from app.db.models import EMBEDDING_DIM, Chunk, Document, Profile, User
from app.main import app
from app.pipeline.providers.errors import TransientError
from app.routers.search import get_answer_llm, get_query_embedder
from app.search import answer
from app.search.embedding import QueryEmbedder
from app.search.retrieval import HIGHLIGHT_START, hybrid_search, keyword_query
from tests.conftest import ALICE, BOB, TestingSession


def axis(i: int) -> list[float]:
    v = [0.0] * EMBEDDING_DIM
    v[i] = 1.0
    return v


def blend(i: int, j: int, wi: float = 0.8) -> list[float]:
    v = [0.0] * EMBEDDING_DIM
    v[i], v[j] = wi, (1 - wi**2) ** 0.5
    return v


BP = "Vitals: Blood Pressure: 146/92 mmHg, Pulse 84/min"
LIPIDS = "| Total Cholesterol | 212 | mg/dL | < 200 |"
RX = "Atorvastatin 10 mg, 1 tablet at night for 3 months"


def seed_user(db, clerk_id: str, profiles: dict[str, list[tuple[str, list[float], int]]]) -> dict:
    """profiles: name -> list of (chunk text, embedding, page). One document per profile."""
    user = User(clerk_id=clerk_id)
    db.add(user)
    db.flush()
    ids = {"user": user.id}
    for name, chunks in profiles.items():
        profile = Profile(user_id=user.id, name=name)
        db.add(profile)
        db.flush()
        doc = Document(
            user_id=user.id,
            profile_id=profile.id,
            storage_key=f"{user.id}/{profile.id}/{uuid.uuid4()}/r.pdf",
            original_name=f"{name.lower()}_report.pdf",
            mime="application/pdf",
            size_bytes=1,
            status="ready",
            doc_type="lab_report",
            doc_date=date(2024, 3, 13),
        )
        db.add(doc)
        db.flush()
        ids[name] = {"profile": profile.id, "doc": doc.id}
        for index, (text_, vector, page) in enumerate(chunks):
            db.add(Chunk(document_id=doc.id, profile_id=profile.id, user_id=user.id, page_no=page,
                         chunk_index=index, text=text_, embedding=vector))
    db.commit()
    return ids


@pytest.fixture
def data():
    with TestingSession() as db:
        alice = seed_user(db, "user_alice", {
            "Asha": [(BP, axis(0), 1), (LIPIDS, axis(1), 2)],
            "Ravi": [(RX, axis(2), 1)],
        })
        bob = seed_user(db, "user_bob", {"Bob": [(BP, axis(0), 1)]})
    return {"alice": alice, "bob": bob}


def search(db, user_id, query, vector, **kw):
    return hybrid_search(db, user_id=user_id, query=query, query_vector=vector, **kw)


# ---------- retrieval ----------


def test_keyword_query_only_passes_words():
    assert keyword_query("HbA1c & 'x' | !(drop)") == "HbA1c | x | drop"
    assert keyword_query("?!") is None


def test_hybrid_ranks_matches_from_both_methods_first(data):
    with TestingSession() as db:
        hits = search(db, data["alice"]["user"], "blood pressure", blend(0, 1))
    assert hits[0].text == BP
    assert sorted(hits[0].matched_by) == ["keyword", "semantic"]
    assert hits[0].profile_name == "Asha" and hits[0].page_no == 1
    assert HIGHLIGHT_START in hits[0].snippet


def test_semantic_only_match(data):
    with TestingSession() as db:
        hits = search(db, data["alice"]["user"], "statin medicine", axis(2))
    assert [h.text for h in hits] == [RX]
    assert hits[0].matched_by == ["semantic"]


def test_unrelated_vectors_are_cut_off(data):
    with TestingSession() as db:
        assert search(db, data["alice"]["user"], "passport renewal", axis(9)) == []


def test_scoped_to_user_and_profile(data):
    alice = data["alice"]
    with TestingSession() as db:
        hits = search(db, alice["user"], "blood pressure 146", axis(0))
        assert {h.document_id for h in hits} <= {alice["Asha"]["doc"], alice["Ravi"]["doc"]}
        bob_hits = search(db, data["bob"]["user"], "cholesterol atorvastatin", axis(1))
        assert bob_hits == []
        ravi = search(db, alice["user"], "blood pressure cholesterol atorvastatin", None, profile_id=alice["Ravi"]["profile"])
        assert [h.text for h in ravi] == [RX]


def test_keyword_only_when_no_vector(data):
    with TestingSession() as db:
        hits = search(db, data["alice"]["user"], "cholesterol", None)
    assert [h.text for h in hits] == [LIPIDS]
    assert hits[0].matched_by == ["keyword"]


def test_query_embedder_caches_and_degrades():
    class Flaky:
        calls = 0

        def embed_query(self, q):
            self.calls += 1
            if q == "boom":
                raise TransientError("429")
            return axis(0)

    inner = Flaky()
    qe = QueryEmbedder(inner)
    assert qe.embed("Blood  Pressure") == axis(0)
    assert qe.embed("blood pressure") == axis(0)
    assert inner.calls == 1
    assert qe.embed("boom") is None


# ---------- /search ----------


class FakeQueryEmbedder:
    def __init__(self, vectors):
        self.vectors = vectors

    def embed(self, query):
        return self.vectors.get(query)


def test_search_endpoint(client, data):
    app.dependency_overrides[get_query_embedder] = lambda: FakeQueryEmbedder({"blood pressure": axis(0)})
    body = client.get("/search", params={"q": "blood pressure"}, headers=ALICE).json()
    assert body["mode"] == "hybrid"
    assert body["results"][0]["snippet"].count(HIGHLIGHT_START) >= 1
    assert body["results"][0]["document_name"] == "asha_report.pdf"

    keyword = client.get("/search", params={"q": "cholesterol"}, headers=ALICE).json()
    assert keyword["mode"] == "keyword"
    assert [r["page_no"] for r in keyword["results"]] == [2]

    # Bob's search never returns Alice's chunks, and he can't filter by her profile.
    assert all(r["profile_name"] == "Bob" for r in client.get("/search", params={"q": "blood pressure"}, headers=BOB).json()["results"])
    other = client.get("/search", params={"q": "x", "profile_id": str(data["alice"]["Asha"]["profile"])}, headers=BOB)
    assert other.status_code == 404
    assert client.get("/search", params={"q": ""}, headers=ALICE).status_code == 422


# ---------- /ask ----------


class FakeStreamingLLM:
    def __init__(self, parts=None, error=None, rewrite=None, rewrite_error=None):
        self.parts = parts or []
        self.error = error
        self.prompts: list[str] = []
        self.rewrite, self.rewrite_error = rewrite, rewrite_error
        self.rewrite_prompts: list[str] = []

    def generate_json(self, *, system, prompt, schema):
        self.rewrite_prompts.append(prompt)
        if self.rewrite_error:
            raise self.rewrite_error
        return schema(question=self.rewrite), "fake-model"

    def stream_text(self, *, system, prompt):
        self.prompts.append(prompt)
        for part in self.parts:
            yield "fake-model", part
        if self.error:
            raise self.error


def parse_sse(body: str) -> list[tuple[str, dict]]:
    events = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    return events


def ask(client, question, headers=ALICE, **extra):
    resp = client.post("/ask", json={"question": question, **extra}, headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/event-stream")
    return parse_sse(resp.text)


def test_ask_streams_answer_with_valid_citations(client, data):
    llm = FakeStreamingLLM(parts=["Asha's BP was 146/92 mmHg ", "[S1] on 13 Mar 2024. See also [S9]."])
    app.dependency_overrides[get_answer_llm] = lambda: llm
    events = ask(client, "what was the blood pressure")

    kinds = [e for e, _ in events]
    assert kinds[0] == "sources" and kinds[-1] == "done" and "delta" in kinds
    sources = events[0][1]["sources"]
    assert sources[0]["number"] == 1 and sources[0]["page_no"] == 1
    assert "".join(d["text"] for e, d in events if e == "delta").startswith("Asha's BP")
    assert events[-1][1] == {"cited": [1], "model": "fake-model"}  # [S9] doesn't exist
    assert "[S1] Asha · asha_report.pdf · lab report · 2024-03-13 · page 1" in llm.prompts[0]


def test_ask_without_matches_skips_llm(client, data):
    llm = FakeStreamingLLM(parts=["should not be called"])
    app.dependency_overrides[get_answer_llm] = lambda: llm
    events = ask(client, "passport renewal")
    assert events[0][1]["sources"] == []
    assert events[1] == ("delta", {"text": answer.NOT_FOUND})
    assert llm.prompts == []


def test_ask_reports_busy_llm(client, data):
    app.dependency_overrides[get_answer_llm] = lambda: FakeStreamingLLM(error=TransientError("503"))
    events = ask(client, "blood pressure")
    assert events[-1][0] == "error"
    assert "busy" in events[-1][1]["message"]


def test_ask_without_llm_configured(client, data):
    events = ask(client, "blood pressure")
    assert events[-1] == ("error", {"message": "GEMINI_API_KEY is not set on the server."})


def test_ask_respects_profile_and_user(client, data):
    llm = FakeStreamingLLM(parts=["ok"])
    app.dependency_overrides[get_answer_llm] = lambda: llm
    events = ask(client, "blood pressure atorvastatin", profile_id=str(data["alice"]["Ravi"]["profile"]))
    assert {s["profile_name"] for s in events[0][1]["sources"]} == {"Ravi"}

    bob_events = ask(client, "blood pressure cholesterol atorvastatin", headers=BOB)
    assert {s["profile_name"] for s in bob_events[0][1]["sources"]} == {"Bob"}
    resp = client.post("/ask", json={"question": "x", "profile_id": str(data["alice"]["Asha"]["profile"])}, headers=BOB)
    assert resp.status_code == 404


def test_cited_numbers():
    assert answer.cited_numbers("a [S2] b [S1][S2] c [S0] [S7]", 3) == [2, 1]


# ---------- /ask with extracted measurements ----------


def add_observations(data):
    from app.db.models import Observation

    alice = data["alice"]
    with TestingSession() as db:
        for code, value, when, profile in [
            ("bp_systolic", 146, date(2023, 11, 5), "Asha"),
            ("bp_diastolic", 92, date(2023, 11, 5), "Asha"),
            ("bp_systolic", 138, date(2024, 3, 13), "Asha"),
            ("bp_diastolic", 88, date(2024, 3, 13), "Asha"),
            ("bp_systolic", 150, date(2024, 5, 1), "Ravi"),
        ]:
            db.add(Observation(
                user_id=alice["user"], profile_id=alice[profile]["profile"], document_id=alice[profile]["doc"],
                page_no=1, code=code, name=code, value_num=value, value_text=str(value), unit="mmHg",
                observed_at=when, source="ai",
            ))
        db.commit()


def test_ask_uses_measurements_first(client, data):
    add_observations(data)
    llm = FakeStreamingLLM(parts=["138/88 [S1]"])
    app.dependency_overrides[get_answer_llm] = lambda: llm
    events = ask(client, "What was Asha's BP in 2024?")

    payload = events[0][1]
    first = payload["sources"][0]
    assert first["kind"] == "measurements" and first["profile_name"] == "Asha"
    assert first["snippet"] == "bp_systolic: 138 mmHg on 2024-03-13\nbp_diastolic: 88 mmHg on 2024-03-13"
    assert all(s["kind"] == "passage" for s in payload["sources"][1:])
    # 2023 readings are outside the asked year; Ravi's reading is excluded because Asha was named.
    assert [(o["value_num"], o["source_number"]) for o in payload["observations"]] == [(138, 1), (88, 1)]
    assert "(measurements extracted from this page)" in llm.prompts[0]
    assert events[-1][1]["cited"] == [1]


def test_ask_measurements_without_matching_text_still_answers(client, data):
    add_observations(data)
    llm = FakeStreamingLLM(parts=["150 [S1]"])
    app.dependency_overrides[get_answer_llm] = lambda: llm
    events = ask(client, "systolic", profile_id=str(data["alice"]["Ravi"]["profile"]))
    assert [s["kind"] for s in events[0][1]["sources"]] == ["measurements"]
    assert events[-1] == ("done", {"cited": [1], "model": "fake-model"})


# ---------- follow-up questions ----------


def test_follow_up_is_rewritten_before_searching(client, data):
    add_observations(data)
    llm = FakeStreamingLLM(parts=["146/92 [S1]"], rewrite="What was Asha's BP in 2023?")
    app.dependency_overrides[get_answer_llm] = lambda: llm
    history = [{"question": "What was Asha's BP in 2024?", "answer": "138/88 mmHg on 2024-03-13 [S1]"}]
    events = ask(client, "and in 2023?", history=history)

    assert "Q: What was Asha's BP in 2024?" in llm.rewrite_prompts[0]
    payload = events[0][1]
    assert payload["standalone_question"] == "What was Asha's BP in 2023?"
    # The rewritten question drove metric detection: 2023 readings, Asha only.
    assert [(o["value_num"], o["observed_at"]) for o in payload["observations"]] == [(146, "2023-11-05"), (92, "2023-11-05")]
    assert "<earlier_conversation>" in llm.prompts[0] and "Question: and in 2023?" in llm.prompts[0]


def test_follow_up_falls_back_when_rewrite_fails(client, data):
    add_observations(data)
    llm = FakeStreamingLLM(parts=["ok"], rewrite_error=TransientError("429"))
    app.dependency_overrides[get_answer_llm] = lambda: llm
    events = ask(client, "and Ravi?", history=[{"question": "What was the systolic BP?", "answer": "..."}])
    assert events[0][1]["standalone_question"] == "What was the systolic BP? and Ravi?"
    assert {o["profile_name"] for o in events[0][1]["observations"]} == {"Ravi"}  # previous question kept the metric
    assert events[-1][0] == "done"


def test_first_question_skips_rewrite(client, data):
    llm = FakeStreamingLLM(parts=["ok"], rewrite="should not be used")
    app.dependency_overrides[get_answer_llm] = lambda: llm
    ask(client, "blood pressure")
    assert llm.rewrite_prompts == []
