"""Modellfüggetlen LLM-réteg: Anthropic, Gemini, helyi OpenAI-kompatibilis szerver (pl. Qwen a DGX
Sparkon), kézi (Claude Code) és teszt (fake) mód."""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, ValidationError

from .config import StepModel


class LLMError(RuntimeError):
    pass


class ManualStep(Exception):
    """Kézi mód: a promptot ki kell adni (pl. Claude Code-nak), a választ fájlból kell visszaadni."""

    def __init__(self, step: str, system: str, user: str):
        super().__init__(step)
        self.step, self.system, self.user = step, system, user


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)


def extract_json(text: str) -> Any:
    t = _FENCE.sub("", _THINK.sub("", text).strip())
    start, end = t.find("{"), t.rfind("}")
    if start < 0 or end < start:
        raise ValueError("A válaszban nincs JSON-objektum.")
    return json.loads(t[start : end + 1])


def anthropic_client():
    """Anthropic-kliens; munkaterülethez nem kötött kulcsnál az ANTHROPIC_WORKSPACE_ID-t fejlécként küldi."""
    import os

    import anthropic

    ws = os.getenv("ANTHROPIC_WORKSPACE_ID", "").strip()
    return anthropic.Anthropic(default_headers={"anthropic-workspace-id": ws} if ws else None)


def anthropic_extra(cfg: StepModel) -> dict:
    """Gondolkodási szint (effort) és a gondolkodás kikapcsolása – a modell szabályai szerint.

    - effort: output_config.effort (low | medium | high | xhigh | max)
    - thinking=off: Sonnet 5.5-nél {"type": "between_tools"}, más modellnél {"type": "disabled"};
      az Opus 5.5 nem engedi kikapcsolni, ott csak az effort számít.
    """
    out: dict[str, Any] = {}
    if cfg.effort:
        out["output_config"] = {"effort": cfg.effort}
    if cfg.thinking == "off" and "opus-5-5" not in cfg.model:
        out["thinking"] = {"type": "between_tools" if "sonnet-5-5" in cfg.model else "disabled"}
    return out


class AnthropicLLM:
    def __init__(self, cfg: StepModel):
        self.client = anthropic_client()
        self.cfg = cfg
        self._send_temperature = cfg.temperature is not None

    def complete(self, system: str, user: str) -> tuple[str, dict]:
        import anthropic

        kwargs: dict[str, Any] = dict(
            model=self.cfg.model,
            max_tokens=self.cfg.max_tokens,
            # A rendszerprompt minden hívásban azonos: gyorsítótárazható.
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
            **anthropic_extra(self.cfg),
        )
        if self._send_temperature:
            kwargs["temperature"] = self.cfg.temperature

        def send():
            # Streamelve: nagy max_tokens mellett az SDK nem enged nem-streamelt hívást (10 perces korlát).
            with self.client.messages.stream(**kwargs) as stream:
                return stream.get_final_message()

        try:
            resp = send()
        except (anthropic.BadRequestError, TypeError) as exc:
            # Egyes modellek (API) vagy SDK-verziók (TypeError) nem fogadnak temperature paramétert:
            # újrapróbálás nélküle.
            if self._send_temperature and "temperature" in str(exc).lower():
                self._send_temperature = False
                kwargs.pop("temperature", None)
                resp = send()
            else:
                raise
        if resp.stop_reason == "max_tokens":
            # A csonka JSON-t a sémajavító újrapróbálás sem menti meg: azonnal jelezzük.
            raise LLMError(f"A válasz elérte a max_tokens korlátot ({self.cfg.max_tokens}); "
                           f"emeld a(z) {self.cfg.model} max_tokens értékét a felületen vagy a .env-ben.")
        text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "text") == "text")
        usage = resp.usage.model_dump() if hasattr(resp.usage, "model_dump") else dict(resp.usage)
        return text, usage


class GeminiLLM:
    def __init__(self, cfg: StepModel):
        from google import genai

        self.client = genai.Client()  # GEMINI_API_KEY vagy GOOGLE_API_KEY környezeti változó
        self.cfg = cfg

    def complete(self, system: str, user: str) -> tuple[str, dict]:
        from google.genai import types

        conf = types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=self.cfg.max_tokens,
            response_mime_type="application/json",
            **({"temperature": self.cfg.temperature} if self.cfg.temperature is not None else {}),
        )
        try:
            resp = self.client.models.generate_content(model=self.cfg.model, contents=user, config=conf)
        except Exception as exc:
            message = str(exc)
            if "no longer available to new users" in message.lower():
                raise LLMError(f"A(z) {self.cfg.model} modell ezzel az API-kulccsal már nem érhető el. "
                               "A Modellek oldalon válassz gemini-3.8-flash modellt az érintett lépéshez, "
                               "mentsd, majd indítsd újra a feldolgozást.") from exc
            raise
        usage = {}
        if getattr(resp, "usage_metadata", None) is not None:
            um = resp.usage_metadata
            usage = {"input_tokens": max(0, (getattr(um, "prompt_token_count", 0) or 0) - (getattr(um, "cached_content_token_count", 0) or 0)),
                     "cache_read_input_tokens": getattr(um, "cached_content_token_count", 0) or 0,
                     "output_tokens": (getattr(um, "candidates_token_count", 0) or 0) + (getattr(um, "thoughts_token_count", 0) or 0),
                     "thinking_tokens": getattr(um, "thoughts_token_count", None)}
        candidates = getattr(resp, "candidates", None) or []
        content = getattr(candidates[0], "content", None) if candidates else None
        parts = getattr(content, "parts", None) or []
        text = "".join(p.text for p in parts if getattr(p, "text", None) and not getattr(p, "thought", False))
        return text, usage


class LocalLLM:
    """Helyi, OpenAI-kompatibilis /chat/completions végpont (vLLM, SGLang, llama.cpp, Ollama ...).

    Külön Python-csomag nem kell. A helyi hálózati címre menő kérés nem megy át a rendszer proxyján.
    - json_mode: object = response_format json_object; schema = JSON-sémás kötött dekódolás; none = nincs
    - thinking=off: Qwen-szerű modelleknél a chat-sablon gondolkodását kikapcsolja (enable_thinking=false)
    """

    def __init__(self, cfg: StepModel, base_url: str, api_key: str = "", json_mode: str = "object",
                 timeout: float = 900.0, use_proxy: bool = False):
        if not base_url:
            raise LLMError("A helyi modellhez add meg a GH_LOCAL_BASE_URL-t (pl. http://spark:8000/v1).")
        if not cfg.model:
            raise LLMError("A helyi modellhez add meg a modell nevét (GH_LOCAL_MODEL vagy GH_MODEL_<lépés>).")
        self.cfg, self.base_url, self.api_key = cfg, base_url.rstrip("/"), api_key
        self.json_mode, self.timeout = json_mode, timeout
        self.use_proxy = use_proxy
        self.schema: dict | None = None  # call_json tölti ki schema módban

    def _open(self, req):
        opener = urllib.request.build_opener() if self.use_proxy else urllib.request.build_opener(urllib.request.ProxyHandler({}))
        return opener.open(req, timeout=self.timeout)

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def complete(self, system: str, user: str) -> tuple[str, dict]:
        body: dict[str, Any] = {
            "model": self.cfg.model, "max_tokens": self.cfg.max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        if self.cfg.temperature is not None:
            body["temperature"] = self.cfg.temperature
        if self.json_mode == "schema" and self.schema:
            body["response_format"] = {"type": "json_schema",
                                       "json_schema": {"name": "valasz", "schema": self.schema}}
        elif self.json_mode in ("object", "schema"):
            body["response_format"] = {"type": "json_object"}
        if self.cfg.thinking == "off":
            body["chat_template_kwargs"] = {"enable_thinking": False}
        req = urllib.request.Request(self.base_url + "/chat/completions", data=json.dumps(body).encode("utf-8"),
                                     headers=self._headers(), method="POST")
        try:
            with self._open(req) as resp:
                out = json.loads(resp.read().decode("utf-8"))
        except TimeoutError as exc:
            raise LLMError(f"Az OpenAI-kompatibilis szerver nem válaszolt {self.timeout:g} mp alatt "
                           f"(modell: {self.cfg.model}). Ellenőrizd a szerver terhelését, "
                           "vagy emeld az API időkorlátját a Modellek oldalon.") from exc
        except urllib.error.HTTPError as exc:
            raise LLMError(f"OpenAI-kompatibilis API hiba ({exc.code}): {exc.read().decode('utf-8', 'replace')[:500]}")
        except urllib.error.URLError as exc:
            raise LLMError(f"A helyi modell nem érhető el itt: {self.base_url} ({exc.reason}).")
        choice = (out.get("choices") or [{}])[0]
        if choice.get("finish_reason") == "length":
            raise LLMError(f"A helyi válasz elérte a max_tokens korlátot ({self.cfg.max_tokens}).")
        text = (choice.get("message") or {}).get("content") or ""
        u = out.get("usage") or {}
        cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
        return text, {"input_tokens": max(0, (u.get("prompt_tokens") or 0) - cached),
                      "cache_read_input_tokens": cached, "output_tokens": u.get("completion_tokens")}

    def models(self) -> list[str]:
        req = urllib.request.Request(self.base_url + "/models", headers=self._headers())
        with self._open(req) as resp:
            return [m.get("id") for m in json.loads(resp.read().decode("utf-8")).get("data", [])]


class ManualLLM:
    """Kézi mód: a választ egy előre megadott fájlból olvassa, különben ManualStep-et dob."""

    def __init__(self, cfg: StepModel, step: str, answer_file: Path | None = None):
        self.cfg, self.step, self.answer_file = cfg, step, answer_file

    def complete(self, system: str, user: str) -> tuple[str, dict]:
        if self.answer_file and Path(self.answer_file).exists():
            return Path(self.answer_file).read_text(encoding="utf-8"), {}
        raise ManualStep(self.step, system, user)


class FakeLLM:
    """Teszteléshez: előre megadott válaszfüggvény."""

    def __init__(self, responder: Callable[[str, str], str]):
        self.responder = responder
        self.cfg = StepModel("fake", "fake", None, 0)

    def complete(self, system: str, user: str) -> tuple[str, dict]:
        return self.responder(system, user), {}


def get_llm(cfg: StepModel, step: str, answer_file: Path | None = None, settings=None):
    if cfg.provider == "anthropic":
        return AnthropicLLM(cfg)
    if cfg.provider == "gemini":
        return GeminiLLM(cfg)
    if cfg.provider in ("local", "openai"):
        if settings is None:
            from .config import settings as _settings

            settings = _settings()
        if cfg.provider == "openai":
            import os

            return LocalLLM(cfg, settings.openai_base_url, os.getenv(settings.openai_api_key_env, ""),
                            settings.openai_json_mode, settings.openai_timeout, use_proxy=True)
        return LocalLLM(cfg, settings.local_base_url, settings.local_api_key, settings.local_json_mode,
                        settings.local_timeout)
    if cfg.provider == "manual":
        return ManualLLM(cfg, step, answer_file)
    raise LLMError(f"Ismeretlen szolgáltató: {cfg.provider}")


def call_json(llm, system: str, user: str, schema: type[BaseModel], retries: int = 1,
              log: Callable[[str, bool, dict], None] | None = None,
              check: Callable[[Any], Any] | None = None):
    """Hívás, JSON-kinyerés, sémaellenőrzés és (ha van) tartalmi ellenőrzés (check).

    Hiba esetén egyszer újrapróbál, a hibaüzenetet a promptba téve.
    """
    if isinstance(llm, LocalLLM) and llm.json_mode == "schema":
        llm.schema = schema.model_json_schema()
    prompt = user
    last_err = ""
    repair_data = None
    repair_indices = set()
    for attempt in range(retries + 1):
        t0 = time.monotonic()
        old_schema = getattr(llm, "schema", None)
        if repair_data is not None and isinstance(llm, LocalLLM) and llm.json_mode == "schema":
            from .schemas import P1RelevanceRepair
            llm.schema = P1RelevanceRepair.model_json_schema()
        try:
            text, usage = llm.complete(system, prompt)
        finally:
            if isinstance(llm, LocalLLM):
                llm.schema = old_schema
        usage = dict(usage or {}) | {"duration_ms": int((time.monotonic() - t0) * 1000)}
        try:
            data = extract_json(text)
            if repair_data is not None:
                from copy import deepcopy
                from .schemas import P1RelevanceRepair

                fixes = P1RelevanceRepair.model_validate(data).fixes
                if len(fixes) != len(repair_indices) or {f.index for f in fixes} != repair_indices:
                    raise ValueError("A javításnak pontosan a kért TESZ-indexeket kell tartalmaznia.")
                data = deepcopy(repair_data)
                for fix in fixes:
                    data["tesz"][fix.index]["relevance"] = fix.relevance
            obj = schema.model_validate(data)
            if check:
                check(obj)
            if log:
                log(text, True, usage)
            return obj, data
        except (ValueError, ValidationError) as exc:
            last_err = str(exc)[:3000]
            if log:
                log(text, False, usage)
            if isinstance(llm, ManualLLM):
                break
            from .schemas import P1Output
            errors = exc.errors() if isinstance(exc, ValidationError) else []
            if (attempt < retries and repair_data is None and schema is P1Output and errors
                    and all(e["type"] == "literal_error" and len(e["loc"]) == 3
                            and e["loc"][0] == "tesz" and isinstance(e["loc"][1], int)
                            and e["loc"][2] == "relevance" for e in errors)):
                repair_data = data
                repair_indices = {e["loc"][1] for e in errors}
                items = [{"index": i, "tesz": data["tesz"][i]} for i in sorted(repair_indices)]
                prompt = (
                    "P1 célzott mezőjavítás. A relevance kizárólag CORE vagy SUPPORTING lehet. "
                    "CORE: a munkakör lényegéhez tartozik; SUPPORTING: támogató/járulékos. "
                    "Csak a felsorolt indexek relevance mezőjéről dönts; a teljes felbontást ne írd újra. "
                    'Kimenet: {"fixes":[{"index":0,"relevance":"CORE"}]}.\n'
                    + "MUNKAKÖR ÉS EREDETI FELADAT:\n" + user
                    + "\nJAVÍTANDÓ TESZ-EK:\n" + json.dumps(items, ensure_ascii=False, separators=(",", ":"))
                )
                continue
            prompt = (user + "\n\nAZ ELŐZŐ VÁLASZOD NEM FELELT MEG A SÉMÁNAK:\n" + last_err
                      + "\nAdd vissza a javított, teljes JSON-t.")
    raise LLMError(f"Sémahiba: {last_err}")
