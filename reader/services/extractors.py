import tempfile
from pathlib import Path

import fitz
from bs4 import BeautifulSoup
from docx import Document as DocxDocument
from ebooklib import ITEM_DOCUMENT, epub


class ExtractionError(ValueError):
    pass


def _normalize(text):
    lines = [line.strip() for line in text.replace("\x00", "").splitlines()]
    cleaned = []
    for line in lines:
        if not line:
            if cleaned and cleaned[-1] != "":
                cleaned.append("")
            continue
        if cleaned and cleaned[-1].endswith("-") and line[:1].islower():
            cleaned[-1] = cleaned[-1][:-1] + line
        else:
            cleaned.append(line)
    return "\n".join(cleaned).strip()


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
            with fitz.open(stream=uploaded_file.read(), filetype="pdf") as pdf:
                return _normalize("\n\n".join(page.get_text("text") for page in pdf))
        if extension == ".docx":
            document = DocxDocument(uploaded_file)
            return _normalize("\n".join(p.text for p in document.paragraphs))
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
    except Exception as exc:
        raise ExtractionError(f"Não foi possível ler o arquivo: {exc}") from exc
    raise ExtractionError("Formato de arquivo não suportado.")


def source_type_for(filename):
    extension = Path(filename).suffix.lower().lstrip(".")
    return extension if extension in {"pdf", "docx", "epub", "txt"} else "text"
