"""In-memory parsing of bounded text/PDF/DOCX inputs, without saving uploads."""
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile
from fastapi import HTTPException

MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TEXT_CHARS = 60000


class PlainHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.skip = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in ('script','style','head'):
            self.skip += 1
        elif tag in ('p','div','br','li','tr','h1','h2','h3'):
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in ('script','style','head') and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def parse_file(filename, raw):
    if len(raw) > MAX_FILE_BYTES:
        raise HTTPException(413, 'A fájl legfeljebb 8 MB lehet.')
    extension = Path(filename or '').suffix.lower()
    if extension not in {'.pdf','.docx','.txt','.md','.html','.htm'}:
        raise HTTPException(415, 'Támogatott fájlok: PDF, DOCX, TXT, MD, HTML.')
    try:
        if extension == '.pdf':
            from pypdf import PdfReader
            reader = PdfReader(BytesIO(raw))
            if reader.is_encrypted:
                raise HTTPException(422, 'A jelszóval védett PDF nem olvasható.')
            if len(reader.pages)>80:
                raise HTTPException(413, 'Legfeljebb 80 oldalas PDF tölthető fel.')
            parts, length = [], 0
            for page in reader.pages:
                contents = page.get_contents()
                if contents is not None and len(contents.get_data()) > 12 * 1024 * 1024:
                    raise HTTPException(413, 'Túl összetett PDF-oldal.')
                text = page.extract_text() or ''
                length += len(text)
                if length > MAX_TEXT_CHARS:
                    raise HTTPException(413, 'A dokumentum szövege legfeljebb 60 000 karakter lehet.')
                parts.append(text)
            text = '\n'.join(parts)
        elif extension == '.docx':
            from docx import Document
            with ZipFile(BytesIO(raw)) as archive:
                if sum(f.file_size for f in archive.infolist()) > 32 * 1024 * 1024:
                    raise HTTPException(413, 'A kicsomagolt dokumentum túl nagy.')
            document = Document(BytesIO(raw))
            text = '\n'.join([p.text for p in document.paragraphs] +
                             [' | '.join(c.text for c in row.cells) for t in document.tables for row in t.rows])
        else:
            for encoding in ('utf-8-sig','cp1250'):
                try:
                    text = raw.decode(encoding)
                    break
                except UnicodeDecodeError:
                    continue
            else:
                raise HTTPException(422, 'A szöveg kódolása nem olvasható; használj UTF-8 kódolást.')
            if extension in ('.html','.htm'):
                parser = PlainHTML()
                parser.feed(text)
                text = ''.join(parser.parts)
        text = text.replace('\x00','').strip()
        if len(text)>MAX_TEXT_CHARS:
            raise HTTPException(413, 'A dokumentum szövege legfeljebb 60 000 karakter lehet.')
        if len(text)<20:
            raise HTTPException(422, 'Nem olvasható ki elegendő szöveg. Szkennelt PDF esetén előbb OCR szükséges.')
        return text
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(422, 'A dokumentum sérült vagy nem olvasható.') from None
