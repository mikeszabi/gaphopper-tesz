"""Read-only Neo4j access. No writes to the default graph or local pipeline state."""
import json
import os
from pathlib import Path
from neo4j import GraphDatabase, READ_ACCESS, unit_of_work

CATALOG = Path(__file__).resolve().parents[1] / "data" / "jobs.json"


class Repository:
    def __init__(self):
        self.driver = GraphDatabase.driver(
            os.getenv("NEO4J_URI", "bolt://localhost:7687"),
            auth=(os.getenv("NEO4J_USERNAME") or os.getenv("NEO4J_USER", "neo4j"),
                  os.getenv("NEO4J_PASSWORD", "")),
            max_connection_pool_size=10, connection_timeout=10, max_transaction_retry_time=5)
        self.database = os.getenv("NEO4J_DATABASE", "neo4j")
        self.catalog = json.loads(CATALOG.read_text(encoding="utf-8")) if CATALOG.exists() else {}

    def close(self):
        self.driver.close()

    def query(self, cypher, **params):
        with self.driver.session(database=self.database, default_access_mode=READ_ACCESS) as session:
            @unit_of_work(timeout=25)
            def read(tx):
                return [r.data() for r in tx.run(cypher, **params)]
            return session.execute_read(read)

    def ready(self):
        self.query("RETURN 1 AS ok")

    def competencies(self):
        return self.query("MATCH (k:Keszseg) RETURN k.id AS id, coalesce(k.label_hu,k.label_en,k.id) AS label, "
                          "coalesce(k.leiras_hu,'') AS description ORDER BY k.id")

    def jobs(self):
        return self.query("MATCH (m:MunkaKor) RETURN m.id AS id, coalesce(m.label_hu,m.label_en,m.id) AS title, "
                          "m.label_en AS title_en, coalesce(m.aliases,[]) AS aliases ORDER BY title")

    def profile(self, job):
        rows = self.query(
            "MATCH (m:MunkaKor {id:$id})-[:TARTALMAZ_TESZT]->(s:TeszSzin)-[:SZINEZI]->(t:TeszNivalo) "
            "MATCH (t)-[r:IGENYEL]->(k:Keszseg) "
            "WHERE r.munkakorok IS NULL OR $id IN r.munkakorok "
            "OPTIONAL MATCH (t)-[:TIPUSA]->(ty:TeszTipus) "
            "OPTIONAL MATCH (t)-[:IRANYUL]->(se:SzerElem) "
            "OPTIONAL MATCH (s)-[:KOZEGBEN_ZAJLIK {primary:true}]->(ko:Kozeg) "
            "OPTIONAL MATCH (s)-[:HELYZETBEN_VEGZENDO {primary:true}]->(he:Helyzet) "
            "RETURN DISTINCT t.id AS tesz_id, t.label_hu AS tesz_nivalo, s.id AS szin_id, s.szin AS szin, "
            "ty.id AS tesz_type, se.label_hu AS szer_elem, se.kind AS szer_elem_kind, ko.id AS kozeg, he.id AS helyzet, "
            "k.id AS competency_id, coalesce(k.label_hu,k.label_en,k.id) AS competency_label", id=job["id"])
        groups = {}
        skills = {}
        for row in rows:
            key = row["szin_id"]
            t = groups.setdefault(key, {"id": key, "tesz_id": row["tesz_id"],
                                       "tesz_nivalo": row["tesz_nivalo"], "szin": row["szin"],
                                       **{k:row[k] for k in ("tesz_type", "szer_elem", "szer_elem_kind", "kozeg", "helyzet")},
                                       "competency_ids": []})
            if row["competency_id"] not in t["competency_ids"]:
                t["competency_ids"].append(row["competency_id"])
            k = skills.setdefault(row["competency_id"], {"id": row["competency_id"],
                                  "label": row["competency_label"], "evidence": []})
            if key not in k["evidence"]:
                k["evidence"].append(key)
        source = self.catalog.get(job["id"])
        return {"triplets": list(groups.values()), "competencies": list(skills.values()),
                "tasks": source.get("tasks", []) if source else [], "baseline_available": bool(source and source.get("tasks"))}

    def candidates(self):
        # All processed roles are ranked, rather than an arbitrary first N roles.
        return self.query(
            "MATCH (m:MunkaKor)-[:TARTALMAZ_TESZT]->(:TeszSzin)-[:SZINEZI]->(t:TeszNivalo) "
            "MATCH (t)-[r:IGENYEL]->(k:Keszseg) "
            "WHERE r.munkakorok IS NULL OR m.id IN r.munkakorok "
            "WITH m, collect(DISTINCT k.id) AS competency_ids "
            "OPTIONAL MATCH (m)-[:BESOROLVA]->(:Besorolas)-[:RESZE*0..5]->(b:Besorolas) "
            "WITH m, competency_ids, collect(DISTINCT b.id) AS taxonomy "
            "OPTIONAL MATCH (m)-[:BESOROLVA]->(:Besorolas)-[:RESZE*0..5]->(:Besorolas)-[:AGAZATBAN]->(a:Agazat) "
            "RETURN m.id AS id, coalesce(m.label_hu,m.label_en,m.id) AS title, competency_ids, taxonomy, "
            "collect(DISTINCT a.id) AS sector_ids, collect(DISTINCT coalesce(a.label_hu,a.label_en,a.id)) AS sectors")
