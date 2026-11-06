"""Pure scoring functions for the eval harness (unit-tested in tests/test_eval_scoring.py)."""

import re
from dataclasses import dataclass, field


def value_close(actual: float, expected: float) -> bool:
    """Within 2% (unit-conversion rounding) or 0.05 absolute (one printed decimal)."""
    return abs(actual - expected) <= max(0.02 * abs(expected), 0.051)


@dataclass
class ObservationScore:
    expected: int = 0
    extracted: int = 0
    matched: int = 0  # right metric, date and value
    page_correct: int = 0  # of the matched, cited on the right page
    wrong_value: list[dict] = field(default_factory=list)  # right metric + date, value off
    wrong_date: list[dict] = field(default_factory=list)  # right metric + value, date off
    missed: list[dict] = field(default_factory=list)
    extra: list[dict] = field(default_factory=list)

    @property
    def precision(self) -> float | None:
        return self.matched / self.extracted if self.extracted else None

    @property
    def recall(self) -> float | None:
        return self.matched / self.expected if self.expected else None


def score_observations(expected: list[dict], actual: list[dict]) -> ObservationScore:
    """expected: {code, value, date, page}; actual: {code, value_num, observed_at, page_no} (catalog codes only)."""
    actual = [a for a in actual if a["code"] != "other" and a.get("value_num") is not None]
    score = ObservationScore(expected=len(expected), extracted=len(actual))
    unused = list(range(len(actual)))

    def take(pred) -> dict | None:
        for i in unused:
            if pred(actual[i]):
                unused.remove(i)
                return actual[i]
        return None

    unmatched = []
    for exp in expected:
        hit = take(lambda a: a["code"] == exp["code"] and str(a["observed_at"]) == str(exp["date"])
                   and value_close(a["value_num"], exp["value"]))
        if hit:
            score.matched += 1
            score.page_correct += hit["page_no"] == exp["page"]
        else:
            unmatched.append(exp)
    # Diagnose the misses: wrong value? wrong date? or not extracted at all?
    for exp in unmatched:
        if hit := take(lambda a: a["code"] == exp["code"] and str(a["observed_at"]) == str(exp["date"])):
            score.wrong_value.append({**exp, "got": hit["value_num"]})
        elif hit := take(lambda a: a["code"] == exp["code"] and value_close(a["value_num"], exp["value"])):
            score.wrong_date.append({**exp, "got": str(hit["observed_at"])})
        else:
            score.missed.append(exp)
    score.extra = [actual[i] for i in unused]
    return score


def _tokens(text: str, min_len: int = 3) -> set[str]:
    # "500mg" and "500 mg" are the same dose: split digits from letters before tokenizing.
    spaced = re.sub(r"(?<=\d)(?=[a-z])|(?<=[a-z])(?=\d)", " ", text.lower())
    return {t for t in re.findall(r"[a-z0-9]+", spaced) if len(t) >= min_len or t.isdigit()}


def name_matches(expected: str, actual: str) -> bool:
    """Medicine/diagnosis names: every significant word of the shorter one appears in the other."""
    e, a = _tokens(expected), _tokens(actual)
    if not e or not a:
        return False
    small, big = (e, a) if len(e) <= len(a) else (a, e)
    return small <= big


def score_names(expected: list[str], actual: list[str]) -> tuple[int, int, int]:
    """(matched expected, len expected, len actual)."""
    used: set[int] = set()
    matched = 0
    for exp in expected:
        for i, act in enumerate(actual):
            if i not in used and name_matches(exp, act):
                used.add(i)
                matched += 1
                break
    return matched, len(expected), len(actual)


def same_text(expected: str | None, actual: str | None) -> bool:
    if expected is None or actual is None:
        return expected is actual
    return _tokens(expected, 1) <= _tokens(actual, 1) or _tokens(actual, 1) <= _tokens(expected, 1)


# ---------------------------------------------------------------- RAG

NOT_FOUND = re.compile(r"couldn.t find|could not find|can.t find|cannot find|not (?:mentioned|found|available)|no (?:information|record|mention)", re.I)


def normalize_answer(text: str) -> str:
    text = text.lower().replace(",", "")
    return re.sub(r"\s*/\s*", "/", text)


def contains_all(answer: str, must_include: list) -> tuple[bool, list]:
    """Each entry is a string, or a list of acceptable alternatives. Returns (ok, missing entries)."""
    norm = normalize_answer(answer)
    missing = []
    for item in must_include:
        options = item if isinstance(item, list) else [item]
        if not any(_contains_value(norm, normalize_answer(str(o))) for o in options):
            missing.append(item)
    return not missing, missing


def _contains_value(text: str, needle: str) -> bool:
    if re.fullmatch(r"[\d./]+", needle):
        # Numbers must match as whole numbers: "6.4" must not match inside "16.45".
        return re.search(rf"(?<![\d.]){re.escape(needle)}(?![\d])", text) is not None
    return needle in text
