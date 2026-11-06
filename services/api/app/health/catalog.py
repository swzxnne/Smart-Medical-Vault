"""Known health metrics: canonical codes, units, conversions and plausible ranges.

The LLM tags each extracted value with one of these codes (or "other"); everything after that
(unit conversion, range checks, flags) is deterministic code in `normalize.py`.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Metric:
    code: str
    name: str
    unit: str  # canonical unit; values in other units are converted when a conversion is known
    group: str  # chart / question group, e.g. "lipids"
    plausible: tuple[float, float]  # outside this range the value is almost certainly misread
    decimals: int = 1
    # Unit spelling (normalised by `unit_key`) -> function converting to the canonical unit.
    conversions: dict[str, Callable[[float], float]] = field(default_factory=dict)


def _same(x: float) -> float:
    return x


def unit_key(unit: str | None) -> str:
    """Loose unit spelling: 'mg/dL' == 'mg / dl' == 'MG/DL'; µ/μ -> u; drop degree signs.
    Also undoes a common OCR confusion: 'IU' read as 'lU'/'1U'/'|U' ('µIU/mL' -> 'ulU/mL')."""
    if not unit:
        return ""
    key = unit.lower().replace(" ", "").replace("µ", "u").replace("μ", "u").replace("°", "").replace("per", "/")
    return re.sub(r"[l1|]u/", "iu/", key)


MG_DL = {"mg/dl": _same, "mg%": _same}

METRICS: dict[str, Metric] = {
    m.code: m
    for m in [
        # Vitals
        Metric("bp_systolic", "Systolic blood pressure", "mmHg", "blood_pressure", (50, 300), 0, {"mmhg": _same}),
        Metric("bp_diastolic", "Diastolic blood pressure", "mmHg", "blood_pressure", (25, 200), 0, {"mmhg": _same}),
        Metric("pulse", "Pulse", "bpm", "pulse", (20, 250), 0, {"bpm": _same, "/min": _same, "beats/min": _same, "b/min": _same}),
        Metric("spo2", "SpO2", "%", "spo2", (50, 100), 0, {"%": _same}),
        Metric("temperature", "Temperature", "°C", "temperature", (30, 45), 1,
               {"c": _same, "f": lambda f: (f - 32) * 5 / 9}),
        Metric("weight", "Weight", "kg", "weight", (1, 350), 1, {"kg": _same, "kgs": _same, "lb": lambda x: x * 0.45359, "lbs": lambda x: x * 0.45359}),
        Metric("height", "Height", "cm", "height", (30, 250), 0, {"cm": _same, "m": lambda x: x * 100, "in": lambda x: x * 2.54, "inch": lambda x: x * 2.54}),
        Metric("bmi", "BMI", "kg/m²", "weight", (8, 80), 1, {"kg/m2": _same, "kg/m²": _same, "": _same}),
        # Diabetes
        Metric("glucose_fasting", "Fasting glucose", "mg/dL", "glucose", (20, 700), 0, {**MG_DL, "mmol/l": lambda x: x * 18.016}),
        Metric("glucose_random", "Random glucose", "mg/dL", "glucose", (20, 900), 0, {**MG_DL, "mmol/l": lambda x: x * 18.016}),
        Metric("glucose_pp", "Post-meal glucose", "mg/dL", "glucose", (20, 900), 0, {**MG_DL, "mmol/l": lambda x: x * 18.016}),
        Metric("hba1c", "HbA1c", "%", "hba1c", (3, 20), 1, {"%": _same, "mmol/mol": lambda x: x * 0.0915 + 2.15}),
        # Lipids
        Metric("cholesterol_total", "Total cholesterol", "mg/dL", "lipids", (50, 700), 0, {**MG_DL, "mmol/l": lambda x: x * 38.67}),
        Metric("ldl", "LDL cholesterol", "mg/dL", "lipids", (10, 500), 0, {**MG_DL, "mmol/l": lambda x: x * 38.67}),
        Metric("hdl", "HDL cholesterol", "mg/dL", "lipids", (5, 200), 0, {**MG_DL, "mmol/l": lambda x: x * 38.67}),
        Metric("triglycerides", "Triglycerides", "mg/dL", "lipids", (10, 5000), 0, {**MG_DL, "mmol/l": lambda x: x * 88.57}),
        # Thyroid
        Metric("tsh", "TSH", "µIU/mL", "thyroid", (0.001, 200), 2, {"uiu/ml": _same, "miu/l": _same, "uu/ml": _same, "miu/ml": lambda x: x * 1000}),
        Metric("t3", "T3", "ng/dL", "thyroid", (10, 1000), 0, {"ng/dl": _same, "nmol/l": lambda x: x * 65.1}),
        Metric("t4", "T4", "µg/dL", "thyroid", (0.5, 30), 1, {"ug/dl": _same, "nmol/l": lambda x: x / 12.87}),
        # Kidney
        Metric("creatinine", "Creatinine", "mg/dL", "kidney", (0.1, 20), 2, {**MG_DL, "umol/l": lambda x: x / 88.42}),
        Metric("urea", "Urea", "mg/dL", "kidney", (2, 400), 0, {**MG_DL, "mmol/l": lambda x: x * 6.006}),
        Metric("egfr", "eGFR", "mL/min/1.73m²", "kidney", (1, 200), 0, {"ml/min/1.73m2": _same, "ml/min/1.73m²": _same, "ml/min": _same}),
        Metric("uric_acid", "Uric acid", "mg/dL", "kidney", (0.5, 20), 1, {**MG_DL, "umol/l": lambda x: x / 59.48}),
        # Blood count
        Metric("hemoglobin", "Hemoglobin", "g/dL", "blood_count", (2, 25), 1, {"g/dl": _same, "gm/dl": _same, "gm%": _same, "g%": _same, "g/l": lambda x: x / 10}),
        Metric("wbc", "White blood cells", "×10³/µL", "blood_count", (0.1, 200), 1,
               {"x10^3/ul": _same, "10^3/ul": _same, "x10³/ul": _same, "10³/ul": _same, "k/ul": _same, "x10^9/l": _same, "10^9/l": _same, "/cumm": lambda x: x / 1000, "cells/cumm": lambda x: x / 1000, "/ul": lambda x: x / 1000}),
        Metric("platelets", "Platelets", "×10³/µL", "blood_count", (5, 2000), 0,
               {"x10^3/ul": _same, "10^3/ul": _same, "k/ul": _same, "x10^9/l": _same, "10^9/l": _same, "lakh/cumm": lambda x: x * 100, "lakhs/cumm": lambda x: x * 100, "/cumm": lambda x: x / 1000, "/ul": lambda x: x / 1000}),
        # Liver
        Metric("alt", "ALT (SGPT)", "U/L", "liver", (1, 5000), 0, {"u/l": _same, "iu/l": _same}),
        Metric("ast", "AST (SGOT)", "U/L", "liver", (1, 5000), 0, {"u/l": _same, "iu/l": _same}),
        # Vitamins / electrolytes
        Metric("vitamin_d", "Vitamin D", "ng/mL", "vitamins", (1, 200), 1, {"ng/ml": _same, "nmol/l": lambda x: x / 2.496}),
        Metric("vitamin_b12", "Vitamin B12", "pg/mL", "vitamins", (50, 5000), 0, {"pg/ml": _same, "pmol/l": lambda x: x * 1.355}),
        Metric("sodium", "Sodium", "mmol/L", "electrolytes", (100, 180), 0, {"mmol/l": _same, "meq/l": _same}),
        Metric("potassium", "Potassium", "mmol/L", "electrolytes", (1.5, 9), 1, {"mmol/l": _same, "meq/l": _same}),
    ]
}

# What the extraction prompt may use. "blood_pressure" is split into systolic/diastolic in code.
EXTRACTION_CODES = ("blood_pressure", *METRICS, "other")

GROUP_LABELS = {
    "blood_pressure": "Blood pressure",
    "pulse": "Pulse",
    "spo2": "Oxygen saturation",
    "temperature": "Temperature",
    "weight": "Weight & BMI",
    "height": "Height",
    "glucose": "Blood sugar",
    "hba1c": "HbA1c",
    "lipids": "Cholesterol & lipids",
    "thyroid": "Thyroid",
    "kidney": "Kidney function",
    "blood_count": "Blood count",
    "liver": "Liver function",
    "vitamins": "Vitamins",
    "electrolytes": "Electrolytes",
}

# Phrases people use in questions -> metric codes. Matched on word boundaries, longest first.
QUESTION_ALIASES: dict[str, tuple[str, ...]] = {
    "blood pressure": ("bp_systolic", "bp_diastolic"),
    "bp": ("bp_systolic", "bp_diastolic"),
    "hypertension": ("bp_systolic", "bp_diastolic"),
    "systolic": ("bp_systolic",),
    "diastolic": ("bp_diastolic",),
    "pulse": ("pulse",),
    "heart rate": ("pulse",),
    "spo2": ("spo2",),
    "oxygen": ("spo2",),
    "temperature": ("temperature",),
    "fever": ("temperature",),
    "weight": ("weight", "bmi"),
    "bmi": ("bmi",),
    "height": ("height",),
    "sugar": ("glucose_fasting", "glucose_random", "glucose_pp", "hba1c"),
    "blood sugar": ("glucose_fasting", "glucose_random", "glucose_pp", "hba1c"),
    "glucose": ("glucose_fasting", "glucose_random", "glucose_pp"),
    "fasting": ("glucose_fasting",),
    "diabetes": ("glucose_fasting", "glucose_random", "glucose_pp", "hba1c"),
    "hba1c": ("hba1c",),
    "a1c": ("hba1c",),
    "cholesterol": ("cholesterol_total", "ldl", "hdl", "triglycerides"),
    "lipid": ("cholesterol_total", "ldl", "hdl", "triglycerides"),
    "lipids": ("cholesterol_total", "ldl", "hdl", "triglycerides"),
    "ldl": ("ldl",),
    "hdl": ("hdl",),
    "triglycerides": ("triglycerides",),
    "thyroid": ("tsh", "t3", "t4"),
    "tsh": ("tsh",),
    "creatinine": ("creatinine",),
    "kidney": ("creatinine", "urea", "egfr", "uric_acid"),
    "egfr": ("egfr",),
    "urea": ("urea",),
    "uric acid": ("uric_acid",),
    "hemoglobin": ("hemoglobin",),
    "haemoglobin": ("hemoglobin",),
    "hb": ("hemoglobin",),
    "platelets": ("platelets",),
    "wbc": ("wbc",),
    "liver": ("alt", "ast"),
    "sgpt": ("alt",),
    "sgot": ("ast",),
    "vitamin d": ("vitamin_d",),
    "vitamin b12": ("vitamin_b12",),
    "b12": ("vitamin_b12",),
    "sodium": ("sodium",),
    "potassium": ("potassium",),
}
