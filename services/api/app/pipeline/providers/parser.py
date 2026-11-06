"""Turn uploaded files into per-page text.

- Born-digital PDF pages and DOCX go through Docling, which keeps tables as Markdown.
- Images and scanned PDF pages (no text layer) go straight to Tesseract. Docling's layout
  model splits "Label: value" lines on scanned forms into separate regions, which scrambles
  which value belongs to which label; Tesseract's line-based output keeps them together.
"""

import csv
import io
import logging
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
OCR_DPI = 300
# Fewer chars than this on a PDF page means it's effectively a scan.
MIN_TEXT_LAYER_CHARS = 20


@dataclass
class ParsedPage:
    page_no: int
    text: str
    engine: str  # "docling" | "tesseract" | "gemini-vision"
    ocr_confidence: float | None = None  # Tesseract's mean word confidence (kept when vision re-reads)


VISION_MAX_SIDE = 2000  # px; keeps uploads small without hurting legibility
# Share of image rows containing ink strokes below which a page is treated as blank. Measured on the
# eval set: a blank phone photo scores 0.000; blurred, faded and handwritten pages score 0.07-0.88.
MIN_TEXT_ROWS = 0.02


def looks_blank(image) -> bool:
    """No text-like ink at all. Vision models invent plausible records for blank pages, so these
    must never be sent to them."""
    import numpy as np
    from PIL import ImageFilter, ImageOps

    gray = ImageOps.grayscale(image)
    gray.thumbnail((800, 800))
    pixels = np.asarray(gray, dtype=float)
    # Ink = darker than its surroundings; subtracting a heavy blur removes shadows and gradients.
    background = np.asarray(gray.filter(ImageFilter.GaussianBlur(25)), dtype=float)
    ink = (background - pixels) > 12
    return float((ink.mean(axis=1) > 0.02).mean()) < MIN_TEXT_ROWS


def get_vision_transcriber():
    """Callable(png_bytes) -> (text, model), or None when the fallback is off or unconfigured."""
    from app.config import get_settings

    settings = get_settings()
    if not settings.vision_ocr_enabled or not settings.gemini_api_key:
        return None
    from app.pipeline.providers.llm import get_llm

    return get_llm().transcribe_image


def parse_file(path: Path, mime: str) -> list[ParsedPage]:
    if mime == "application/pdf":
        return _parse_pdf(path)
    if mime == DOCX:
        return [ParsedPage(1, _docling_markdown(_docling_document(path)), "docling")]
    if mime.startswith("image/"):
        return [_ocr_page(1, _load_image(path))]
    raise ValueError(f"Unsupported type: {mime}")


def _parse_pdf(path: Path) -> list[ParsedPage]:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(path))
    try:
        scanned = {
            i + 1
            for i in range(len(pdf))
            if pdf[i].get_textpage().count_chars() < MIN_TEXT_LAYER_CHARS
        }
        pages: dict[int, ParsedPage] = {}
        for page_no in sorted(scanned):
            image = pdf[page_no - 1].render(scale=OCR_DPI / 72).to_pil()
            pages[page_no] = _ocr_page(page_no, image)
        num_pages = len(pdf)
    finally:
        pdf.close()

    if len(scanned) < num_pages:
        doc = _docling_document(path)
        for page_no in range(1, num_pages + 1):
            if page_no not in scanned:
                text = _docling_markdown(doc, page_no=page_no)
                pages[page_no] = ParsedPage(page_no, text, "docling")
    return [pages[n] for n in sorted(pages)]


@lru_cache
def _docling_converter():
    # Imported lazily: Docling pulls in PyTorch, which only the worker image installs.
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    # Scanned pages never reach Docling, so its OCR stays off.
    options = PdfPipelineOptions(do_ocr=False, do_table_structure=True, document_timeout=600)
    return DocumentConverter(
        allowed_formats=[InputFormat.PDF, InputFormat.DOCX],
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)},
    )


def _docling_document(path: Path):
    return _docling_converter().convert(path).document


def _docling_markdown(doc, page_no: int | None = None) -> str:
    return doc.export_to_markdown(page_no=page_no, escape_html=False).strip()


def _load_image(path: Path):
    import pillow_heif
    from PIL import Image, ImageOps

    pillow_heif.register_heif_opener()
    with Image.open(path) as img:
        # Phone photos store rotation in EXIF; apply it so text is upright for OCR.
        return ImageOps.exif_transpose(img).convert("RGB")


def _ocr_page(page_no: int, image) -> ParsedPage:
    buf = io.BytesIO()
    image.save(buf, "PNG")
    proc = subprocess.run(
        ["tesseract", "stdin", "stdout", "--psm", "4", "-l", "eng", "tsv"],
        input=buf.getvalue(),
        capture_output=True,
        check=True,
        timeout=300,
    )
    text, confidence = tsv_to_text(proc.stdout.decode("utf-8", errors="replace"))
    page = ParsedPage(page_no, text, "tesseract", confidence)
    return _maybe_vision(page, image)


def _maybe_vision(page: ParsedPage, image) -> ParsedPage:
    """Low-confidence OCR (blurry photos, handwriting): ask Gemini vision to re-read the page.
    On any failure keep Tesseract's text; the page still shows its low-confidence warning."""
    from app.config import get_settings
    from app.pipeline.providers.errors import PipelineError, TransientError

    threshold = get_settings().vision_ocr_threshold
    if page.ocr_confidence is not None and page.ocr_confidence >= threshold:
        return page
    transcribe = get_vision_transcriber()
    if transcribe is None or looks_blank(image):
        return page
    scaled = image.copy()
    scaled.thumbnail((VISION_MAX_SIDE, VISION_MAX_SIDE))
    buf = io.BytesIO()
    scaled.save(buf, "PNG")
    try:
        text, model = transcribe(buf.getvalue())
    except (TransientError, PipelineError) as exc:
        log.warning("Vision re-read of page %d failed, keeping Tesseract text: %s", page.page_no, exc)
        return page
    if not text.strip() or text.strip().upper().startswith("NO_TEXT"):
        return page
    log.info("Page %d re-read by %s (Tesseract confidence %s)", page.page_no, model, page.ocr_confidence)
    return ParsedPage(page.page_no, text.strip(), "gemini-vision", page.ocr_confidence)


def tsv_to_text(tsv: str) -> tuple[str, float | None]:
    """Rebuild reading-order text from Tesseract TSV; return it with mean word confidence (0-1)."""
    lines: dict[tuple[int, int, int], list[str]] = defaultdict(list)
    confidences: list[float] = []
    for row in csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE):
        word = (row.get("text") or "").strip()
        conf = float(row.get("conf") or -1)
        if not word or conf < 0:
            continue
        key = (int(row["block_num"]), int(row["par_num"]), int(row["line_num"]))
        lines[key].append(word)
        confidences.append(conf)

    out: list[str] = []
    previous_block = None
    for block, par, line in sorted(lines):
        if previous_block is not None and block != previous_block:
            out.append("")
        out.append(" ".join(lines[(block, par, line)]))
        previous_block = block
    confidence = round(sum(confidences) / len(confidences) / 100, 3) if confidences else None
    return "\n".join(out).strip(), confidence
