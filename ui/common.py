"""Közös segédek a felület oldalainak."""
from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from gaphopper.config import Settings, settings
from gaphopper.store import Store

STATUS_HU = {
    "NYERS": "feldolgozásra vár", "P1_KOTEGBEN": "P1 kötegben", "P1_KESZ": "P1 kész, bírálatra vár",
    "P2_KOTEGBEN": "P2 kötegben", "P2_ELFOGADVA": "elfogadva, gráfba írható", "SZAKERTORE": "szakértőre vár",
    "GRAFBAN": "a gráfban", "ELVETVE": "elvetve", "HIBA": "hiba",
}


def cfg() -> Settings:
    return settings()


def store() -> Store:
    return Store(cfg().state_db)


@st.cache_resource(show_spinner=False)
def _graph(uri: str, user: str, password: str, database: str):
    from gaphopper.graph import Graph

    s = Settings()
    s.neo4j_uri, s.neo4j_user, s.neo4j_password, s.neo4j_database = uri, user, password, database
    return Graph(s)


def graph():
    """neo4j-kapcsolat, vagy None (a hibát kiírja)."""
    s = cfg()
    try:
        g = _graph(s.neo4j_uri, s.neo4j_user, s.neo4j_password, s.neo4j_database)
        g.driver.verify_connectivity()
        return g
    except Exception as exc:  # nem fut a neo4j, rossz jelszó, hiányzó csomag
        st.warning(f"A neo4j nem érhető el ({s.neo4j_uri}): {exc}")
        return None


def df(rows, columns: list[str] | None = None) -> pd.DataFrame:
    rows = [dict(r) for r in rows]
    d = pd.DataFrame(rows)
    if columns and not d.empty:
        d = d[[c for c in columns if c in d.columns]]
    return d


def tesz_table(rows: list[dict]) -> pd.DataFrame:
    cols = ["cand_id", "label_hu", "tesz_type", "primary_szer", "kozeg", "helyzet", "relevance",
            "expected_eqf", "confidence"]
    out = []
    for t in rows:
        r = {c: t.get(c) for c in cols}
        r["primary_szer"] = (t.get("primary_szer") or {}).get("label_hu")
        out.append(r)
    return pd.DataFrame(out)


def show_json(obj, expanded: bool = False) -> None:
    st.json(json.loads(json.dumps(obj, default=str)), expanded=expanded)
