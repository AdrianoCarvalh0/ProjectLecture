from html import escape
from io import BytesIO
from uuid import uuid4

from ebooklib import epub


def document_as_epub(document):
    book = epub.EpubBook()
    book.set_identifier(f"projectlecture-{document.pk}-{uuid4()}")
    book.set_title(document.title)
    book.set_language("pt-BR")

    paragraphs = [
        paragraph.strip()
        for paragraph in document.extracted_text.splitlines()
        if paragraph.strip()
    ]
    content = "".join(f"<p>{escape(paragraph)}</p>" for paragraph in paragraphs)
    chapter = epub.EpubHtml(
        title=document.title,
        file_name="leitura.xhtml",
        lang="pt-BR",
    )
    chapter.content = f"<h1>{escape(document.title)}</h1>{content}"
    book.add_item(chapter)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.toc = (chapter,)
    book.spine = ["nav", chapter]

    output = BytesIO()
    epub.write_epub(output, book, {"raise_exceptions": True})
    output.seek(0)
    return output
