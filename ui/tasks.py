"""Háttérben futó CLI-parancsok indítása és követése a felületről.

Minden indítás a `python -m gaphopper ...` parancsot futtatja külön folyamatként; a kimenet a
runs/ui_tasks/ mappába kerül, így a felület újratöltése vagy bezárása után is visszanézhető.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import streamlit as st

from gaphopper.config import ROOT

TASK_DIR = ROOT / "runs" / "ui_tasks"


@st.cache_resource(show_spinner=False)
def _registry() -> dict:
    return {}


def start(label: str, args: list[str]) -> str:
    TASK_DIR.mkdir(parents=True, exist_ok=True)
    tid = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
    log = TASK_DIR / f"{tid}.log"
    cmd = [sys.executable, "-m", "gaphopper", *args]
    fh = open(log, "w", encoding="utf-8")
    fh.write("$ " + " ".join(cmd[1:]) + "\n")
    fh.flush()
    proc = subprocess.Popen(cmd, cwd=ROOT, stdout=fh, stderr=subprocess.STDOUT,
                            env=os.environ | {"PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"})
    meta = {"id": tid, "label": label, "args": args, "pid": proc.pid, "log": str(log),
            "started": datetime.now().isoformat(timespec="seconds"), "returncode": None}
    (TASK_DIR / f"{tid}.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    _registry()[tid] = proc
    return tid


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def tasks(limit: int = 15) -> list[dict]:
    if not TASK_DIR.exists():
        return []
    out = []
    for f in sorted(TASK_DIR.glob("*.json"), reverse=True)[:limit]:
        meta = json.loads(f.read_text(encoding="utf-8"))
        proc = _registry().get(meta["id"])
        if proc is not None:
            rc = proc.poll()
            if rc is not None and meta["returncode"] is None:
                meta["returncode"] = rc
                f.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
            meta["status"] = "fut" if rc is None else ("kész" if rc == 0 else f"hiba ({rc})")
        elif meta["returncode"] is not None:
            meta["status"] = "kész" if meta["returncode"] == 0 else f"hiba ({meta['returncode']})"
        else:
            meta["status"] = "fut" if _alive(meta["pid"]) else "befejeződött"
        out.append(meta)
    return out


def running() -> list[dict]:
    return [t for t in tasks() if t["status"] == "fut"]


def stop(tid: str) -> None:
    proc = _registry().get(tid)
    if proc is not None:
        proc.terminate()
        return
    meta = json.loads((TASK_DIR / f"{tid}.json").read_text(encoding="utf-8"))
    if _alive(meta["pid"]):
        os.kill(meta["pid"], signal.SIGTERM)


def tail(log: str, lines: int = 60) -> str:
    p = Path(log)
    if not p.exists():
        return ""
    data = p.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(data[-lines:])


def panel(key: str = "tasks") -> None:
    """Futó és legutóbbi parancsok; 3 másodpercenként frissül."""

    @st.fragment(run_every=3)
    def _panel():
        items = tasks()
        if not items:
            st.caption("Még nem indítottál parancsot a felületről.")
            return
        labels = [f"{t['status']:>12} · {t['label']} · {t['started'][11:]}" for t in items]
        i = st.selectbox("Parancsok", range(len(items)), format_func=lambda k: labels[k], key=f"{key}_sel")
        t = items[i]
        st.code(tail(t["log"]) or "(még nincs kimenet)", language="text")
        if t["status"] == "fut" and st.button("Leállítás", key=f"{key}_stop_{t['id']}"):
            stop(t["id"])
            time.sleep(0.5)

    _panel()
