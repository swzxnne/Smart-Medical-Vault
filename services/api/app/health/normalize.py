"""Turn LLM-extracted values into trustworthy observations: parse, ground, convert, flag."""

import re
from dataclasses import dataclass
from datetime import date

from app.health.catalog import METRICS, unit_key

_NUMBER = re.compile(r"-?\d+(?:[.,]\d+)?")


def parse_number(text: str | None) -> float | None:
    if not text:
        return None
    match = _NUMBER.search(text.replace(",", "") if re.search(r"\d,\d{3}\b", text) else text)
    return float(match.group(0).replace(",", ".")) if match else None


def value_in_text(value: str, text: str) -> bool:
    """Is this printed value actually on the page? Guards against invented numbers."""
    compact_value = re.sub(r"\s+", "", value)
    if compact_value and compact_value in re.sub(r"\s+", "", text):
        return True
    wanted = _NUMBER.findall(value)
    if not wanted:
        return False
    present = set(_NUMBER.findall(text))
    return all(n in present for n in wanted)


_GENERIC_WORDS = {"blood", "serum", "plasma", "level", "levels", "count", "test", "total", "value", "result", "random",
                  # condition words from question aliases: they don't name a test on a report line
                  "diabetes", "hypertension", "fever", "kidney", "liver", "thyroid", "oxygen"}


def label_words(name: str, code: str) -> set[str]:
    """Words that identify a test on the page: the printed name, the catalog name and question aliases."""
    from app.health.catalog import METRICS, QUESTION_ALIASES

    codes = {"bp_systolic", "bp_diastolic"} if code == "blood_pressure" else {code}
    words = set(re.findall(r"[a-z0-9]+", name.lower()))
    for c in codes & set(METRICS):
        words |= set(re.findall(r"[a-z0-9]+", METRICS[c].name.lower()))
    words |= {alias for alias, alias_codes in QUESTION_ALIASES.items() if codes & set(alias_codes) and " " not in alias}
    words = {w for w in words if (len(w) >= 3 or w in ("bp", "hb")) and w not in _GENERIC_WORDS}
    return words | {a for a, cs in QUESTION_ALIASES.items() if " " in a and codes & set(cs)}


def label_near_value(value: str, words: set[str], text: str) -> bool:
    """The value's line (or the previous non-empty line, for wrapped text) mentions the test.
    Catches values borrowed from another row, e.g. "LDL 138" taken from "Blood pressure 138/88"."""
    lines = [line for line in text.splitlines() if line.strip()]  # parsers sometimes split a sentence with a blank line
    for i, line in enumerate(lines):
        if value_in_text(value, line):
            window = " ".join(lines[max(0, i - 1): i + 1]).lower()
            if any(re.search(r"(?<![a-z0-9])" + re.escape(w) + r"(?![a-z0-9])", window) for w in words):
                return True
    return False


@dataclass
class RefRange:
    low: float | None
    high: float | None


def parse_reference_range(text: str | None) -> RefRange | None:
    """'70 - 100', '70-100 mg/dL', '< 200', '<=5.6', '> 40', 'up to 150'."""
    if not text:
        return None
    t = text.strip().lower()
    numbers = [float(n.replace(",", ".")) for n in _NUMBER.findall(t.replace(" - ", " to "))]
    if re.match(r"^(<|≤|<=|up to|upto|below|less than)", t) and numbers:
        return RefRange(None, numbers[0])
    if re.match(r"^(>|≥|>=|above|more than|greater than)", t) and numbers:
        return RefRange(numbers[0], None)
    range_match = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:-|–|to)\s*(\d+(?:[.,]\d+)?)", t)
    if range_match:
        low, high = (float(g.replace(",", ".")) for g in range_match.groups())
        return RefRange(min(low, high), max(low, high))
    return None


def flag_for(value: float, ref: RefRange | None) -> str | None:
    if ref is None:
        return None
    if ref.low is not None and value < ref.low:
        return "low"
    if ref.high is not None and value > ref.high:
        return "high"
    return "normal"


@dataclass
class Normalized:
    code: str
    name: str
    value_num: float | None
    value_text: str
    unit: str | None
    ref_low: float | None
    ref_high: float | None
    ref_text: str | None
    flag: str | None


def convert(code: str, value: float, unit: str | None) -> tuple[float, str | None] | None:
    """Value in the metric's canonical unit, or None when the unit is unknown for this metric."""
    metric = METRICS[code]
    key = unit_key(unit)
    if key == unit_key(metric.unit) or (not key and "" in metric.conversions):
        return round(value, metric.decimals + 2), metric.unit
    fn = metric.conversions.get(key)
    if fn is None:
        return None
    return round(fn(value), metric.decimals + 2), metric.unit


def normalize(code: str, name: str, value: str, unit: str | None, reference: str | None) -> list[Normalized]:
    """One printed value -> zero or more normalized observations.

    "blood_pressure" '138/88' becomes systolic + diastolic. Values that can't be parsed, use an
    unknown unit, or fall outside the metric's plausible range are dropped (likely misreads).
    """
    if code == "blood_pressure":
        numbers = _NUMBER.findall(value)
        if len(numbers) != 2:
            return []
        out = []
        for sub_code, number in zip(("bp_systolic", "bp_diastolic"), numbers, strict=True):
            out += normalize(sub_code, METRICS[sub_code].name, number, unit or "mmHg", None)
        return out

    number = parse_number(value)
    if code not in METRICS:
        # "other": keep as printed, with a flag if the report gives a range in the same unit.
        ref = parse_reference_range(reference)
        return [Normalized("other", name, number, value, unit, ref.low if ref else None,
                           ref.high if ref else None, reference, flag_for(number, ref) if number is not None else None)]

    metric = METRICS[code]
    if number is None:
        return []
    converted = convert(code, number, unit)
    if converted is None:
        return []
    value_num, canonical_unit = converted
    low, high = metric.plausible
    if not low <= value_num <= high:
        return []

    ref = parse_reference_range(reference)
    if ref is not None:
        # The report's range is in the report's unit; convert it the same way as the value.
        scale = [convert(code, bound, unit) if bound is not None else None for bound in (ref.low, ref.high)]
        ref = RefRange(scale[0][0] if scale[0] else None, scale[1][0] if scale[1] else None)
    return [Normalized(code, metric.name, value_num, value, canonical_unit,
                       ref.low if ref else None, ref.high if ref else None, reference,
                       flag_for(value_num, ref))]


def valid_date(value: date | None, fallback: date | None, today: date | None = None) -> date | None:
    today = today or date.today()
    for candidate in (value, fallback):
        if candidate is not None and date(1900, 1, 1) <= candidate <= today:
            return candidate
    return None
