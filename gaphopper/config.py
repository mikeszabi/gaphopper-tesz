"""Beállítások, zárt szótárak és promptok betöltése.

A lépésenkénti modellbeállítás forrása, erősorrendben:
  1. config/models.yaml  (a webes felület írja; ami itt meg van adva, az nyer)
  2. .env / környezeti változók (GH_PROVIDER_<lépés>, GH_MODEL_<lépés>, GH_EFFORT_<lépés> ...)
  3. a kód alapértékei (olcsó modellek, alacsony gondolkodási szint)
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
PROMPT_DIR = ROOT / "prompts"
VOCAB_PATH = ROOT / "config" / "vocab.yaml"
MODELS_PATH = ROOT / "config" / "models.yaml"

STEPS = ["E0", "P1", "P2", "P3", "P4", "P5"]
STEP_NAMES = {"E0": "kinyerés dokumentumból", "P1": "felbontás", "P2": "bírálat", "P3": "egyesítés",
              "P4": "KÉSZ-ség-horgonyzás", "P5": "tanúsítási szöveg (rubrika)"}
PROVIDERS = ["anthropic", "gemini", "openai", "local", "manual"]
DEFAULT_MODEL = {"anthropic": "claude-sonnet-5-5", "gemini": "gemini-3.8-flash"}


# Listaár USD / 1M token (input, output, gyorsítótárból olvasott input). A felületen
# (config/models.yaml -> prices) felülírható; a helyi modell ingyenes, az ismeretlen modell nincs árazva.
PRICES = {
    "claude-opus-5-5": (4.0, 20.0, 0.20),
    "claude-sonnet-5-5": (2.0, 10.0, 0.20),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
}


# Google Gemini Developer API, fizetős Standard szöveges tarifa.
# Ellenőrizve: 2026-10-21. Forrás: https://ai.google.dev/gemini-api/docs/pricing
GEMINI_PRICES = {
    "gemini-3-flash-preview": (0.50, 3.0, 0.05),
    "gemini-2.5-pro": (1.25, 10.0, 0.125),
    "gemini-2.5-flash": (0.30, 2.50, 0.03),
    "gemini-2.5-flash-lite": (0.10, 0.40, 0.01),
    "gemini-4-argon": (2.0, 10.0, 0.10),
    "gemini-3.8-flash": (0.75, 3.75, 0.075),
    "gemini-3.6-flash": (0.75, 3.75, 0.075),
    "gemini-3.5-flash-lite": (0.30, 2.50, 0.03),
    "gemini-3.1-flash-lite": (0.25, 1.50, 0.025),
    "gemini-3.1-pro-preview": (2.0, 12.0, 0.20),
}
PRICES.update(GEMINI_PRICES)
MODEL_CATALOG = {"gemini": ["gemini-3.8-flash"] + [m for m in GEMINI_PRICES if m != "gemini-3.8-flash"],
    "anthropic": ["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"]}
GEMINI_PRICE_NOTE = ("Google Developer API • fizetős Standard, szöveges tokenek • ellenőrizve: 2026-10-21. "
    "3.1 Pro: a táblázat ≤200 000 bemeneti tokenre; efelett 4 / 18 / 0.40 USD. "
    "2.5 Pro: ≤200k 1.25 / 10 / 0.125; >200k 2.50 / 15 / 0.25 USD. A 2.5 modellek hozzáférése korlátozott lehet. "
    "Argon: a megadott akciós ár 2026-11-30-ig, 3.8 és 3.6 Flash: 2026-12-31-ig érvényes. "
    "Ingyenes kvóta, cache-tárolás és külön eszközdíjak nincsenek levonva/hozzáadva. "
    "Az egyedi ár felülírja a beépített tarifát, a hosszú kontextusét is.")


def price(provider: str, model: str, input_tokens: int = 0) -> tuple[float, float, float] | None:
    custom = (ui_models().get("prices") or {}).get(model)
    if custom:
        return tuple(float(x) for x in custom)  # type: ignore[return-value]
    if provider in ("local", "manual", "fake"):
        return (0.0, 0.0, 0.0)
    if model == "gemini-3.1-pro-preview" and input_tokens > 200000:
        return (4.0, 18.0, 0.40)
    if model == "gemini-2.5-pro" and input_tokens > 200000:
        return (2.50, 15.0, 0.25)
    return PRICES.get(model)


def load_env() -> None:
    """A .env fájl betöltése, ha a python-dotenv telepítve van."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(ROOT / ".env")


@lru_cache(maxsize=1)
def vocab() -> dict:
    with open(VOCAB_PATH, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def prompt(name: str) -> str:
    return (PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8")


def prompt_version() -> str:
    """A promptfájlok és a szótár tartalmából képzett verzió: ha bármelyik változik, változik."""
    h = hashlib.sha1()
    for p in sorted(PROMPT_DIR.glob("*.md")):
        h.update(p.name.encode())
        h.update(p.read_bytes())
    h.update(VOCAB_PATH.read_bytes())
    return f"{vocab().get('version', 'v')}-{h.hexdigest()[:8]}"


def render(template: str, **values: str) -> str:
    out = template
    for key, val in values.items():
        out = out.replace("{{" + key + "}}", val)
    return out


def ui_models() -> dict:
    """A felületen mentett beállítások (config/models.yaml); hiányzó fájlnál üres."""
    if not MODELS_PATH.exists():
        return {}
    with open(MODELS_PATH, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def save_ui_models(data: dict) -> None:
    MODELS_PATH.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


@dataclass
class StepModel:
    provider: str  # anthropic | gemini | local | manual | fake
    model: str
    temperature: float | None
    max_tokens: int
    effort: str = ""     # Anthropic: low | medium | high | xhigh | max (üres = a modell alapértéke)
    thinking: str = ""   # "off": gondolkodás kikapcsolása, ahol a modell engedi (Sonnet 5.5, Haiku 5.5, Qwen)


def _pick(ui: dict, key: str, env: str, default):
    if ui.get(key) not in (None, ""):
        return ui[key]
    v = os.getenv(env)
    return default if v is None else v


def _step(step: str, provider: str, model: str, temperature: float | None, max_tokens: int,
          effort: str = "", thinking: str = "") -> StepModel:
    ui = (ui_models().get("steps") or {}).get(step) or {}
    if ui.get("provider"):
        # A felületen beállított lépés: csak a felület és a kód alapértékei számítanak, a .env nem.
        p = ui["provider"]
        m = ui.get("model") or (model if p == provider else "")
        t = ui.get("temperature")
        mt = ui.get("max_tokens") or max_tokens
        e = ui.get("effort", effort if p == provider else "")
        th = ui.get("thinking", thinking)
    else:
        p = os.getenv(f"GH_PROVIDER_{step}", os.getenv("GH_PROVIDER", provider))
        m = os.getenv(f"GH_MODEL_{step}", model if p == provider else "")
        t_env = os.getenv(f"GH_TEMPERATURE_{step}")
        t = temperature if t_env is None else (None if t_env == "" else float(t_env))
        mt = os.getenv(f"GH_MAX_TOKENS_{step}", max_tokens)
        e = os.getenv(f"GH_EFFORT_{step}", effort if p == provider else "")
        th = os.getenv(f"GH_THINKING_{step}", thinking)
    if not m:
        m = _local("model", "GH_LOCAL_MODEL", "") if p == "local" else DEFAULT_MODEL.get(p, "")
    return StepModel(p, m, None if t in (None, "") else float(t), int(mt), str(e or ""), str(th or ""))


def _local(key: str, env: str, default: str) -> str:
    return str(_pick(ui_models().get("local") or {}, key, env, default) or "")


def _p2(key: str, env: str, default):
    return _pick(ui_models().get("p2") or {}, key, env, default)


@dataclass
class Settings:
    neo4j_uri: str = field(default_factory=lambda: os.getenv("NEO4J_URI", "bolt://localhost:7687"))
    neo4j_user: str = field(default_factory=lambda: os.getenv("NEO4J_USERNAME") or os.getenv("NEO4J_USER", "neo4j"))
    neo4j_password: str = field(default_factory=lambda: os.getenv("NEO4J_PASSWORD", ""))
    neo4j_database: str = field(default_factory=lambda: os.getenv("NEO4J_DATABASE", "neo4j"))
    state_db: Path = field(default_factory=lambda: ROOT / os.getenv("GH_STATE_DB", "runs/state.sqlite"))
    embed_model: str = field(default_factory=lambda: str((ui_models().get("embed") or {}).get("model", os.getenv("GH_EMBED_MODEL", ""))))
    embed_provider: str = field(default_factory=lambda: str(_pick(ui_models().get("embed") or {}, "provider", "GH_EMBED_PROVIDER", "ollama")))
    embed_prefix: str = field(default_factory=lambda: str((ui_models().get("embed") or {}).get("prefix", os.getenv("GH_EMBED_PREFIX", ""))))
    ollama_host: str = field(default_factory=lambda: str(_pick(ui_models().get("embed") or {}, "host", "OLLAMA_HOST", "http://localhost:11434")))
    # hasonlósági küszöbök (csak beágyazással): >= hi automatikus SAME, < lo automatikus NEW
    sim_hi: float = field(default_factory=lambda: float(os.getenv("GH_SIM_HI", "0.92")))
    sim_lo: float = field(default_factory=lambda: float(os.getenv("GH_SIM_LO", "0.70")))
    # E0: dokumentumrészlet mérete karakterben (kb. 4 karakter = 1 token) és átfedés
    chunk_chars: int = field(default_factory=lambda: int(os.getenv("GH_CHUNK_CHARS", "24000")))
    chunk_overlap: int = field(default_factory=lambda: int(os.getenv("GH_CHUNK_OVERLAP", "1500")))
    # kötegelt futás: hőmérsékletet csak akkor küldünk, ha kifejezetten kérjük
    batch_send_temperature: bool = field(default_factory=lambda: os.getenv("GH_BATCH_TEMPERATURE", "0") == "1")
    # P2 bírálat: auto = csak szabályjelzésnél és a véletlen mintán; always | never
    p2_mode: str = field(default_factory=lambda: str(_p2("mode", "GH_P2_MODE", "auto")))
    p2_sample: float = field(default_factory=lambda: float(_p2("sample", "GH_P2_SAMPLE", "0.15")))
    # helyi, OpenAI-kompatibilis szerver (pl. vLLM / SGLang / Ollama a DGX Sparkon)
    local_base_url: str = field(default_factory=lambda: _local("base_url", "GH_LOCAL_BASE_URL", "http://localhost:8000/v1"))
    local_api_key: str = field(default_factory=lambda: os.getenv("GH_LOCAL_API_KEY", ""))
    local_json_mode: str = field(default_factory=lambda: _local("json_mode", "GH_LOCAL_JSON_MODE", "object"))
    local_timeout: float = field(default_factory=lambda: float(_local("timeout", "GH_LOCAL_TIMEOUT", "900")))
    openai_base_url: str = field(default_factory=lambda: str(_pick(ui_models().get("openai") or {}, "base_url", "OPENAI_BASE_URL", "https://api.openai.com/v1")))
    openai_api_key_env: str = field(default_factory=lambda: (ui_models().get("openai") or {}).get("api_key_env", "OPENAI_API_KEY"))
    openai_json_mode: str = field(default_factory=lambda: (ui_models().get("openai") or {}).get("json_mode", "object"))
    openai_timeout: float = field(default_factory=lambda: float((ui_models().get("openai") or {}).get("timeout", 120)))
    e0: StepModel = field(default_factory=lambda: _step("E0", "anthropic", "claude-sonnet-5-5", None, 8000, "low"))
    p1: StepModel = field(default_factory=lambda: _step("P1", "anthropic", "claude-sonnet-5-5", None, 12000, "low"))
    p2: StepModel = field(default_factory=lambda: _step("P2", "anthropic", "claude-haiku-5-5", None, 6000, "low"))
    p3: StepModel = field(default_factory=lambda: _step("P3", "anthropic", "claude-haiku-5-5", None, 3000, "low"))
    p4: StepModel = field(default_factory=lambda: _step("P4", "anthropic", "claude-sonnet-5-5", None, 8000, "medium"))
    p5: StepModel = field(default_factory=lambda: _step("P5", "anthropic", "claude-haiku-5-5", None, 6000, "low"))

    def step(self, name: str) -> StepModel:
        return getattr(self, name.lower())


def settings() -> Settings:
    load_env()
    return Settings()
