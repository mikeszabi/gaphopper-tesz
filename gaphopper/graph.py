"""neo4j-réteg: séma, alias- és hasonlósági keresés, idempotens, kötegelt írás (UNWIND + MERGE).

Séma (TESZ-MAG × SZÍN):
  (:TeszMag)-[:TIPUSA]->(:TeszTipus), (:TeszMag)-[:IRANYUL {role}]->(:SzerElem)
  (:TeszNivalo)-[:MAGJA]->(:TeszMag), (:TeszNivalo)-[:SZINBEN]->(:Szin)
  (:TeszNivalo)-[:KOZEGBEN_ZAJLIK {primary}]->(:Kozeg), -[:HELYZETBEN_VEGZENDO {primary}]->(:Helyzet)
  (:TeszNivalo)-[:HASZNAL {role, kozeg_id, helyzet_id}]->(:SzerElem)
  (:Szin {id: 'KOZ_x|HELY_y'})-[:KOZEGE]->(:Kozeg), -[:HELYZETE]->(:Helyzet)
  (:Helyzet {tengely, fok})-[:MELYEBB]->(:Helyzet)   (KISZ tengely, fok szerint)
  (:TeszMag)-[:MELYITESI_JELOLT {indok_hu, forras_munkakor}]->(:Szin)
  (:MunkaKor)-[:TARTALMAZ_TESZT {relevance, frequency, expected_eqf, ...}]->(:TeszNivalo)
"""
from __future__ import annotations

import re

from .config import Settings, kesz_torzs, vocab
from .normalize import alias_tag, norm_key

LABELS = {"MUNKA_KOR": "MunkaKor", "TESZ": "TeszNivalo", "TESZ_MAG": "TeszMag", "SZER_ELEM": "SzerElem",
          "KESZSEG": "Keszseg"}
FULLTEXT = {"MunkaKor": "munkakor_text", "TeszNivalo": "tesz_text", "TeszMag": "mag_text", "SzerElem": "szer_text",
            "Keszseg": "keszseg_text"}
VECTOR = {"MunkaKor": "munkakor_vec", "TeszMag": "mag_vec", "SzerElem": "szer_vec", "Keszseg": "keszseg_vec"}
NODE_LABELS = ["MunkaKor", "TeszMag", "TeszNivalo", "SzerElem", "TeszTipus", "Kozeg", "Helyzet", "Szin", "Keszseg"]


def szin_id(kozeg: str, helyzet: str) -> str:
    return f"{kozeg}|{helyzet}"
_LUCENE = re.compile(r'([+\-&|!(){}\[\]^"~*?:\\/])')


def _label(entity_type: str) -> str:
    return LABELS[entity_type]


def neo4j_driver():
    """Load the driver lazily, with an actionable error for missing dependencies."""
    try:
        from neo4j import GraphDatabase, Query
    except ImportError as exc:
        raise RuntimeError(
            "Hiányzik a Neo4j Python-driver az alkalmazás Python-környezetéből. "
            "Telepítés: python -m pip install 'neo4j>=5.20', majd indítsd újra a webes szervert."
        ) from exc
    return GraphDatabase, Query


class Graph:
    def __init__(self, s: Settings):
        GraphDatabase, _ = neo4j_driver()

        self.driver = GraphDatabase.driver(s.neo4j_uri, auth=(s.neo4j_user, s.neo4j_password))
        self.database = s.neo4j_database

    def close(self) -> None:
        self.driver.close()

    def run(self, query: str, **params):
        records, _, _ = self.driver.execute_query(query, params, database_=self.database)
        return [r.data() for r in records]

    # ---------------------------------------------------------------- séma
    def setup(self, embed_dim: int | None = None) -> None:
        for lbl in NODE_LABELS:
            self.run(f"CREATE CONSTRAINT {lbl.lower()}_id IF NOT EXISTS FOR (n:{lbl}) REQUIRE n.id IS UNIQUE")
        for lbl, idx in FULLTEXT.items():
            self.run(f"CREATE FULLTEXT INDEX {idx} IF NOT EXISTS FOR (n:{lbl}) "
                     f"ON EACH [n.label_hu, n.label_en, n.alias_text]")
        if embed_dim:
            for lbl, idx in VECTOR.items():
                self.run(f"CREATE VECTOR INDEX {idx} IF NOT EXISTS FOR (n:{lbl}) ON n.embedding "
                         f"OPTIONS {{indexConfig: {{`vector.dimensions`: {int(embed_dim)}, "
                         f"`vector.similarity_function`: 'cosine'}}}}")
        v = vocab()
        rows = [{"id": k, "hu": x["hu"], "gyok": x.get("gyok", ""), "en": x.get("en", "")}
                for k, x in v["tesz_tipus"].items()]
        self.run("UNWIND $rows AS r MERGE (n:TeszTipus {id: r.id}) "
                 "SET n.label_hu = r.hu, n.gyok = r.gyok, n.label_en = r.en", rows=rows)
        rows = [{"id": k, "hu": x["hu"], "gyok": x.get("gyok", ""), "en": x.get("en", "")}
                for k, x in v["kozeg"].items()]
        self.run("UNWIND $rows AS r MERGE (n:Kozeg {id: r.id}) "
                 "SET n.label_hu = r.hu, n.gyok = r.gyok, n.label_en = r.en", rows=rows)
        rows = [{"id": k, "hu": x["hu"], "gyok": x.get("gyok", ""), "en": x.get("en", ""),
                 "tengely": x.get("tengely", ""), "fok": x.get("fok")} for k, x in v["helyzet"].items()]
        self.run("UNWIND $rows AS r MERGE (n:Helyzet {id: r.id}) "
                 "SET n.label_hu = r.hu, n.gyok = r.gyok, n.label_en = r.en, n.tengely = r.tengely, n.fok = r.fok",
                 rows=rows)
        # A MÉLY-ítés lánca a KISZ tengelyen: fok szerint egymás után.
        kisz = sorted((r for r in rows if r["fok"] is not None), key=lambda r: r["fok"])
        self.run("UNWIND $pairs AS p MATCH (a:Helyzet {id: p[0]}), (b:Helyzet {id: p[1]}) MERGE (a)-[:MELYEBB]->(b)",
                 pairs=[[a["id"], b["id"]] for a, b in zip(kisz, kisz[1:])])
        # KÉSZ-ség-törzs: rögzített Keszseg-csomópontok (gh_c_<n>), a MAG-ok IGENYEL éllel kötnek hozzájuk.
        rows = [{"id": k, "en": x["en"], "group": x["group"], "type": x["type"]} for k, x in kesz_torzs().items()]
        self.run("UNWIND $rows AS r MERGE (k:Keszseg {id: r.id}) SET k.label_en = r.en, k.label_hu = coalesce(k.label_hu, r.en), "
                 "k.csoport = r.group, k.fajta = r.type, k.forras = 'GH_competency_list', k.status = 'TORZS'", rows=rows)
        # SZÍN: minden ⟨KÖZ-eg, HELY-zet⟩ pár rögzített csomópont.
        szin = [{"id": szin_id(k, h), "k": k, "h": h, "hu": f"{kx['hu']} / {hx['hu']}"}
                for k, kx in v["kozeg"].items() for h, hx in v["helyzet"].items()]
        self.run("UNWIND $rows AS r MERGE (s:Szin {id: r.id}) SET s.label_hu = r.hu, s.kozeg = r.k, s.helyzet = r.h "
                 "WITH s, r MATCH (k:Kozeg {id: r.k}), (h:Helyzet {id: r.h}) "
                 "MERGE (s)-[:KOZEGE]->(k) MERGE (s)-[:HELYZETE]->(h)", rows=szin)

    def reset(self) -> dict:
        """Teljes ürítés: minden csomópont, index és constraint törlődik (az init újraépíti)."""
        n = self.run("MATCH (n) RETURN count(n) AS n")[0]["n"]
        while self.run("MATCH (n) WITH n LIMIT 10000 DETACH DELETE n RETURN count(*) AS c")[0]["c"]:
            pass
        idx = [r["name"] for r in self.run("SHOW CONSTRAINTS YIELD name RETURN name")]
        for name in idx:
            self.run(f"DROP CONSTRAINT `{name}` IF EXISTS")
        ix = [r["name"] for r in self.run("SHOW INDEXES YIELD name, type WHERE type <> 'LOOKUP' RETURN name")]
        for name in ix:
            self.run(f"DROP INDEX `{name}` IF EXISTS")
        return {"csomopont": n, "constraint": len(idx), "index": len(ix)}

    # ---------------------------------------------------------------- keresés
    def find_by_alias(self, entity_type: str, key: str) -> str | None:
        lbl = _label(entity_type)
        res = self.run(f"MATCH (n:{lbl}) WHERE $key IN coalesce(n.alias_keys, []) RETURN n.id AS id LIMIT 1",
                       key=key)
        return res[0]["id"] if res else None

    def find_by_aliases(self, entity_type: str, keys: list[str]) -> dict[str, str]:
        """Több alias-kulcs egy lekérdezésben: kulcs -> entitás-id."""
        if not keys:
            return {}
        lbl = _label(entity_type)
        rows = self.run(f"MATCH (n:{lbl}) WHERE any(k IN coalesce(n.alias_keys, []) WHERE k IN $keys) "
                        "RETURN n.id AS id, [k IN n.alias_keys WHERE k IN $keys] AS hit", keys=keys)
        out: dict[str, str] = {}
        for r in rows:
            for k in r["hit"]:
                out.setdefault(k, r["id"])
        return out

    def existing_ids(self, entity_type: str, ids: list[str]) -> set[str]:
        lbl = _label(entity_type)
        return {r["id"] for r in self.run(f"UNWIND $ids AS i MATCH (n:{lbl} {{id: i}}) RETURN n.id AS id", ids=ids)}

    def exists(self, entity_type: str, entity_id: str) -> bool:
        lbl = _label(entity_type)
        return bool(self.run(f"MATCH (n:{lbl} {{id: $id}}) RETURN n.id AS id", id=entity_id))

    def fulltext_candidates(self, entity_type: str, text: str, k: int = 8) -> list[dict]:
        lbl = _label(entity_type)
        q = " ".join(_LUCENE.sub(r"\\\1", w) for w in text.split() if w)
        if not q:
            return []
        return self.run(
            f"CALL db.index.fulltext.queryNodes('{FULLTEXT[lbl]}', $q) YIELD node, score "
            "RETURN node {.*, embedding: null} AS props, score ORDER BY score DESC LIMIT $k", q=q, k=k)

    def vector_candidates(self, entity_type: str, vec: list[float], k: int = 8) -> list[dict]:
        lbl = _label(entity_type)
        return self.run(
            f"CALL db.index.vector.queryNodes('{VECTOR[lbl]}', $k, $vec) YIELD node, score "
            "RETURN node {.*, embedding: null} AS props, score", k=k, vec=vec)

    def mag_torzs(self, gen: str, limit: int = 80) -> list[dict]:
        """A P1 MAG-törzse: az adott szótárverzióval (gen) készült, legtöbb munkakörben használt MAG-ok."""
        return self.run(
            "MATCH (g:TeszMag {gen: $gen}) "
            "OPTIONAL MATCH (g)<-[:MAGJA]-()<-[:TARTALMAZ_TESZT]-(m:MunkaKor) "
            "WITH g, count(DISTINCT m) AS n ORDER BY n DESC, g.created_at LIMIT $limit "
            "MATCH (g)-[:TIPUSA]->(ty), (g)-[i:IRANYUL]->(s:SzerElem) "
            "OPTIONAL MATCH (g)-[:IGENYEL]->(k:Keszseg) "
            "WITH g, n, ty, i, s, collect(k.id) AS kesz "
            "RETURN g.id AS id, g.label_hu AS label_hu, g.label_en AS label_en, g.outcome_hu AS outcome_hu, "
            "ty.id AS tesz_type, {label_hu: s.label_hu, label_en: coalesce(s.label_en, ''), kind: s.kind, "
            "role: i.role, definition_hu: coalesce(s.definition_hu, '')} AS primary_szer, kesz AS kesz_ids, "
            "n AS munkakor_db ORDER BY n DESC", gen=gen, limit=limit)

    def mag_context(self, mag_id: str) -> dict:
        res = self.run(
            "MATCH (g:TeszMag {id: $id}) "
            "OPTIONAL MATCH (g)-[:TIPUSA]->(ty) OPTIONAL MATCH (g)-[:IRANYUL]->(s:SzerElem) "
            "RETURN ty.id AS tesz_type, s.id AS primary_szer_id, s.label_hu AS primary_szer_label LIMIT 1", id=mag_id)
        return res[0] if res else {}

    # ---------------------------------------------------------------- írás
    def add_aliases(self, entity_type: str, entity_id: str, aliases: list[tuple[str, str]]) -> None:
        """aliases: [(szöveg, nyelv)]. Az aliasok az entitás tulajdonságai, nem külön csomópontok."""
        lbl = _label(entity_type)
        tags = [alias_tag(t, l) for t, l in aliases if t and t.strip()]
        keys = [norm_key(t, l) for t, l in aliases if t and t.strip()]
        if not tags:
            return
        self.run(
            f"MATCH (n:{lbl} {{id: $id}}) "
            "WITH n, [x IN $tags WHERE NOT x IN coalesce(n.aliases, [])] AS nt, "
            "[x IN $keys WHERE NOT x IN coalesce(n.alias_keys, [])] AS nk "
            "SET n.aliases = coalesce(n.aliases, []) + nt, n.alias_keys = coalesce(n.alias_keys, []) + nk "
            "SET n.alias_text = reduce(s = '', x IN n.aliases | s + ' ' + substring(x, 3))",
            id=entity_id, tags=tags, keys=keys)

    def set_embedding(self, entity_type: str, entity_id: str, vec: list[float]) -> None:
        lbl = _label(entity_type)
        self.run(f"MATCH (n:{lbl} {{id: $id}}) SET n.embedding = $vec", id=entity_id, vec=vec)

    def upsert_job(self, job_id: str, props: dict) -> None:
        self.run("MERGE (m:MunkaKor {id: $id}) SET m += $props", id=job_id, props=props)

    def clear_job_links(self, job_id: str) -> None:
        """Újrafeldolgozás előtt: a munkakör régi TESZ-kapcsolatai törlődnek (a TESZ-ek maradnak)."""
        self.run("MATCH (:MunkaKor {id: $j})-[r:TARTALMAZ_TESZT]->() DELETE r", j=job_id)

    def write_plan(self, plan) -> dict:
        """Egy munkakör egyesítés utáni írása egy tranzakcióban, típusonként egy UNWIND-lekérdezéssel."""
        aliases = [{"lbl": _label(et), "id": eid, "tags": [alias_tag(t, l) for t, l in labs],
                    "keys": [norm_key(t, l) for t, l in labs]} for (et, eid), labs in plan.aliases.items() if labs]
        szer = [{"id": k, "props": {x: y for x, y in v.items() if x != "embedding"}, "emb": v.get("embedding")}
                for k, v in plan.szer.items()]
        magok = [{"id": k, **v} for k, v in plan.magok.items()]
        mely = [{"mag": k, "szin": szin_id(x["kozeg"], x["helyzet"]), "indok": x.get("indok_hu", ""),
                 "job": plan.job["id"]} for k, v in plan.magok.items() for x in v.get("melyites", [])]
        igenyel = [{"mag": k, "k": kid} for k, v in plan.magok.items() for kid in v.get("kesz_ids", [])]
        tesz = [{"id": k, **v} for k, v in plan.tesz.items()]
        kozeg = [{"t": t["id"], "k": k, "p": i == 0} for t in tesz for i, k in enumerate(t["kozeg"])]
        helyzet = [{"t": t["id"], "h": h, "p": i == 0} for t in tesz for i, h in enumerate(t["helyzet"])]
        hasznal = [{"t": t["id"], **s} for t in tesz for s in t["secondary"]]

        def work(tx):
            tx.run("MERGE (m:MunkaKor {id: $id}) SET m += $props", plan.job)
            tx.run("UNWIND $rows AS r MERGE (s:SzerElem {id: r.id}) "
                   "ON CREATE SET s += r.props, s.embedding = r.emb, s.created_at = datetime() "
                   "SET s.last_seen = datetime()", rows=szer)
            tx.run("UNWIND $rows AS r MATCH (a:SzerElem {id: r.a}), (b:SzerElem {id: r.b}) MERGE (a)-[:FAJTAJA]->(b)",
                   rows=plan.szer_broader)
            tx.run("UNWIND $rows AS r MERGE (g:TeszMag {id: r.id}) "
                   "ON CREATE SET g += r.props, g.embedding = r.emb, g.created_at = datetime() "
                   "SET g.last_seen = datetime() "
                   "WITH g, r WHERE r.new "
                   "MATCH (ty:TeszTipus {id: r.tesz_type}), (s:SzerElem {id: r.primary}) "
                   "MERGE (g)-[:TIPUSA]->(ty) MERGE (g)-[i:IRANYUL]->(s) SET i.role = r.role", rows=magok)
            tx.run("UNWIND $rows AS r MATCH (g:TeszMag {id: r.mag}), (s:Szin {id: r.szin}) "
                   "MERGE (g)-[e:MELYITESI_JELOLT]->(s) SET e.indok_hu = r.indok, e.forras_munkakor = r.job", rows=mely)
            tx.run("UNWIND $rows AS r MATCH (g:TeszMag {id: r.mag}), (k:Keszseg {id: r.k}) MERGE (g)-[:IGENYEL]->(k)",
                   rows=igenyel)
            tx.run("UNWIND $rows AS r MATCH (g:TeszMag {id: r.mag}), (s:Szin {id: r.szin}) "
                   "MERGE (t:TeszNivalo {id: r.id}) ON CREATE SET t += r.props, t.created_at = datetime() "
                   "SET t.last_seen = datetime() MERGE (t)-[:MAGJA]->(g) MERGE (t)-[:SZINBEN]->(s)", rows=tesz)
            tx.run("UNWIND $rows AS r MATCH (t:TeszNivalo {id: r.t}), (k:Kozeg {id: r.k}) "
                   "MERGE (t)-[e:KOZEGBEN_ZAJLIK]->(k) SET e.primary = coalesce(e.primary, false) OR r.p", rows=kozeg)
            tx.run("UNWIND $rows AS r MATCH (t:TeszNivalo {id: r.t}), (h:Helyzet {id: r.h}) "
                   "MERGE (t)-[e:HELYZETBEN_VEGZENDO]->(h) SET e.primary = coalesce(e.primary, false) OR r.p",
                   rows=helyzet)
            tx.run("UNWIND $rows AS r MATCH (t:TeszNivalo {id: r.t}), (s:SzerElem {id: r.id}) "
                   "MERGE (t)-[e:HASZNAL]->(s) SET e.role = r.role, e.kozeg_id = r.kozeg, e.helyzet_id = r.helyzet",
                   rows=hasznal)
            for lbl in sorted({a["lbl"] for a in aliases}):
                tx.run(f"UNWIND $rows AS r MATCH (n:{lbl} {{id: r.id}}) "
                       "WITH n, [x IN r.tags WHERE NOT x IN coalesce(n.aliases, [])] AS nt, "
                       "[x IN r.keys WHERE NOT x IN coalesce(n.alias_keys, [])] AS nk "
                       "SET n.aliases = coalesce(n.aliases, []) + nt, n.alias_keys = coalesce(n.alias_keys, []) + nk "
                       "SET n.alias_text = reduce(s = '', x IN n.aliases | s + ' ' + substring(x, 3))",
                       rows=[{"id": a["id"], "tags": a["tags"], "keys": a["keys"]} for a in aliases if a["lbl"] == lbl])
            tx.run("MATCH (:MunkaKor {id: $j})-[r:TARTALMAZ_TESZT]->() DELETE r", j=plan.job["id"])
            tx.run("UNWIND $rows AS r MATCH (m:MunkaKor {id: $j}), (t:TeszNivalo {id: r.t}) "
                   "MERGE (m)-[e:TARTALMAZ_TESZT]->(t) SET e += r.props", j=plan.job["id"], rows=plan.links)

        with self.driver.session(database=self.database) as sess:
            sess.execute_write(work)
        return {"szer": len(szer), "mag": len(magok), "tesz": len(tesz), "link": len(plan.links)}

    def merge_entities(self, entity_type: str, keep_id: str, drop_id: str) -> None:
        """Két azonos entitás összevonása: a drop nevei aliasok lesznek, kapcsolatai átkötődnek."""
        lbl = _label(entity_type)
        self.run(
            f"MATCH (k:{lbl} {{id: $keep}}), (d:{lbl} {{id: $drop}}) "
            "SET k.aliases = coalesce(k.aliases, []) + [x IN coalesce(d.aliases, []) + "
            "['hu:' + coalesce(d.label_hu, ''), 'en:' + coalesce(d.label_en, '')] "
            "  WHERE NOT x IN coalesce(k.aliases, []) AND size(x) > 3], "
            "k.alias_keys = coalesce(k.alias_keys, []) + [x IN coalesce(d.alias_keys, []) "
            "  WHERE NOT x IN coalesce(k.alias_keys, [])], "
            "k.merged_ids = coalesce(k.merged_ids, []) + [$drop] + coalesce(d.merged_ids, [])",
            keep=keep_id, drop=drop_id)
        # Kapcsolatok átkötése APOC nélkül: a leggyakoribb éltípusokra.
        for rel in ["TARTALMAZ_TESZT"]:
            self.run(f"MATCH (a)-[r:{rel}]->(d:{lbl} {{id: $drop}}), (k:{lbl} {{id: $keep}}) "
                     f"MERGE (a)-[r2:{rel}]->(k) SET r2 += properties(r) DELETE r", keep=keep_id, drop=drop_id)
        for rel in ["TIPUSA", "IRANYUL", "HASZNAL", "KOZEGBEN_ZAJLIK", "HELYZETBEN_VEGZENDO", "FAJTAJA", "MAGJA",
                    "SZINBEN", "MELYITESI_JELOLT", "IGENYEL"]:
            self.run(f"MATCH (d:{lbl} {{id: $drop}})-[r:{rel}]->(b), (k:{lbl} {{id: $keep}}) "
                     f"MERGE (k)-[r2:{rel}]->(b) SET r2 += properties(r) DELETE r", keep=keep_id, drop=drop_id)
            self.run(f"MATCH (a)-[r:{rel}]->(d:{lbl} {{id: $drop}}), (k:{lbl} {{id: $keep}}) "
                     f"MERGE (a)-[r2:{rel}]->(k) SET r2 += properties(r) DELETE r", keep=keep_id, drop=drop_id)
        self.run(f"MATCH (d:{lbl} {{id: $drop}}) DETACH DELETE d", drop=drop_id)
        self.run(f"MATCH (k:{lbl} {{id: $keep}}) "
                 "SET k.alias_text = reduce(s = '', x IN k.aliases | s + ' ' + substring(x, 3))", keep=keep_id)

    def missing_embeddings(self, entity_type: str, limit: int = 1000) -> list[dict]:
        lbl = _label(entity_type)
        return self.run(
            f"MATCH (n:{lbl}) WHERE n.embedding IS NULL RETURN n.id AS id, n.label_hu AS label_hu, "
            "n.label_en AS label_en, coalesce(n.outcome_hu, n.definition_hu, '') AS extra LIMIT $limit",
            limit=limit)

    # ---------------------------------------------------------------- P5: tanúsítási szöveg
    def p5_todo(self, limit: int = 1000) -> list[dict]:
        """MAG-onként csoportosítva azok a változatok, amelyeknek még nincs igazolási mondata; a MAG-nál
        jelölve, kell-e még alaprubrika."""
        return self.run(
            "MATCH (t:TeszNivalo)-[:MAGJA]->(g:TeszMag) "
            "WHERE t.attestation_hu IS NULL OR g.rubric_alap_hu IS NULL "
            "WITH g, t ORDER BY g.id, t.alap_szin DESC, t.id "
            "MATCH (t)-[:SZINBEN]->(s:Szin) "
            "WITH g, collect({id: t.id, label_hu: t.label_hu, kozeg: s.kozeg, helyzet: s.helyzet, "
            "  alap: t.alap_szin, szin_kulonbseg_hu: t.szin_kulonbseg_hu, kesz: t.attestation_hu IS NOT NULL}) AS v "
            "OPTIONAL MATCH (g)-[:TIPUSA]->(ty) OPTIONAL MATCH (g)-[:IRANYUL]->(sz:SzerElem) "
            "OPTIONAL MATCH (g)<-[:MAGJA]-()<-[r:TARTALMAZ_TESZT]-() "
            "WITH g, v, ty, sz, [x IN collect(DISTINCT r.kontextus_hu) WHERE x IS NOT NULL AND x <> ''][..4] AS ktx "
            "RETURN g.id AS id, g.label_hu AS label_hu, g.outcome_hu AS outcome_hu, ty.id AS tesz_type, "
            "sz.label_hu AS primary_szer, g.rubric_alap_hu AS rubric_alap_hu, ktx AS kontextusok, v AS valtozatok "
            "ORDER BY g.id LIMIT $limit", limit=limit)

    def set_certificates(self, magok: list[dict], valtozatok: list[dict], prompt_version: str) -> None:
        self.run("UNWIND $rows AS r MATCH (g:TeszMag {id: r.id}) SET g.rubric_alap_hu = r.rubric_alap_hu, "
                 "g.cert_prompt_version = $pv", rows=magok, pv=prompt_version)
        self.run("UNWIND $rows AS r MATCH (t:TeszNivalo {id: r.id}) SET t.attestation_hu = r.attestation_hu, "
                 "t.rubric_szin_hu = r.rubric_szin_hu, t.cert_prompt_version = $pv", rows=valtozatok, pv=prompt_version)

    def read(self, query: str, limit: int = 500, **params) -> list[dict]:
        """Csak olvasó lekérdezés (a felület adatbázis-nézetéhez): írási kísérletnél a neo4j hibát ad."""
        from neo4j import READ_ACCESS

        with self.driver.session(database=self.database, default_access_mode=READ_ACCESS) as sess:
            def work(tx):
                res = tx.run(query, params)
                out = []
                for rec in res:
                    out.append(rec.data())
                    if len(out) >= limit:
                        break
                return out
            return sess.execute_read(work)

    # ---------------------------------------------------------------- statisztika
    def stats(self) -> dict:
        out = {}
        out["csomopontok"] = self.run(
            "MATCH (n) WHERE n:MunkaKor OR n:TeszMag OR n:TeszNivalo OR n:SzerElem OR n:Keszseg "
            "RETURN labels(n)[0] AS tipus, count(*) AS db ORDER BY tipus")
        out["tesz_tipus"] = self.run(
            "MATCH (:MunkaKor)-[:TARTALMAZ_TESZT]->(t)-[:MAGJA]->(:TeszMag)-[:TIPUSA]->(ty) "
            "RETURN ty.id AS tipus, count(*) AS db ORDER BY db DESC")
        out["helyzet"] = self.run(
            "MATCH (:MunkaKor)-[:TARTALMAZ_TESZT]->(t)-[r:HELYZETBEN_VEGZENDO {primary: true}]->(h) "
            "RETURN h.id AS helyzet, count(*) AS db ORDER BY db DESC")
        out["valtozat_per_mag"] = self.run(
            "MATCH (g:TeszMag)<-[:MAGJA]-(t) WITH g, count(t) AS v RETURN v AS valtozat, count(g) AS mag_db ORDER BY v")
        out["ujrahasznositas_mag"] = self.run(
            "MATCH (g:TeszMag)<-[:MAGJA]-()<-[:TARTALMAZ_TESZT]-(m) WITH g, count(DISTINCT m) AS mk "
            "RETURN mk AS munkakorok_szama, count(g) AS mag_db ORDER BY mk")
        out["ujrahasznositas"] = self.run(
            "MATCH (t:TeszNivalo)<-[:TARTALMAZ_TESZT]-(m) WITH t, count(m) AS mk "
            "RETURN mk AS munkakorok_szama, count(t) AS tesz_db ORDER BY mk")
        return out

    def anchors(self, min_tesz: int = 5, min_jobs: int = 3) -> list[dict]:
        """Az 1. kapu (gyakoriság) feletti SZÍN-horgonyok a P4a-hoz, a Szin csomópontból."""
        return self.run(
            "MATCH (m:MunkaKor)-[:TARTALMAZ_TESZT]->(t:TeszNivalo)-[:SZINBEN]->(s:Szin) "
            "WITH s, count(DISTINCT t) AS tesz_db, count(DISTINCT m) AS munkakor_db "
            "WHERE tesz_db >= $mt AND munkakor_db >= $mj "
            "RETURN s.id AS szin, s.kozeg AS kozeg, s.helyzet AS helyzet, tesz_db, munkakor_db "
            "ORDER BY tesz_db DESC", mt=min_tesz, mj=min_jobs)
