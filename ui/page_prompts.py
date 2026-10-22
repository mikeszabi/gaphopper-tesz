"""Promptok szerkesztése, mentés előtti eltérés, korábbi változatok visszaállítása."""
from __future__ import annotations

import difflib
from datetime import datetime

import streamlit as st

from gaphopper.config import PROMPT_DIR, VOCAB_PATH, prompt_version

HISTORY = PROMPT_DIR / ".history"
ABOUT = {
    "p0_system": "közös rendszerprompt (P1, P2, P4, P5): fogalmak, zárt listák, szabályok",
    "p1_felbontas": "P1: munkakör felbontása TESZ-nivalókra",
    "p2_biralat": "P2: bírálat, csak eltéréseket ad vissza",
    "p3_system": "P3: az egyesítő rövid rendszerpromptja",
    "p3_egyesites": "P3: kétes egyesítések egy hívásban",
    "p4a_keszseg": "P4a: KÉSZ-ség-horgonyzás",
    "p4b_eqf": "P4b: EQF-szintleírók",
    "p5_tanusitas": "P5: igazolási mondat és rubrika kanonikus TESZ-enként",
    "e0_system": "E0: kinyerés rendszerprompt",
    "e0_kinyeres": "E0: munkakörök kinyerése dokumentumrészletből",
    "vocab": "zárt szótárak és szabálykuszöbök (config/vocab.yaml)",
}


def _path(name: str):
    return VOCAB_PATH if name == "vocab" else PROMPT_DIR / f"{name}.md"


def _diff(a: str, b: str, na: str, nb: str) -> str:
    return "".join(difflib.unified_diff(a.splitlines(True), b.splitlines(True), na, nb, n=2)) or "(nincs eltérés)"


def page() -> None:
    st.title("Promptok")
    st.caption(f"Jelenlegi promptverzió: `{prompt_version()}`. Minden mentés új verziót ad; a már kész munkakörök "
               "nem futnak újra maguktól (az Állapot oldalon visszaállíthatók NYERS-re).")
    names = [p.stem for p in sorted(PROMPT_DIR.glob("*.md"))] + ["vocab"]
    name = st.selectbox("Fájl", names, format_func=lambda n: f"{n} – {ABOUT.get(n, '')}")
    path = _path(name)
    current = path.read_text(encoding="utf-8")
    text = st.text_area("Tartalom", current, height=520, key=f"pr_{name}")
    if text != current:
        with st.expander("Eltérés a mentetthez képest", expanded=True):
            st.code(_diff(current, text, "mentett", "szerkesztett"), language="diff")
    c1, c2 = st.columns(2)
    if c1.button("Mentés", type="primary", disabled=text == current):
        if name == "vocab":
            import yaml

            try:
                yaml.safe_load(text)
            except yaml.YAMLError as exc:
                st.error(f"Hibás YAML: {exc}")
                return
        HISTORY.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        (HISTORY / f"{name}.{stamp}{path.suffix}").write_text(current, encoding="utf-8")
        path.write_text(text, encoding="utf-8")
        if name == "vocab":
            from gaphopper.config import vocab

            vocab.cache_clear()
        st.success(f"Mentve; a régi változat: .history/{name}.{stamp}{path.suffix}")
        st.rerun()

    hist = sorted(HISTORY.glob(f"{name}.*"), reverse=True) if HISTORY.exists() else []
    if hist:
        st.subheader("Korábbi változatok")
        h = st.selectbox("Változat", hist, format_func=lambda p: p.name)
        old = h.read_text(encoding="utf-8")
        st.code(_diff(old, current, h.name, "jelenlegi"), language="diff")
        if c2.button(f"Visszaállítás: {h.name}"):
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            (HISTORY / f"{name}.{stamp}{path.suffix}").write_text(current, encoding="utf-8")
            path.write_text(old, encoding="utf-8")
            st.rerun()
