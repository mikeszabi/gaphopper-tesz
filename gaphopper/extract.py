"""E0 – a 0. lépés: beadott dokumentumokból (PDF, DOCX, TXT, MD, HTML, ODT, RTF) jobs.jsonl.

A dokumentum szövegét kód olvassa ki, részletekre vágja, a részleteket az E0 prompt dolgozza fel,
végül kód fésüli össze a részletek határán szétvágott vagy ismétlődő munkaköröket.
"""
from __future__ import annotations

import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from pathlib import Path

from .config import ROOT, prompt, render
from .llm import LLMError, ManualLLM, ManualStep, call_json
from .normalize import detect_lang, job_title_key, norm_key, stable_id
from .schemas import E0Output, ExtractedJob

SUPPORTED = {".pdf", ".docx", ".txt", ".md", ".html", ".htm", ".odt", ".rtf"}
MANUAL_DIR = ROOT / "runs" / "manual" / "extract"


class ExtractError(RuntimeError):
    pass


# ------------------------------------------------------------------ szövegkiolvasás
class _HTMLText(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "head"}
    BLOCK = {"p", "div", "li", "br", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article"}

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")
        if tag == "li":
            self.parts.append("- ")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def _read_plain(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp1250", "iso-8859-2"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _pdf(path: Path) -> str:
    text = ""
    try:
        from pypdf import PdfReader

        text = "\n\n".join((p.extract_text() or "") for p in PdfReader(str(path)).pages)
    except Exception:
        text = ""
    if len(text.strip()) < 50:  # tartalék: poppler pdftotext
        try:
            text = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True,
                                  text=True, check=True).stdout
        except (OSError, subprocess.CalledProcessError):
            pass
    if len(text.strip()) < 50:
        raise ExtractError(f"{path.name}: a PDF-ből nem olvasható ki szöveg (valószínűleg szkennelt; OCR kell).")
    return text


def _docx(path: Path) -> str:
    import docx

    d = docx.Document(str(path))
    parts = [p.text for p in d.paragraphs]
    for table in d.tables:
        for row in table.rows:
            parts.append(" | ".join(c.text.strip() for c in row.cells))
    return "\n".join(parts)


def _pandoc(path: Path) -> str:
    try:
        return subprocess.run(["pandoc", str(path), "-t", "plain", "--wrap=none"], capture_output=True,
                              text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ExtractError(f"{path.name}: ehhez a formátumhoz pandoc kell ({exc}).")


def read_text(path: Path) -> str:
    suf = path.suffix.lower()
    if suf == ".pdf":
        text = _pdf(path)
    elif suf == ".docx":
        text = _docx(path)
    elif suf in (".html", ".htm"):
        p = _HTMLText()
        p.feed(_read_plain(path))
        text = "".join(p.parts)
    elif suf in (".txt", ".md"):
        text = _read_plain(path)
    elif suf in (".odt", ".rtf"):
        text = _pandoc(path)
    else:
        raise ExtractError(f"Nem támogatott formátum: {path.name}")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def chunk_text(text: str, size: int, overlap: int) -> list[str]:
    """Bekezdéshatáron vág; a részletek között `overlap` karakter átfedés marad."""
    if len(text) <= size:
        return [text]
    paras = text.split("\n\n")
    chunks, cur = [], ""
    for p in paras:
        while len(p) > size:  # túl hosszú bekezdés: kemény vágás
            chunks.append((cur + "\n\n" + p[:size]).strip() if cur else p[:size])
            cur, p = "", p[size - overlap:]
        if len(cur) + len(p) + 2 > size and cur:
            chunks.append(cur.strip())
            cur = cur[-overlap:] + "\n\n" + p
        else:
            cur = (cur + "\n\n" + p) if cur else p
    if cur.strip():
        chunks.append(cur.strip())
    return chunks


# ------------------------------------------------------------------ összefésülés
def _job_key(j: ExtractedJob) -> str:
    return job_title_key(j.title)[0] + "|" + norm_key(j.employer)


def merge_jobs(items: list[ExtractedJob]) -> list[ExtractedJob]:
    merged: dict[str, ExtractedJob] = {}
    for j in items:
        k = _job_key(j)
        if k not in merged:
            merged[k] = j.model_copy(deep=True)
            continue
        m = merged[k]
        seen = {norm_key(t) for t in m.tasks}
        m.tasks += [t for t in j.tasks if norm_key(t) not in seen]
        for f in ("description", "requirements", "conditions", "entry_route", "field", "title_en"):
            a, b = getattr(m, f), getattr(j, f)
            if b and norm_key(b) not in norm_key(a):
                setattr(m, f, (a + "\n" + b).strip() if a else b)
        for code in ("feor", "isco", "esco", "onet"):
            if not getattr(m.codes, code) and getattr(j.codes, code):
                setattr(m.codes, code, getattr(j.codes, code))
        m.confidence = max(m.confidence, j.confidence)
        m.truncated = m.truncated and j.truncated
    return list(merged.values())


def to_record(j: ExtractedJob, doc_type: str, file_name: str, locale: str = "hu-HU") -> dict:
    prefix = {"JOB_AD": "ad", "CATALOG": "cat"}.get(doc_type, "doc")
    jid = stable_id(prefix, file_name, j.title, j.employer).replace(f"{prefix}_", f"{prefix}:", 1)
    return {
        "job_id": jid, "title": j.title, "title_en": j.title_en, "field": j.field,
        "description": j.description, "tasks": j.tasks, "requirements": j.requirements,
        "conditions": j.conditions, "entry_route": j.entry_route,
        "codes": {k: v for k, v in j.codes.model_dump().items() if v}, "employer": j.employer,
        "doc_type": doc_type, "lang": j.lang or detect_lang(j.title), "source": file_name,
        "source_quote": j.source_quote, "extract_confidence": j.confidence, "locale": locale,
    }


# ------------------------------------------------------------------ futtatás
def extract_file(ctx, path: Path, workers: int = 1) -> tuple[list[dict], list[str]]:
    """Egy dokumentum feldolgozása. Visszaadja: (rekordok, figyelmeztetések)."""
    text = read_text(path)
    s = ctx.settings
    chunks = chunk_text(text, s.chunk_chars, s.chunk_overlap)
    system = prompt("e0_system")
    template = prompt("e0_kinyeres")
    users = [render(template, file_name=path.name, chunk_no=str(i + 1), chunk_total=str(len(chunks)),
                    chunk_text=c) for i, c in enumerate(chunks)]
    llm = ctx.llm("E0")
    warnings: list[str] = []

    def one(i: int):
        model = llm
        if isinstance(llm, ManualLLM):
            d = MANUAL_DIR / path.stem
            model = ManualLLM(llm.cfg, "E0", d / f"E0_{i + 1}_answer.json")
            try:
                return i, call_json(model, system, users[i], E0Output)[0], []
            except ManualStep as ms:
                d.mkdir(parents=True, exist_ok=True)
                (d / "E0_system.md").write_text(ms.system, encoding="utf-8")
                (d / f"E0_{i + 1}_user.md").write_text(ms.user, encoding="utf-8")
                return i, None, [f"kézi mód: {d / f'E0_{i + 1}_user.md'} -> E0_{i + 1}_answer.json"]
        logs: list = []
        try:
            obj, _ = call_json(model, system, users[i], E0Output,
                               log=lambda raw, ok, usage: logs.append((raw, ok, usage)))
            return i, obj, logs
        except LLMError as exc:
            return i, None, [f"{path.name} {i + 1}. részlet: {exc}"]

    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        results = list(ex.map(one, range(len(chunks))))
    cfg = s.e0
    jobs: list[ExtractedJob] = []
    doc_types: list[str] = []
    for i, obj, extra in sorted(results, key=lambda r: r[0]):
        if obj is None:
            warnings += extra
            continue
        for raw, ok, usage in extra:
            ctx.store.log(None, "E0", cfg.provider, cfg.model, ctx.pv, path.name, raw, ok, usage)
        doc_types.append(obj.doc_type)
        jobs += obj.jobs
    doc_type = max(set(doc_types), key=doc_types.count) if doc_types else "OTHER"
    merged = merge_jobs(jobs)
    for j in merged:
        if j.truncated:
            warnings.append(f"{path.name}: '{j.title}' leírása csonka lehet (a részlet határán vágódott).")
        if j.confidence < 0.5:
            warnings.append(f"{path.name}: '{j.title}' bizonytalan kinyerés ({j.confidence:.2f}).")
    return [to_record(j, doc_type, path.name) for j in merged], warnings


def iter_files(paths: list[str]) -> list[Path]:
    out = []
    for p in map(Path, paths):
        if p.is_dir():
            out += sorted(f for f in p.rglob("*") if f.suffix.lower() in SUPPORTED and f.is_file())
        elif p.suffix.lower() in SUPPORTED:
            out.append(p)
    return out


def write_jsonl(records: list[dict], out: Path, append: bool = True) -> int:
    """Hozzáfűz; azonos job_id esetén a régi sort lecseréli. Visszaadja a sorok számát."""
    existing: dict[str, dict] = {}
    if append and out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                existing[r["job_id"]] = r
    for r in records:
        existing[r["job_id"]] = r
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        for r in existing.values():
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(existing)
