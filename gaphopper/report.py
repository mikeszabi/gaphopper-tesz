"""Költség- és időkimutatás az llm_log alapján (régi és új promptverzió összevetése is)."""
from __future__ import annotations

from .config import price
from .store import Store


def with_cost(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        p = price(r["provider"].replace("anthropic-batch", "anthropic"), r["model"], r.get("pricing_input_tokens", 0))
        cost = None
        if p is not None:
            plain = max(r["input"], 0)
            # A gyorsítótárba írt bemenet (Anthropic, 5 perces cache) a bemeneti ár 1,25-szöröse.
            cost = (plain * p[0] + r.get("cache_write", 0) * p[0] * 1.25 + r["cache_read"] * p[2]
                    + r["output"] * p[1]) / 1e6
            if r["provider"] == "anthropic-batch":
                cost /= 2  # kötegelt kedvezmény
        out.append(r | {"koltseg_usd": None if cost is None else round(cost, 4)})
    return out


def summary(store: Store, job_ids: list[str] | None = None, by: tuple[str, ...] = ("prompt_version", "step")) -> list[dict]:
    """Összesítés a megadott mezők szerint; a munkakörönkénti átlaghoz a munkakörök számával."""
    rows = with_cost(store.usage(job_ids))
    agg: dict[tuple, dict] = {}
    for r in rows:
        k = tuple(r[b] for b in by)
        a = agg.setdefault(k, {b: r[b] for b in by} | {"munkakor": set(), "hivas": 0, "sikeres": 0, "input": 0,
                                                       "cache_read": 0, "cache_write": 0, "output": 0, "ido_mp": 0.0,
                                                       "koltseg_usd": 0.0})
        if r["job_id"] != "-":
            a["munkakor"].add(r["job_id"])
        for f in ("hivas", "sikeres", "input", "cache_read", "cache_write", "output", "ido_mp"):
            a[f] += r[f]
        a["koltseg_usd"] = None if a["koltseg_usd"] is None or r["koltseg_usd"] is None \
            else a["koltseg_usd"] + r["koltseg_usd"]
    out = []
    for a in agg.values():
        n = len(a.pop("munkakor")) or 1
        a["munkakor_db"] = n
        a["output_per_munkakor"] = round(a["output"] / n)
        a["ido_mp"] = round(a["ido_mp"], 1)
        if a["koltseg_usd"] is not None:
            a["koltseg_usd"] = round(a["koltseg_usd"], 3)
            a["koltseg_per_munkakor"] = round(a["koltseg_usd"] / n, 4)
        out.append(a)
    return out
