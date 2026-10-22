"""Szakértői sor: a SZAKERTORE tételek szerkesztése, jóváhagyása vagy elutasítása."""
from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from gaphopper.config import vocab
from ui.common import cfg, show_json, store

EDIT_COLS = ["cand_id", "label_hu", "label_en", "outcome_hu", "tesz_type", "kozeg", "helyzet", "relevance",
             "expected_eqf", "confidence"]


def page() -> None:
    st.title("Szakértői sor")
    S, V = store(), vocab()
    queue = S.queue()
    if not queue:
        st.info("A szakértői sor üres.")
        return
    for row in queue:
        payload = json.loads(row["payload_json"])
        title = S.job(row["job_id"]).get("title", "")
        with st.expander(f"[{row['id']}] {row['job_id']} {title} – {row['kind']}", expanded=False):
            if payload.get("flags"):
                st.dataframe(pd.DataFrame(payload["flags"]), hide_index=True)
            for q in payload.get("questions") or []:
                st.write(f"❓ {q}")
            for g in payload.get("gaps") or []:
                st.write(f"➕ {g}")
            if row["kind"] != "job_review":
                st.write("Egyesítési kérdés: ellenőrizd a gráfban a jelzett elemeket.")
                show_json(payload)
                if st.button("Rendben, lezárom", key=f"ok{row['id']}"):
                    S.decide(row["id"], "LEZARVA")
                    st.rerun()
                continue
            p2 = S.artifact(row["job_id"], "P2") or {}
            final = p2.get("final_tesz", [])
            df = pd.DataFrame([{c: t.get(c) for c in EDIT_COLS} for t in final])
            edited = st.data_editor(
                df, key=f"ed{row['id']}", num_rows="dynamic", hide_index=True,
                column_config={
                    "tesz_type": st.column_config.SelectboxColumn(options=list(V["tesz_tipus"])),
                    "kozeg": st.column_config.SelectboxColumn(options=list(V["kozeg"])),
                    "helyzet": st.column_config.SelectboxColumn(options=list(V["helyzet"])),
                    "relevance": st.column_config.SelectboxColumn(options=V["relevance"]),
                    "expected_eqf": st.column_config.NumberColumn(min_value=1, max_value=8, step=1),
                })
            c1, c2 = st.columns(2)
            if c1.button("Jóváhagyás és gráfba írás", key=f"ap{row['id']}", type="primary"):
                by_id = {t["cand_id"]: t for t in final}
                merged = []
                for rec in edited.to_dict("records"):
                    base = dict(by_id.get(rec["cand_id"], {}))
                    if not base:
                        st.error(f"Új sor ({rec['cand_id']}) csak a teljes JSON-ban adható hozzá.")
                        st.stop()
                    base.update({k: v for k, v in rec.items() if v is not None and v == v})
                    base["expected_eqf"] = int(base["expected_eqf"])
                    base["confidence"] = 1.0  # szakértő jóváhagyta
                    merged.append(base)
                from gaphopper.pipeline import Context, approve_job

                ctx = Context(cfg())
                summary = approve_job(ctx, row["id"], merged)
                ctx.close()
                st.success(f"{len(summary['tesz'])} TESZ-nivaló a gráfban.")
                st.rerun()
            if c2.button("Elutasítás", key=f"rj{row['id']}"):
                S.decide(row["id"], "ELUTASITVA")
                S.set_status(row["job_id"], "ELVETVE")
                st.rerun()
