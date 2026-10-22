"""Modellek: lépésenkénti szolgáltató, modell, gondolkodási szint; helyi (DGX Spark) szerver; P2-mód."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from gaphopper.config import (MODELS_PATH, PRICES, PROVIDERS, STEP_NAMES, STEPS, save_ui_models, settings,
                              ui_models)

EFFORTS = ["alap", "low", "medium", "high", "xhigh", "max"]
THINKING = ["be", "off"]

PRESETS = {
    "Olcsó felhő (Sonnet + Haiku)": {
        "E0": ("anthropic", "claude-sonnet-5-5", "low", ""), "P1": ("anthropic", "claude-sonnet-5-5", "low", ""),
        "P2": ("anthropic", "claude-haiku-5-5", "low", ""), "P3": ("anthropic", "claude-haiku-5-5", "low", "off"),
        "P4": ("anthropic", "claude-sonnet-5-5", "medium", ""), "P5": ("anthropic", "claude-haiku-5-5", "low", "off")},
    "Sonnet + helyi Qwen (DGX Spark)": {
        "E0": ("anthropic", "claude-sonnet-5-5", "low", ""), "P1": ("anthropic", "claude-sonnet-5-5", "low", ""),
        "P2": ("local", "", "", "off"), "P3": ("local", "", "", "off"),
        "P4": ("anthropic", "claude-sonnet-5-5", "medium", ""), "P5": ("local", "", "", "off")},
    "Minden helyben (Qwen)": {s: ("local", "", "", "off") for s in STEPS},
}


def _rows(s) -> list[dict]:
    return [{"lépés": st_, "feladat": STEP_NAMES[st_], "szolgáltató": s.step(st_).provider,
             "modell": s.step(st_).model, "effort": s.step(st_).effort or "alap",
             "thinking": s.step(st_).thinking or "be",
             "max_tokens": s.step(st_).max_tokens} for st_ in STEPS]


def page() -> None:
    st.title("Modellek")
    st.caption(f"A felületen mentett beállítás a {MODELS_PATH.relative_to(MODELS_PATH.parent.parent)} fájlba kerül, "
               "és erősebb, mint a .env. Az API-kulcsok a .env-ben maradnak.")
    s = settings()
    data = ui_models()

    st.subheader("Lépésenként")
    preset = st.selectbox("Előbeállítás", ["(jelenlegi)"] + list(PRESETS))
    rows = _rows(s)
    if preset != "(jelenlegi)":
        local_model = (data.get("local") or {}).get("model", "")
        for r in rows:
            p, m, e, t = PRESETS[preset][r["lépés"]]
            r.update({"szolgáltató": p, "modell": m or (local_model if p == "local" else ""),
                      "effort": e or "alap", "thinking": t or "be"})
    ed = st.data_editor(pd.DataFrame(rows), hide_index=True, key=f"models_ed_{preset}",
                        disabled=["lépés", "feladat"], column_config={
                            "szolgáltató": st.column_config.SelectboxColumn(options=PROVIDERS, required=True),
                            "effort": st.column_config.SelectboxColumn(
                                options=EFFORTS, help="Anthropic gondolkodási szint; alap = a modell alapértéke"),
                            "thinking": st.column_config.SelectboxColumn(
                                options=THINKING, help="off: gondolkodás ki (Sonnet 5.5, Haiku 5.5, Qwen)"),
                            "max_tokens": st.column_config.NumberColumn(min_value=256, max_value=128000, step=256),
                        })
    st.caption("Helyi szolgáltatónál üres modellnév esetén a lent megadott helyi modell fut.")

    st.subheader("Helyi modell (OpenAI-kompatibilis szerver, pl. vLLM a DGX Sparkon)")
    loc = data.get("local") or {}
    c1, c2 = st.columns(2)
    base_url = c1.text_input("Cím (base URL)", loc.get("base_url") or s.local_base_url,
                             help="pl. http://spark.local:8000/v1 – a /v1 végződéssel")
    model = c2.text_input("Modell neve a szerveren", loc.get("model", ""), help="a szerver /v1/models listája szerint")
    c3, c4 = st.columns(2)
    json_mode = c3.selectbox("JSON-kényszer", ["object", "schema", "none"],
                             index=["object", "schema", "none"].index(loc.get("json_mode", s.local_json_mode)),
                             help="object: json_object; schema: JSON-sémás kötött dekódolás (vLLM); none: nincs")
    timeout = c4.number_input("Időkorlát (mp)", 30, 7200, int(loc.get("timeout", s.local_timeout)))
    if st.button("Kapcsolat próba"):
        from gaphopper.config import StepModel
        from gaphopper.llm import LocalLLM

        try:
            ids = LocalLLM(StepModel("local", model or "x", None, 1), base_url, s.local_api_key).models()
            st.success("Elérhető modellek: " + (", ".join(ids) or "-"))
        except Exception as exc:  # hálózati hiba
            st.error(f"Nem érhető el: {exc}")

    st.subheader("Bírálat (P2)")
    p2 = data.get("p2") or {}
    modes = ["auto", "always", "never"]
    c5, c6 = st.columns(2)
    mode = c5.selectbox("Mikor fusson?", modes, index=modes.index(p2.get("mode", s.p2_mode)), format_func={
        "auto": "csak szabályjelzésnél + véletlen minta", "always": "mindig", "never": "soha"}.get)
    sample = c6.slider("Véletlen minta aránya", 0.0, 1.0, float(p2.get("sample", s.p2_sample)), 0.05)

    if st.button("Mentés", type="primary"):
        steps = {}
        for r in ed.to_dict("records"):
            steps[r["lépés"]] = {"provider": r["szolgáltató"], "model": r["modell"] or "",
                                 "effort": "" if r["effort"] in (None, "alap") else r["effort"],
                                 "thinking": "off" if r["thinking"] == "off" else "",
                                 "max_tokens": int(r["max_tokens"])}
        data.update({"steps": steps, "local": {"base_url": base_url, "model": model, "json_mode": json_mode,
                                               "timeout": int(timeout)},
                     "p2": {"mode": mode, "sample": float(sample)}})
        save_ui_models(data)
        st.success("Mentve. A következő futtatás már ezzel indul.")
        st.rerun()
    if MODELS_PATH.exists() and st.button("Felületi beállítás törlése (vissza a .env-re)"):
        MODELS_PATH.unlink()
        st.rerun()

    with st.expander("Árak (USD / 1M token: input, output, gyorsítótárból olvasott input)"):
        st.dataframe(pd.DataFrame([{"modell": k, "input": v[0], "output": v[1], "cache": v[2]}
                                   for k, v in (PRICES | (data.get("prices") or {})).items()]), hide_index=True)
        st.caption("A helyi modell ingyenesnek számít. Más modell árát a config/models.yaml 'prices' részébe "
                   "írhatod: modellnév: [input, output, cache].")
