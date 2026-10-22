"""Állapot: munkakörök, futtatás, köztes eredmények, költség."""
from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from gaphopper.report import summary
from ui import tasks
from ui.common import STATUS_HU, df, show_json, store, tesz_table


def page() -> None:
    st.title("Állapot és futtatás")
    S = store()
    jobs = S.jobs()
    counts = pd.Series([r["status"] for r in jobs]).value_counts().to_dict() if jobs else {}
    if not jobs:
        st.info("Még nincs munkakör. A Feltöltések oldalon tölthetsz be rekordokat vagy dokumentumokat.")
    else:
        cols = st.columns(min(len(counts), 6) or 1)
        for i, (k, v) in enumerate(sorted(counts.items())):
            cols[i % len(cols)].metric(STATUS_HU.get(k, k), v)

    # ------------------------------------------------------------ munkakörök táblázata
    st.subheader("Munkakörök")
    c1, c2 = st.columns([2, 3])
    status_f = c1.multiselect("Állapot", sorted(counts), default=[], placeholder="mind")
    text_f = c2.text_input("Keresés (azonosító vagy cím)", "")
    rows = []
    for r in jobs:
        if status_f and r["status"] not in status_f:
            continue
        rec = json.loads(r["record_json"])
        if text_f and text_f.lower() not in (r["job_id"] + " " + str(rec.get("title", ""))).lower():
            continue
        rows.append({"job_id": r["job_id"], "cím": rec.get("title", ""), "állapot": r["status"],
                     "megjegyzés": r["note"] or "", "frissítve": r["updated_at"]})
    table = pd.DataFrame(rows)
    sel_ids: list[str] = []
    if not table.empty:
        ev = st.dataframe(table, hide_index=True, on_select="rerun", selection_mode="multi-row",
                          key="jobs_table")
        sel_ids = [table.iloc[i]["job_id"] for i in ev.selection.rows]
        st.caption(f"{len(rows)} munkakör a szűrés után; kijelölve: {len(sel_ids)}")

    # ------------------------------------------------------------ futtatás
    st.subheader("Futtatás")
    a, b, c, d = st.columns(4)
    scope = a.radio("Mire?", ["kijelöltek", "első N várakozó", "összes várakozó"], index=0 if sel_ids else 1)
    limit = b.number_input("N", 1, 100000, 3)
    until = c.selectbox("Meddig?", ["graph", "p2", "p1"], format_func={
        "graph": "gráfba írásig", "p2": "bírálatig (P2)", "p1": "felbontásig (P1)"}.get)
    workers = d.number_input("Párhuzamos hívások", 1, 32, 4)
    force = st.checkbox("Újrafuttatás akkor is, ha már kész (force)", False)
    args = ["run", "--until", until, "--workers", str(workers)]
    if scope == "kijelöltek":
        for j in sel_ids:
            args += ["--job", j]
    elif scope == "első N várakozó":
        args += ["--limit", str(limit)]
    if force:
        args.append("--force")
    b1, b2, b3, b4 = st.columns(4)
    if b1.button("Feldolgozás indítása", type="primary", disabled=scope == "kijelöltek" and not sel_ids):
        tasks.start(f"run ({scope})", args)
        st.session_state["status_sel"] = 0
        st.rerun()
    if b2.button("Tanúsítási szöveg (P5)", help="Igazolási mondat és rubrika a rubrika nélküli TESZ-ekhez"):
        tasks.start("certify", ["certify"])
        st.session_state["status_sel"] = 0
        st.rerun()
    with b3.popover("Kötegelt mód"):
        step = st.radio("Lépés", ["P1", "P2"], horizontal=True)
        if st.button("Köteg beküldése"):
            tasks.start(f"batch submit {step}", ["batch", "submit", "--step", step]
                        + (["--limit", str(limit)] if scope == "első N várakozó" else []))
        if st.button("Kötegek állapota"):
            tasks.start("batch status", ["batch", "status"])
        if st.button("Begyűjtés (várakozással)"):
            tasks.start("batch collect", ["batch", "collect", "--wait"])
    if b4.button("Kijelöltek vissza NYERS-re", disabled=not sel_ids,
                 help="A következő futtatás elölről dolgozza fel őket."):
        for j in sel_ids:
            S.set_status(j, "NYERS", "a felületről visszaállítva")
        st.rerun()
    tasks.panel("status")

    # ------------------------------------------------------------ egy munkakör részletei
    if len(sel_ids) == 1:
        jid = sel_ids[0]
        st.subheader(f"Részletek: {jid}")
        t_rec, t_p1, t_p2, t_p3, t_log = st.tabs(["Rekord", "P1 felbontás", "P2 bírálat", "P3 egyesítés", "Napló"])
        with t_rec:
            show_json(S.job(jid), expanded=True)
        with t_p1:
            art = S.artifact(jid, "P1")
            if art:
                st.write(f"**{art.get('job_label_hu')}** · {art.get('purpose_hu', '')}")
                st.dataframe(tesz_table(art.get("tesz", [])), hide_index=True)
                with st.expander("Teljes JSON"):
                    show_json(art)
            err = S.artifact(jid, "P1_ERR")
            if err:
                st.error(err.get("error", ""))
        with t_p2:
            art = S.artifact(jid, "P2")
            if art:
                st.write(f"Verdikt: **{art.get('verdict')}** {('· ' + art['p2_reason']) if art.get('p2_reason') else ''}")
                if art.get("changes"):
                    st.dataframe(df(art["changes"], ["op", "cand_id", "into", "set", "reason_hu"]), hide_index=True)
                st.dataframe(tesz_table(art.get("final_tesz", [])), hide_index=True)
                with st.expander("Teljes JSON"):
                    show_json(art)
            err = S.artifact(jid, "P2_ERR")
            if err:
                st.error(err.get("error", ""))
        with t_p3:
            art = S.artifact(jid, "P3")
            if art:
                st.write(f"Kétes eset (egy P3-hívásban): {art.get('p3_cases', '-')}")
                st.dataframe(df(art.get("tesz", [])), hide_index=True)
                if art.get("review"):
                    st.warning("Szakértői ellenőrzésre jelölt egyesítések:")
                    st.dataframe(df(art["review"]), hide_index=True)
        with t_log:
            st.dataframe(df(summary(S, [jid], ("step", "model", "prompt_version"))), hide_index=True)
            logs = S.db.execute("SELECT id, step, model, parsed_ok, created_at, usage_json FROM llm_log "
                                "WHERE job_id=? ORDER BY id DESC LIMIT 50", (jid,)).fetchall()
            st.dataframe(df(logs), hide_index=True)

    # ------------------------------------------------------------ költség
    with st.expander("Tokenek, idő és költség promptverziónként"):
        st.caption("A régi és az új promptverzió itt vethető össze: munkakörönkénti kimeneti token, idő és költség. "
                   "Az idő csak az új naplóbejegyzéseknél ismert.")
        by = st.multiselect("Csoportosítás", ["prompt_version", "step", "model", "provider", "job_id"],
                            default=["prompt_version", "step"])
        if by:
            st.dataframe(df(summary(S, sel_ids or None, tuple(by))), hide_index=True)
