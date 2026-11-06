"""Extraction eval: parse -> Gemini analysis -> grounding/normalization, scored against answer keys.

Runs the pipeline's own code paths (parser, prompt, `prepare_facts`), not the API, so it needs no
database or embeddings. Gemini responses are cached in eval/.cache: re-runs are free unless the
prompt, schema or model list changes.

  docker compose run --rm worker python -m eval.extraction            # all 30 documents
  docker compose run --rm worker python -m eval.extraction --only lab_scanned
  ... --no-cache          # force fresh Gemini calls
  ... --save-baseline     # make this run the baseline that later runs are compared against
"""

import argparse
import logging
import time
from collections import defaultdict

from app.config import get_settings
from app.health.facts import prepare_facts
from app.pipeline import analysis
from app.pipeline.providers.llm import get_llm
from app.pipeline.providers import parser as parser_module
from app.pipeline.providers.parser import parse_file
from eval.common import DOCS, CachingLLM, delta, load_cases, pct, ratio, write_results
from eval.scoring import same_text, score_names, score_observations


def run_case(case: dict, llm: CachingLLM) -> dict:
    exp = case["expected"]
    started = time.time()
    pages = parse_file(DOCS / case["file"], case["mime"])
    parse_seconds = round(time.time() - started, 1)
    has_text = any(p.text.strip() for p in pages)
    row = dict(file=case["file"], category=case["category"], parse_seconds=parse_seconds,
               ocr_confidence=min((p.ocr_confidence for p in pages if p.ocr_confidence is not None), default=None),
               engines=sorted({p.engine for p in pages}))

    if case["expect_failure"] or not has_text:
        # The pipeline refuses documents with no readable text ("No readable text found").
        row.update(expected_failure=case["expect_failure"], refused=not has_text, ok=case["expect_failure"] == (not has_text))
        if not case["expect_failure"]:
            # A readable document that was refused: every expected fact counts as missed.
            obs = score_observations(exp["observations"], [])
            row.update(
                doc_type_ok=False, doc_date_ok=False, provider_ok=False, patient_ok=False, mismatch_flag_ok=False,
                got=dict(doc_type=None, doc_date=None, provider=None, patient=None),
                obs=dict(expected=obs.expected, extracted=0, matched=0, page_correct=0, wrong_value=[], wrong_date=[],
                         missed=obs.missed, extra=[]),
                dropped_by_grounding=0, rejected_by_normalization=[],
                meds=dict(matched=0, expected=len(exp["medications"]), extracted=0),
                conds=dict(matched=0, expected=len(exp["conditions"]), extracted=0),
            )
        return row

    page_list = [(p.page_no, p.text) for p in pages]
    prompt = analysis.build_prompt(case["file"], page_list, get_settings().llm_max_input_chars)
    result, model = llm.generate_json(system=analysis.SYSTEM_PROMPT, prompt=prompt, schema=analysis.DocumentAnalysis)
    doc_date = analysis.parse_date(result.doc_date)
    vision_pages = frozenset(p.page_no for p in pages if p.engine == "gemini-vision")
    facts = prepare_facts(result, dict(page_list), doc_date, vision_pages=vision_pages)

    obs = score_observations(exp["observations"], facts.observations)
    meds = score_names(exp["medications"], [m["name"] for m in facts.medications])
    conds = score_names(exp["conditions"], [c["name"] for c in facts.conditions])
    flagged = analysis.name_mismatch(case["profile"], result.patient_name) is not None
    row.update(
        model=model,
        doc_type_ok=result.doc_type == exp["doc_type"],
        doc_date_ok=(doc_date.isoformat() if doc_date else None) == exp["doc_date"],
        provider_ok=same_text(exp["provider"], result.provider),
        patient_ok=same_text(exp["patient_name"], result.patient_name),
        mismatch_flag_ok=flagged == exp["name_mismatch"],
        got=dict(doc_type=result.doc_type, doc_date=result.doc_date, provider=result.provider, patient=result.patient_name),
        obs=dict(expected=obs.expected, extracted=obs.extracted, matched=obs.matched, page_correct=obs.page_correct,
                 wrong_value=obs.wrong_value, wrong_date=obs.wrong_date, missed=obs.missed,
                 extra=[{k: o[k] for k in ("code", "value_num", "observed_at", "page_no")} for o in obs.extra]),
        dropped_by_grounding=facts.dropped,
        # Review flags: flagged_correct are false alarms (a click for the user), flagged_wrong are catches.
        flagged=sum(not o["verified"] for o in facts.observations),
        flagged_wrong=sum(not o.get("verified", True) for o in obs.extra),
        flagged_notes=sorted({o["review_note"] for o in facts.observations if o["review_note"]}),
        rejected_by_normalization=facts.rejected,
        meds=dict(matched=meds[0], expected=meds[1], extracted=meds[2]),
        conds=dict(matched=conds[0], expected=conds[1], extracted=conds[2]),
    )
    return row


def summarize(rows: list[dict]) -> dict:
    def agg(rs):
        scored = [r for r in rs if "obs" in r]
        o = defaultdict(int)
        for r in scored:
            for k in ("expected", "extracted", "matched", "page_correct"):
                o[k] += r["obs"][k]
            o["wrong_value"] += len(r["obs"]["wrong_value"])
            o["wrong_date"] += len(r["obs"]["wrong_date"])
            o["flagged"] += r.get("flagged", 0)
            o["flagged_wrong"] += r.get("flagged_wrong", 0)
        precision, recall = ratio(o["matched"], o["extracted"]), ratio(o["matched"], o["expected"])
        f1 = 2 * precision * recall / (precision + recall) if precision and recall else None
        n = len(scored)
        return dict(
            documents=len(rs),
            values_expected=o["expected"],
            precision=precision, recall=recall, f1=f1,
            page_accuracy=ratio(o["page_correct"], o["matched"]),
            wrong_value=o["wrong_value"], wrong_date=o["wrong_date"],
            flagged=o["flagged"], flagged_wrong=o["flagged_wrong"],
            doc_type=ratio(sum(r["doc_type_ok"] for r in scored), n),
            doc_date=ratio(sum(r["doc_date_ok"] for r in scored), n),
            provider=ratio(sum(r["provider_ok"] for r in scored), n),
            patient=ratio(sum(r["patient_ok"] for r in scored), n),
            medications_recall=ratio(sum(r["meds"]["matched"] for r in scored), sum(r["meds"]["expected"] for r in scored)),
            conditions_recall=ratio(sum(r["conds"]["matched"] for r in scored), sum(r["conds"]["expected"] for r in scored)),
            unreadable_refused=ratio(sum(r["refused"] for r in rs if r.get("expected_failure")),
                                     sum(bool(r.get("expected_failure")) for r in rs)),
            wrongly_refused=sum(bool(r.get("refused")) and not r.get("expected_failure") for r in rs),
            mismatch_flag=ratio(sum(r["mismatch_flag_ok"] for r in scored), n),
        )

    by_category = defaultdict(list)
    for r in rows:
        by_category[r["category"]].append(r)
    return dict(overall=agg(rows), by_category={c: agg(rs) for c, rs in by_category.items()})


def print_report(summary: dict, rows: list[dict], baseline: dict | None) -> None:
    base = (baseline or {}).get("summary", {})
    o, bo = summary["overall"], base.get("overall", {})
    print("\n## Extraction eval\n")
    print(f"{o['documents']} documents, {o['values_expected']} expected values\n")
    print("| metric | score |\n|---|---|")
    for key, label in [("precision", "Values: precision"), ("recall", "Values: recall"), ("f1", "Values: F1"),
                       ("page_accuracy", "Values: cited page correct"), ("doc_type", "Document type"),
                       ("doc_date", "Document date"), ("provider", "Provider"), ("patient", "Patient name"),
                       ("mismatch_flag", "Name-mismatch flag"), ("medications_recall", "Medications recall"),
                       ("conditions_recall", "Conditions recall"), ("unreadable_refused", "Unreadable docs refused")]:
        print(f"| {label} | {pct(o[key])}{delta(o[key], bo.get(key))} |")
    print(f"| Wrong value (right test + date) | {o['wrong_value']} |\n| Wrong date (right test + value) | {o['wrong_date']} |")
    print(f"| Readable docs refused as unreadable | {o['wrongly_refused']} |")
    print(f"| Values flagged for review (wrong ones caught) | {o['flagged']} ({o['flagged_wrong']}) |")

    print("\n| category | docs | precision | recall | page | doc date | meds | conditions |\n|---|---|---|---|---|---|---|---|")
    for cat, s in summary["by_category"].items():
        b = base.get("by_category", {}).get(cat, {})
        print(f"| {cat} | {s['documents']} | {pct(s['precision'])}{delta(s['precision'], b.get('precision'))} "
              f"| {pct(s['recall'])}{delta(s['recall'], b.get('recall'))} | {pct(s['page_accuracy'])} "
              f"| {pct(s['doc_date'])} | {pct(s['medications_recall'])} | {pct(s['conditions_recall'])} |")

    problems = [r for r in rows if "obs" in r and not r.get("refused") and (r["obs"]["missed"] or r["obs"]["wrong_value"] or r["obs"]["wrong_date"]
                                                    or r["obs"]["extra"] or r["rejected_by_normalization"]
                                                    or not r["doc_date_ok"] or not r["doc_type_ok"])]
    if problems:
        print("\n### Problems by document\n")
        for r in problems:
            issues = []
            if not r["doc_type_ok"]:
                issues.append(f"type={r['got']['doc_type']}")
            if not r["doc_date_ok"]:
                issues.append(f"date={r['got']['doc_date']}")
            issues += [f"missed {m['code']} {m['value']:g}" for m in r["obs"]["missed"]]
            issues += [f"{m['code']} {m['value']:g} read as {m['got']:g}" for m in r["obs"]["wrong_value"]]
            issues += [f"{m['code']} dated {m['got']} (want {m['date']})" for m in r["obs"]["wrong_date"]]
            issues += [f"extra {m['code']} {m['value_num']:g}" for m in r["obs"]["extra"]]
            issues += [f"rejected {x['code']} {x['value']} {x['unit']!r} (normalization)" for x in r["rejected_by_normalization"]]
            print(f"- **{r['file']}**: " + "; ".join(issues))
    for r in rows:
        if "refused" in r and not r["ok"]:
            print(f"- **{r['file']}**: " + ("refused as unreadable" if r["refused"] else "should have been refused"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", help="file substring or category name")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--save-baseline", action="store_true")
    parser.add_argument("--no-vision", action="store_true", help="disable the Gemini vision fallback for low-confidence OCR")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(message)s")

    llm = CachingLLM(get_llm(), enabled=not args.no_cache)
    # Route the parser's vision fallback through the same cache (or switch it off to compare).
    parser_module.get_vision_transcriber = (lambda: None) if args.no_vision else (lambda: llm.transcribe_image)
    rows = []
    for case in load_cases(args.only):
        row = run_case(case, llm)
        rows.append(row)
        status = "ok" if row.get("ok", True) else "FAIL"
        recall = pct(ratio(row["obs"]["matched"], row["obs"]["expected"])) if "obs" in row else status
        vision = " +vision" if "gemini-vision" in row.get("engines", []) else ""
        print(f"{case['file']:38} parse {row['parse_seconds']:>5}s  values {recall:>4}  {row.get('model', '')}{vision}", flush=True)

    summary = summarize(rows)
    baseline = write_results("extraction", dict(summary=summary, documents=rows,
                                                 llm=dict(calls=llm.calls, cache_hits=llm.hits)), args.save_baseline)
    print_report(summary, rows, baseline)
    print(f"\nGemini calls: {llm.calls} (cache hits: {llm.hits}). Full results: eval/results/extraction-latest.json")


if __name__ == "__main__":
    main()
