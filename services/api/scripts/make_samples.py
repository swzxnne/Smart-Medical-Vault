"""Generate synthetic medical documents (fictional patient) for local testing.

Usage: python scripts/make_samples.py [out_dir]   (default: ../../samples)
"""

import sys
from pathlib import Path

from docx import Document as DocxDocument
from fpdf import FPDF
from PIL import Image, ImageDraw, ImageFilter, ImageFont

PATIENT = "Asha Verma"

LAB_ROWS = [
    ("Hemoglobin", "13.1", "g/dL", "12.0 - 15.5"),
    ("Fasting Blood Glucose", "118", "mg/dL", "70 - 100"),
    ("HbA1c", "6.4", "%", "4.0 - 5.6"),
    ("Total Cholesterol", "212", "mg/dL", "< 200"),
    ("LDL Cholesterol", "138", "mg/dL", "< 100"),
    ("HDL Cholesterol", "44", "mg/dL", "> 40"),
    ("Triglycerides", "165", "mg/dL", "< 150"),
    ("TSH", "2.8", "uIU/mL", "0.4 - 4.0"),
    ("Serum Creatinine", "0.9", "mg/dL", "0.6 - 1.1"),
]


def lab_report_pdf(path: Path) -> None:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "City Diagnostics Laboratory", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 11)
    for line in [
        f"Patient: {PATIENT}    Age/Sex: 52 / F",
        "Sample collected: 12 March 2024    Report date: 13 March 2024",
        "Referred by: Dr. R. Menon",
        "Vitals at collection: Blood pressure 138/88 mmHg, Pulse 78 bpm, Weight 68 kg",
    ]:
        pdf.cell(0, 7, line, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)
    pdf.set_font("Helvetica", "B", 11)
    with pdf.table(col_widths=(60, 25, 25, 40)) as table:
        table.row(["Test", "Result", "Unit", "Reference range"])
        pdf.set_font("Helvetica", "", 11)
        for row in LAB_ROWS[:5]:
            table.row(list(row))

    pdf.add_page()
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 10, "Lipid & Thyroid Profile (continued)", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 11)
    with pdf.table(col_widths=(60, 25, 25, 40)) as table:
        table.row(["Test", "Result", "Unit", "Reference range"])
        for row in LAB_ROWS[5:]:
            table.row(list(row))
    pdf.ln(6)
    pdf.multi_cell(
        0,
        7,
        "Impression: Impaired fasting glucose with HbA1c in the prediabetic range. "
        "Borderline high total and LDL cholesterol. Thyroid and renal function normal. "
        "Suggest lifestyle modification and repeat HbA1c and lipid profile in 3 months.",
    )
    pdf.output(str(path))


def prescription_docx(path: Path) -> None:
    doc = DocxDocument()
    doc.add_heading("Sunrise Family Clinic", level=1)
    doc.add_paragraph(f"Patient: {PATIENT}        Date: 20 March 2024")
    doc.add_paragraph("Doctor: Dr. R. Menon, MD (Internal Medicine)")
    doc.add_heading("Diagnosis", level=2)
    doc.add_paragraph("Prediabetes; Dyslipidemia; Stage 1 hypertension (BP 136/86 mmHg)")
    doc.add_heading("Medications", level=2)
    table = doc.add_table(rows=1, cols=3)
    table.rows[0].cells[0].text, table.rows[0].cells[1].text, table.rows[0].cells[2].text = (
        "Medicine", "Dose", "Duration",
    )
    for med in [
        ("Metformin 500 mg", "1 tablet after dinner", "3 months"),
        ("Atorvastatin 10 mg", "1 tablet at night", "3 months"),
        ("Telmisartan 20 mg", "1 tablet in the morning", "3 months"),
    ]:
        cells = table.add_row().cells
        for cell, value in zip(cells, med):
            cell.text = value
    doc.add_heading("Advice", level=2)
    doc.add_paragraph("30 minutes brisk walk daily. Reduce sugar and refined carbs. Review with HbA1c after 3 months.")
    doc.save(str(path))


def scanned_bp_log(path_png: Path, path_pdf: Path) -> None:
    img = Image.new("RGB", (1240, 1754), "white")
    draw = ImageDraw.Draw(img)
    title = ImageFont.load_default(size=44)
    body = ImageFont.load_default(size=32)
    draw.text((90, 90), "Green Valley Hospital - OPD Card", fill="black", font=title)
    lines = [
        f"Name: {PATIENT}          Date: 05 November 2023",
        "Complaint: occasional headache, fatigue",
        "",
        "Vitals:",
        "  Blood Pressure: 146/92 mmHg",
        "  Pulse: 84 /min     SpO2: 98 %",
        "  Weight: 70 kg      Height: 158 cm",
        "  Random Blood Sugar: 162 mg/dL",
        "",
        "Assessment: Elevated blood pressure, rule out diabetes.",
        "Plan: Home BP monitoring for 2 weeks.",
        "      Fasting glucose, HbA1c and lipid profile.",
    ]
    for i, line in enumerate(lines):
        draw.text((90, 200 + i * 52), line, fill="black", font=body)
    # Make it look scanned: slight rotation and blur.
    img = img.rotate(1.2, fillcolor="white").filter(ImageFilter.GaussianBlur(0.8))
    img.save(path_png)
    img.save(path_pdf, "PDF", resolution=150)


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parents[3] / "samples")
    out.mkdir(parents=True, exist_ok=True)
    lab_report_pdf(out / "lab_report_2024-03.pdf")
    prescription_docx(out / "prescription_2024-03.docx")
    scanned_bp_log(out / "opd_card_2023-11.png", out / "opd_card_2023-11_scanned.pdf")
    print(f"Wrote samples to {out}")


if __name__ == "__main__":
    main()
