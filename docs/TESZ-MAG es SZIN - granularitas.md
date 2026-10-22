# TESZ-nivaló granularitás: MAG és SZÍN

*Javaslat a gaphopper-tesz modellhez, 2026-10-22. A kódot nem módosítja, csak javasol.*

## 1. A gond röviden

Most a TESZ-nivaló egyetlen szinten él, és a HELY-zet a **kilétébe** van égetve:

- `merge.py`: `stable_id("gh_tesz", label_hu, tesz_type, primary_szer_id, helyzet)`, a KÖZ-eg viszont **nincs** benne;
- a P0 T4-próbája: „rutin és kritikus változat két TESZ”;
- a P3 `VARIANT` döntése és a `VARIANS_OF` él csak **utólag**, két TESZ között köt össze, nincs közös pontjuk.

Ezért ugyanaz a tennivaló hol egy TESZ-be olvad, hol HELY-zetenként szétesik, és nincs hely, ahová a „mélyebb” változat felfűzhető. A gráfban nem látszik, hogy a *rutin gyógyszerbeadás* és a *sürgősségi gyógyszerbeadás* ugyanannak a tennivalónak két fokozata. Pedig a Gaphopper lényege éppen ez a kis rés: kevés tanulással át lehet ugrani.

## 2. A javaslat: két szint, MAG és SZÍN

A TESZ-nivalót két rétegre bontjuk. A ⟨KÖZ-eg × HELY-zet⟩ párnak a P4a-ban már van neve: **SZÍN**. Ezt emeljük a TESZ szintjére is, így a TESZ-t a SZÍN szó szerint „megSZÍNezi”.

| Szint | Gyök | Mi határozza meg | Mi nem | Szerep |
|---|---|---|---|---|
| **TESZ-KÖR** | KÖR | a munkakör feladatköre | – | rendező szint (marad) |
| **TESZ-MAG** (új) | MAG | TESZ-típus (ige) + meghatározó SZER-ELEM + ERED-mény fajtája | KÖZ-eg, HELY-zet | a tennivaló „magja”, munkakör- és SZÍN-semleges |
| **TESZ-nivaló** | TESZ | MAG × SZÍN ⟨KÖZ-eg, HELY-zet⟩ | – | **a mikrotanúsítvány egysége** (marad) |
| **TESZ-LÉPÉS** | LÉP | – | – | a rubrikában él (marad) |

A TESZ-nivaló tehát egy **MAG SZÍN-ezett változata**. A MAG-nak van egy **ALAP-SZÍN-e**, a leggyakoribb, belépő változat, és erre épülnek a **MÉLY-ítő** és **ÁT-vivő** változatok.

### 2.1 A SZÍN három iránya

A `vocab.yaml` HELY-zet-tengelyei már előkészítették ezt:

1. **MÉLY-ítés, a KISZ tengelyen** (sorrendes). Egy MAG nehezebb kiszámíthatósági fokon:
   `HELY_RUTIN (1) → HELY_VALTOZO (2) → HELY_PROBLEMA (3) → HELY_KRITIKUS (4)`.
   Ez a valódi „mélység”: a mélyebb fokozat az alacsonyabbra épül.
2. **SZEREP-váltás, a SZEREP tengelyen** (nem sorrendes): DÖNTÉSI, ALKOTÓ, EGYÜTTMŰKÖDÉSI, SZOLGÁLTATÁSI, IRÁNYÍTÁSI. Ez oldalirányú mélyítés: ugyanaz a mag más felelősségi szereppel.
3. **ÁT-vitel, a KÖZ-eg mentén**: ugyanaz a MAG más közvetítő térben, például személyes és telefonos betegtájékoztatás, vagy papíralapú és elektronikus dokumentáció.

> Javaslat: a `HELY_TANULASI` jelentése kettős („elsajátítás *vagy* tanítás”). A KISZ tengelyen legyen 0. fok (betanulás), a **tanítás** pedig kerüljön a SZEREP tengelyre, akár új `HELY_TANITO` kóddal. E nélkül a mélyítési sorrend nem egyértelmű.

### 2.2 Mitől lesz egy SZÍN külön TESZ-nivaló? (a granularitás kulcsa)

Nem minden SZÍN érdemel külön mikrotanúsítványt, különben a készlet szétrobban. Ezt három próba dönti el. Ezek kiegészítik a T1–T6-ot, nem helyettesítik.

- **M1 – SZÍN-csere-próba (MAG-kilét):** Cseréld ki a KÖZ-eget vagy a HELY-zetet. Ha az ige, a meghatározó SZER-ELEM és az ERED-mény fajtája marad, akkor **ugyanaz a MAG**. Ha bármelyik változik, akkor **más MAG**.
  *Példa: a vitális paraméterek mérése (OBSERVE_MEASURE, ERED-mény: mért érték) és az állapotromlás felismerése (DECIDE, ERED-mény: riasztási döntés) két MAG, nem egy MAG két SZÍN-e.*
- **M2 – Rubrika-különbség-próba (külön változat-e):** A SZÍN akkor ad külön TESZ-nivalót, ha legalább **egy új vagy szigorúbb rubrikakritériumot** hoz az ALAP-SZÍN-hez képest, például időkorlátot, eltérő hibatűrést vagy más címzettet. Ha nem hoz ilyet, a SZÍN csak a MAG kísérő SZÍN-e, és nem lesz belőle külön TESZ.
- **M3 – Előfordulás-próba:** Változat csak akkor kerül a munkakörbe, ha ott **rendszeresen előfordul**. A lehetséges, de itt nem jellemző SZÍN-ek *mélyítési jelöltek* lesznek (lásd 3.1), nem TESZ-nivalók.

### 2.3 Címkeszabály

- **A MAG címkéje SZÍN-semleges:** „Gyógyszer beadása orvosi rendelés szerint”. Nincs benne „sürgős”, „digitális”, „telefonos”.
- **A TESZ-nivaló címkéje = MAG + SZÍN-jelző:** „Gyógyszer beadása orvosi rendelés szerint *sürgősségi helyzetben*”.
  Az ALAP-SZÍN-nél a jelző elmaradhat.

Ez gépileg ellenőrizhető (R14 lent), és a P3-egyesítést is egyszerűsíti: a MAG-címke stabilabb, mint a változatoké.

## 3. P1 – felbontás: mi változik

### 3.1 Munkamenet (a `p1_felbontas.md` 2–6. lépése helyett)

```
2. TESZ-MAGOK: feladatkörönként 2-4, összesen 10-18 MAG. A MAG = TESZ-típus +
   meghatározó SZER-ELEM + ERED-mény fajtája; KÖZ-eg- és HELY-zet-semleges, a
   címkéje SZÍN-jelzőt nem tartalmaz.
3. SZÍN-EZÉS: minden MAG-hoz add meg az ALAP-SZÍN-t ⟨KÖZ-eg, HELY-zet⟩: a munkakörben
   leggyakoribb változatot. További változatot (TESZ-nivalót) csak akkor adj, ha
   (M2) legalább egy új vagy szigorúbb értékelési kritériumot hoz, és (M3) ebben a
   munkakörben rendszeresen előfordul. Egy MAG-on legfeljebb 3 változat.
   A változat szin_kulonbseg_hu mezőjében egy mondatban írd le, mitől nehezebb vagy
   mitől más, mint az ALAP-SZÍN.
4. MÉLYÍTÉSI JELÖLTEK: ha egy CORE MAG-nak van a szakmában ismert nehezebb SZÍN-e,
   amely ebben a munkakörben NEM jellemző, írd a melyites_jeloltek listába (nem TESZ!).
5. ÖNELLENŐRZÉS: a T1-T6 a TESZ-nivalókra (változatokra), az M1 a MAG-okra.
   Ha két MAG csak KÖZ-egben vagy HELY-zetben tér el: egy MAG két változata.
   Ha egy változat ERED-ménye vagy meghatározó SZER-ELEM-e eltér: külön MAG.
```

A darabszám-elvárás így válik ketté. Most `tesz_min 12 / tesz_max 25` a változatokra vonatkozik. Javaslat: **MAG 10–18**, **TESZ-nivaló 12–25** (mint most), és a változatok aránya a MAG-okhoz legfeljebb ~1,6. A küszöbök kalibrálandók.

### 3.2 Kimeneti séma (P1)

```json
{
  "tesz_magok": [
    {"mag_id": "M01", "kor_id": "K2",
     "label_hu": "Gyógyszer beadása orvosi rendelés szerint",
     "label_en": "Administer medication as prescribed",
     "outcome_hu": "A rendelt gyógyszer helyes dózisban, úton és időben bejut.",
     "tesz_type": "EXECUTE", "tesz_type_runner_up": "COMPLY",
     "primary_szer": {"label_hu": "Gyógyszerelési rendelés", "kind": "RULE",
                      "role": "GUIDED_BY", "definition_hu": "..."},
     "relevance": "CORE",
     "melyites_jeloltek": [{"kozeg": "KOZ_FIZIKAI", "helyzet": "HELY_KRITIKUS",
                            "indok_hu": "Újraélesztés alatti gyógyszerelés, osztályos ápolónál ritka."}]}
  ],
  "tesz": [
    {"cand_id": "C05", "mag_id": "M01", "alap_szin": true,
     "label_hu": "Gyógyszer beadása orvosi rendelés szerint",
     "kozeg": "KOZ_FIZIKAI", "helyzet": "HELY_RUTIN",
     "secondary_szer": [...], "frequency": "DAILY", "expected_eqf": 4,
     "rationale_hu": "...", "confidence": 0.8},
    {"cand_id": "C05b", "mag_id": "M01", "alap_szin": false,
     "label_hu": "Gyógyszer beadása orvosi rendelés szerint állapotromlás idején",
     "kozeg": "KOZ_FIZIKAI", "helyzet": "HELY_VALTOZO",
     "szin_kulonbseg_hu": "Változó paraméterek mellett ismételt dózisellenőrzés és orvosi visszacsatolás.",
     "frequency": "WEEKLY", "expected_eqf": 5, "confidence": 0.7}
  ]
}
```

Mi kerül hová:

- **MAG-ra:** `tesz_type`, `tesz_type_runner_up`, `primary_szer`, `outcome_hu` (közös ERED-mény), `relevance`, `kor_id`. Ezeket a változat örökli, és nem írja újra, ami tokent is spórol.
- **TESZ-nivalóra:** `kozeg`, `kozeg_secondary`, `helyzet`, `helyzet_secondary`, `secondary_szer` (közegfüggő eszközök!), `frequency`, `expected_eqf`, `szin_kulonbseg_hu`, `alap_szin`.
- A `kozeg_secondary` és `helyzet_secondary` jelentése szűkül: a *kísérő* SZÍN az, ami nem éri el az M2 küszöbét. Ha eléri, akkor külön változat lesz belőle.

Pydantic oldalon ez egy új `TeszMag` modell, a `Tesz`-ben pedig `mag_id`, `alap_szin` és `szin_kulonbseg_hu`. Átmenetileg a `Tesz` megtarthatja a `tesz_type` és `primary_szer` mezőt opcionálisként, hogy a régi futások betölthetők maradjanak.

## 4. P2 – bírálat: új próbák és műveletek

### 4.1 Determinisztikus szabályok (`rules.py`)

| Kód | Szabály | Jelzés |
|---|---|---|
| R1 | MAG 10–18, TESZ-nivaló 12–25, CORE MAG 8–15 | soft |
| R6 | a természetes pár aránya most az **ALAP-SZÍN-ekre** számolandó, a változatokra nem | soft |
| R7 | a kritikus arány a **változatokra** számolandó | soft |
| R8 | az azonos ⟨típus, SZER-ELEM, KÖZ-eg, HELY-zet⟩ négyes marad | soft |
| **R11** | két **külön MAG** azonos ⟨típus, meghatározó SZER-ELEM⟩ párral → „egy MAG két SZÍN-e?” | soft |
| **R12** | a MAG-ra jutó változatok száma > 3 | soft |
| **R13** | nem ALAP-SZÍN változat üres vagy rövid `szin_kulonbseg_hu` mezővel (M2 sérül) | soft |
| **R14** | a MAG `label_hu` HELY-zet- vagy KÖZ-eg-jelzőt tartalmaz (szólista a vocabból: „sürgős”, „kritikus”, „digitális”, „online”, „telefonos” …) | soft |
| **R15** | minden MAG-nak pontosan egy `alap_szin: true` változata van | séma |
| **R16** | csak KÖZ-egben eltérő változat (a HELY-zet azonos), és a `secondary_szer` sem változik → valószínű kísérő KÖZ-eg | soft |

Az R11 a mostani hiba fő jelzője: ma ez R8 alatt sem jelez, mert ott a HELY-zet eltér.

### 4.2 A P2 promptba

A „MINDEN TESZ-RE” rész kiegészítése:

```
- MAG-próba (M1): a MAG KÖZ-eg- és HELY-zet-semleges-e? Ha két MAG csak SZÍN-ben
  tér el, vond egy MAG alá (REMAG). Ha egy MAG változatainak ERED-ménye lényegében
  eltér, bontsd külön MAG-okra (SPLIT).
- Változat-próba (M2): ad-e a változat új vagy szigorúbb értékelési kritériumot?
  Ha nem, olvaszd a MAG ALAP-SZÍN-ébe (FOLD).
```

A „KÉSZLETRE” rész kiegészítése:

```
Q4 Mélyíthetőség: van-e CORE MAG, amelynek ebben a munkakörben rendszeres
   nehezebb SZÍN-e hiányzik? Ha igen, ADD-dal add hozzá változatként (ugyanazzal a
   mag_id-val); ha csak a szakmában ismert, a munkakörben nem jellemző, a
   melyites_jeloltek közé.
```

Új műveletek a `changes` listában:

```json
{"op": "REMAG", "cand_ids": ["C05", "C17"], "mag_id": "M01", "reason_hu": "..."}
{"op": "FOLD",  "cand_id": "C05b", "into": "C05", "as": "kozeg_secondary|helyzet_secondary", "reason_hu": "..."}
```

A FIX a `mag_id`-t, a SZÍN-t és a `szin_kulonbseg_hu`-t is javíthatja. A `primary_szer` és a `tesz_type` javítása mostantól **MAG-szintű** FIX (`"mag_id": "M01", "set": {...}`), és minden változatra érvényes.

## 5. Neo4j séma

### 5.1 Csomópontok és élek

```
(:TeszMag {id, label_hu, label_en, outcome_hu, aliases, alias_keys, alias_text,
           embedding, status, rubric_alap_hu, prompt_version})
  -[:TIPUSA]->(:TeszTipus)
  -[:IRANYUL {role}]->(:SzerElem)            // a meghatározó SZER-ELEM a MAG-on ül

(:TeszNivalo {id, label_hu, label_en, szin_kulonbseg_hu, rubric_szin_hu,
              attestation_hu, alap_szin, ...})
  -[:MAGJA]->(:TeszMag)                      // új: minden TESZ-nek pontosan egy MAG-ja
  -[:SZINBEN]->(:Szin)                       // új: az elsődleges SZÍN
  -[:KOZEGBEN_ZAJLIK {primary}]->(:Kozeg)    // marad (kísérő közegekre is)
  -[:HELYZETBEN_VEGZENDO {primary}]->(:Helyzet)
  -[:HASZNAL {role}]->(:SzerElem)            // kísérő SZER-ELEM: a változaton (közegfüggő)

(:Szin {id: "KOZ_FIZIKAI|HELY_KRITIKUS", label_hu})   // 5×10 = 50 rögzített csomópont
  -[:KOZEGE]->(:Kozeg)
  -[:HELYZETE]->(:Helyzet)

(:Helyzet {..., tengely, fok})               // fok: KISZ tengelyen 0..4, SZEREP-en null
(:Helyzet)-[:MELYEBB]->(:Helyzet)            // RUTIN→VALTOZO→PROBLEMA→KRITIKUS

(:MunkaKor)-[:TARTALMAZ_TESZT {relevance, frequency, expected_eqf}]->(:TeszNivalo)   // marad
(:TeszMag)-[:MELYITESI_JELOLT {indok_hu, forras_munkakor}]->(:Szin)                 // a P1 4. lépéséből
```

Miért kell külön `Szin` csomópont? A P4a KÉSZ-SÉG-ASPEKTUSa már ⟨KÖZ-eg × HELY-zet⟩, és a `narrower_anchor` is „SZIN:…” alakú. Ha a SZÍN csomópont, akkor a TESZ-nivaló és a KÉSZ-ség-aspektus **ugyanahhoz a ponthoz** köt: `(:Keszseg)-[:ASPEKTUSA]->(:Szin)<-[:SZINBEN]-(:TeszNivalo)`. Így a TESZ mélyülése és a KÉSZ-ség mélyülése egy gráfúton látszik. A KÉP-esség, a TUD-ás és a KÉSZ-ség ugyanazon a SZÍN-en találkozik a TESZ-szel, és a TETT-re-KÉSZ-ség ott mérhető, ahol a SZÍN a legnehezebb.

A `MELYITI` és `ATVISZI` éleket **nem tárolnám**, hanem lekérdezéssel számolnám: a MAG és a `Helyzet.fok` együtt egyértelműen megadja őket.

### 5.2 Kilét (stable_id)

```
mag_id   = stable_id("gh_mag",  tesz_type, primary_szer_id, norm(label_hu))
tesz_id  = stable_id("gh_tesz", mag_id, kozeg, helyzet)
```

A KÖZ-eg így bekerül a TESZ kilétébe (ma kimarad), a HELY-zet pedig a MAG-ból kikerül.

### 5.3 P3-egyesítés két lépcsőben

1. **MAG-egyezés** (HELY-zet nélkül): azonos típus, azonos vagy alias SZER-ELEM, lényegében azonos ERED-mény. A gyors úton ez a mai feltétel `helyzet` nélkül (`merge.py` `plan_tesz`), és az embeddingre a MAG-címke és az `outcome_hu` megy.
2. **SZÍN-egyezés** a talált MAG-on belül, **determinisztikusan**: ha ugyanaz a ⟨KÖZ-eg, HELY-zet⟩, akkor SAME, egyébként új változat ugyanazon MAG alatt.

A P3 LLM-döntésből így a `VARIANT` kikerülhet, mert a kód dönti el. Az LLM csak a MAG kérdésében dönt (SAME / NARROWER / BROADER / NEW), és az a könnyebbik kérdés.

### 5.4 Hasznos lekérdezések

Egy MAG mélyítési létrája:
```cypher
MATCH (g:TeszMag {id:$mag})<-[:MAGJA]-(t:TeszNivalo)-[:HELYZETBEN_VEGZENDO {primary:true}]->(h:Helyzet)
RETURN t.label_hu, h.id, h.fok ORDER BY h.tengely, h.fok
```

Gap-ugrás: mit tud már a személy (TESZ-ek), és melyik változat van egy lépésre:
```cypher
MATCH (have:TeszNivalo)-[:MAGJA]->(g)<-[:MAGJA]-(next:TeszNivalo),
      (have)-[:HELYZETBEN_VEGZENDO {primary:true}]->(h1),
      (next)-[:HELYZETBEN_VEGZENDO {primary:true}]->(h2)
WHERE have.id IN $person_tesz AND h1.tengely='KISZ' AND h2.fok = h1.fok + 1
RETURN g.label_hu, have.label_hu, next.label_hu, next.szin_kulonbseg_hu
```

A P4a horgonyai a MAG-ra és a SZÍN-re (a mostani `anchors()` az `IRANYUL` él tulajdonságából olvas):
```cypher
MATCH (m:MunkaKor)-[:TARTALMAZ_TESZT]->(t)-[:SZINBEN]->(s:Szin)
WITH s, count(DISTINCT t) AS tesz_db, count(DISTINCT m) AS munkakor_db
WHERE tesz_db >= $mt AND munkakor_db >= $mj RETURN s.id, tesz_db, munkakor_db
```

### 5.5 Átállás a meglévő gráfon

```cypher
// 1. MAG minden (típus, meghatározó SZER-ELEM) csoportra
MATCH (t:TeszNivalo)-[:TIPUSA]->(ty), (t)-[:IRANYUL]->(s:SzerElem)
WITH ty, s, collect(t) AS ts
MERGE (g:TeszMag {id: 'gh_mag_' + ty.id + '_' + s.id})
  ON CREATE SET g.label_hu = ts[0].label_hu, g.outcome_hu = ts[0].outcome_hu, g.status = 'ATALLITOTT'
MERGE (g)-[:TIPUSA]->(ty) MERGE (g)-[:IRANYUL]->(s)
FOREACH (t IN ts | MERGE (t)-[:MAGJA]->(g));
// 2. A VARIANS_OF párok közös MAG alá (ha eltérő csoportba estek: szakértői sor)
// 3. Szin csomópontok és SZINBEN élek a primary KOZEGBEN/HELYZETBEN élekből
```

Az 1. lépés durva: két, ténylegesen eltérő ERED-ményű TESZ is egy MAG-ba eshet. Ezeket érdemes egy P3-szerű „MAG-bontás” körrel vagy szakértői sorral átnézni, mielőtt a `status` `ELFOGADOTT` lesz.

## 6. Mit nyerünk

- **Stabil, újrahasznosítható egység:** a MAG munkakörök között sokkal jobban egyesül, mint a SZÍN-ezett TESZ, így a `ujrahasznositas` statisztika értelmesebb lesz.
- **Halmozható mikrotanúsítvány:** MAG-rubrika (közös) + SZÍN-rubrika (1–3 kiegészítő kritérium). A mélyebb változat tanúsítványa a sekélyebbre épül, és a P5 is két részre bomlik (`rubric_alap_hu` a MAG-on, `rubric_szin_hu` a változaton).
- **Mérhető rés:** két munkakör távolsága MAG-szinten (hiányzó tennivaló) és SZÍN-szinten (meglévő tennivaló, nehezebb körülmény) külön számolható. Ez a Gaphopper ugrásának alapja.
- **A KÉSZ-ség-aspektushoz közvetlen kötés** a `Szin` csomóponton át.

## 7. Döntések (2026-10-22, CoLearnAI)

1. **Név: TESZ-MAG.**
2. **HELY_TANULASI kettéválik:** `HELY_TANULASI` = betanulás (KISZ tengely, 0. fok); új `HELY_TANITO` = tanítás (SZEREP tengely).
3. **Mélyítési jelölteket a P1 írja**, CORE MAG-onként legfeljebb 1.
4. **Küszöbök: MAG 10–18, MAG-onként legfeljebb 3 változat.** Kalibrálás 3–5 referencia-munkakörön (ápoló, egyetemi oktató, egy O*NET-tétel), új `prompt_version`-nel.
5. **Újrakezdés:** a meglévő adatbázisok törlődnek (előtte mentés), a gráf az új sémával épül újra; munkakörönként dry run lehetőség kell.
6. **Elsődleges szempont:** gyors futás és optimalizált tokenhasználat.

## 8. Kalibrálási eredmények (2026-10-22)

Referencia-munkakörök: ápoló (`onet:29-1141.00`, `data/sample_jobs.jsonl`), egyetemi oktató (`ad:3486c602e3`,
`data/jobs_dokumentumokbol.jsonl`), villanyszerelő (`onet:47-2111.00`, `data/job_100.jsonl`). Modellek változatlanul:
P1 `claude-sonnet-5-5` (effort low), P2 `gemini-3.6-flash`, P3 `shared-local`. P2 mód: auto (15% minta).

| Kör (prompt_version) | Munkakör | MAG (CORE) | TESZ-nivaló (nem ALAP) | Mélyítési jelölt | Jelzés a P1 után | P2 |
|---|---|---|---|---|---|---|
| 1. dry run (`…-1da03af5`) | ápoló | 15 (11) | 19 (4) | 2 | R10 | lefutott |
| | oktató | 13 (9) | 15 (2) | 0 | R4, R8, R9, R10 | lefutott, R9 + R10 maradt |
| | villanyszerelő | 15 (9) | 17 (2) | 0 | R5, R9 | lefutott |
| 2. dry run (`…-59aef6aa`) | ápoló | 13 (10) | 21 (8) | 2 | – | kimaradt |
| | oktató | 11 (8) | 14 (3) | 1 | R1 (7 CORE), R4, R8 | lefutott, R8 maradt |
| | villanyszerelő | 14 (9) | 17 (3) | 0 | R5 (33%) | lefutott |
| Valódi futás (`…-67e499ff`) | ápoló | 12 | 21 (9) | 2 | – | kimaradt |
| | oktató | 11 | 13 (2) | 0 | – | kimaradt |
| | villanyszerelő | 14 | 16 (2) | 0 | – | kimaradt |

Az M1–M3 próbák a végső futásban: R11 (MAG-iker), R12 (>3 változat), R13 (üres SZÍN-különbség) és R14
(SZÍN-jelző a MAG-címkében) egyik munkakörnél sem jelzett. A változat/MAG arány: ápoló 1,75, oktató 1,18,
villanyszerelő 1,14. Az ápolónál a ~1,6-os irányszám fölött van, de MAG-onként legfeljebb 3 változattal, és
mindegyik változat konkrét SZÍN-különbséget nevez meg. A ritkán ismétlődő szakmákban az M3 szigorúan érvényesül.

A két kör változtatásai:
1. kör: `mag_label_words_min: 3`, mert a SZÍN-semleges MAG-címke rövidebb. `mag_per_kor_min: 1` (R9 csak MAG
   nélküli feladatkörre jelez), a P1-ben „feladatkörönként 1-4 MAG”. A tanításnál `HELY_TANITO`, mert az
   oktatónál az 1. körben a tanító helyzet nem jelent meg. Confidence 0,6 alatt csak valódi bizonytalanságnál.
2. kör: R5 `execute_max_share` 0,30 → 0,40 MAG-szinten (a villanyszerelő 33–36%-a a szakmában természetes). Egy
   MAG változatainak SZÍN-e különböző; a nyelv, a címzett köre vagy a téma nem SZÍN. Az azonos SZÍN-ű változat
   (ugyanaz a ⟨MAG, KÖZ-eg, HELY-zet⟩ kilét) determinisztikusan összeolvad, figyelmeztetéssel.

Az egyesítésben a helyi P3-modell az első valódi futásban négy villanyszerelő-MAG-ot SAME-nek ítélt más
SZER-ELEM-ű ápolói és oktatói MAG-okkal (pl. ápolási dokumentáció ≈ szerelési nyilvántartás). Erre M1-őr került
a kódba: az LLM SAME-döntése csak azonos TESZ-típusnál és meghatározó SZER-ELEM-nél áll meg, különben új MAG
jön létre, és szakértői átnézésre kerül. A három munkakör ezután újra beíródott.

Token- és költségmérés (P1 + P2, ugyanaz a 3 munkakör, ugyanazok a modellek; régi kód: `main` ág):

| | Hívás (sikeres) | Bemenet | Kimenet | Becsült költség | Idő |
|---|---|---|---|---|---|
| Előtte (régi prompt, P2 mindig) | 7 (5) | 53 164 | 56 363 | 0,566 USD | 323 mp |
| Utána (valódi futás) | 3 (3) | 18 815 | 20 973 | 0,232 USD | 127 mp |

- A régi P1 a három munkakörből kettőnél sémahibát adott (`frequency`, `relevance`, `kind`), és újra kellett
  hívni; a villanyszerelőnél két kísérlet után is elbukott. Az új P1 mindháromnál elsőre érvényes volt. Ez
  egyrészt az `alias_ertek` táblának köszönhető, másrészt annak, hogy a P0 megadja a zárt értéklistát.
- Munkakörönként a P1 kimenete sikeres hívásonként 8,2–9,4 ezer tokenről 6,1–8,1 ezerre csökkent: a változat
  nem ismétli a MAG mezőit, és nincs indoklás.
- A P0 rendszerprompt 3122 tokenről 2863-ra csökkent, és gyorsítótárazva megy (Anthropic `cache_control`).
- A P2 bemenete hívásonként 9,5–10,7 ezerről 6,7–7,5 ezer tokenre csökkent (MAG-onként csoportosított, karcsú
  nézet). A végső futásban a P2 egyik munkakörnél sem kellett.
- A gráfírás munkakörönként 0,1–0,5 mp egy tranzakcióban (UNWIND). Az egyesítés ideje a helyi P3-modellen múlik
  (30–70 mp, ha van kétes eset).

Nyitott: a P5 (MAG- és SZÍN-rubrika) a mérés idején nem futott le, mert az Anthropic-fiók egyenlege elfogyott.
