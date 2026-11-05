"""Háttérben futó CLI-parancsok indítása és követése a webes felületről.

Minden indítás a `python -m gaphopper ...` parancsot futtatja külön folyamatként; a kimenet a
runs/ui_tasks/ mappába kerül, így a felület újratöltése vagy újraindítása után is visszanézhető.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from .config import ROOT

TASK_DIR = ROOT / "runs" / "ui_tasks"
_procs: dict[str, subprocess.Popen] = {}
# szolgáltató -> szükséges Python-csomag; az openai-kompatibilis és a local szolgáltató saját HTTP-klienssel megy
_PROVIDER_PKG = {"anthropic": "anthropic", "gemini": "google.genai"}


def missing_packages() -> list[str]:
    """A beállított szolgáltatókhoz és a neo4j-hez szükséges csomagok közül azok, amelyeket a felületet futtató
    Python nem lát (a parancsok ugyanezzel a Pythonnal indulnak, ezért így biztosan hibára futnának)."""
    import importlib.util

    from .config import settings

    s = settings()
    need = {"neo4j"} | {_PROVIDER_PKG[getattr(s, st).provider] for st in ("e0", "p1", "p2", "p3", "p4", "p5")
                        if getattr(s, st).provider in _PROVIDER_PKG}
    out = []
    for m in sorted(need):
        try:
            ok = importlib.util.find_spec(m) is not None
        except ModuleNotFoundError:
            ok = False
        if not ok:
            out.append(m)
    return out


def env_problem() -> str:
    miss = missing_packages()
    if not miss:
        return ""
    return (f"A felületet futtató Python ({sys.executable}) nem látja ezeket a csomagokat: {', '.join(miss)}. "
            "Állítsd le a felületet, és indítsd a gaphopper környezetből: "
            "conda activate gaphopper && python -m gaphopper ui")


def start(label: str, args: list[str], targets: list[str] | str | None = None) -> str:
    """targets: a parancs által érintett munkakörök (vagy "all"), hogy a felület jelölni tudja őket."""
    problem = env_problem()
    if problem:  # a parancs biztosan elbukna: azonnal, érthetően jelezzük (a felület hibaüzenetként mutatja)
        raise ValueError(problem)
    TASK_DIR.mkdir(parents=True, exist_ok=True)
    tid = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
    log = TASK_DIR / f"{tid}.log"
    cmd = [sys.executable, "-m", "gaphopper", *args]
    fh = open(log, "w", encoding="utf-8")
    shown = args if len(args) < 40 else args[:12] + ["…"]
    fh.write("$ gaphopper " + " ".join(shown) + "\n")
    fh.flush()
    proc = subprocess.Popen(cmd, cwd=ROOT, stdout=fh, stderr=subprocess.STDOUT,
                            env=os.environ | {"PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"})
    meta = {"id": tid, "label": label, "args": args, "pid": proc.pid, "log": str(log), "targets": targets,
            "started": datetime.now().isoformat(timespec="seconds"), "returncode": None}
    (TASK_DIR / f"{tid}.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    _procs[tid] = proc
    return tid


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _status(meta: dict, path: Path) -> dict:
    proc = _procs.get(meta["id"])
    if proc is not None:
        rc = proc.poll()
        if rc is not None and meta["returncode"] is None:
            meta["returncode"] = rc
            meta["ended"] = datetime.now().isoformat(timespec="seconds")
            path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        meta["status"] = "fut" if rc is None else ("kész" if rc == 0 else "hiba")
    elif meta["returncode"] is not None:
        meta["status"] = "kész" if meta["returncode"] == 0 else "hiba"
    else:
        meta["status"] = "fut" if _alive(meta["pid"]) else "megszakadt"
    return meta


def tasks(limit: int = 20) -> list[dict]:
    if not TASK_DIR.exists():
        return []
    files = sorted(TASK_DIR.glob("*.json"), reverse=True)[:limit]
    return [_status(json.loads(f.read_text(encoding="utf-8")), f) for f in files]


def get(tid: str) -> dict | None:
    f = TASK_DIR / f"{Path(tid).name}.json"
    return _status(json.loads(f.read_text(encoding="utf-8")), f) if f.exists() else None


def running() -> list[dict]:
    return [t for t in tasks() if t["status"] == "fut"]


def stop(tid: str) -> None:
    proc = _procs.get(tid)
    if proc is not None:
        proc.terminate()
        return
    meta = get(tid)
    if meta and meta["status"] == "fut":
        os.kill(meta["pid"], signal.SIGTERM)


def tail(tid: str, lines: int = 200) -> str:
    p = TASK_DIR / f"{Path(tid).name}.log"
    if not p.exists():
        return ""
    return "\n".join(p.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
