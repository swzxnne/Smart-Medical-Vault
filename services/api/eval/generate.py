"""Generate the synthetic golden set: documents + answer keys + RAG questions.

Three fictional family members, 30 documents across the formats the app accepts. Every value
printed on a document is recorded in its answer key in canonical units (converted with the app's
own catalog), so a report printed in mmol/L expects the same mg/dL value the pipeline should store.

Usage (inside the worker container):  python -m eval.generate
Output: eval/docs/*, eval/expected/*.json, eval/questions.json  (deterministic: same seed, same files)
"""

import json
import random
import shutil
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from docx import Document as DocxDocument
from fpdf import FPDF
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from app.health.catalog import METRICS
from app.health.normalize import convert

ROOT = Path(__file__).resolve().parent
DOCS, EXPECTED = ROOT / "docs", ROOT / "expected"
SEED = 20240313

PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PNG, JPEG = "image/png", "image/jpeg"


# ---------------------------------------------------------------- model


@dataclass
class Value:
    """One printed measurement. `code` may be "blood_pressure" with value (sys, dia)."""

    code: str
    label: str
    value: float | tuple[int, int]
    unit: str
    ref: str | None = None
    page: int = 1
    when: date | None = None  # printed date for this value, if different from the document date

    def printed(self) -> str:
        if isinstance(self.value, tuple):
            return f"{self.value[0]}/{self.value[1]}"
        return f"{self.value:g}"


@dataclass
class Doc:
    file: str
    category: str
    profile: str
    patient: str  # name printed on the document (differs from profile for the mismatch case)
    doc_type: str | None
    doc_date: date | None
    provider: str | None
    values: list[Value] = field(default_factory=list)
    medications: list[str] = field(default_factory=list)  # name + strength, as printed
    conditions: list[str] = field(default_factory=list)
    expect_failure: bool = False  # e.g. blank scan: pipeline should refuse it
    notes: str = ""
    strength: float = 0.0  # scan degradation (0 = clean)

    @property
    def mime(self) -> str:
        return {".pdf": PDF, ".docx": DOCX, ".png": PNG, ".jpg": JPEG}[Path(self.file).suffix]


PEOPLE = {
    "Asha Verma": dict(age=52, sex="F"),
    "Ravi Verma": dict(age=58, sex="M"),
    "Meera Verma": dict(age=12, sex="F"),
}
LABS = ["City Diagnostics Laboratory", "Lifeline Pathology", "Metro Lab Services", "Apollo Diagnostics Centre"]
CLINICS = ["Sunrise Family Clinic", "Green Valley Hospital", "St. Anne's Children's Clinic", "Care Point Clinic"]

DATE_STYLES = [
    lambda d: d.strftime("%d %B %Y"),  # 13 March 2024
    lambda d: d.strftime("%d/%m/%Y"),  # 13/03/2024 (Indian day-first)
    lambda d: d.isoformat(),  # 2024-03-13
    lambda d: d.strftime("%b %d, %Y"),  # Mar 13, 2024
]


# ---------------------------------------------------------------- expected answers


def expected_observations(doc: Doc) -> list[dict]:
    out = []
    for v in doc.values:
        when = (v.when or doc.doc_date).isoformat() if (v.when or doc.doc_date) else None
        if v.code == "blood_pressure":
            for code, part in zip(("bp_systolic", "bp_diastolic"), v.value, strict=True):
                out.append(dict(code=code, value=float(part), unit="mmHg", date=when, page=v.page))
            continue
        converted = convert(v.code, float(v.value), v.unit)
        assert converted is not None, f"generator bug: no conversion for {v.code} {v.unit}"
        out.append(dict(code=v.code, value=round(converted[0], 3), unit=METRICS[v.code].unit, date=when, page=v.page))
    return out


def answer_key(doc: Doc) -> dict:
    return dict(
        file=doc.file,
        mime=doc.mime,
        category=doc.category,
        profile=doc.profile,
        notes=doc.notes,
        expect_failure=doc.expect_failure,
        expected=dict(
            doc_type=doc.doc_type,
            doc_date=doc.doc_date.isoformat() if doc.doc_date else None,
            provider=doc.provider,
            patient_name=doc.patient,
            name_mismatch=doc.patient != doc.profile,
            observations=expected_observations(doc),
            medications=doc.medications,
            conditions=doc.conditions,
        ),
    )


# ---------------------------------------------------------------- renderers

FONT = {size: ImageFont.load_default(size=size) for size in (22, 26, 30, 40)}


def header_lines(doc: Doc, date_style) -> list[str]:
    person = PEOPLE[doc.profile]
    lines = [f"Patient: {doc.patient}    Age/Sex: {person['age']} / {person['sex']}"]
    if doc.doc_date:
        lines.append(f"Report date: {date_style(doc.doc_date)}")
    return lines


def value_rows(values: list[Value], page: int) -> list[tuple[str, str, str, str]]:
    return [(v.label, v.printed(), v.unit, v.ref or "") for v in values if v.page == page]


def render_lab_pdf(doc: Doc, path: Path, date_style) -> None:
    pdf = FPDF()
    pages = sorted({v.page for v in doc.values}) or [1]
    for page in pages:
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 15)
        pdf.cell(0, 10, doc.provider or "Laboratory Report", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 10)
        if page == 1:
            for line in header_lines(doc, date_style):
                pdf.cell(0, 6, line, new_x="LMARGIN", new_y="NEXT")
            vitals = [v for v in doc.values if v.page == 1 and v.code in ("blood_pressure", "pulse", "weight")]
            if vitals:
                pdf.cell(0, 6, "Vitals: " + ", ".join(f"{v.label} {v.printed()} {v.unit}" for v in vitals),
                         new_x="LMARGIN", new_y="NEXT")
        else:
            pdf.cell(0, 6, f"{doc.patient} - continued", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(3)
        rows = [r for r in value_rows(doc.values, page) if not any(
            r[0] == v.label and v.code in ("blood_pressure", "pulse", "weight") for v in doc.values)]
        if rows:
            pdf.set_font("Helvetica", "", 10)
            with pdf.table(col_widths=(60, 25, 25, 40)) as table:
                table.row(["Test", "Result", "Unit", "Reference range"])
                for row in rows:
                    table.row(list(row))
        if page == pages[-1] and doc.conditions:
            pdf.ln(4)
            pdf.multi_cell(0, 6, "Impression: " + "; ".join(doc.conditions) + ".", new_x="LMARGIN", new_y="NEXT")
    pdf.output(str(path))


def text_image(lines: list[tuple[str, int]], width=1240, height=1754) -> Image.Image:
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)
    y = 90
    for text, size in lines:
        draw.text((80, y), text, fill="black", font=FONT[size])
        y += int(size * 1.7)
    return img


def degrade(img: Image.Image, rng: random.Random, strength: float) -> Image.Image:
    """Make a clean render look like a phone photo / office scan."""
    img = img.rotate(rng.uniform(-1.8, 1.8) * strength, fillcolor="white", resample=Image.BICUBIC)
    img = img.filter(ImageFilter.GaussianBlur(0.5 + 0.6 * strength))
    px = img.load()
    for _ in range(int(4000 * strength)):  # speckle noise
        x, y = rng.randrange(img.width), rng.randrange(img.height)
        shade = rng.randrange(120, 230)
        px[x, y] = (shade, shade, shade)
    return img


def scan_lines(doc: Doc, date_style) -> list[tuple[str, int]]:
    lines: list[tuple[str, int]] = [(doc.provider or "Report", 40), ("", 22)]
    lines += [(line, 26) for line in header_lines(doc, date_style)]
    lines.append(("", 22))
    for v in doc.values:
        ref = f"   (ref {v.ref})" if v.ref else ""
        lines.append((f"{v.label}: {v.printed()} {v.unit}{ref}", 26))
    if doc.medications:
        lines += [("", 22), ("Rx:", 26)] + [(f"  {m}", 26) for m in doc.medications]
    if doc.conditions:
        lines += [("", 22), ("Assessment: " + ", ".join(doc.conditions), 26)]
    return lines


def render_scan(doc: Doc, path: Path, date_style, rng: random.Random, strength: float) -> None:
    img = degrade(text_image(scan_lines(doc, date_style)), rng, strength)
    if path.suffix == ".pdf":
        img.save(path, "PDF", resolution=150)
    elif path.suffix == ".jpg":
        img.save(path, "JPEG", quality=int(85 - 25 * strength))
    else:
        img.save(path, "PNG")


def render_handwritten(doc: Doc, path: Path, date_style, rng: random.Random) -> None:
    """Approximate handwriting: per-character jitter in position, size and angle, slanted, inky blue.
    Real handwriting is harder; treat scores on these as an optimistic floor."""
    img = Image.new("RGB", (1240, 1754), (250, 248, 240))
    y = 110
    for text, size in scan_lines(doc, date_style):
        x = 90 + rng.randint(-6, 6)
        for ch in text:
            glyph_font = FONT[30 if size >= 30 else 26]
            box = glyph_font.getbbox(ch) if ch.strip() else (0, 0, 12, 20)
            w, h = max(box[2], 8) + 6, max(box[3], 20) + 10
            glyph = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            ImageDraw.Draw(glyph).text((3, 3), ch, font=glyph_font, fill=(20, 40, 120, 255))
            glyph = glyph.rotate(rng.uniform(-9, 9), expand=True, resample=Image.BICUBIC)
            img.paste(glyph, (x, y + rng.randint(-3, 3)), glyph)
            x += w - 6 + rng.randint(-1, 2)
        y += int(size * 1.9)
    img = img.transform(img.size, Image.AFFINE, (1, 0.12, -100, 0, 1, 0), fillcolor=(250, 248, 240))
    degrade(img, rng, 0.6).save(path, "PNG")


def render_docx(doc: Doc, path: Path, date_style) -> None:
    d = DocxDocument()
    d.add_heading(doc.provider or "Clinic", level=1)
    for line in header_lines(doc, date_style):
        d.add_paragraph(line)
    vitals = [v for v in doc.values]
    if vitals:
        d.add_paragraph("Vitals: " + ", ".join(f"{v.label} {v.printed()} {v.unit}" for v in vitals))
    if doc.conditions:
        d.add_heading("Diagnosis", level=2)
        d.add_paragraph("; ".join(doc.conditions))
    if doc.medications:
        d.add_heading("Medications", level=2)
        table = d.add_table(rows=1, cols=3)
        for cell, text in zip(table.rows[0].cells, ("Medicine", "Dose", "Duration"), strict=True):
            cell.text = text
        for med in doc.medications:
            cells = table.add_row().cells
            cells[0].text, cells[1].text, cells[2].text = med, "1 tablet daily", "3 months"
    d.add_heading("Advice", level=2)
    d.add_paragraph("Review after 3 months with reports.")
    d.save(str(path))


def render_discharge(doc: Doc, path: Path, date_style) -> None:
    """Prose-heavy two-page discharge summary: values live inside sentences, not tables."""
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 15)
    pdf.cell(0, 10, f"{doc.provider} - Discharge Summary", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    for line in header_lines(doc, date_style):
        pdf.cell(0, 6, line, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)
    p1 = [v for v in doc.values if v.page == 1]
    text = ("The patient was admitted with breathlessness and fatigue. "
            + " ".join(f"On admission, {v.label.lower()} was {v.printed()} {v.unit}." for v in p1)
            + f" Final diagnosis: {', '.join(doc.conditions)}.")
    pdf.multi_cell(0, 6, text, new_x="LMARGIN", new_y="NEXT")
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "Course in hospital and discharge advice", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    p2 = [v for v in doc.values if v.page == 2]
    pdf.multi_cell(0, 6, "Treated with IV fluids and oral medication. "
                   + " ".join(f"At discharge, {v.label.lower()} was {v.printed()} {v.unit}." for v in p2),
                   new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)
    pdf.multi_cell(0, 6, "Discharge medications: " + "; ".join(doc.medications) + ".", new_x="LMARGIN", new_y="NEXT")
    pdf.multi_cell(0, 6, "Follow up in the OPD after two weeks with fresh blood reports.", new_x="LMARGIN", new_y="NEXT")
    pdf.output(str(path))


def harden(img: Image.Image, rng: random.Random, kind: str) -> Image.Image:
    """Failure modes Tesseract struggles with: heavy blur, faded print under a shadow, camera angle."""
    from PIL import ImageEnhance

    if kind == "blur":
        img = img.rotate(rng.uniform(3, 5), fillcolor="white", resample=Image.BICUBIC).filter(ImageFilter.GaussianBlur(2.2))
    elif kind == "faded":
        img = ImageEnhance.Contrast(img).enhance(0.28)
        shadow = Image.linear_gradient("L").resize(img.size).point(lambda v: 150 + v * 105 // 255)
        img = Image.composite(img, Image.new("RGB", img.size, (70, 70, 70)), shadow)
        img = img.filter(ImageFilter.GaussianBlur(1.2))
    elif kind == "angle":
        w, h = img.size
        # Shrink the whole page into a trapezoid, like a phone held at an angle above the desk:
        # output trapezoid corners map to the input's full corners, so nothing is cropped.
        coeffs = _perspective_coeffs([(w * 0.10, h * 0.04), (w * 0.93, 0), (w, h), (0, h * 0.97)],
                                     [(0, 0), (w, 0), (w, h), (0, h)])
        img = img.transform(img.size, Image.PERSPECTIVE, coeffs, Image.BICUBIC, fillcolor=(120, 115, 110))
        img = img.filter(ImageFilter.GaussianBlur(1.6))
    return degrade(img, rng, 1.0)


def _perspective_coeffs(dst, src):
    """PIL wants the inverse mapping: coefficients taking output points `dst` to input points `src`."""
    import numpy as np

    rows = []
    for (x, y), (u, v) in zip(dst, src, strict=True):
        rows.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        rows.append([0, 0, 0, x, y, 1, -v * x, -v * y])
    A, b = np.array(rows, dtype=float), np.array([c for p in src for c in p], dtype=float)
    return list(np.linalg.solve(A, b))


def render_hard_handwritten(doc: Doc, path: Path, date_style, rng: random.Random) -> None:
    """Messier than render_handwritten: bigger jitter and rotation, uneven baseline, faint ink."""
    img = Image.new("RGB", (1240, 1754), (246, 243, 232))
    y = 110
    for text, size in scan_lines(doc, date_style):
        x, drift = 90 + rng.randint(-12, 12), 0.0
        for ch in text:
            drift += rng.uniform(-1.2, 1.2)
            glyph_font = FONT[30]
            box = glyph_font.getbbox(ch) if ch.strip() else (0, 0, 12, 20)
            w, h = max(box[2], 8) + 6, max(box[3], 20) + 10
            glyph = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            ink = rng.randint(70, 140)
            ImageDraw.Draw(glyph).text((3, 3), ch, font=glyph_font, fill=(ink // 3, ink // 2, ink, 235))
            glyph = glyph.rotate(rng.uniform(-18, 18), expand=True, resample=Image.BICUBIC)
            img.paste(glyph, (x, int(y + drift + rng.randint(-5, 5))), glyph)
            x += w - 9 + rng.randint(-2, 2)
        y += int(size * 1.9)
    img = img.transform(img.size, Image.AFFINE, (1, 0.2, -170, 0.02, 1, -10), fillcolor=(246, 243, 232))
    degrade(img.filter(ImageFilter.GaussianBlur(0.9)), rng, 0.9).save(path, "PNG")


def build_hard_docs(rng: random.Random) -> list[Doc]:
    """Kept separate (own RNG) so adding them doesn't change the other 30 documents."""
    hard = []
    for file, person, when, panels, kind in [
        ("hard_blur_ravi_2024-04.jpg", "Ravi Verma", date(2024, 4, 16), ["kidney", "thyroid"], "blur"),
        ("hard_faded_asha_2024-10.jpg", "Asha Verma", date(2024, 10, 7), ["diabetes", "lipids"], "faded"),
        ("hard_angle_meera_2024-02.jpg", "Meera Verma", date(2024, 2, 19), ["cbc", "vitamins"], "angle"),
    ]:
        hard.append(Doc(file, "hard_scan", person, person, "lab_report", when, LABS[2],
                        lab_values(person, panels, when, rng), notes=f"hard: {kind}"))
    hard.append(Doc("hard_handwritten_rx_asha_2024-11.png", "hard_scan", "Asha Verma", "Asha Verma", "prescription",
                    date(2024, 11, 12), CLINICS[0], vitals("Asha Verma", date(2024, 11, 12), rng, include=("bp",)),
                    medications=["Metformin 500 mg", "Telmisartan 40 mg"], notes="hard: messy handwriting"))
    return hard


def render_hard(doc: Doc, rng: random.Random) -> None:
    path = DOCS / doc.file
    date_style = rng.choice(DATE_STYLES)
    if "handwritten" in doc.file:
        render_hard_handwritten(doc, path, date_style, rng)
        return
    img = harden(text_image(scan_lines(doc, date_style)), rng, doc.notes.split(": ")[1])
    img.save(path, "JPEG", quality=45)


def render_blank(path: Path, rng: random.Random) -> None:
    degrade(Image.new("RGB", (1240, 1754), "white"), rng, 0.4).save(path, "JPEG", quality=70)


# ---------------------------------------------------------------- the golden set

LAB_PANELS = {
    "diabetes": [("glucose_fasting", "Fasting Blood Glucose", "mg/dL", "70 - 100"), ("hba1c", "HbA1c", "%", "4.0 - 5.6")],
    "lipids": [("cholesterol_total", "Total Cholesterol", "mg/dL", "< 200"), ("ldl", "LDL Cholesterol", "mg/dL", "< 100"),
               ("hdl", "HDL Cholesterol", "mg/dL", "> 40"), ("triglycerides", "Triglycerides", "mg/dL", "< 150")],
    "thyroid": [("tsh", "TSH", "uIU/mL", "0.4 - 4.0")],
    "kidney": [("creatinine", "Serum Creatinine", "mg/dL", "0.6 - 1.2"), ("urea", "Blood Urea", "mg/dL", "15 - 40"),
               ("uric_acid", "Uric Acid", "mg/dL", "3.5 - 7.2")],
    "cbc": [("hemoglobin", "Hemoglobin", "g/dL", "12.0 - 15.5"), ("platelets", "Platelet Count", "lakh/cumm", "1.5 - 4.5")],
    "vitamins": [("vitamin_d", "Vitamin D (25-OH)", "ng/mL", "30 - 100"), ("vitamin_b12", "Vitamin B12", "pg/mL", "200 - 900")],
}

# Per-person baselines and yearly drift, so questions like "how has X changed" have real answers.
BASE = {
    "Asha Verma": dict(glucose_fasting=(112, 4), hba1c=(6.1, 0.2), cholesterol_total=(205, 5), ldl=(132, 4), hdl=(44, 0),
                       triglycerides=(160, 6), tsh=(2.6, 0.1), creatinine=(0.8, 0.0), urea=(26, 1), uric_acid=(5.1, 0.1),
                       hemoglobin=(12.8, -0.2), platelets=(2.6, 0.0), vitamin_d=(18, 4), vitamin_b12=(310, 20),
                       bp=((146, 92), (-4, -2)), pulse=(82, -2), weight=(70, -1)),
    "Ravi Verma": dict(glucose_fasting=(98, 2), hba1c=(5.6, 0.1), cholesterol_total=(225, -8), ldl=(150, -10), hdl=(38, 1),
                       triglycerides=(190, -12), tsh=(5.8, 0.6), creatinine=(1.3, 0.15), urea=(38, 4), uric_acid=(7.6, 0.3),
                       hemoglobin=(13.9, -0.3), platelets=(2.2, 0.0), vitamin_d=(22, 2), vitamin_b12=(240, -15),
                       bp=((138, 86), (3, 2)), pulse=(76, 1), weight=(82, 1)),
    "Meera Verma": dict(glucose_fasting=(86, 0), hba1c=(5.0, 0.0), cholesterol_total=(150, 0), ldl=(80, 0), hdl=(52, 0),
                        triglycerides=(90, 0), tsh=(2.1, 0), creatinine=(0.5, 0), urea=(20, 0), uric_acid=(3.9, 0),
                        hemoglobin=(10.9, 0.6), platelets=(3.1, 0), vitamin_d=(14, 6), vitamin_b12=(420, 10),
                        bp=((104, 68), (2, 1)), pulse=(92, -2), weight=(38, 4)),
}


def measured(person: str, key: str, when: date, rng: random.Random):
    base, drift = BASE[person][key]
    years = when.year - 2023 + (when.month - 1) / 12
    if key == "bp":
        return (round(base[0] + drift[0] * years + rng.randint(-2, 2)), round(base[1] + drift[1] * years + rng.randint(-1, 1)))
    value = base + drift * years + rng.uniform(-0.03, 0.03) * base
    decimals = METRICS[key].decimals if key in METRICS else 1
    return round(value, max(decimals, 1) if abs(value) < 10 else decimals)


def lab_values(person: str, panels: list[str], when: date, rng: random.Random, units: dict | None = None,
               split_after: int | None = None) -> list[Value]:
    units = units or {}
    values: list[Value] = []
    for i, (code, label, unit, ref) in enumerate(t for p in panels for t in LAB_PANELS[p]):
        value = measured(person, code, when, rng)
        if code in units:  # print in a non-canonical unit; convert value and range back to that unit
            unit, factor, ref = units[code]
            value = round(value / factor, 1)
        values.append(Value(code, label, value, unit, ref, page=2 if split_after is not None and i >= split_after else 1))
    return values


def vitals(person: str, when: date, rng: random.Random, page: int = 1, include=("bp", "pulse", "weight")) -> list[Value]:
    out = []
    if "bp" in include:
        out.append(Value("blood_pressure", "Blood Pressure", measured(person, "bp", when, rng), "mmHg", page=page))
    if "pulse" in include:
        out.append(Value("pulse", "Pulse", round(measured(person, "pulse", when, rng)), "bpm", page=page))
    if "weight" in include:
        out.append(Value("weight", "Weight", round(measured(person, "weight", when, rng)), "kg", page=page))
    return out


def build_docs(rng: random.Random) -> list[Doc]:
    docs: list[Doc] = []

    def lab(file, person, when, panels, provider, units=None, split_after=None, conditions=(), with_vitals=False, notes=""):
        values = (vitals(person, when, rng) if with_vitals else []) + lab_values(person, panels, when, rng, units, split_after)
        docs.append(Doc(file, "lab_typed", person, person, "lab_report", when, provider, values,
                        conditions=list(conditions), notes=notes))

    # --- typed lab PDFs (7)
    lab("lab_asha_2023-02.pdf", "Asha Verma", date(2023, 2, 8), ["diabetes", "lipids"], LABS[0], with_vitals=True)
    lab("lab_asha_2024-03.pdf", "Asha Verma", date(2024, 3, 13), ["diabetes", "lipids", "thyroid", "kidney"], LABS[0],
        split_after=5, conditions=["Prediabetes", "Dyslipidemia"], with_vitals=True, notes="two pages, table continues")
    lab("lab_asha_2025-01.pdf", "Asha Verma", date(2025, 1, 20), ["diabetes", "vitamins"], LABS[1])
    lab("lab_ravi_2023-06.pdf", "Ravi Verma", date(2023, 6, 2), ["kidney", "thyroid", "lipids"], LABS[2],
        units={"creatinine": ("umol/L", 1 / 88.42, "53 - 106")}, notes="creatinine in umol/L")
    lab("lab_ravi_2024-07.pdf", "Ravi Verma", date(2024, 7, 15), ["kidney", "thyroid"], LABS[2],
        conditions=["Subclinical hypothyroidism"])
    lab("lab_ravi_2025-02.pdf", "Ravi Verma", date(2025, 2, 11), ["diabetes", "lipids", "kidney"], LABS[3],
        units={"glucose_fasting": ("mmol/L", 18.016, "3.9 - 5.6"), "cholesterol_total": ("mmol/L", 38.67, "< 5.2")},
        split_after=4, notes="glucose and cholesterol in mmol/L, two pages")
    lab("lab_meera_2024-05.pdf", "Meera Verma", date(2024, 5, 4), ["cbc", "vitamins"], LABS[1],
        units={"hemoglobin": ("g/L", 0.1, "115 - 155")}, conditions=["Iron deficiency anaemia", "Vitamin D deficiency"],
        notes="hemoglobin in g/L")

    # --- scanned labs (6): image-only PDFs and phone photos, increasing degradation
    for i, (file, person, when, panels, strength) in enumerate([
        ("scan_lab_asha_2023-09.pdf", "Asha Verma", date(2023, 9, 14), ["diabetes"], 0.3),
        ("scan_lab_ravi_2023-11.pdf", "Ravi Verma", date(2023, 11, 21), ["kidney"], 0.5),
        ("scan_lab_meera_2023-08.jpg", "Meera Verma", date(2023, 8, 30), ["cbc"], 0.6),
        ("scan_lab_ravi_2024-12.jpg", "Ravi Verma", date(2024, 12, 9), ["thyroid", "lipids"], 0.8),
        ("scan_lab_asha_2024-08.jpg", "Asha Verma", date(2024, 8, 18), ["diabetes", "lipids"], 1.0),
        ("scan_lab_meera_2025-03.pdf", "Meera Verma", date(2025, 3, 6), ["vitamins", "cbc"], 1.2),
    ]):
        docs.append(Doc(file, "lab_scanned", person, person, "lab_report", when, LABS[i % len(LABS)],
                        lab_values(person, panels, when, rng), notes=f"degradation {strength}", strength=strength))

    # --- DOCX prescriptions (5)
    for file, person, when, meds, conds in [
        ("rx_asha_2024-03.docx", "Asha Verma", date(2024, 3, 20), ["Metformin 500 mg", "Atorvastatin 10 mg", "Telmisartan 20 mg"],
         ["Prediabetes", "Dyslipidemia", "Stage 1 hypertension"]),
        ("rx_asha_2025-01.docx", "Asha Verma", date(2025, 1, 27), ["Metformin 500 mg", "Atorvastatin 20 mg", "Cholecalciferol 60000 IU"],
         ["Prediabetes", "Vitamin D deficiency"]),
        ("rx_ravi_2024-07.docx", "Ravi Verma", date(2024, 7, 22), ["Levothyroxine 25 mcg", "Rosuvastatin 10 mg"],
         ["Subclinical hypothyroidism", "Dyslipidemia"]),
        ("rx_ravi_2025-02.docx", "Ravi Verma", date(2025, 2, 18), ["Levothyroxine 50 mcg", "Febuxostat 40 mg", "Rosuvastatin 10 mg"],
         ["Hypothyroidism", "Hyperuricemia"]),
        ("rx_meera_2024-05.docx", "Meera Verma", date(2024, 5, 10), ["Ferrous sulphate 100 mg", "Cholecalciferol 1000 IU"],
         ["Iron deficiency anaemia"]),
    ]:
        docs.append(Doc(file, "prescription_docx", person, person, "prescription", when, CLINICS[0 if person != "Meera Verma" else 2],
                        vitals(person, when, rng, include=("bp", "weight")), medications=meds, conditions=conds))

    # --- discharge summaries (4): values inside prose, across two pages
    for file, person, when, conds, meds in [
        ("discharge_ravi_2024-10.pdf", "Ravi Verma", date(2024, 10, 3), ["Acute gastroenteritis", "Pre-renal acute kidney injury"],
         ["Ondansetron 4 mg", "Oral rehydration salts", "Levothyroxine 25 mcg"]),
        ("discharge_asha_2023-12.pdf", "Asha Verma", date(2023, 12, 5), ["Community acquired pneumonia"],
         ["Amoxicillin-clavulanate 625 mg", "Paracetamol 650 mg"]),
        ("discharge_meera_2024-09.pdf", "Meera Verma", date(2024, 9, 12), ["Dengue fever without warning signs"],
         ["Paracetamol 250 mg"]),
        ("discharge_ravi_2025-04.pdf", "Ravi Verma", date(2025, 4, 2), ["Hypertensive urgency"],
         ["Amlodipine 5 mg", "Telmisartan 40 mg"]),
    ]:
        admit = vitals(person, when, rng, page=1, include=("bp", "pulse"))
        admit.append(Value("glucose_random", "Random blood sugar", round(measured(person, "glucose_fasting", when, rng) * 1.4), "mg/dL"))
        if "kidney" in conds[-1].lower():
            admit.append(Value("creatinine", "Serum creatinine", round(measured(person, "creatinine", when, rng) * 1.6, 2), "mg/dL"))
        if "Dengue" in conds[0]:
            admit.append(Value("platelets", "Platelet count", 0.9, "lakh/cumm"))
        discharge_vitals = vitals(person, when, rng, page=2, include=("bp",))
        docs.append(Doc(file, "discharge_summary", person, person, "discharge_summary", when, CLINICS[1],
                        admit + discharge_vitals, medications=meds, conditions=conds,
                        notes="values in prose; same metric on both pages (admission vs discharge)"))

    # --- OPD cards (4): photographed vitals cards
    for file, person, when in [
        ("opd_asha_2023-11.png", "Asha Verma", date(2023, 11, 5)),
        ("opd_ravi_2024-03.png", "Ravi Verma", date(2024, 3, 2)),
        ("opd_meera_2025-01.jpg", "Meera Verma", date(2025, 1, 15)),
        ("opd_asha_2025-06.jpg", "Asha Verma", date(2025, 6, 9)),
    ]:
        values = vitals(person, when, rng) + [Value("spo2", "SpO2", 98, "%")]
        docs.append(Doc(file, "opd_card", person, person, "consultation_note", when, CLINICS[1], values,
                        conditions=["Elevated blood pressure"] if person == "Asha Verma" and when.year == 2023 else []))

    # --- simulated handwritten prescriptions (2)
    docs.append(Doc("handwritten_rx_ravi_2024-03.png", "handwritten_rx", "Ravi Verma", "Ravi Verma", "prescription",
                    date(2024, 3, 2), CLINICS[3], vitals("Ravi Verma", date(2024, 3, 2), rng, include=("bp",)),
                    medications=["Levothyroxine 25 mcg", "Pantoprazole 40 mg"], notes="simulated handwriting"))
    docs.append(Doc("handwritten_rx_meera_2024-09.png", "handwritten_rx", "Meera Verma", "Meera Verma", "prescription",
                    date(2024, 9, 20), CLINICS[2], [Value("weight", "Weight", 41, "kg")],
                    medications=["Paracetamol 250 mg"], notes="simulated handwriting"))

    # --- edge cases (2)
    docs.append(Doc("edge_blank_scan.jpg", "edge", "Asha Verma", "Asha Verma", None, None, None,
                    expect_failure=True, notes="blank photo: pipeline should fail with 'No readable text'"))
    mismatch_when = date(2024, 6, 1)
    docs.append(Doc("edge_wrong_patient.pdf", "edge", "Asha Verma", "Sunita Rao", "lab_report", mismatch_when, LABS[3],
                    lab_values("Asha Verma", ["thyroid"], mismatch_when, rng),
                    notes="someone else's report uploaded to Asha's profile: expect a name-mismatch flag"))
    return docs


def render(doc: Doc, rng: random.Random) -> None:
    path = DOCS / doc.file
    date_style = rng.choice(DATE_STYLES)
    if doc.category == "lab_typed" or doc.file == "edge_wrong_patient.pdf":
        render_lab_pdf(doc, path, date_style)
    elif doc.category == "lab_scanned":
        render_scan(doc, path, date_style, rng, doc.strength)
    elif doc.category == "prescription_docx":
        render_docx(doc, path, date_style)
    elif doc.category == "discharge_summary":
        render_discharge(doc, path, date_style)
    elif doc.category == "opd_card":
        render_scan(doc, path, date_style, rng, 0.5)
    elif doc.category == "handwritten_rx":
        render_handwritten(doc, path, date_style, rng)
    elif doc.file == "edge_blank_scan.jpg":
        render_blank(path, rng)
    else:
        raise ValueError(doc.file)


# ---------------------------------------------------------------- RAG questions


def build_questions(docs: list[Doc]) -> list[dict]:
    keys = {d.file: answer_key(d)["expected"] for d in docs}

    def obs(file, code):
        return next(o for o in keys[file]["observations"] if o["code"] == code)

    def num(file, code):
        """The value as the app shows it: rounded to the metric's display precision."""
        return f"{round(obs(file, code)['value'], METRICS[code].decimals):g}"

    def page(file, code):
        return obs(file, code)["page"]

    q = []

    def add(question, must_include, sources, kind="fact", profile=None):
        q.append(dict(id=f"q{len(q) + 1:02d}", question=question, kind=kind, profile=profile,
                      must_include=must_include, expected_sources=[dict(file=f, page=p) for f, p in sources]))

    a24 = "lab_asha_2024-03.pdf"
    add("What was Asha's HbA1c in March 2024?", [num(a24, "hba1c")], [(a24, page(a24, "hba1c"))], "metric")
    rx24 = "rx_asha_2024-03.docx"
    add("What was Asha's BP in 2024?",
        [f"{obs(a24, 'bp_systolic')['value']:g}/{obs(a24, 'bp_diastolic')['value']:g}",
         f"{obs(rx24, 'bp_systolic')['value']:g}/{obs(rx24, 'bp_diastolic')['value']:g}"],
        [(a24, 1), (rx24, 1)], "metric")
    add("How has Asha's HbA1c changed over time?",
        [num("lab_asha_2023-02.pdf", "hba1c"), num(a24, "hba1c"), num("lab_asha_2025-01.pdf", "hba1c")],
        [("lab_asha_2023-02.pdf", 1), (a24, 1), ("lab_asha_2025-01.pdf", 1)], "trend")
    r23 = "lab_ravi_2023-06.pdf"
    printed = next(v for v in docs if v.file == r23).values
    creat_printed = next(v.printed() for v in printed if v.code == "creatinine")
    add("What was Ravi's creatinine in June 2023?",
        [[creat_printed, f"{obs(r23, 'creatinine')['value']:.2f}", f"{obs(r23, 'creatinine')['value']:.1f}"]],
        [(r23, page(r23, "creatinine"))], "metric")
    add("What was Ravi's TSH in July 2024?", [num("lab_ravi_2024-07.pdf", "tsh")], [("lab_ravi_2024-07.pdf", 1)], "metric")
    add("Which thyroid medicine is Ravi taking and at what dose?", ["Levothyroxine", "50 mcg"],
        [("rx_ravi_2025-02.docx", 1)], "medication")
    add("Why was Ravi admitted to hospital in October 2024?", ["gastroenteritis"],
        [("discharge_ravi_2024-10.pdf", 1)], "narrative")
    add("What medicines was Ravi given at discharge in April 2025?", ["Amlodipine", "Telmisartan"],
        [("discharge_ravi_2025-04.pdf", 2)], "medication")
    add("What was Meera's platelet count during dengue?", ["0.9"], [("discharge_meera_2024-09.pdf", 1)], "narrative")
    meera_hb = next(v.printed() for v in next(d for d in docs if d.file == "lab_meera_2024-05.pdf").values
                    if v.code == "hemoglobin")
    add("What was Meera's hemoglobin in May 2024?",  # printed in g/L; either unit is a correct answer
        [[f"{obs('lab_meera_2024-05.pdf', 'hemoglobin')['value']:g}", meera_hb]],
        [("lab_meera_2024-05.pdf", 1)], "metric")
    add("Which iron supplement was prescribed for Meera?", ["Ferrous sulphate"], [("rx_meera_2024-05.docx", 1)], "medication")
    add("Has Asha ever had pneumonia?", ["pneumonia"], [("discharge_asha_2023-12.pdf", 1)], "narrative")
    add("Is Ravi's uric acid high?", [num("lab_ravi_2025-02.pdf", "uric_acid")],
        [("lab_ravi_2025-02.pdf", page("lab_ravi_2025-02.pdf", "uric_acid"))], "metric")
    add("What vitamin D dose was Asha prescribed?", ["60000"], [("rx_asha_2025-01.docx", 1)], "medication")
    add("What was Ravi's fasting glucose in February 2025?", [num("lab_ravi_2025-02.pdf", "glucose_fasting")],
        [("lab_ravi_2025-02.pdf", 1)], "metric")
    # Not in any document: the answer must say so and cite nothing.
    for question in ["What is Ravi's blood group?", "Has Meera been vaccinated against measles?",
                     "What was Asha's eye power at her last eye test?", "Does anyone in the family have an allergy to penicillin?"]:
        add(question, [], [], "not_found")
    return q


def main() -> None:
    rng = random.Random(SEED)
    for directory in (DOCS, EXPECTED):
        shutil.rmtree(directory, ignore_errors=True)
        directory.mkdir(parents=True)
    docs = build_docs(rng)
    for doc in docs:
        render(doc, rng)
        (EXPECTED / f"{doc.file}.json").write_text(json.dumps(answer_key(doc), indent=2) + "\n")
    hard_rng = random.Random(SEED + 1)
    hard = build_hard_docs(hard_rng)
    for doc in hard:
        render_hard(doc, hard_rng)
        (EXPECTED / f"{doc.file}.json").write_text(json.dumps(answer_key(doc), indent=2) + "\n")
    docs += hard
    questions = build_questions(docs)
    (ROOT / "questions.json").write_text(json.dumps(questions, indent=2) + "\n")
    by_category: dict[str, int] = {}
    for doc in docs:
        by_category[doc.category] = by_category.get(doc.category, 0) + 1
    n_obs = sum(len(expected_observations(d)) for d in docs)
    print(f"{len(docs)} documents, {n_obs} expected values, {len(questions)} questions -> {ROOT}")
    for category, count in by_category.items():
        print(f"  {category:18} {count}")


if __name__ == "__main__":
    main()
