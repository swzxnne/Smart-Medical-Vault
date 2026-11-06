import uuid
from datetime import date

import pytest
from sqlalchemy import select

from app.db.models import Condition, Document, DocumentPage, Medication, Observation, Profile, Summary, User
from app.health.facts import save_document_facts
from app.health.normalize import normalize, parse_number, parse_reference_range, value_in_text
from app.pipeline import tasks
from app.pipeline.analysis import DocumentAnalysis, ExtractedCondition, ExtractedMedication, ExtractedObservation
from app.pipeline.enqueue import PIPELINE_VERSION
from app.search.metric_query import detect_dates, detect_metrics, mentioned_profiles
from tests.conftest import ALICE, BOB, TestingSession

# ---------- normalization ----------


def one(code, value, unit=None, ref=None, name="x"):
    [n] = normalize(code, name, value, unit, ref)
    return n


def test_blood_pressure_split():
    sys_, dia = normalize("blood_pressure", "BP", "138/88", "mmHg", None)
    assert (sys_.code, sys_.value_num, dia.code, dia.value_num) == ("bp_systolic", 138, "bp_diastolic", 88)
    assert normalize("blood_pressure", "BP", "138", "mmHg", None) == []


def test_unit_conversion_and_ranges():
    glucose = one("glucose_fasting", "6.5", "mmol/L", "3.9 - 5.5")
    assert glucose.unit == "mg/dL" and round(glucose.value_num) == 117
    assert (round(glucose.ref_low), round(glucose.ref_high)) == (70, 99)
    assert glucose.flag == "high"
    assert one("weight", "150", "lbs").value_num == pytest.approx(68.04, abs=0.01)
    assert one("hba1c", "6.4", "%", "4.0 - 5.6").flag == "high"
    assert one("hdl", "44", "mg/dL", "> 40").flag == "normal"
    assert one("cholesterol_total", "212", "MG/DL", "< 200").flag == "high"
    assert one("tsh", "2.8", "µIU/mL").unit == "µIU/mL"
    assert one("tsh", "7.06", "ulU/mL").value_num == 7.06  # OCR read "µIU" as "ulU"
    assert one("tsh", "7.06", "mlU/L").value_num == 7.06


def test_misreads_are_dropped():
    assert normalize("glucose_fasting", "x", "118", "furlongs", None) == []  # unknown unit
    assert normalize("bp_systolic", "x", "1380", "mmHg", None) == []  # implausible
    assert normalize("hba1c", "x", "pending", "%", None) == []  # not a number


def test_other_metric_kept_as_printed():
    n = one("other", "positive", None, None, name="Dengue NS1")
    assert (n.code, n.value_num, n.value_text, n.name) == ("other", None, "positive", "Dengue NS1")


@pytest.mark.parametrize("text,low,high", [
    ("70 - 100", 70, 100), ("70-100 mg/dL", 70, 100), ("< 200", None, 200), ("<=5.6", None, 5.6),
    ("> 40", 40, None), ("up to 150", None, 150), ("4.0 to 5.6", 4.0, 5.6),
])
def test_reference_ranges(text, low, high):
    ref = parse_reference_range(text)
    assert (ref.low, ref.high) == (low, high)


def test_helpers():
    assert parse_number("2,50,000") == 250000
    assert parse_number("1,5") == 1.5
    assert value_in_text("138/88", "Blood pressure 138 / 88 mmHg")
    assert value_in_text("6.4", "| HbA1c | 6.4 | % |")
    assert not value_in_text("7.2", "| HbA1c | 6.4 | % |")


# ---------- storing facts ----------


PAGES = {
    1: "Vitals: Blood pressure 138/88 mmHg\n| HbA1c | 6.4 | % | 4.0 - 5.6 |",
    2: "| TSH | 2.8 | uIU/mL | 0.4 - 4.0 |\nImpression: Prediabetes. Rx: Metformin 500 mg",
}


def obs(metric, value, unit=None, ref=None, page=1, when=None):
    return ExtractedObservation(metric=metric, name=metric, value=value, unit=unit, reference_range=ref, date=when, page=page)


def analysis_result(observations=(), medications=(), conditions=()):
    return DocumentAnalysis(
        doc_type="lab_report", doc_date="2024-03-13", provider=None, patient_name=None, summary_md="s",
        observations=list(observations), medications=list(medications), conditions=list(conditions),
    )


@pytest.fixture
def doc():
    with TestingSession() as db:
        user = User(clerk_id="user_alice")
        db.add(user)
        db.flush()
        profile = Profile(user_id=user.id, name="Asha Verma")
        db.add(profile)
        db.flush()
        d = Document(user_id=user.id, profile_id=profile.id, storage_key=f"{user.id}/{profile.id}/k/r.pdf",
                     original_name="r.pdf", mime="application/pdf", size_bytes=1, status="ready",
                     doc_date=date(2024, 3, 13), doc_type="lab_report", pipeline_version=PIPELINE_VERSION)
        db.add(d)
        db.commit()
        return d


def save(document, result):
    with TestingSession() as db:
        row = db.get(Document, document.id)
        stats = save_document_facts(db, row, result, PAGES)
        db.commit()
        return stats


def stored(document):
    with TestingSession() as db:
        return db.scalars(select(Observation).where(Observation.document_id == document.id).order_by(Observation.code)).all()


def test_facts_are_grounded_normalized_and_paged(doc):
    stats = save(doc, analysis_result(
        observations=[
            obs("blood_pressure", "138/88", "mmHg"),
            obs("hba1c", "6.4", "%", "4.0 - 5.6"),
            obs("tsh", "2.8", "uIU/mL", "0.4 - 4.0", page=1),  # LLM said page 1; it's on page 2
            obs("ldl", "138", "mg/dL"),  # the number is on the page (in the BP), so it passes grounding
            obs("hdl", "44", "mg/dL"),  # not on any page: dropped
        ],
        medications=[ExtractedMedication(name="Metformin 500 mg", dose=None, frequency=None, duration=None, page=2),
                     ExtractedMedication(name="Insulin glargine", dose=None, frequency=None, duration=None, page=2)],
        conditions=[ExtractedCondition(name="Prediabetes", kind="diagnosis", page=2),
                    ExtractedCondition(name="Asthma", kind="diagnosis", page=2)],
    ))
    rows = {o.code: o for o in stored(doc)}
    assert set(rows) == {"bp_systolic", "bp_diastolic", "hba1c", "tsh", "ldl"}
    assert rows["tsh"].page_no == 2
    assert rows["hba1c"].flag == "high" and rows["hba1c"].observed_at == date(2024, 3, 13)
    assert stats["dropped"] == 3  # hdl, insulin, asthma
    with TestingSession() as db:
        assert [m.name for m in db.scalars(select(Medication))] == ["Metformin 500 mg"]
        assert [c.name for c in db.scalars(select(Condition))] == ["Prediabetes"]


def test_rerun_keeps_user_corrections_and_removals(doc):
    result = analysis_result(observations=[obs("hba1c", "6.4", "%"), obs("blood_pressure", "138/88")])
    save(doc, result)
    with TestingSession() as db:
        hba1c = db.scalar(select(Observation).where(Observation.code == "hba1c"))
        hba1c.value_num, hba1c.source = 6.3, "corrected"
        systolic = db.scalar(select(Observation).where(Observation.code == "bp_systolic"))
        systolic.hidden = True
        db.commit()

    save(doc, result)  # e.g. the user clicked Reprocess
    rows = stored(doc)
    assert [(o.code, o.value_num, o.source, o.hidden) for o in rows] == [
        ("bp_diastolic", 88, "ai", False),
        ("bp_systolic", 138, "ai", True),
        ("hba1c", 6.3, "corrected", False),
    ]


def test_same_value_on_another_page_is_a_separate_reading(doc):
    from app.health.facts import prepare_facts

    pages = {1: "On admission, blood pressure was 146/90 mmHg. BP 146/90.", 2: "At discharge, blood pressure was 145/90 mmHg."}
    result = analysis_result(observations=[
        obs("blood_pressure", "146/90", "mmHg", page=1),
        obs("blood_pressure", "146/90", "mmHg", page=1),  # repeated on the same page: duplicate
        obs("blood_pressure", "145/90", "mmHg", page=2),
    ])
    facts = prepare_facts(result, pages, date(2025, 4, 2))
    assert sorted((o["code"], o["value_num"], o["page_no"]) for o in facts.observations) == [
        ("bp_diastolic", 90, 1), ("bp_diastolic", 90, 2), ("bp_systolic", 145, 2), ("bp_systolic", 146, 1),
    ]


def test_value_dated_on_report_overrides_document_date(doc):
    save(doc, analysis_result(observations=[obs("hba1c", "6.4", "%", when="2023-12-01"), obs("tsh", "2.8", "uIU/mL", page=2, when="2999-01-01")]))
    dates = {o.code: o.observed_at for o in stored(doc)}
    assert dates == {"hba1c": date(2023, 12, 1), "tsh": date(2024, 3, 13)}  # future date rejected


# ---------- profile summary task ----------


class FakeRedis:
    def __init__(self):
        self.data = {}

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value, ex=None):
        self.data[key] = value

    def delete(self, key):
        self.data.pop(key, None)


class SummaryLLM:
    model = "fake"

    def __init__(self):
        self.prompts = []

    def generate_json(self, *, system, prompt, schema):
        self.prompts.append(prompt)
        return schema(summary_md="### At a glance\nPrediabetes [D1]"), "fake-model"


@pytest.fixture
def summary_env(monkeypatch):
    fake_redis, llm = FakeRedis(), SummaryLLM()
    monkeypatch.setattr(tasks, "redis_client", lambda: fake_redis)
    monkeypatch.setattr(tasks, "get_llm", lambda: llm)
    return fake_redis, llm


def test_profile_summary(doc, summary_env):
    fake_redis, llm = summary_env
    save(doc, analysis_result(observations=[obs("hba1c", "6.4", "%", "4.0 - 5.6")],
                              conditions=[ExtractedCondition(name="Prediabetes", kind="diagnosis", page=2)]))
    fake_redis.set(f"profile-summary:{doc.profile_id}", "t2")

    tasks.summarize_profile(str(doc.profile_id), "t1")  # superseded token: skipped
    assert llm.prompts == []

    tasks.summarize_profile(str(doc.profile_id), "t2")
    prompt = llm.prompts[0]
    assert "[D1] r.pdf · lab report · 2024-03-13" in prompt
    assert "- HbA1c: 6.4 % (ref 4.0 - 5.6) [high] on 2024-03-13 [D1]" in prompt
    assert "- Prediabetes (diagnosis) [D1]" in prompt
    with TestingSession() as db:
        summary = db.scalar(select(Summary).where(Summary.profile_id == doc.profile_id))
        assert summary.model == "fake-model"
        assert summary.citations == [{"number": 1, "document_id": str(doc.id), "document_name": "r.pdf", "doc_date": "2024-03-13"}]


def test_profile_summary_waits_for_busy_documents_and_clears_when_empty(doc, summary_env):
    _, llm = summary_env
    with TestingSession() as db:
        db.get(Document, doc.id).status = "parsing"
        db.add(Summary(profile_id=doc.profile_id, content_md="old", model="m"))
        db.commit()
    tasks.summarize_profile(str(doc.profile_id))
    assert llm.prompts == []

    with TestingSession() as db:
        db.get(Document, doc.id).status = "failed"
        db.commit()
    tasks.summarize_profile(str(doc.profile_id))  # no ready documents left
    with TestingSession() as db:
        assert db.scalar(select(Summary).where(Summary.profile_id == doc.profile_id)) is None


# ---------- API ----------


@pytest.fixture
def seeded(client, doc):
    save(doc, analysis_result(
        observations=[obs("blood_pressure", "138/88", "mmHg"), obs("hba1c", "6.4", "%", "4.0 - 5.6")],
        medications=[ExtractedMedication(name="Metformin 500 mg", dose="1 tab", frequency="night", duration=None, page=2)],
        conditions=[ExtractedCondition(name="Prediabetes", kind="diagnosis", page=2)],
    ))
    return doc


def test_health_overview_and_series(client, seeded):
    pid = seeded.profile_id
    client.post(f"/profiles/{pid}/observations", json={"code": "hba1c", "value": 6.1, "observed_at": "2024-06-01"}, headers=ALICE)

    overview = client.get(f"/profiles/{pid}/health", headers=ALICE).json()
    metrics = {m["code"]: m for m in overview["metrics"]}
    assert list(metrics) == ["bp_systolic", "bp_diastolic", "hba1c"]  # catalog order
    assert metrics["hba1c"]["latest"]["value_num"] == 6.1 and metrics["hba1c"]["latest"]["source"] == "manual"
    assert metrics["hba1c"]["previous"]["value_num"] == 6.4
    assert metrics["hba1c"]["previous"]["document_name"] == "r.pdf"
    assert [m["name"] for m in overview["medications"]] == ["Metformin 500 mg"]
    assert [c["name"] for c in overview["conditions"]] == ["Prediabetes"]
    assert overview["summary"] is None and overview["outdated_documents"] == 0

    series = client.get(f"/profiles/{pid}/observations", params={"group": "blood_pressure"}, headers=ALICE).json()
    assert sorted(o["code"] for o in series) == ["bp_diastolic", "bp_systolic"]
    ranged = client.get(f"/profiles/{pid}/observations", params={"code": "hba1c", "from": "2024-04-01"}, headers=ALICE).json()
    assert [o["value_num"] for o in ranged] == [6.1]

    facts = client.get(f"/documents/{seeded.id}/facts", headers=ALICE).json()
    assert len(facts["observations"]) == 3 and facts["medications"][0]["dose"] == "1 tab"


def test_manual_entry_validation(client, seeded):
    pid = seeded.profile_id
    url = f"/profiles/{pid}/observations"
    ok = client.post(url, json={"code": "weight", "value": 150, "unit": "lbs", "observed_at": "2024-01-01"}, headers=ALICE)
    assert ok.status_code == 201 and ok.json()["unit"] == "kg" and round(ok.json()["value_num"]) == 68
    assert client.post(url, json={"code": "mood", "value": 1, "observed_at": "2024-01-01"}, headers=ALICE).status_code == 422
    assert client.post(url, json={"code": "bp_systolic", "value": 900, "observed_at": "2024-01-01"}, headers=ALICE).status_code == 422
    assert client.post(url, json={"code": "weight", "value": 60, "unit": "stone", "observed_at": "2024-01-01"}, headers=ALICE).status_code == 422
    assert client.post(url, json={"code": "weight", "value": 60, "observed_at": "2999-01-01"}, headers=ALICE).status_code == 422


def test_correct_and_remove(client, seeded):
    rows = {o["code"]: o for o in client.get(f"/documents/{seeded.id}/facts", headers=ALICE).json()["observations"]}
    fixed = client.patch(f"/observations/{rows['hba1c']['id']}", json={"value": 5.4}, headers=ALICE).json()
    assert (fixed["value_num"], fixed["source"], fixed["flag"]) == (5.4, "corrected", "normal")

    assert client.delete(f"/observations/{rows['bp_systolic']['id']}", headers=ALICE).status_code == 204
    remaining = client.get(f"/documents/{seeded.id}/facts", headers=ALICE).json()["observations"]
    assert "bp_systolic" not in {o["code"] for o in remaining}
    assert client.patch(f"/observations/{rows['bp_systolic']['id']}", json={"value": 1}, headers=ALICE).status_code == 404
    with TestingSession() as db:
        assert db.get(Observation, uuid.UUID(rows["bp_systolic"]["id"])).hidden is True

    manual = client.post(f"/profiles/{seeded.profile_id}/observations", json={"code": "pulse", "value": 70, "observed_at": "2024-01-01"}, headers=ALICE).json()
    client.delete(f"/observations/{manual['id']}", headers=ALICE)
    with TestingSession() as db:
        assert db.get(Observation, uuid.UUID(manual["id"])) is None


def test_health_isolation(client, seeded):
    pid = seeded.profile_id
    obs_id = client.get(f"/documents/{seeded.id}/facts", headers=ALICE).json()["observations"][0]["id"]
    client.get("/profiles", headers=BOB)  # create Bob
    assert client.get(f"/profiles/{pid}/health", headers=BOB).status_code == 404
    assert client.get(f"/profiles/{pid}/observations", headers=BOB).status_code == 404
    assert client.post(f"/profiles/{pid}/observations", json={"code": "pulse", "value": 70, "observed_at": "2024-01-01"}, headers=BOB).status_code == 404
    assert client.patch(f"/observations/{obs_id}", json={"value": 1}, headers=BOB).status_code == 404
    assert client.delete(f"/observations/{obs_id}", headers=BOB).status_code == 404
    assert client.get(f"/documents/{seeded.id}/facts", headers=BOB).status_code == 404
    assert client.post(f"/profiles/{pid}/summary/refresh", headers=BOB).status_code == 404


def test_reprocess_outdated_picks_cheapest_stage(client, seeded, pipeline):
    with TestingSession() as db:
        ready = db.get(Document, seeded.id)
        ready.pipeline_version = 1
        db.add(DocumentPage(document_id=ready.id, page_no=1, text="t", engine="docling"))
        failed = Document(user_id=ready.user_id, profile_id=ready.profile_id, storage_key="k2", original_name="f.pdf",
                          mime="application/pdf", size_bytes=1, status="failed")
        db.add(failed)
        db.commit()
        failed_id = failed.id

        scan = Document(user_id=ready.user_id, profile_id=ready.profile_id, storage_key="k3", original_name="s.jpg",
                        mime="image/jpeg", size_bytes=1, status="ready", pipeline_version=1)
        db.add(scan)
        db.flush()
        db.add(DocumentPage(document_id=scan.id, page_no=1, text="t", engine="tesseract", ocr_confidence=0.6))
        db.commit()
        scan_id = scan.id

    assert client.get(f"/profiles/{seeded.profile_id}/health", headers=ALICE).json()["outdated_documents"] == 3
    assert client.post("/documents/reprocess-outdated", headers=ALICE).json() == {"queued": 3}
    # Text + embeddings fine: summarize only. Failed, or low-confidence OCR (now eligible for vision): full redo.
    assert dict(pipeline.stages) == {seeded.id: "pipeline.summarize", failed_id: "pipeline.parse", scan_id: "pipeline.parse"}
    assert client.post("/documents/reprocess-outdated", headers=ALICE).json() == {"queued": 0}  # now queued


def test_refresh_and_delete_trigger_profile_summary(client, seeded, pipeline):
    assert client.post(f"/profiles/{seeded.profile_id}/summary/refresh", headers=ALICE).status_code == 202
    client.delete(f"/documents/{seeded.id}", headers=ALICE)
    assert pipeline.refreshed == [seeded.profile_id, seeded.profile_id]


# ---------- question parsing ----------


def test_detect_metrics():
    assert detect_metrics("What was the BP in 2024?") == ["bp_systolic", "bp_diastolic"]
    assert detect_metrics("how is my blood sugar") == ["glucose_fasting", "glucose_random", "glucose_pp", "hba1c"]
    assert detect_metrics("LDL and HDL trend") == ["ldl", "hdl"]
    assert detect_metrics("which doctor did I see?") == []
    assert detect_metrics("hbA1c") == ["hba1c"]


def test_detect_dates():
    today = date(2026, 9, 29)
    assert detect_dates("BP in 2024", today) == (date(2024, 1, 1), date(2024, 12, 31))
    assert detect_dates("sugar since 2023", today) == (date(2023, 1, 1), None)
    assert detect_dates("before 2024", today) == (None, date(2023, 12, 31))
    assert detect_dates("between 2025 and 2022", today) == (date(2022, 1, 1), date(2025, 12, 31))
    assert detect_dates("weight last year", today) == (date(2025, 1, 1), date(2025, 12, 31))
    assert detect_dates("bp over time", today) == (None, None)


def test_mentioned_profiles():
    profiles = [(1, "Asha Verma"), (2, "Ravi"), (3, "Al")]
    assert mentioned_profiles("What was Ravi's sugar?", profiles) == [2]
    assert mentioned_profiles("asha verma bp", profiles) == [1]
    assert mentioned_profiles("normal values", profiles) == []  # "Al" is too short to match inside words


# ---------- review flags ----------


def test_label_check_flags_borrowed_values(doc):
    from app.health.facts import NOTE_LABEL, prepare_facts

    pages = {1: "Vitals: Blood pressure 138/88 mmHg\n| LDL Cholesterol | 132 | mg/dL | < 100 |"}
    result = analysis_result(observations=[
        obs("ldl", "132", "mg/dL"),  # on its own row: fine
        obs("hdl", "138", "mg/dL"),  # the number only appears in the BP line: flag it
        obs("blood_pressure", "138/88", "mmHg"),
    ])
    facts = {o["code"]: o for o in prepare_facts(result, pages, date(2024, 3, 13)).observations}
    assert facts["ldl"]["verified"] and facts["bp_systolic"]["verified"]
    assert (facts["hdl"]["verified"], facts["hdl"]["review_note"]) == (False, NOTE_LABEL)


def test_values_from_ai_reread_pages_need_review(doc):
    from app.health.facts import NOTE_VISION, prepare_facts

    pages = {1: "| HbA1c | 6.4 | % |", 2: "| TSH | 2.8 | uIU/mL |"}
    result = analysis_result(observations=[obs("hba1c", "6.4", "%"), obs("tsh", "2.8", "uIU/mL", page=2)])
    facts = {o["code"]: o for o in prepare_facts(result, pages, date(2024, 3, 13), vision_pages=frozenset({2})).observations}
    assert facts["hba1c"]["verified"]
    assert (facts["tsh"]["verified"], facts["tsh"]["review_note"]) == (False, NOTE_VISION)


def test_label_words():
    from app.health.normalize import label_near_value, label_words

    assert label_near_value("136/86", label_words("BP", "blood_pressure"), "Stage 1 hypertension (BP 136/86 mmHg)")
    assert label_near_value("146/90", label_words("Blood pressure", "blood_pressure"), "On admission, blood pressure\nwas 146/90 mmHg")
    assert not label_near_value("118", label_words("Fasting glucose", "glucose_fasting"), "Rule out diabetes. Pulse 118")
    # Docling sometimes splits a sentence with a blank line.
    assert label_near_value("2.46", label_words("Serum creatinine", "creatinine"), "serum creatinine was\n\n2.46 mg/dL.")


def test_confirm_and_correct_clear_review(client, doc, pipeline):
    with TestingSession() as db:
        o = Observation(user_id=doc.user_id, profile_id=doc.profile_id, document_id=doc.id, page_no=1, code="hdl",
                        name="HDL", value_num=138, value_text="138", unit="mg/dL", observed_at=date(2024, 3, 13),
                        source="ai", verified=False, review_note="Test name not found next to this value on the page")
        db.add(o)
        db.commit()
        oid = o.id
    overview = client.get(f"/profiles/{doc.profile_id}/health", headers=ALICE).json()
    assert overview["unverified_count"] == 1 and overview["summary_pending"] is False

    confirmed = client.patch(f"/observations/{oid}", json={"confirm": True}, headers=ALICE).json()
    assert (confirmed["verified"], confirmed["review_note"], confirmed["source"], confirmed["value_num"]) == (True, None, "corrected", 138)
    assert pipeline.refreshed == [doc.profile_id]
    overview = client.get(f"/profiles/{doc.profile_id}/health", headers=ALICE).json()
    assert overview["unverified_count"] == 0 and overview["summary_pending"] is True


def test_manual_and_delete_refresh_summary(client, doc, pipeline):
    pid = doc.profile_id
    added = client.post(f"/profiles/{pid}/observations", json={"code": "pulse", "value": 70, "observed_at": "2024-01-01"}, headers=ALICE).json()
    assert added["verified"] is True
    client.delete(f"/observations/{added['id']}", headers=ALICE)
    assert pipeline.refreshed == [pid, pid]


# ---------- profile summary pending state ----------


def test_pending_key_cleared_after_success_but_kept_for_retries(doc, summary_env, monkeypatch):
    fake_redis, llm = summary_env
    key = f"profile-summary:{doc.profile_id}"
    save(doc, analysis_result(observations=[obs("hba1c", "6.4", "%")]))

    fake_redis.set(key, "t1")
    tasks.summarize_profile(str(doc.profile_id), "t1")
    assert key not in fake_redis.data  # done: no longer pending

    from app.pipeline.providers.errors import TransientError

    class Busy:
        model = "busy"

        def generate_json(self, **kw):
            raise TransientError("503")

    monkeypatch.setattr(tasks, "get_llm", lambda: Busy())
    fake_redis.set(key, "t2")
    with pytest.raises(TransientError):
        tasks.summarize_profile(str(doc.profile_id), "t2")
    assert fake_redis.get(key) == "t2"  # Celery's retry must still find its token
    tasks.summarize_profile.on_failure(TransientError("503"), "id", (str(doc.profile_id), "t2"), {}, None)
    assert key not in fake_redis.data  # retries exhausted: stop showing "updating"


def test_failed_document_triggers_profile_refresh(doc, pipeline, monkeypatch):
    monkeypatch.setattr(tasks, "get_pipeline", lambda: pipeline)
    tasks.mark_failed(str(doc.id), "parse: boom")
    assert pipeline.refreshed == [doc.profile_id]
    with TestingSession() as db:
        assert db.get(Document, doc.id).pipeline_version == PIPELINE_VERSION  # not offered for re-analysis
