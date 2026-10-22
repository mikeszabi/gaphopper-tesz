"""Gaphopper – webes felület a parancssor mellé.

Régi, opcionális felület: streamlit run app.py.
Elsődleges webes felület: python -m gaphopper ui (http://localhost:8765).
"""
from __future__ import annotations

import streamlit as st

from ui import page_db, page_models, page_prompts, page_review, page_status, page_uploads

st.set_page_config(page_title="Gaphopper – TESZ-felbontás", layout="wide")

nav = st.navigation([
    st.Page(page_status.page, title="Állapot és futtatás", default=True),
    st.Page(page_uploads.page, title="Feltöltések", url_path="feltoltesek"),
    st.Page(page_review.page, title="Szakértői sor", url_path="szakertoi-sor"),
    st.Page(page_db.page, title="Adatbázis", url_path="adatbazis"),
    st.Page(page_models.page, title="Modellek", url_path="modellek"),
    st.Page(page_prompts.page, title="Promptok", url_path="promptok"),
])
nav.run()
