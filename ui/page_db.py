"""Adatbázis: a futási SQLite és a neo4j-gráf szemrevételezése (csak olvasás)."""
from __future__ import annotations

import json
import sqlite3

import pandas as pd
import streamlit as st

from ui.common import cfg, df, graph, show_json

TABLES = ["jobs", "artifacts", "llm_log", "review_queue", "alias_index", "batches"]
LONG = {"record_json", "json", "raw_text", "payload_json", "decision_json", "map_json"}

CYPHER = {
    "Csomópontok száma": "MATCH (n) RETURN labels(n)[0] AS tipus, count(*) AS db ORDER BY db DESC",
    "Munkakörök TESZ-számmal": "MATCH (m:MunkaKor) OPTIONAL MATCH (m)-[:TARTALMAZ_TESZT]->(t) "
                               "RETURN m.id AS id, m.label_hu AS munkakor, count(t) AS tesz_db ORDER BY munkakor",
    "Legtöbb munkakörben szereplő TESZ": "MATCH (m:MunkaKor)-[:TARTALMAZ_TESZT]->(t:TeszNivalo) "
                                         "RETURN t.id AS id, t.label_hu AS tesz, count(m) AS munkakor_db "
                                         "ORDER BY munkakor_db DESC LIMIT 50",
    "SZER-ELEM-ek használat szerint": "MATCH (t:TeszNivalo)-[:IRANYUL|HASZNAL]->(s:SzerElem) "
                                      "RETURN s.id AS id, s.label_hu AS szer_elem, s.kind AS fajta, "
                                      "count(t) AS tesz_db ORDER BY tesz_db DESC LIMIT 100",
    "TESZ-típus × HELY-zet": "MATCH (:MunkaKor)-[:TARTALMAZ_TESZT]->(t)-[:TIPUSA]->(ty), "
                             "(t)-[:HELYZETBEN_VEGZENDO {primary: true}]->(h) "
                             "RETURN ty.id AS tipus, h.id AS helyzet, count(*) AS db ORDER BY db DESC",
    "Rubrika nélküli TESZ-ek": "MATCH (t:TeszNivalo) WHERE t.rubric_hu IS NULL OR size(t.rubric_hu) = 0 "
                               "RETURN t.id AS id, t.label_hu AS tesz LIMIT 200",
    "Aliasok (TESZ)": "MATCH (t:TeszNivalo) WHERE size(coalesce(t.aliases, [])) > 2 "
                      "RETURN t.label_hu AS tesz, t.aliases AS aliasok LIMIT 100",
}


def _ro_conn():
    return sqlite3.connect(f"file:{cfg().state_db}?mode=ro", uri=True)


def _sqlite() -> None:
    con = _ro_conn()
    con.row_factory = sqlite3.Row
    c1, c2, c3 = st.columns([2, 2, 1])
    table = c1.selectbox("Tábla", TABLES)
    cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})").fetchall()]
    col = c2.selectbox("Szűrés oszlopra", ["(nincs)"] + cols)
    limit = c3.number_input("Sorok", 10, 5000, 200)
    needle = st.text_input("Tartalmazza", "") if col != "(nincs)" else ""
    q, params = f"SELECT rowid AS _rowid, * FROM {table}", []
    if needle:
        q += f" WHERE CAST({col} AS TEXT) LIKE ?"
        params.append(f"%{needle}%")
    q += " ORDER BY rowid DESC LIMIT ?"
    params.append(int(limit))
    rows = [dict(r) for r in con.execute(q, params).fetchall()]
    total = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    st.caption(f"{total} sor a táblában, {len(rows)} látszik.")
    view = [{k: (str(v)[:120] + "…" if k in LONG and v and len(str(v)) > 120 else v) for k, v in r.items()}
            for r in rows]
    if view:
        ev = st.dataframe(pd.DataFrame(view), hide_index=True, on_select="rerun", selection_mode="single-row",
                          key=f"sq_{table}")
        if ev.selection.rows:
            r = rows[ev.selection.rows[0]]
            for k in LONG & r.keys():
                try:
                    r[k] = json.loads(r[k])
                except (TypeError, ValueError):
                    pass
            show_json(r, expanded=True)
    with st.expander("Saját SQL (csak olvasás)"):
        sql = st.text_area("Lekérdezés", "SELECT status, count(*) AS db FROM jobs GROUP BY status")
        if st.button("Futtatás", key="sql_run"):
            try:
                st.dataframe(df(con.execute(sql).fetchall()), hide_index=True)
            except sqlite3.Error as exc:
                st.error(str(exc))


def _neo4j() -> None:
    g = graph()
    if g is None:
        return
    st.markdown("**Munkakör böngésző**")
    jobs = g.read("MATCH (m:MunkaKor) RETURN m.id AS id, m.label_hu AS label ORDER BY label", limit=5000)
    if jobs:
        j = st.selectbox("Munkakör", jobs, format_func=lambda r: f"{r['label']} ({r['id']})")
        rows = g.read(
            "MATCH (m:MunkaKor {id: $id})-[r:TARTALMAZ_TESZT]->(t:TeszNivalo) "
            "OPTIONAL MATCH (t)-[:TIPUSA]->(ty) OPTIONAL MATCH (t)-[i:IRANYUL]->(s:SzerElem) "
            "OPTIONAL MATCH (other:MunkaKor)-[:TARTALMAZ_TESZT]->(t) "
            "RETURN t.id AS id, t.label_hu AS tesz, ty.id AS tipus, s.label_hu AS szer_elem, "
            "i.kozeg_id AS kozeg, i.helyzet_id AS helyzet, r.relevance AS relevance, r.expected_eqf AS eqf, "
            "count(DISTINCT other) AS munkakor_db, size(coalesce(t.rubric_hu, [])) AS rubrika "
            "ORDER BY r.relevance, tesz", id=j["id"])
        ev = st.dataframe(df(rows), hide_index=True, on_select="rerun", selection_mode="single-row", key="neo_job")
        if ev.selection.rows:
            tid = rows[ev.selection.rows[0]]["id"]
            node = g.read("MATCH (t:TeszNivalo {id: $id}) RETURN t {.*, embedding: null} AS t", id=tid)
            if node:
                show_json(node[0]["t"], expanded=True)
    else:
        st.info("A gráfban még nincs munkakör.")
    st.markdown("**Lekérdezések**")
    name = st.selectbox("Előre megírt", list(CYPHER))
    q = st.text_area("Cypher (csak olvasás)", CYPHER[name], key=f"cy_{name}")
    if st.button("Futtatás", key="cy_run"):
        try:
            st.dataframe(df(g.read(q, limit=2000)), hide_index=True)
        except Exception as exc:  # szintaktikai hiba vagy írási kísérlet
            st.error(str(exc))


def page() -> None:
    st.title("Adatbázis")
    t1, t2 = st.tabs(["Futási állapot (SQLite)", "Gráf (neo4j)"])
    with t1:
        _sqlite()
    with t2:
        _neo4j()
