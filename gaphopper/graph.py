"""neo4j-réteg: séma, alias- és hasonlósági keresés, idempotens írás (MERGE)."""
from __future__ import annotations

import re

from .config import Settings, vocab
from .normalize import alias_tag, norm_key

LABELS = {"MUNKA_KOR": "MunkaKor", "TESZ": "TeszNivalo", "SZER_ELEM": "SzerElem", "KESZSEG": "Keszseg"}
FULLTEXT = {"MunkaKor": "munkakor_text", "TeszNivalo": "tesz_text", "SzerElem": "szer_text", "Keszseg": "keszseg_text"}
VECTOR = {"MunkaKor": "munkakor_vec", "TeszNivalo": "tesz_vec", "SzerElem": "szer_vec", "Keszseg": "keszseg_vec"}
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
        for lbl in ["MunkaKor", "TeszNivalo", "SzerElem", "TeszTipus", "Kozeg", "Helyzet", "Keszseg"]:
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
                 "tengely": x.get("tengely", "")} for k, x in v["helyzet"].items()]
        self.run("UNWIND $rows AS r MERGE (n:Helyzet {id: r.id}) "
                 "SET n.label_hu = r.hu, n.gyok = r.gyok, n.label_en = r.en, n.tengely = r.tengely", rows=rows)

    # ---------------------------------------------------------------- keresés
    def find_by_alias(self, entity_type: str, key: str) -> str | None:
        lbl = _label(entity_type)
        res = self.run(f"MATCH (n:{lbl}) WHERE $key IN coalesce(n.alias_keys, []) RETURN n.id AS id LIMIT 1",
                       key=key)
        return res[0]["id"] if res else None

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

    def tesz_context(self, tesz_id: str) -> dict:
        res = self.run(
            "MATCH (t:TeszNivalo {id: $id}) "
            "OPTIONAL MATCH (t)-[:TIPUSA]->(ty) OPTIONAL MATCH (t)-[r:IRANYUL]->(s:SzerElem) "
            "RETURN t {.*, embedding: null} AS t, ty.id AS tesz_type, s.id AS primary_szer_id, "
            "s.label_hu AS primary_szer_label, r.helyzet_id AS helyzet", id=tesz_id)
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

    def upsert_szer(self, szer_id: str, props: dict) -> None:
        self.run("MERGE (s:SzerElem {id: $id}) ON CREATE SET s += $props, s.created_at = datetime() "
                 "SET s.last_seen = datetime()", id=szer_id, props=props)

    def link_szer_broader(self, narrow_id: str, broad_id: str) -> None:
        self.run("MATCH (a:SzerElem {id: $a}), (b:SzerElem {id: $b}) MERGE (a)-[:FAJTAJA]->(b)",
                 a=narrow_id, b=broad_id)

    def upsert_tesz(self, tesz_id: str, props: dict, tesz_type: str, primary: dict,
                    kozeg: list[tuple[str, bool]], helyzet: list[tuple[str, bool]],
                    secondary: list[dict], variant_of: str | None = None) -> None:
        self.run(
            "MERGE (t:TeszNivalo {id: $id}) ON CREATE SET t += $props, t.created_at = datetime() "
            "SET t.last_seen = datetime(), t.prompt_version = $props.prompt_version "
            "WITH t MATCH (ty:TeszTipus {id: $tt}) MERGE (t)-[:TIPUSA]->(ty) "
            "WITH t MATCH (s:SzerElem {id: $p.id}) "
            "MERGE (t)-[r:IRANYUL]->(s) SET r.role = $p.role, r.kozeg_id = $p.kozeg, r.helyzet_id = $p.helyzet",
            id=tesz_id, props=props, tt=tesz_type, p=primary)
        for kid, prim in kozeg:
            self.run("MATCH (t:TeszNivalo {id: $id}), (k:Kozeg {id: $k}) "
                     "MERGE (t)-[r:KOZEGBEN_ZAJLIK]->(k) SET r.primary = $p", id=tesz_id, k=kid, p=prim)
        for hid, prim in helyzet:
            self.run("MATCH (t:TeszNivalo {id: $id}), (h:Helyzet {id: $h}) "
                     "MERGE (t)-[r:HELYZETBEN_VEGZENDO]->(h) SET r.primary = $p", id=tesz_id, h=hid, p=prim)
        for s in secondary:
            self.run("MATCH (t:TeszNivalo {id: $id}), (s:SzerElem {id: $s.id}) "
                     "MERGE (t)-[r:HASZNAL]->(s) SET r.role = $s.role, r.kozeg_id = $s.kozeg, "
                     "r.helyzet_id = $s.helyzet", id=tesz_id, s=s)
        if variant_of:
            self.run("MATCH (a:TeszNivalo {id: $a}), (b:TeszNivalo {id: $b}) MERGE (a)-[:VARIANS_OF]->(b)",
                     a=tesz_id, b=variant_of)

    def link_job_tesz(self, job_id: str, tesz_id: str, props: dict) -> None:
        self.run("MATCH (m:MunkaKor {id: $j}), (t:TeszNivalo {id: $t}) "
                 "MERGE (m)-[r:TARTALMAZ_TESZT]->(t) SET r += $props", j=job_id, t=tesz_id, props=props)

    def clear_job_links(self, job_id: str) -> None:
        """Újrafeldolgozás előtt: a munkakör régi TESZ-kapcsolatai törlődnek (a TESZ-ek maradnak)."""
        self.run("MATCH (:MunkaKor {id: $j})-[r:TARTALMAZ_TESZT]->() DELETE r", j=job_id)

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
        for rel in ["TIPUSA", "IRANYUL", "HASZNAL", "KOZEGBEN_ZAJLIK", "HELYZETBEN_VEGZENDO", "FAJTAJA"]:
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
    def tesz_without_rubric(self, limit: int = 1000) -> list[dict]:
        """Kanonikus TESZ-ek rubrika nélkül, a P5-höz szükséges összefoglalóval."""
        return self.run(
            "MATCH (t:TeszNivalo) WHERE t.rubric_hu IS NULL OR size(t.rubric_hu) = 0 "
            "OPTIONAL MATCH (t)-[:TIPUSA]->(ty) OPTIONAL MATCH (t)-[r:IRANYUL]->(s:SzerElem) "
            "OPTIONAL MATCH (m:MunkaKor)-[:TARTALMAZ_TESZT]->(t) "
            "WITH t, ty, r, s, collect(DISTINCT m.label_hu)[..3] AS jobs "
            "RETURN t.id AS id, t.label_hu AS label_hu, t.outcome_hu AS outcome_hu, ty.id AS tesz_type, "
            "s.label_hu AS primary_szer, r.kozeg_id AS kozeg, r.helyzet_id AS helyzet, jobs "
            "ORDER BY t.id LIMIT $limit", limit=limit)

    def set_certificate(self, tesz_id: str, attestation: str, rubric: list[str], prompt_version: str) -> None:
        self.run("MATCH (t:TeszNivalo {id: $id}) SET t.attestation_hu = $a, t.rubric_hu = $r, "
                 "t.cert_prompt_version = $pv", id=tesz_id, a=attestation, r=rubric, pv=prompt_version)

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
            "MATCH (n) WHERE n:MunkaKor OR n:TeszNivalo OR n:SzerElem OR n:Keszseg "
            "RETURN labels(n)[0] AS tipus, count(*) AS db ORDER BY tipus")
        out["tesz_tipus"] = self.run(
            "MATCH (:MunkaKor)-[:TARTALMAZ_TESZT]->(t)-[:TIPUSA]->(ty) "
            "RETURN ty.id AS tipus, count(*) AS db ORDER BY db DESC")
        out["helyzet"] = self.run(
            "MATCH (:MunkaKor)-[:TARTALMAZ_TESZT]->(t)-[r:HELYZETBEN_VEGZENDO {primary: true}]->(h) "
            "RETURN h.id AS helyzet, count(*) AS db ORDER BY db DESC")
        out["ujrahasznositas"] = self.run(
            "MATCH (t:TeszNivalo)<-[:TARTALMAZ_TESZT]-(m) WITH t, count(m) AS mk "
            "RETURN mk AS munkakorok_szama, count(t) AS tesz_db ORDER BY mk")
        return out

    def anchors(self, min_tesz: int = 5, min_jobs: int = 3) -> list[dict]:
        """Az 1. kapu (gyakoriság) feletti SZÍN-horgonyok a P4a-hoz."""
        return self.run(
            "MATCH (m:MunkaKor)-[:TARTALMAZ_TESZT]->(t:TeszNivalo)-[r:IRANYUL]->(:SzerElem) "
            "WITH r.kozeg_id AS kozeg, r.helyzet_id AS helyzet, count(DISTINCT t) AS tesz_db, "
            "count(DISTINCT m) AS munkakor_db WHERE tesz_db >= $mt AND munkakor_db >= $mj "
            "RETURN kozeg, helyzet, tesz_db, munkakor_db ORDER BY tesz_db DESC", mt=min_tesz, mj=min_jobs)
