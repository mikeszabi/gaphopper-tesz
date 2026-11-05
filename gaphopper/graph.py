"""neo4j-réteg: séma, alias- és hasonlósági keresés, idempotens, kötegelt írás (UNWIND + MERGE).

Séma (TESZ-nivaló × SZÍN):
  (:TeszNivalo)-[:TIPUSA]->(:TeszTipus), (:TeszNivalo)-[:IRANYUL {role}]->(:SzerElem)
  (:TeszSzin {id, szin, attestation_hu, rubric_szin_hu})-[:SZINEZI]->(:TeszNivalo)   szin: "Társas / Kritikus"
     a TESZ-SZÍN a ⟨TESZ-nivaló, KÖZ-eg, HELY-zet⟩ példány; címkét nem tárol (TESZ-nivaló + KÖZ-eg/HELY-zet neve)
  (:TeszSzin)-[:KOZEGBEN_ZAJLIK {primary}]->(:Kozeg), -[:HELYZETBEN_VEGZENDO {primary}]->(:Helyzet)
  (:TeszSzin)-[:HASZNAL {role, kozeg_id, helyzet_id}]->(:SzerElem)
  (:Helyzet {tengely, fok})-[:MELYEBB]->(:Helyzet)   (KISZ tengely, fok szerint)
  (:TeszNivalo)-[:MELYITESI_JELOLT {kozeg, indok_hu, forras_munkakor}]->(:Helyzet)
  (:MunkaKor)-[:TARTALMAZ_TESZT {alap_szin, szin_kulonbseg_hu, kontextus_hu, ...}]->(:TeszSzin)   az ALAP-SZÍN munkakörönkénti
  (nincs külön Szin csomópont: a SZÍN a KÖZ-eg és a HELY-zet kombinációja, lekérdezéssel számolható)
  (:MunkaKor)-[:TARTALMAZ_TESZT {relevance, frequency, expected_eqf, ...}]->(:TeszSzin)
"""
from __future__ import annotations

import re

from .config import Settings, kesz_torzs, vocab
from .normalize import alias_tag, norm_key

LABELS = {"MUNKA_KOR": "MunkaKor", "TESZ_SZIN": "TeszSzin", "TESZ_NIVALO": "TeszNivalo", "SZER_ELEM": "SzerElem",
          "KESZSEG": "Keszseg"}
FULLTEXT = {"MunkaKor": "munkakor_text", "TeszSzin": "tesz_text", "TeszNivalo": "nivalo_text", "SzerElem": "szer_text",
            "Keszseg": "keszseg_text"}
VECTOR = {"MunkaKor": "munkakor_vec", "TeszNivalo": "nivalo_vec", "SzerElem": "szer_vec", "Keszseg": "keszseg_vec"}
NODE_LABELS = ["MunkaKor", "TeszNivalo", "TeszSzin", "SzerElem", "TeszTipus", "Kozeg", "Helyzet", "Keszseg"]

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
        # KÉSZ-ség-törzs: rögzített Keszseg-csomópontok (gh_c_<n>), a TESZ-nivalók IGENYEL éllel kötnek hozzájuk.
        rows = [{"id": k, "en": x["en"], "group": x["group"], "type": x["type"]} for k, x in kesz_torzs().items()]
        self.run("UNWIND $rows AS r MERGE (k:Keszseg {id: r.id}) SET k.label_en = r.en, k.label_hu = coalesce(k.label_hu, r.en), "
                 "k.csoport = r.group, k.fajta = r.type, k.forras = 'GH_competency_list', k.status = 'TORZS'", rows=rows)
        # A régi (v1) Szin-csomópontok eltávolítása: a SZÍN a KÖZ-eg és a HELY-zet kombinációja.
        self.run("MATCH (s:Szin) DETACH DELETE s")

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

    def tesz_torzs(self, gen: str, limit: int = 80) -> list[dict]:
        """A P1 TESZ-törzse: az adott szótárverzióval (gen) készült, legtöbb munkakörben használt TESZ-nivalók."""
        return self.run(
            "MATCH (g:TeszNivalo {gen: $gen}) "
            "OPTIONAL MATCH (g)<-[:SZINEZI]-()<-[:TARTALMAZ_TESZT]-(m:MunkaKor) "
            "WITH g, count(DISTINCT m) AS n ORDER BY n DESC, g.created_at LIMIT $limit "
            "MATCH (g)-[:TIPUSA]->(ty), (g)-[i:IRANYUL]->(s:SzerElem) "
            "OPTIONAL MATCH (g)-[e:IGENYEL]->(k:Keszseg) "
            "WITH g, n, ty, i, s, k, e ORDER BY coalesce(e.suly, 0) DESC, coalesce(e.rang, 9) "
            "WITH g, n, ty, i, s, [x IN collect(k.id) WHERE x IS NOT NULL][..5] AS kesz "
            "RETURN g.id AS id, g.label_hu AS label_hu, g.label_en AS label_en, g.outcome_hu AS outcome_hu, "
            "ty.id AS tesz_type, {label_hu: s.label_hu, label_en: coalesce(s.label_en, ''), kind: s.kind, "
            "role: i.role, definition_hu: coalesce(s.definition_hu, '')} AS primary_szer, kesz AS kesz_ids, "
            "n AS munkakor_db ORDER BY n DESC", gen=gen, limit=limit)

    def nivalo_context(self, nivalo_id: str) -> dict:
        res = self.run(
            "MATCH (g:TeszNivalo {id: $id}) "
            "OPTIONAL MATCH (g)-[:TIPUSA]->(ty) OPTIONAL MATCH (g)-[:IRANYUL]->(s:SzerElem) "
            "RETURN ty.id AS tesz_type, s.id AS primary_szer_id, s.label_hu AS primary_szer_label LIMIT 1", id=nivalo_id)
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
        magok = [{"id": k, **v} for k, v in plan.nivalok.items()]
        mely = [{"mag": k, "kozeg": x["kozeg"], "helyzet": x["helyzet"], "indok": x.get("indok_hu", ""),
                 "job": plan.job["id"]} for k, v in plan.nivalok.items() for x in v.get("melyites", [])]
        igenyel = [{"mag": k, "k": kid, "rang": i + 1} for k, v in plan.nivalok.items()
                   for i, kid in enumerate(v.get("kesz_ids", []))]
        tesz = [{"id": k, **v} for k, v in plan.szinek.items()]
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
            tx.run("UNWIND $rows AS r MERGE (g:TeszNivalo {id: r.id}) "
                   "ON CREATE SET g += r.props, g.embedding = r.emb, g.created_at = datetime() "
                   "SET g.last_seen = datetime() "
                   "WITH g, r WHERE r.new "
                   "MATCH (ty:TeszTipus {id: r.tesz_type}), (s:SzerElem {id: r.primary}) "
                   "MERGE (g)-[:TIPUSA]->(ty) MERGE (g)-[i:IRANYUL]->(s) SET i.role = r.role", rows=magok)
            tx.run("UNWIND $rows AS r MATCH (g:TeszNivalo {id: r.mag}), (h:Helyzet {id: r.helyzet}) "
                   "MERGE (g)-[e:MELYITESI_JELOLT {kozeg: r.kozeg}]->(h) SET e.indok_hu = r.indok, "
                   "e.forras_munkakor = r.job", rows=mely)
            # IGENYEL súlyozva: munkakorok = az igénylő munkakörök, suly = darabszámuk. Újrafeldolgozáskor a
            # munkakör korábbi igénylései előbb kikerülnek (a kiürült él törlődik), így nem számolunk kétszer.
            tx.run("MATCH (:TeszNivalo)-[e:IGENYEL]->(:Keszseg) WHERE $j IN coalesce(e.munkakorok, []) "
                   "WITH e, [x IN e.munkakorok WHERE x <> $j] AS rest "
                   "SET e.munkakorok = rest, e.suly = size(rest) WITH e, rest WHERE size(rest) = 0 DELETE e",
                   j=plan.job["id"])
            tx.run("UNWIND $rows AS r MATCH (g:TeszNivalo {id: r.mag}), (k:Keszseg {id: r.k}) MERGE (g)-[e:IGENYEL]->(k) "
                   "WITH e, r, [x IN coalesce(e.munkakorok, []) WHERE x <> $j] + [$j] AS mk "
                   "SET e.munkakorok = mk, e.suly = size(mk), e.rang = r.rang", rows=igenyel, j=plan.job["id"])
            tx.run("UNWIND $rows AS r MATCH (g:TeszNivalo {id: r.mag}) "
                   "MERGE (t:TeszSzin {id: r.id}) ON CREATE SET t += r.props, t.created_at = datetime() "
                   "SET t.last_seen = datetime() MERGE (t)-[:SZINEZI]->(g)", rows=tesz)
            tx.run("UNWIND $rows AS r MATCH (t:TeszSzin {id: r.t}), (k:Kozeg {id: r.k}) "
                   "MERGE (t)-[e:KOZEGBEN_ZAJLIK]->(k) SET e.primary = coalesce(e.primary, false) OR r.p", rows=kozeg)
            tx.run("UNWIND $rows AS r MATCH (t:TeszSzin {id: r.t}), (h:Helyzet {id: r.h}) "
                   "MERGE (t)-[e:HELYZETBEN_VEGZENDO]->(h) SET e.primary = coalesce(e.primary, false) OR r.p",
                   rows=helyzet)
            tx.run("UNWIND $rows AS r MATCH (t:TeszSzin {id: r.t}), (s:SzerElem {id: r.id}) "
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
            tx.run("UNWIND $rows AS r MATCH (m:MunkaKor {id: $j}), (t:TeszSzin {id: r.t}) "
                   "MERGE (m)-[e:TARTALMAZ_TESZT]->(t) SET e += r.props", j=plan.job["id"], rows=plan.links)

        with self.driver.session(database=self.database) as sess:
            sess.execute_write(work)
        return {"szer": len(szer), "mag": len(magok), "szinek": len(tesz), "link": len(plan.links)}

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
        for rel in ["TIPUSA", "IRANYUL", "HASZNAL", "KOZEGBEN_ZAJLIK", "HELYZETBEN_VEGZENDO", "FAJTAJA", "SZINEZI",
                    "MELYITESI_JELOLT", "IGENYEL"]:
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
        """TESZ-nivalónként csoportosítva azok a változatok, amelyeknek még nincs igazolási mondata; a TESZ-nivalónál
        jelölve, kell-e még alaprubrika."""
        return self.run(
            "MATCH (t:TeszSzin)-[:SZINEZI]->(g:TeszNivalo) "
            "WHERE t.attestation_hu IS NULL OR g.rubric_alap_hu IS NULL "
            "MATCH (t)-[:KOZEGBEN_ZAJLIK {primary: true}]->(k:Kozeg), (t)-[:HELYZETBEN_VEGZENDO {primary: true}]->(h:Helyzet) "
            "OPTIONAL MATCH (t)<-[l:TARTALMAZ_TESZT]-() "
            "WITH g, t, k, h, any(x IN collect(l.alap_szin) WHERE x) AS alap, "
            "  [x IN collect(DISTINCT l.szin_kulonbseg_hu) WHERE x IS NOT NULL AND x <> ''][..2] AS kul "
            "ORDER BY g.id, alap DESC, t.id "
            "WITH g, collect({id: t.id, kozeg: k.id, helyzet: h.id, alap: alap, "
            "  szin_kulonbseg_hu: kul, kesz: t.attestation_hu IS NOT NULL}) AS v "
            "OPTIONAL MATCH (g)-[:TIPUSA]->(ty) OPTIONAL MATCH (g)-[:IRANYUL]->(sz:SzerElem) "
            "OPTIONAL MATCH (g)<-[:SZINEZI]-()<-[r:TARTALMAZ_TESZT]-() "
            "WITH g, v, ty, sz, [x IN collect(DISTINCT r.kontextus_hu) WHERE x IS NOT NULL AND x <> ''][..4] AS ktx "
            "RETURN g.id AS id, g.label_hu AS label_hu, g.outcome_hu AS outcome_hu, ty.id AS tesz_type, "
            "sz.label_hu AS primary_szer, g.rubric_alap_hu AS rubric_alap_hu, ktx AS kontextusok, v AS szinek "
            "ORDER BY g.id LIMIT $limit", limit=limit)

    def generalize_candidates(self, min_jobs: int = 2, limit: int = 1000) -> list[dict]:
        """A törzskarbantartás jelöltjei: legalább min_jobs munkakör, legalább 2 eltérő kontextus."""
        return self.run(
            "MATCH (g:TeszNivalo)<-[:SZINEZI]-(:TeszSzin)<-[r:TARTALMAZ_TESZT]-(m:MunkaKor) "
            "WITH g, collect(DISTINCT {munkakor: m.label_hu, kontextus: r.kontextus_hu}) AS mk, "
            "count(DISTINCT m) AS n, [x IN collect(DISTINCT r.kontextus_hu) WHERE x IS NOT NULL AND x <> ''] AS ktx "
            "WHERE n >= $mj AND size(ktx) >= 2 "
            "OPTIONAL MATCH (g)-[:TIPUSA]->(ty) OPTIONAL MATCH (g)-[:IRANYUL]->(s:SzerElem) "
            "OPTIONAL MATCH (g)-[e:IGENYEL]->(k:Keszseg) "
            "WITH g, mk, n, ty, s, k, e ORDER BY coalesce(e.suly, 0) DESC "
            "WITH g, mk, n, ty, s, [x IN collect(k.label_en) WHERE x IS NOT NULL][..5] AS kesz "
            "RETURN g.id AS id, g.label_hu AS label_hu, g.label_en AS label_en, ty.id AS tesz_type, "
            "s.id AS szer_id, s.label_hu AS primary_szer, n AS munkakor_db, mk AS munkakorok, kesz "
            "ORDER BY n DESC LIMIT $limit", mj=min_jobs, limit=limit)

    def rename_nivalo(self, rows: list[dict]) -> None:
        """TESZ-nivaló átnevezése: a régi név alias lesz, az azonosító és a kapcsolatok maradnak."""
        self.run("UNWIND $rows AS r MATCH (g:TeszNivalo {id: r.id}) "
                 "WITH g, r, [x IN ['hu:' + g.label_hu, 'en:' + coalesce(g.label_en, '')] "
                 "  WHERE size(x) > 3 AND NOT x IN coalesce(g.aliases, [])] AS old "
                 "SET g.aliases = coalesce(g.aliases, []) + old, g.alias_keys = coalesce(g.alias_keys, []) + "
                 "  [k IN r.old_keys WHERE NOT k IN coalesce(g.alias_keys, [])], "
                 "g.label_hu = r.label_hu, g.label_en = CASE WHEN r.label_en = '' THEN g.label_en ELSE r.label_en END, "
                 "g.renamed_at = datetime(), g.rename_reason = r.indok "
                 "SET g.alias_text = reduce(s = '', x IN g.aliases | s + ' ' + substring(x, 3))", rows=rows)

    def generalize_szer(self, rows: list[dict]) -> None:
        """A meghatározó SZER-ELEM általánosítása: új, fajta szintű SZER-ELEM; a régi FAJTAJA éllel alá kerül,
        az IRANYUL él az újra mutat (a szerep megmarad)."""
        self.run("UNWIND $rows AS r MATCH (g:TeszNivalo {id: r.id})-[i:IRANYUL]->(old:SzerElem) "
                 "MERGE (new:SzerElem {id: r.szer_id}) ON CREATE SET new.label_hu = r.szer_hu, new.kind = old.kind, "
                 "  new.status = 'JAVASOLT', new.created_at = datetime() "
                 "MERGE (old)-[:FAJTAJA]->(new) MERGE (g)-[i2:IRANYUL]->(new) SET i2.role = i.role DELETE i", rows=rows)

    def split_context(self, nivalo_id: str) -> dict:
        """A bontáshoz: a TESZ-nivaló adatai és munkakörönként a TESZ-SZÍN-jei."""
        g = self.run("MATCH (g:TeszNivalo {id: $id}) OPTIONAL MATCH (g)-[:TIPUSA]->(ty) "
                     "OPTIONAL MATCH (g)-[i:IRANYUL]->(s:SzerElem) "
                     "RETURN g {.*, embedding: null} AS g, ty.id AS tesz_type, s.id AS szer_id, s.kind AS szer_kind, "
                     "i.role AS role", id=nivalo_id)
        if not g:
            raise KeyError(nivalo_id)
        links = self.run("MATCH (m:MunkaKor)-[r:TARTALMAZ_TESZT]->(t:TeszSzin)-[:SZINEZI]->(:TeszNivalo {id: $id}) "
                         "MATCH (t)-[:KOZEGBEN_ZAJLIK {primary: true}]->(k), (t)-[:HELYZETBEN_VEGZENDO {primary: true}]->(h) "
                         "RETURN m.id AS job, m.label_hu AS job_hu, t.id AS szin_id, k.id AS kozeg, h.id AS helyzet",
                         id=nivalo_id)
        return g[0] | {"links": links}

    def apply_split(self, old_id: str, new: dict, moves: list[dict]) -> None:
        """Egy rész leválasztása: új TESZ-nivaló (a régi típusával, a megadott SZER-ELEM-mel), a mozgó munkakörök
        TESZ-SZÍN-jei az új alá kerülnek (KÖZ-eg/HELY-zet/HASZNAL és a munkakör-él tulajdonságai másolva), a
        KÉSZ-ség-súlyok a mozgó munkakörökkel együtt átkerülnek. A régi név és aliasai a régi TESZ-nivalón maradnak."""
        jobs = sorted({m["job"] for m in moves})

        def work(tx):
            tx.run("MATCH (old:TeszNivalo {id: $old}) MERGE (n:TeszNivalo {id: $n.id}) "
                   "ON CREATE SET n.label_hu = $n.label_hu, n.label_en = $n.label_en, n.outcome_hu = old.outcome_hu, "
                   "  n.status = 'BONTASBOL', n.gen = old.gen, n.prompt_version = old.prompt_version, "
                   "  n.created_at = datetime(), n.split_from = $old, "
                   "  n.aliases = ['hu:' + $n.label_hu], n.alias_keys = [$n.key], n.alias_text = ' ' + $n.label_hu "
                   "WITH old, n MATCH (old)-[:TIPUSA]->(ty) MERGE (n)-[:TIPUSA]->(ty)", old=old_id, n=new)
            tx.run("MATCH (n:TeszNivalo {id: $n.id}) MERGE (s:SzerElem {id: $n.szer_id}) "
                   "ON CREATE SET s.label_hu = $n.szer_hu, s.kind = $n.szer_kind, s.status = 'JAVASOLT', "
                   "  s.created_at = datetime() MERGE (n)-[i:IRANYUL]->(s) SET i.role = $n.role", n=new)
            tx.run("UNWIND $rows AS r MATCH (old:TeszSzin {id: r.old_szin}), (n:TeszNivalo {id: $nid}) "
                   "MERGE (t:TeszSzin {id: r.new_szin}) ON CREATE SET t.szin = old.szin, t.status = old.status, "
                   "  t.prompt_version = old.prompt_version, t.created_at = datetime() "
                   "MERGE (t)-[:SZINEZI]->(n) "
                   "WITH old, t, r MATCH (old)-[e:KOZEGBEN_ZAJLIK]->(k) MERGE (t)-[e2:KOZEGBEN_ZAJLIK]->(k) "
                   "SET e2.primary = e.primary", rows=moves, nid=new["id"])
            tx.run("UNWIND $rows AS r MATCH (old:TeszSzin {id: r.old_szin}), (t:TeszSzin {id: r.new_szin}) "
                   "MATCH (old)-[e:HELYZETBEN_VEGZENDO]->(h) MERGE (t)-[e2:HELYZETBEN_VEGZENDO]->(h) "
                   "SET e2.primary = e.primary", rows=moves)
            tx.run("UNWIND $rows AS r MATCH (old:TeszSzin {id: r.old_szin}), (t:TeszSzin {id: r.new_szin}) "
                   "MATCH (old)-[e:HASZNAL]->(s) MERGE (t)-[e2:HASZNAL]->(s) SET e2 += properties(e)", rows=moves)
            tx.run("UNWIND $rows AS r MATCH (m:MunkaKor {id: r.job})-[l:TARTALMAZ_TESZT]->(old:TeszSzin {id: r.old_szin}), "
                   "(t:TeszSzin {id: r.new_szin}) MERGE (m)-[l2:TARTALMAZ_TESZT]->(t) SET l2 += properties(l) DELETE l",
                   rows=moves)
            # a munkakör nélkül maradt régi TESZ-SZÍN-ek törlődnek
            tx.run("UNWIND $rows AS r MATCH (old:TeszSzin {id: r.old_szin}) WHERE NOT ()-[:TARTALMAZ_TESZT]->(old) "
                   "DETACH DELETE old", rows=moves)
            # KÉSZ-ség-súlyok: a mozgó munkakörök igénylései átkerülnek az új TESZ-nivalóra
            tx.run("MATCH (old:TeszNivalo {id: $old})-[e:IGENYEL]->(k:Keszseg), (n:TeszNivalo {id: $nid}) "
                   "WITH e, k, n, [x IN coalesce(e.munkakorok, []) WHERE x IN $jobs] AS mv WHERE size(mv) > 0 "
                   "MERGE (n)-[e2:IGENYEL]->(k) "
                   "WITH e, e2, mv, [x IN coalesce(e2.munkakorok, []) WHERE NOT x IN mv] + mv AS nk, "
                   "  [x IN e.munkakorok WHERE NOT x IN mv] AS rest "
                   "SET e2.munkakorok = nk, e2.suly = size(nk), e2.rang = e.rang, e.munkakorok = rest, e.suly = size(rest) "
                   "WITH e, rest WHERE size(rest) = 0 DELETE e", old=old_id, nid=new["id"], jobs=jobs)

        with self.driver.session(database=self.database) as sess:
            sess.execute_write(work)

    def set_certificates(self, magok: list[dict], valtozatok: list[dict], prompt_version: str) -> None:
        self.run("UNWIND $rows AS r MATCH (g:TeszNivalo {id: r.id}) SET g.rubric_alap_hu = r.rubric_alap_hu, "
                 "g.cert_prompt_version = $pv", rows=magok, pv=prompt_version)
        self.run("UNWIND $rows AS r MATCH (t:TeszSzin {id: r.id}) SET t.attestation_hu = r.attestation_hu, "
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
            "MATCH (n) WHERE n:MunkaKor OR n:TeszNivalo OR n:TeszSzin OR n:SzerElem OR n:Keszseg "
            "RETURN labels(n)[0] AS tipus, count(*) AS db ORDER BY tipus")
        out["tesz_tipus"] = self.run(
            "MATCH (:MunkaKor)-[:TARTALMAZ_TESZT]->(t)-[:SZINEZI]->(:TeszNivalo)-[:TIPUSA]->(ty) "
            "RETURN ty.id AS tipus, count(*) AS db ORDER BY db DESC")
        out["helyzet"] = self.run(
            "MATCH (:MunkaKor)-[:TARTALMAZ_TESZT]->(t)-[r:HELYZETBEN_VEGZENDO {primary: true}]->(h) "
            "RETURN h.id AS helyzet, count(*) AS db ORDER BY db DESC")
        out["valtozat_per_mag"] = self.run(
            "MATCH (g:TeszNivalo)<-[:SZINEZI]-(t) WITH g, count(t) AS v RETURN v AS valtozat, count(g) AS mag_db ORDER BY v")
        out["ujrahasznositas_mag"] = self.run(
            "MATCH (g:TeszNivalo)<-[:SZINEZI]-()<-[:TARTALMAZ_TESZT]-(m) WITH g, count(DISTINCT m) AS mk "
            "RETURN mk AS munkakorok_szama, count(g) AS mag_db ORDER BY mk")
        out["ujrahasznositas"] = self.run(
            "MATCH (t:TeszSzin)<-[:TARTALMAZ_TESZT]-(m) WITH t, count(m) AS mk "
            "RETURN mk AS munkakorok_szama, count(t) AS tesz_db ORDER BY mk")
        return out

    def anchors(self, min_tesz: int = 5, min_jobs: int = 3) -> list[dict]:
        """Az 1. kapu (gyakoriság) feletti SZÍN-horgonyok a P4a-hoz: ⟨KÖZ-eg, HELY-zet⟩ párok."""
        return self.run(
            "MATCH (m:MunkaKor)-[:TARTALMAZ_TESZT]->(t:TeszSzin)-[:KOZEGBEN_ZAJLIK {primary: true}]->(k:Kozeg), "
            "(t)-[:HELYZETBEN_VEGZENDO {primary: true}]->(h:Helyzet) "
            "WITH k, h, count(DISTINCT t) AS tesz_db, count(DISTINCT m) AS munkakor_db "
            "WHERE tesz_db >= $mt AND munkakor_db >= $mj "
            "RETURN k.id + '|' + h.id AS szin, k.id AS kozeg, h.id AS helyzet, tesz_db, munkakor_db "
            "ORDER BY tesz_db DESC", mt=min_tesz, mj=min_jobs)
