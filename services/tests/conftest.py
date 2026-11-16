"""Configuration tests never contact a real model provider."""
import pytest


@pytest.fixture(autouse=True)
def offline_model_checks(monkeypatch):
    from services.api import main
    monkeypatch.setattr(main,'verify_model',lambda *args,**kwargs:None)
