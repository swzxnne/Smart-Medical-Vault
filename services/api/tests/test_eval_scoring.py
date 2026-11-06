from datetime import date

from eval.scoring import contains_all, name_matches, same_text, score_names, score_observations, value_close


def act(code, value, when="2024-03-13", page=1):
    return dict(code=code, value_num=value, observed_at=date.fromisoformat(when), page_no=page)


def exp(code, value, when="2024-03-13", page=1):
    return dict(code=code, value=value, date=when, page=page)


def test_value_tolerance():
    assert value_close(104.5, 104.49)  # mmol/L -> mg/dL rounding
    assert value_close(6.45, 6.4)  # one printed decimal
    assert not value_close(6.6, 6.4)
    assert not value_close(130, 118)


def test_observation_matching_and_diagnosis():
    expected = [exp("hba1c", 6.4), exp("ldl", 138), exp("tsh", 2.8, page=2), exp("hdl", 44), exp("bp_systolic", 138)]
    actual = [
        act("hba1c", 6.4),
        act("ldl", 183),  # misread value
        act("tsh", 2.8, page=1),  # right value, wrong page
        act("hdl", 44, when="2024-03-12"),  # wrong date
        act("triglycerides", 165),  # not in the key
        act("other", 1.0),  # free-text tests are ignored
    ]
    s = score_observations(expected, actual)
    assert (s.expected, s.extracted, s.matched, s.page_correct) == (5, 5, 2, 1)
    assert [w["code"] for w in s.wrong_value] == ["ldl"]
    assert [w["code"] for w in s.wrong_date] == ["hdl"]
    assert [m["code"] for m in s.missed] == ["bp_systolic"]
    assert [e["code"] for e in s.extra] == ["triglycerides"]
    assert s.precision == 2 / 5 and s.recall == 2 / 5


def test_duplicate_metric_same_day_matches_each_once():
    # Discharge summaries report BP on admission and at discharge, same date.
    expected = [exp("bp_systolic", 150), exp("bp_systolic", 128, page=2)]
    actual = [act("bp_systolic", 128, page=2), act("bp_systolic", 150)]
    s = score_observations(expected, actual)
    assert s.matched == 2 and s.page_correct == 2 and not s.extra


def test_names():
    assert name_matches("Metformin 500 mg", "Metformin 500mg tablet")  # formatting doesn't matter
    assert not name_matches("Metformin 500 mg", "Metformin 1000 mg")  # strength does
    assert name_matches("Iron deficiency anaemia", "Iron deficiency anaemia (mild)")
    assert not name_matches("Prediabetes", "Diabetes")
    assert score_names(["Prediabetes", "Dyslipidemia"], ["Dyslipidemia", "Hypertension"]) == (1, 2, 2)
    assert same_text("City Diagnostics Laboratory", "City Diagnostics Laboratory, Pune")
    assert not same_text("Asha Verma", "Sunita Rao")


def test_answer_contains():
    assert contains_all("BP was 138 / 88 mmHg [S1]", ["138/88"]) == (True, [])
    assert contains_all("HbA1c 16.45%", ["6.4"])[0] is False  # numbers match whole, not inside others
    assert contains_all("Creatinine 115 umol/L", [["1.3", "115"]])[0] is True
    ok, missing = contains_all("Levothyroxine 25 mcg", ["Levothyroxine", "50 mcg"])
    assert not ok and missing == ["50 mcg"]
