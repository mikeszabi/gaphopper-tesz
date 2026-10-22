import json
from types import SimpleNamespace

from gaphopper import components


def test_start_stopped_container(monkeypatch):
    calls = []
    monkeypatch.setenv("GH_NEO4J_CONTAINER", "custom-neo4j")

    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout=json.dumps({"Running": False, "Status": "exited"}), stderr="")

    monkeypatch.setattr(components.subprocess, "run", run)
    assert components.start_neo4j()["ok"]
    assert calls[-1] == ["docker", "container", "start", "custom-neo4j"]


def test_running_container_is_not_restarted(monkeypatch):
    monkeypatch.setattr(components, "docker_status", lambda: {"state": "done", "container": "neo4j_gh"})
    monkeypatch.setattr(components.subprocess, "run", lambda *a, **kw: (_ for _ in ()).throw(AssertionError()))
    assert components.start_neo4j()["ok"]


def test_missing_docker_is_reported(monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError("docker")
    monkeypatch.setattr(components.subprocess, "run", missing)
    assert components.docker_status()["state"] == "error"
    assert not components.start_neo4j()["ok"]


def test_missing_container_is_created_with_requested_image_and_volume(monkeypatch):
    calls = []
    monkeypatch.setenv("GH_NEO4J_CONTAINER", "neo4j_gh")

    def run(args, **kwargs):
        calls.append(args)
        if args[1:3] == ["container", "inspect"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="Error response from daemon: No such container: neo4j_gh")
        return SimpleNamespace(returncode=0, stdout="container-id", stderr="")

    monkeypatch.setattr(components.subprocess, "run", run)
    assert components.docker_status()["state"] == "missing"
    assert components.start_neo4j()["ok"]
    assert calls[-1] == ["docker", "run", "-d", "--rm", "-i", "--name", "neo4j_gh",
                         "-p", "7474:7474", "-p", "7687:7687",
                         "-v", "/mnt/data/project_data/gh:/data", "neo4j"]


def test_daemon_error_does_not_create_container(monkeypatch):
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=1, stdout="", stderr="Cannot connect to the Docker daemon")

    monkeypatch.setattr(components.subprocess, "run", run)
    assert not components.start_neo4j()["ok"]
    assert len(calls) == 1


def test_creation_failure_is_reported(monkeypatch):
    monkeypatch.setattr(components, "docker_status", lambda: {"state": "missing", "container": "neo4j_gh"})
    monkeypatch.setattr(components.subprocess, "run", lambda *args, **kwargs:
                        SimpleNamespace(returncode=1, stdout="", stderr="port is already allocated"))
    assert components.start_neo4j() == {"ok": False, "error": "port is already allocated"}
