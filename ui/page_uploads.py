"""Feltöltések: dokumentumok (0. lépés, E0) és munkakör-rekordok (import)."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from gaphopper.config import ROOT
from ui import tasks
from ui.common import store

UPLOAD_DIR = ROOT / "data" / "uploads"
DATA_DIR = ROOT / "data"
DOC_TYPES = ["pdf", "docx", "txt", "md", "html", "htm", "odt", "rtf"]
JOB_TYPES = ["jsonl", "json", "csv", "xlsx"]


def _save(files, folder: Path) -> list[str]:
    folder.mkdir(parents=True, exist_ok=True)
    saved = []
    for f in files:
        name = Path(f.name).name  # csak a fájlnév, mappa nélkül
        (folder / name).write_bytes(f.getbuffer())
        saved.append(name)
    return saved


def _listing(folder: Path, exts: list[str]) -> pd.DataFrame:
    rows = []
    if folder.exists():
        for p in sorted(folder.iterdir()):
            if p.is_file() and p.suffix.lower().lstrip(".") in exts:
                st_ = p.stat()
                rows.append({"kijelöl": False, "fájl": p.name, "méret (kB)": round(st_.st_size / 1024, 1),
                             "módosítva": datetime.fromtimestamp(st_.st_mtime).strftime("%Y-%m-%d %H:%M")})
    return pd.DataFrame(rows)


def page() -> None:
    st.title("Feltöltések")
    t_docs, t_jobs = st.tabs(["Dokumentumok (0. lépés)", "Munkakör-rekordok"])

    with t_docs:
        st.caption("Álláshirdetés, munkaköri leírás vagy katalógus. A kinyerés (E0) munkakör-rekordokat "
                   "készít belőlük; szkennelt PDF-hez előbb szövegfelismerés kell.")
        up = st.file_uploader("Dokumentumok feltöltése", type=DOC_TYPES, accept_multiple_files=True, key="up_docs")
        if up and st.button("Mentés a data/uploads mappába"):
            st.success("Mentve: " + ", ".join(_save(up, UPLOAD_DIR)))
        lst = _listing(UPLOAD_DIR, DOC_TYPES)
        if lst.empty:
            st.info("A data/uploads mappa üres.")
        else:
            ed = st.data_editor(lst, hide_index=True, disabled=["fájl", "méret (kB)", "módosítva"], key="docs_ed")
            chosen = [r["fájl"] for r in ed.to_dict("records") if r["kijelöl"]]
            c1, c2, c3 = st.columns([2, 2, 1])
            out = c1.text_input("Kimeneti fájl", "data/jobs_dokumentumokbol.jsonl")
            do_import = c2.checkbox("Rögtön importálja is", True)
            if c1.button("Kinyerés (E0) a kijelöltekre", type="primary", disabled=not chosen):
                args = ["extract", *[str(UPLOAD_DIR / n) for n in chosen], "--out", out]
                if do_import:
                    args.append("--import")
                tasks.start(f"extract ({len(chosen)} fájl)", args)
                st.rerun()
            if c3.button("Törlés", disabled=not chosen):
                for n in chosen:
                    (UPLOAD_DIR / n).unlink(missing_ok=True)
                st.rerun()
        tasks.panel("uploads")

    with t_jobs:
        st.caption("JSONL / JSON / CSV / Excel munkakör-rekordok. Importáláskor az új és a megváltozott rekord "
                   "feldolgozásra vár (NYERS) állapotba kerül; a változatlan nem fut újra.")
        up = st.file_uploader("Rekordfájl feltöltése", type=JOB_TYPES, accept_multiple_files=True, key="up_jobs")
        if up and st.button("Mentés a data mappába"):
            st.success("Mentve: " + ", ".join(_save(up, DATA_DIR)))
        lst = _listing(DATA_DIR, JOB_TYPES)
        if lst.empty:
            st.info("A data mappában nincs rekordfájl.")
            return
        name = st.selectbox("Fájl", lst["fájl"].tolist())
        path = DATA_DIR / name
        if path.suffix == ".jsonl":
            lines = path.read_text(encoding="utf-8").splitlines()
            st.caption(f"{len(lines)} sor; az első 20:")
            prev = [json.loads(x) for x in lines[:20] if x.strip()]
            st.dataframe(pd.DataFrame([{k: r.get(k) for k in ("job_id", "title", "field", "source")}
                                       | {"feladatok": len(r.get("tasks") or [])} for r in prev]), hide_index=True)
        if st.button("Importálás", type="primary"):
            from gaphopper.importers import load

            try:
                recs = load(path)
            except Exception as exc:  # formátumhiba
                st.error(f"Nem olvasható: {exc}")
                return
            S = store()
            new = sum(S.upsert_job(r) for r in recs)
            st.success(f"{len(recs)} rekord beolvasva, ebből {new} új vagy változott.")
