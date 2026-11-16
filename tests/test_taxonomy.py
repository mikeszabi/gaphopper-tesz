"""T0 (taxonomy) determinisztikus részei: hierarchia-élek, refs -> BESOROLVA-sorok, ágazat-származtatás."""
from collections import defaultdict

from gaphopper.job_all import Sources
from gaphopper.taxonomy import job_all_edges, occupation_nodes, ref_rows, sector_edges


def _src():
    s = Sources.__new__(Sources)
    s.isco = {"8": {"en": "Operators", "hu": "Gépkezelők", "uri": "u:isco8"},
              "83": {"en": "Drivers", "hu": "Járművezetők", "uri": "u:isco83"},
              "834": {"en": "Mobile plant", "hu": "Mobilgép", "uri": "u:isco834"},
              "8344": {"en": "Lifting truck", "hu": "Emelőgép", "uri": "u:isco8344"}}
    s.esco = {"u:fork": {"code": "8344.1", "isco": "8344", "en": "forklift operator", "hu": "targoncavezető"},
              "u:fork2": {"code": "8344.1.1", "isco": "8344", "en": "reach truck operator", "hu": "oszlopos"}}
    s.esco_by_code = {"8344.1": "u:fork", "8344.1.1": "u:fork2"}
    s.feor = {"8": {"name": "Gépkezelők", "level": 1, "parent": ""},
              "8425": {"name": "Targoncavezető", "level": 4, "parent": "842"}}
    s.soc = {"53-0000": ("Major", "Transportation"), "53-7000": ("Minor", "Material Moving"),
             "53-7050": ("Broad", "Industrial Truck"), "53-7051": ("Detailed", "Industrial Truck")}
    s.onet = {"53-7051.00": {"title": "Industrial Truck and Tractor Operators", "desc": ""}}
    s.isco_feor, s.cw = defaultdict(list), defaultdict(list)
    return s


def test_hierarchy_parents():
    par = {n["id"]: n["_parent"] for n in occupation_nodes(_src())}
    assert par["isco:8344"] == "isco:834" and par["isco:8"] is None
    assert par["esco:8344.1"] == "isco:8344"          # legfelső ESCO-szint az ISCO-csoport alá
    assert par["esco:8344.1.1"] == "esco:8344.1"      # beágyazott ESCO-foglalkozás
    assert par["soc:53-7051"] == "soc:53-7050" and par["soc:53-7000"] == "soc:53-0000"
    assert par["onet:53-7051.00"] == "soc:53-7051"    # O*NET a SOC detailed alá
    assert par["feor:8425"] is None                    # nem létező szülő (842) nem kerül be


REC = {"job_id": "onet:53-7051.00", "codes": {"onet": "53-7051.00"},
       "refs": {"esco": [{"code": "8344.1", "uri": "u:fork", "match": "broadMatch", "method": "crosswalk+llm",
                          "primary": True, "confidence": 0.9},
                         {"code": "8344.1.1", "uri": "u:fork2", "match": "closeMatch", "method": "crosswalk",
                          "primary": False}],
                "feor": [{"code": "8425", "primary": True, "method": "crosswalk"}],
                "crawl": [{"code": "TARGONCA", "primary": True}]}}


def test_ref_rows_and_job_all_edges():
    rows = ref_rows(REC)
    assert {r["b"] for r in rows} == {"esco:8344.1", "esco:8344.1.1", "feor:8425"}  # a crawl nem taxonómia
    assert next(r for r in rows if r["b"] == "esco:8344.1")["confidence"] == 0.9
    edges = job_all_edges({"onet:53-7051.00": REC})
    assert all(e["a"] == "onet:53-7051.00" and e["forras"] == "job_all" for e in edges)


def test_sector_edges_share_and_wef_merge(tmp_path):
    (tmp_path / "ESCO").mkdir()
    (tmp_path / "ESCO/occupation_isic.csv").write_text(
        "Title,uri,ISIC_Code\nforklift,u:fork,\"H,N\"\nreach,u:fork2,O\nlifting,u:isco8344,H\n", encoding="utf-8")
    isic_wef = {"H": "w_trans", "N": "w_pub", "O": "w_pub"}
    out = sector_edges(_src(), tmp_path, {"onet:53-7051.00": REC}, isic_wef)
    onet = {(e["b"], e["forras"]): e for e in out if e["a"] == "onet:53-7051.00"}
    assert onet[("isic:H", "job_all-esco-isic")]["primary"] is True          # az elsődleges ESCO első kódja
    assert onet[("isic:H", "job_all-esco-isic")]["arany"] == 0.333
    wef = onet[("wef:w_pub", "job_all-esco-isic+isic-wef")]                   # N és O egy WEF-re: összevonva
    assert wef["db"] == 2 and wef["arany"] == 0.667
    assert any(e["a"] == "isco:8344" and e["b"] == "isic:H" for e in out)
    assert any(e["a"] == "soc:53-0000" and e["forras"] == "soc-major-aggregate" for e in out)


def test_job_node_rows_without_p1():
    from gaphopper.taxonomy import job_node_rows

    rec = {"job_id": "onet:53-7051.00", "title": "targonca vezetője", "title_en": "Industrial Truck Operators",
           "field": "f", "codes": {"onet": "53-7051.00", "soc": "53-7051", "isco": "8344", "feor": "8425"}}
    (row,) = job_node_rows({rec["job_id"]: rec})
    assert row["id"] == "onet:53-7051.00"
    assert row["props"]["label_hu"] == "targonca vezetője" and row["props"]["label_en"] == "Industrial Truck Operators"
    assert row["kodok"] == {"kod_onet": "53-7051.00", "kod_isco": "8344", "kod_feor": "8425"}
    assert "kod_soc" not in row["props"]


def test_job_props_prefers_p1_labels():
    from gaphopper.merge import job_props

    p = job_props({"title": "a", "title_en": "b"}, {"job_label_hu": "A", "job_label_en": "B", "purpose_hu": "c"})
    assert (p["label_hu"], p["label_en"], p["purpose_hu"]) == ("A", "B", "c")
