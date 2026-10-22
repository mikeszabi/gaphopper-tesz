"""Opcionális többnyelvű beágyazás a hasonlóságkereséshez.

GH_EMBED_PROVIDER = ollama | sentence-transformers   (üres GH_EMBED_MODEL = kikapcsolva)
- ollama: helyi Ollama-szerver (OLLAMA_HOST, alapból http://localhost:11434), külön Python-csomag nélkül
- sentence-transformers: a modell a Python-folyamatban fut
"""
from __future__ import annotations

import json
import math
import urllib.error
import urllib.parse
import urllib.request
from functools import lru_cache

_LOCAL = {"localhost", "127.0.0.1", "::1"}


class EmbedError(RuntimeError):
    pass


def _normalize(vec: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [float(x) / n for x in vec]


class Embedder:
    def __init__(self, model_name: str, provider: str = "ollama", host: str = "http://localhost:11434",
                 prefix: str = "", timeout: float = 60.0):
        self.model_name = model_name
        self.provider = provider or "ollama"
        self.host = host.rstrip("/")
        self.prefix = prefix  # pl. nomic-embed-text: "search_document: "
        self.timeout = timeout
        self._model = None
        self._dim: int | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.model_name)

    # ---------------------------------------------------------------- Ollama
    def _post(self, path: str, payload: dict) -> dict:
        req = urllib.request.Request(self.host + path, data=json.dumps(payload).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
        # helyi szervernél ne menjen a kérés a rendszer proxyján át
        local = urllib.parse.urlparse(self.host).hostname in _LOCAL
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) if local else urllib.request.build_opener()
        with opener.open(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _ollama(self, text: str) -> list[float]:
        try:
            out = self._post("/api/embed", {"model": self.model_name, "input": text})
            vecs = out.get("embeddings")
            if vecs:
                return vecs[0]
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[:300]
            if exc.code != 404 or "not found" in body.lower():
                # 404 + "model not found": rossz modellnév; más 404: régebbi Ollama (csak /api/embeddings)
                raise EmbedError(f"Ollama hiba ({exc.code}): {body} – a modellnév az 'ollama list' szerint kell.")
        except urllib.error.URLError as exc:
            raise EmbedError(f"Az Ollama nem érhető el itt: {self.host} ({exc.reason}). Fut az 'ollama serve'?")
        try:
            out = self._post("/api/embeddings", {"model": self.model_name, "prompt": text})
        except urllib.error.HTTPError as exc:
            raise EmbedError(f"Ollama hiba ({exc.code}): {exc.read().decode('utf-8', 'replace')[:300]}")
        if not out.get("embedding"):
            raise EmbedError(f"Az Ollama nem adott vektort a(z) '{self.model_name}' modellel.")
        return out["embedding"]

    # ---------------------------------------------------------------- sentence-transformers
    def _st(self, text: str) -> list[float]:
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model_name)
        return [float(x) for x in self._model.encode([text])[0]]

    # ---------------------------------------------------------------- közös
    def encode(self, text: str) -> list[float]:
        t = self.prefix + (text or "")
        vec = self._ollama(t) if self.provider == "ollama" else self._st(t)
        return _normalize(vec)

    @property
    def dim(self) -> int:
        if self._dim is None:
            self._dim = len(self.encode("próba / probe"))
        return self._dim


@lru_cache(maxsize=4)
def get_embedder(model_name: str, provider: str = "ollama", host: str = "http://localhost:11434",
                 prefix: str = "") -> Embedder:
    return Embedder(model_name, provider, host, prefix)
