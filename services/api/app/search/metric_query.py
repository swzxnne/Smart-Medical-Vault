"""Rule-based detection of measurement questions ("What was the BP in 2024?").

Deterministic on purpose: it's free (no extra LLM call on the free tier) and testable.
"""

import re
from dataclasses import dataclass
from datetime import date

from app.health.catalog import QUESTION_ALIASES

_ALIASES = sorted(QUESTION_ALIASES, key=len, reverse=True)
_YEAR = r"((?:19|20)\d{2})"


@dataclass
class MetricQuery:
    codes: list[str]
    date_from: date | None = None
    date_to: date | None = None


def detect_metrics(question: str) -> list[str]:
    text = question.lower()
    codes: list[str] = []
    for alias in _ALIASES:
        pattern = r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])"
        if re.search(pattern, text):
            text = re.sub(pattern, " ", text)  # "blood sugar" shouldn't also count as "sugar"
            codes += [c for c in QUESTION_ALIASES[alias] if c not in codes]
    return codes


def detect_dates(question: str, today: date | None = None) -> tuple[date | None, date | None]:
    today = today or date.today()
    q = question.lower()
    if m := re.search(rf"between {_YEAR} and {_YEAR}", q):
        a, b = sorted(int(y) for y in m.groups())
        return date(a, 1, 1), date(b, 12, 31)
    if m := re.search(rf"(?:since|after|from) {_YEAR}", q):
        return date(int(m.group(1)), 1, 1), None
    if m := re.search(rf"(?:before|until|till) {_YEAR}", q):
        return None, date(int(m.group(1)) - 1, 12, 31)
    if "last year" in q:
        return date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)
    if "this year" in q:
        return date(today.year, 1, 1), today
    if m := re.search(rf"(?:last|past) (\d+) years", q):
        return date(today.year - int(m.group(1)), today.month, 1), None
    years = sorted({int(y) for y in re.findall(rf"(?<!\d){_YEAR}(?!\d)", q)})
    if len(years) == 1:
        return date(years[0], 1, 1), date(years[0], 12, 31)
    if len(years) > 1:
        return date(years[0], 1, 1), date(years[-1], 12, 31)
    return None, None


def parse(question: str, today: date | None = None) -> MetricQuery | None:
    codes = detect_metrics(question)
    if not codes:
        return None
    date_from, date_to = detect_dates(question, today)
    return MetricQuery(codes, date_from, date_to)


def mentioned_profiles(question: str, profiles: list[tuple[object, str]]) -> list[object]:
    """Profile ids whose name (or first name) appears in the question, e.g. "Ravi's sugar"."""
    q = question.lower()
    found = []
    for profile_id, name in profiles:
        parts = [name.lower()] + [p for p in name.lower().split() if len(p) >= 3]
        if any(re.search(r"(?<![a-z])" + re.escape(p) + r"(?![a-z])", q) for p in parts):
            found.append(profile_id)
    return found
