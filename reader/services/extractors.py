import math
import re
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import fitz
from bs4 import BeautifulSoup
from docx import Document as DocxDocument
from ebooklib import ITEM_DOCUMENT, epub


class ExtractionError(ValueError):
    pass


@dataclass
class ExtractedPage:
    number: int
    text: str


@dataclass
class ExtractedPart:
    order: int
    text: str
    page_start: int | None = None
    page_end: int | None = None


PAGE_NUMBER_PATTERN = re.compile(
    r"^[\s\-–—|·•]*"
    r"(?:p(?:á|a)g(?:ina)?\.?\s*)?"
    r"(?:\d{1,5}|[ivxlcdm]{1,12})"
    r"(?:\s*(?:/|de)\s*\d{1,5})?"
    r"[\s\-–—|·•]*$",
    re.IGNORECASE,
)
DECORATIVE_PATTERN = re.compile(r"(?:_{2,}|[-–—]{4,}|[.·•]{5,})")
PDF_MARGIN_RATIO = 0.18


def _clean_line(line):
    line = line.replace("\x00", " ").replace("\u00ad", "")
    line = DECORATIVE_PATTERN.sub(" ", line)
    return re.sub(r"\s+", " ", line).strip()


def _normalize(text):
    lines = [_clean_line(line) for line in text.splitlines()]
    cleaned = []
    for line in lines:
        if not line:
            if cleaned and cleaned[-1] != "":
                cleaned.append("")
            continue
        if not any(character.isalnum() for character in line):
            continue
        if cleaned and cleaned[-1].endswith("-") and line[:1].islower():
            cleaned[-1] = cleaned[-1][:-1] + line
        else:
            cleaned.append(line)
    return "\n".join(cleaned).strip()


def _margin_key(text):
    normalized = _clean_line(text).casefold()
    normalized = re.sub(r"\d+", "#", normalized)
    return normalized[:240]


def extract_pdf_pages(uploaded_file):
    uploaded_file.seek(0)
    try:
        with fitz.open(stream=uploaded_file.read(), filetype="pdf") as pdf:
            pages = []
            margin_counts = Counter()
            page_blocks = []
            for page_index, page in enumerate(pdf):
                width = max(float(page.rect.width), 1.0)
                height = max(float(page.rect.height), 1.0)
                blocks = []
                page_margin_keys = set()
                page_data = page.get_text("dict", sort=True)
                for block in page_data.get("blocks", []):
                    if block.get("type") != 0:
                        continue
                    for line in block.get("lines", []):
                        x0, y0, x1, y1 = (
                            float(value) for value in line.get("bbox", (0, 0, 0, 0))
                        )
                        raw_text = "".join(
                            span.get("text", "")
                            for span in line.get("spans", [])
                        )
                        text = _normalize(raw_text)
                        if not text:
                            continue
                        in_margin = (
                            y1 <= height * PDF_MARGIN_RATIO
                            or y0 >= height * (1 - PDF_MARGIN_RATIO)
                        )
                        key = _margin_key(text) if in_margin else ""
                        if key:
                            page_margin_keys.add(key)
                        blocks.append(
                            (x0, y0, x1, y1, width, height, text, key)
                        )
                margin_counts.update(page_margin_keys)
                page_blocks.append((page_index + 1, blocks))

            repeated_threshold = max(2, math.ceil(len(page_blocks) * 0.4))
            repeated_margins = {
                key for key, count in margin_counts.items() if count >= repeated_threshold
            }

            for page_number, blocks in page_blocks:
                selected = []
                for x0, y0, x1, y1, width, height, text, key in blocks:
                    in_margin = (
                        y1 <= height * PDF_MARGIN_RATIO
                        or y0 >= height * (1 - PDF_MARGIN_RATIO)
                    )
                    compact = re.sub(r"\s+", " ", text).strip()
                    if key and key in repeated_margins:
                        continue
                    if in_margin and PAGE_NUMBER_PATTERN.fullmatch(compact):
                        continue
                    selected.append(text)
                pages.append(
                    ExtractedPage(
                        number=page_number,
                        text=_normalize("\n".join(selected)),
                    )
                )
            return pages
    except Exception as exc:
        raise ExtractionError(f"Não foi possível ler o PDF: {exc}") from exc


def extract_text(uploaded_file):
    extension = Path(uploaded_file.name).suffix.lower()
    uploaded_file.seek(0)
    try:
        if extension == ".txt":
            raw = uploaded_file.read()
            for encoding in ("utf-8-sig", "utf-8", "latin-1"):
                try:
                    return _normalize(raw.decode(encoding))
                except UnicodeDecodeError:
                    continue
        if extension == ".pdf":
            return _normalize(
                "\n\n".join(page.text for page in extract_pdf_pages(uploaded_file))
            )
        if extension == ".docx":
            document = DocxDocument(uploaded_file)
            return _normalize("\n".join(paragraph.text for paragraph in document.paragraphs))
        if extension == ".epub":
            with tempfile.NamedTemporaryFile(suffix=".epub") as temporary:
                temporary.write(uploaded_file.read())
                temporary.flush()
                book = epub.read_epub(temporary.name, options={"ignore_ncx": True})
                sections = []
                for item in book.get_items_of_type(ITEM_DOCUMENT):
                    soup = BeautifulSoup(item.get_content(), "html.parser")
                    sections.append(soup.get_text("\n", strip=True))
                return _normalize("\n\n".join(sections))
    except ExtractionError:
        raise
    except Exception as exc:
        raise ExtractionError(f"Não foi possível ler o arquivo: {exc}") from exc
    raise ExtractionError("Formato de arquivo não suportado.")


def _split_oversized_text(text, max_characters):
    paragraphs = [part.strip() for part in re.split(r"\n{2,}", text) if part.strip()]
    chunks = []
    current = ""
    for paragraph in paragraphs:
        remaining = paragraph
        while len(remaining) > max_characters:
            boundary = remaining.rfind(" ", 0, max_characters)
            boundary = boundary if boundary >= max_characters // 2 else max_characters
            piece, remaining = remaining[:boundary].strip(), remaining[boundary:].strip()
            if current:
                chunks.append(current)
                current = ""
            if piece:
                chunks.append(piece)
        candidate = f"{current}\n\n{remaining}".strip() if current else remaining
        if len(candidate) > max_characters and current:
            chunks.append(current)
            current = remaining
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def split_into_parts(uploaded_file, max_characters, max_pages=None):
    extension = Path(uploaded_file.name).suffix.lower()
    if extension != ".pdf":
        text = extract_text(uploaded_file)
        return [
            ExtractedPart(order=index, text=chunk)
            for index, chunk in enumerate(
                _split_oversized_text(text, max_characters)
            )
        ]

    pages = extract_pdf_pages(uploaded_file)
    parts = []
    current_pages = []
    current_text = ""

    def flush():
        nonlocal current_pages, current_text
        if current_text.strip():
            parts.append(
                ExtractedPart(
                    order=len(parts),
                    text=current_text.strip(),
                    page_start=current_pages[0],
                    page_end=current_pages[-1],
                )
            )
        current_pages = []
        current_text = ""

    for page in pages:
        if not page.text:
            continue
        candidate = (
            f"{current_text}\n\n{page.text}".strip()
            if current_text
            else page.text
        )
        reached_page_limit = bool(
            max_pages
            and current_pages
            and page.number - current_pages[0] >= max_pages
        )
        if current_text and (
            len(candidate) > max_characters or reached_page_limit
        ):
            flush()
            candidate = page.text
        current_text = candidate
        current_pages.append(page.number)
        if len(current_text) >= max_characters:
            flush()
    flush()
    return parts


def create_pdf_slice(pdf_bytes, page_start, page_end):
    """Return a standalone PDF containing the inclusive one-based page range."""
    try:
        with fitz.open(stream=pdf_bytes, filetype="pdf") as source:
            if page_start < 1 or page_end < page_start or page_end > source.page_count:
                raise ExtractionError("Intervalo de páginas inválido ao dividir o PDF.")
            with fitz.open() as target:
                target.insert_pdf(
                    source,
                    from_page=page_start - 1,
                    to_page=page_end - 1,
                )
                return target.tobytes(garbage=4, deflate=True)
    except ExtractionError:
        raise
    except Exception as exc:
        raise ExtractionError(
            f"Não foi possível criar uma parte física do PDF: {exc}"
        ) from exc


def source_type_for(filename):
    extension = Path(filename).suffix.lower().lstrip(".")
    return extension if extension in {"pdf", "docx", "epub", "txt"} else "text"
