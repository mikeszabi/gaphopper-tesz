"""Read-only component probes and starting the configured Neo4j container."""
from __future__ import annotations

import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from .config import settings


def docker_status() -> dict:
    settings()  # load .env before reading the container name
    name = os.getenv("GH_NEO4J_CONTAINER", "neo4j_gh")
    try:
        result = subprocess.run(
            ["docker", "container", "inspect", "--format", "{{json .State}}", name],
            capture_output=True, text=True, timeout=8, check=False,
        )
        if result.returncode:
            missing = "no such container" in result.stderr.lower() or "no such object" in result.stderr.lower()
            return {"state": "missing" if missing else "error",
                    "detail": "A konténer még nincs létrehozva; az indítógomb létrehozza." if missing else result.stderr.strip()[:400],
                    "container": name}
        state = json.loads(result.stdout)
        return {"state": "done" if state.get("Running") else "wait",
                "detail": state.get("Status", "ismeretlen"), "container": name}
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        return {"state": "error", "detail": str(exc)[:400], "container": name}


def start_neo4j() -> dict:
    status = docker_status()
    if status["state"] == "error":
        return {"ok": False, "error": status["detail"] +
                " A Dockernek futnia kell és elérhetőnek kell lennie a webes szerver számára. "
                "A konténer neve: GH_NEO4J_CONTAINER (alapból neo4j_gh)."}
    if status["state"] == "done":
        return {"ok": True, "message": "A Neo4j-konténer már fut."}
    try:
        if status["state"] == "missing":
            # Web requests have no terminal: -t would fail with "not a TTY".
            command = ["docker", "run", "-d", "--rm", "-i", "--name", status["container"],
                       "-p", "7474:7474", "-p", "7687:7687",
                       "-v", "/mnt/data/project_data/gh:/data", "neo4j"]
        else:
            command = ["docker", "container", "start", status["container"]]
        result = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
        if result.returncode:
            raise RuntimeError(result.stderr.strip()[:400])
        return {"ok": True, "message": "A konténer elindult. A Neo4j felállása után frissítsd az állapotot."}
    except (OSError, subprocess.TimeoutExpired, RuntimeError) as exc:
        return {"ok": False, "error": str(exc)[:400]}


def component_status() -> dict:
    from .embed import Embedder
    from .web import test_openai, test_local

    s = settings()

    def neo4j():
        from .graph import neo4j_driver

        GraphDatabase, Query = neo4j_driver()
        with GraphDatabase.driver(s.neo4j_uri, auth=(s.neo4j_user, s.neo4j_password),
                                  connection_timeout=5, connection_acquisition_timeout=8,
                                  max_transaction_retry_time=0) as driver:
            with driver.session(database=s.neo4j_database) as session:
                session.run(Query("RETURN 1 AS ok", timeout=5)).single()
        return {"state": "done", "detail": "Kapcsolat, hitelesítés és olvasási próba sikeres."}

    def embedding():
        if not s.embed_model:
            return {"state": "skip", "detail": "Kikapcsolva: nincs beágyazó modell beállítva."}
        if s.embed_provider != "ollama":
            return {"state": "unknown", "detail": "Folyamatban futó modell; a Modellek oldal beágyazáspróbája tölti be és ellenőrzi."}
        vec = Embedder(s.embed_model, s.embed_provider, s.ollama_host, s.embed_prefix, timeout=8).encode("állapotpróba")
        if not vec:
            raise ValueError("Üres beágyazási vektor.")
        return {"state": "done", "detail": f"Beágyazás sikeres, dimenzió: {len(vec)}."}

    def api_probe(fn):
        result = fn({})
        return {"state": "done" if result["ok"] else "error",
                "detail": f"Modelllista elérhető: {len(result.get('models', []))} modell. Generálás nem lett tesztelve."
                if result["ok"] else result["error"]}

    checks = [("Neo4j Docker", os.getenv("GH_NEO4J_CONTAINER", "neo4j_gh"), docker_status),
              ("Neo4j adatbázis", s.neo4j_uri, neo4j),
              ("Helyi beágyazás", f"{s.embed_provider} · {s.embed_model or 'nincs modell'}", embedding),
              ("OpenAI-kompatibilis API", s.openai_base_url, lambda: api_probe(test_openai))]
    if any(s.step(step).provider == "local" for step in ("E0", "P1", "P2", "P3", "P4", "P5")):
        checks.append(("Régi local modell API", s.local_base_url, lambda: api_probe(test_local)))

    def run(check):
        name, target, fn = check
        try:
            result = fn()
        except Exception as exc:
            result = {"state": "error", "detail": str(exc)[:400]}
        # Probe errors may contain credentials echoed by remote services.
        for secret in (s.neo4j_password, s.local_api_key, os.getenv(s.openai_api_key_env, "")):
            if secret:
                result["detail"] = result["detail"].replace(secret, "[rejtett]")
        return {"name": name, "target": target, **result}

    with ThreadPoolExecutor(max_workers=len(checks)) as pool:
        rows = list(pool.map(run, checks))
    return {"components": rows, "checked_at": datetime.now(timezone.utc).isoformat()}
