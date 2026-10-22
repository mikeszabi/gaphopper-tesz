"""Az offline tesztek nem olvassák a gép mentett modell- és titkos beállításait."""
import pytest


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    from gaphopper import config

    monkeypatch.setattr(config, "MODELS_PATH", tmp_path / "models.yaml")
    monkeypatch.setattr(config, "load_env", lambda: None)
