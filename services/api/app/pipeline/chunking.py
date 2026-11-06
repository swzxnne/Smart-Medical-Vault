"""Split page text into retrieval chunks.

Chunks never cross page boundaries, so every chunk cites exactly one page. Markdown tables
stay whole where possible; oversized tables are split by rows with the header repeated.
"""

from dataclasses import dataclass

MAX_CHARS = 1800  # ~450 tokens


@dataclass
class TextChunk:
    page_no: int
    chunk_index: int
    text: str


def chunk_pages(pages: list[tuple[int, str]], max_chars: int = MAX_CHARS) -> list[TextChunk]:
    chunks: list[TextChunk] = []
    for page_no, text in pages:
        for piece in _pack(_split_blocks(text, max_chars), max_chars):
            chunks.append(TextChunk(page_no=page_no, chunk_index=len(chunks), text=piece))
    return chunks


def _split_blocks(text: str, max_chars: int) -> list[str]:
    """Paragraphs and tables, each no longer than max_chars."""
    blocks: list[str] = []
    for raw in _paragraphs(text):
        if len(raw) <= max_chars:
            blocks.append(raw)
        elif _is_table(raw):
            blocks.extend(_split_table(raw, max_chars))
        else:
            blocks.extend(_split_long_text(raw, max_chars))
    return blocks


def _paragraphs(text: str) -> list[str]:
    """Blank-line separated blocks, with consecutive table lines kept in one block."""
    blocks: list[str] = []
    current: list[str] = []
    in_table = False
    for line in text.splitlines():
        is_table_line = line.lstrip().startswith("|")
        if not line.strip() or is_table_line != in_table:
            if current:
                blocks.append("\n".join(current))
            current = []
        if line.strip():
            current.append(line)
        in_table = is_table_line
    if current:
        blocks.append("\n".join(current))
    return blocks


def _is_table(block: str) -> bool:
    return all(line.lstrip().startswith("|") for line in block.splitlines())


def _split_table(block: str, max_chars: int) -> list[str]:
    lines = block.splitlines()
    header = lines[:2] if len(lines) > 1 and set(lines[1].replace("|", "").strip()) <= set("-: ") else lines[:1]
    rows = lines[len(header) :]
    parts: list[str] = []
    current = list(header)
    for row in rows:
        if len("\n".join(current + [row])) > max_chars and len(current) > len(header):
            parts.append("\n".join(current))
            current = list(header)
        current.append(row)
    parts.append("\n".join(current))
    # A header plus one very wide row can exceed max_chars; keep it whole rather than drop data.
    return parts


def _split_long_text(block: str, max_chars: int) -> list[str]:
    parts: list[str] = []
    current = ""
    for word in block.split():
        candidate = f"{current} {word}" if current else word
        if len(candidate) > max_chars and current:
            parts.append(current)
            candidate = word
        current = candidate
    if current:
        parts.append(current)
    return parts


def _pack(blocks: list[str], max_chars: int) -> list[str]:
    """Greedily merge consecutive blocks up to max_chars."""
    out: list[str] = []
    current = ""
    for block in blocks:
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) > max_chars and current:
            out.append(current)
            candidate = block
        current = candidate
    if current:
        out.append(current)
    return out
