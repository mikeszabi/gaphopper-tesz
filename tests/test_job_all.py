"""J0 (job_all) determinisztikus részei: FEOR-út, FEOR-jelöltek, elsődleges ESCO kiválasztása."""
from collections import defaultdict

from gaphopper.job_all import Sources, _needs_pick, apply_esco


def _src():
    s = Sources.__new__(Sources)
    s.feor = {"8": {"name": "Gépkezelők", "level": 1, "parent": ""},
              "84": {"name": "Járművezetők", "level": 2, "parent": "8"},
              "841": {"name": "Anyagmozgató gépek kezelői", "level": 3, "parent": "84"},
              "8417": {"name": "Targoncavezető", "level": 4, "parent": "841"},
              "8418": {"name": "Rakodógép-kezelő", "level": 4, "parent": "841"}}
    s.isco_feor = defaultdict(list, {"8344": ["8417", "8418", "999"], "834": ["841"]})
    s.esco = {"u_fork": {"isco": "8344", "code": "8344.1", "en": "forklift operator", "hu": "targoncavezető"},
              "u_move": {"isco": "8332", "code": "8332.7", "en": "moving truck driver", "hu": "költöztető"}}
    return s


def test_feor_path_and_candidates():
    s = _src()
    assert [n["code"] for n in s.feor_path("8417")] == ["8", "84", "841", "8417"]
    assert s.feor_candidates(["8344"]) == ["8417", "8418"]          # nem létező kód kimarad
    assert s.feor_candidates(["7111"]) == []                        # nincs 4 jegyű és 3 jegyű sem
    assert sorted(s.feor_candidates(["8349"])) == ["8417", "8418"]  # 3 jegyű tartalék: az alcsoport elemei


def test_needs_pick():
    assert not _needs_pick({"esco_rows": [{"match": "closeMatch"}]})
    assert not _needs_pick({"esco_rows": [{"match": "exactMatch"}, {"match": "broadMatch"}]})
    assert _needs_pick({"esco_rows": [{"match": "closeMatch"}, {"match": "broadMatch"}]})


def test_apply_esco_reorders_and_recomputes_feor():
    s = _src()
    plans = {"53-7051.00": {"esco_open": False, "isco_rows": [], "feor_cands": [],
                            "esco_rows": [{"uri": "u_move", "match": "closeMatch"},
                                          {"uri": "u_fork", "match": "broadMatch"}]}}
    apply_esco(s, plans, {"53-7051.00": {"esco": "u_fork", "confidence": 0.9}})
    p = plans["53-7051.00"]
    assert p["esco_rows"][0]["uri"] == "u_fork" and p["esco_rows"][0]["method"] == "crosswalk+llm"
    assert p["esco_rows"][0]["match"] == "broadMatch"  # a crosswalk viszonytípusa megmarad
    assert p["isco_order"][0] == "8344" and p["feor_cands"] == ["8417", "8418"]
