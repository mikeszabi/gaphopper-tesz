# Gaphopper TESZ – futtatási segédlet

Frissítve: 2026. október 22. (TESZ-nivaló × SZÍN modell, próbafutás)

Beállítási referencia: [README.md](../README.md); fejlesztési szabályok: [CLAUDE.md](../CLAUDE.md).

**Fogalmak röviden.** A P1 általános TESZ-nivalókat ad (munkaminta × SZER-ELEM-fajta + ERED-mény, legfeljebb 6
szó, szakterület-, célcsoport-, KÖZ-eg- és HELY-zet-semleges; 2–5 KÉSZ-ség-törzselem; a munkaköri specifikum a
`kontextus_hu`-ban). Ha a TESZ-törzsben van illeszkedő TESZ-nivaló, a P1 azt választja (`nivalo_ref`), és mindegyikhez SZÍN-ezett változatokat, a TESZ-SZÍN-eket
(SZÍN = ⟨KÖZ-eg, HELY-zet⟩). Minden TESZ-nivalónak pontosan egy ALAP-SZÍN-e van (a leggyakoribb változat); további
változat csak akkor, ha új vagy szigorúbb értékelési kritériumot hoz (M2) és a munkakörben rendszeres (M3). A
szakmában ismert, itt nem jellemző nehezebb SZÍN mélyítési jelölt (CORE TESZ-nivalónként legfeljebb 1). A
mikrotanúsítvány egysége a TESZ-SZÍN; a rubrikája TESZ-nivaló-rubrika + SZÍN-rubrika. Részletek: README 5–5d.

A Top 100 munkakör feldolgozása hat lépés: előkészítés, próbafutás 3 munkakörön, tömeges futtatás, szakértői átnézés, KÉSZ-ségek, végül ellenőrzés. Minden parancs a `gaphopper-tesz` mappából indul, bekapcsolt virtuális környezettel.

## 1. Előfeltételek

A gráfba íráshoz neo4j, a programhoz Python-környezet kell. Az Ollama csak Ollama-alapú beágyazás vagy helyi LLM használatakor szükséges.

| Mi | Ellenőrzés | Elvárt eredmény |
| --- | --- | --- |
| neo4j 5.11+ | böngészőben `http://localhost:7474` | belépés a `.env`-ben megadott jelszóval |
| Ollama | `ollama list` | látszik a beágyazó modell, pl. `bge-m3` |
| Python 3.11+ | `python --version` a venv-ben | 3.11 vagy újabb |
| API-kulcs | `.env`: `ANTHROPIC_API_KEY`, ha a P2 Geminin fut, `GEMINI_API_KEY` is | nem üres |

Első alkalommal:

```bash
cd gaphopper-tesz
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

A `.env` fájlban ezek a sorok kellenek (a többi maradhat a `.env.example` szerint):

```bash
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=<a jelszavad>
ANTHROPIC_API_KEY=<kulcs>
# a bíráló (P2) más modellen fusson; kötegelt módhoz anthropic kell
GH_PROVIDER_P2=gemini
GEMINI_API_KEY=<kulcs>
# helyi beágyazás
GH_EMBED_PROVIDER=ollama
GH_EMBED_MODEL=bge-m3
OLLAMA_HOST=http://localhost:11434
```

Ha a P2-t is kötegben akarod futtatni (olcsóbb), állítsd `GH_PROVIDER_P2=anthropic` értékre; ilyenkor a bíráló ugyanabból a modellcsaládból jön, mint a felbontó, ezért kevésbé független.

## Modellbeállítás a futás előtt

```bash
python -m gaphopper ui       # http://localhost:8765
python -m gaphopper models   # ténylegesen érvényes beállítások
```

A **Modellek** oldalon frissítsd az Anthropic / Gemini / OpenAI-kompatibilis modelllistát,
válassz modellt az E0 és P1–P5 lépésekhez, majd ments. A `config/models.yaml` erősebb a `.env`-nél:
ha létezik felületi beállítás, a lenti `.env`-példa önmagában nem írja felül.
A P2-t Geminire a felületen is át kell állítani. OpenAI-kompatibilis szolgáltatóhoz külön URL és
kulcsváltozónév adható meg; a kulcs értéke a `.env`-be kerül. A külön **Kapcsolati próba**
gomb mentés nélkül ellenőrzi a címet és a modelllista elérését, szöveggenerálás nélkül.
Helyi generatív modellpanel nincs; az Ollama-beágyazás továbbra is használható.
A beágyazó modellnél a **Beágyazás ellenőrzése** dimenziót és magyar–angol hasonlóságot mutat.
A Futás oldal összesített token- és költségszámlálót mutat; az árakat a Modellek oldalon ellenőrizd.
Az ismeretlen ár nem nulla költség. Részletek és korlátok a README modellválasztási részében.

## 2. Egyszeri előkészítés

Ha rendelkezésre áll, a `data/job_100.jsonl` a táblázat 100 munkakörét tartalmazza rang szerint (1. Registered nurse … 100. Event planner), `onet:<kód>` azonosítóval.

```bash
python -m gaphopper embed check                 # dimenzió + magyar–angol próba
python -m gaphopper init                        # séma, szótárak, KÉSZ-ség-törzs, vektorindexek
python -m gaphopper import data/job_100.jsonl   # 100 rekord NYERS állapotba
python -m gaphopper stats                       # Munkakörök állapota: {'NYERS': 100}
```

Az `embed check` akkor jó, ha a „szoftverfejlesztő – software developer” érték jóval nagyobb, mint az „ápoló – software developer”. Ha nem, a modell nem többnyelvű: cseréld, mielőtt az `init` létrehozza a vektorindexeket.

Mi van egy rekordban:

| Mező | Forrás a táblázatban | Példa (1. sor) |
| --- | --- | --- |
| `title`, `alt_titles` | title, official\_title, bls\_title | Registered nurse / Registered Nurses |
| `codes` | code, bls\_code | onet 29-1141.00, soc 29-1141 |
| `field` | cluster + field | Health and care / Health and welfare |
| `description` | description | Assess patients and deliver coordinated nursing care. |
| `requirements` | skills | Clinical assessment; care planning; … |
| `conditions`, `entry_route` | condition, route | Shift work …; Degree / regulated |
| `evidence_hint`, `first_step`, `outlook`, `resilience` | Forrás lap | felbontási támpontok |
| `locale`, `source_lang` | — | hu-HU, en |

A táblázat kész TESZ- és triplet-lapjai szándékosan nincsenek a rekordban: a felbontás a leírásból indul, a meglévő tervvel utólag lehet összevetni. A leírás munkakörönként egyetlen mondat, és a `tasks` üres, ezért a P1 nagyrészt a szakma ismert gyakorlatára támaszkodik. Pontosabb eredményhez a 100 munkakör O\*NET-feladatlistáját érdemes `tasks` mezőként betölteni.

## 3. Próbafutás 3 munkakörön

Három eltérő területű munkakörrel (egészségügy, digitális, szakmunka) olcsón kiderül, jó lesz-e a 100-as futás.
Előbb **dry run**: a P1, a szükség szerinti P2 és az egyesítés P3-hívása lefut (tokenek, költség, idő látszik),
de sem a gráf, sem a `state.sqlite` nem változik; az eredmény a `runs/dryrun/<job>.json`-ba kerül.

```bash
python -m gaphopper run --dry-run --workers 3 --job onet:29-1141.00 --job onet:15-1252.00 --job onet:47-2111.00
```

A webes felületen ugyanez: Futás oldal → „Próbafutás (dry run)” kapcsoló → indítás; az eredmény a munkakör
részleteinél („Próbafutás eredménye”). Utána, ha a köztes eredményt menteni is akarod:

```bash
python -m gaphopper run --job onet:29-1141.00 --job onet:15-1252.00 --job onet:47-2111.00 --until p2
python -m gaphopper show --job onet:29-1141.00 | less
```

Az `--until p2` a felbontást és a bírálatot a `state.sqlite`-ba menti, a gráfba még nem ír. Nézd meg munkakörönként:

- [ ] 6–12 TESZ-nivaló (4–9 CORE), 8–18 TESZ-SZÍN, TESZ-nivalónként legfeljebb 3 változat, 3–6 TESZ-kör
- [ ] G1: a TESZ-nivaló-címke legfeljebb 6 szó, munkakör-, szakterület-, célcsoport- és SZÍN-semleges; a specifikum a `kontextus_hu`-ban
- [ ] G2: TESZ-nivalónként 2–5 legjellemzőbb `kesz_ids` a KÉSZ-ség-törzsből; G4: nincs két azonos típusú, nagy KÉSZ-ség-átfedésű TESZ-nivaló
- [ ] G3: a változatnak nincs saját címkéje; a SZÍN a KÖZ-eg/HELY-zet mezőben és a `szin_kulonbseg_hu`-ban
- [ ] lánc-próbafutásnál (`--chain`) a későbbi munkakörök a törzsből választanak (`[törzs]` jelölés)
- [ ] M1: két TESZ-nivaló nem csak KÖZ-egben vagy HELY-zetben tér el (különben egy TESZ-nivaló két változata)
- [ ] M2: minden nem ALAP-SZÍN változat `szin_kulonbseg_hu`-ja új vagy szigorúbb kritériumot nevez meg
- [ ] tanítás → `HELY_TANITO`; a `HELY_TANULASI` a személy saját betanulása
- [ ] a címkék munkakör-specifikusak, nem általános minták („dokumentálás”)
- [ ] minden TESZ-nél van egy meghatározó SZER-ELEM, egy domináns KÖZ-eg és egy elsődleges HELY-zet
- [ ] a magyar név régi gyökű, az angol a megfeleltetés; a `title_aliases` tartalmazza az `alt_titles` neveit
- [ ] a készség jellegű elemek a `skill_mentions` listában vannak, nem TESZ-ként
- [ ] a P2 javításai értelmesek, nem csak átfogalmazások

Ha jó, írd a gráfba, és nézd meg az egyesítést:

```bash
python -m gaphopper run --job onet:29-1141.00 --job onet:15-1252.00 --job onet:47-2111.00
python -m gaphopper stats
```

Ha a felbontás rossz, a `prompts/p1_felbontas.md` vagy a `config/vocab.yaml` módosítása után `--force`-szal futtasd újra ugyanezt a hármat. A promptváltozás új `prompt_version`-t ad, így a naplóban külön látszik.

## 4. A Top 100 tömeges futtatása

A maradék 97 munkakörre két mód van; 100 munkakörnél a párhuzamos mód az egyszerűbb, a kötegelt az olcsóbb.

| Mód | Mikor | Átfutás | Ár |
| --- | --- | --- | --- |
| Párhuzamos (`run --workers`) | P2 Geminin, azonnali eredmény kell | percek–órák | normál API-ár |
| Kötegelt (`batch`) | minden lépés Anthropicon (`GH_PROVIDER_P2=anthropic`) | a szolgáltató kötegablakán belül, akár órák | kedvezményes kötegár |

**Párhuzamos mód**

```bash
python -m gaphopper run --workers 8 --limit 20   # első 20, utána átnézés
python -m gaphopper run --workers 8              # a többi
```

A P1 és a P2 hívásai párhuzamosan futnak, a gráfba írás egy szálon. Ha a szolgáltató sebességkorlátot jelez, csökkentsd a `--workers` értékét.

**Régi Anthropic-kötegelt mód – csak CLI**

```bash
python -m gaphopper batch submit --step P1
python -m gaphopper batch collect --wait          # percenként ellenőriz
python -m gaphopper batch submit --step P2
python -m gaphopper batch collect --wait
python -m gaphopper run                          # P3 + gráfba írás helyben
```

A `collect --wait` megszakítható (Ctrl+C) és később újraindítható: a köteg azonosítója a `runs/state.sqlite`-ban van. Az állapotot a `batch status` mutatja.

Mindkét mód idempotens: a kész munkakör nem fut újra, a hibás visszakerül `NYERS` állapotba, és a következő futás a hibaüzenettel együtt próbálja újra.

## 5. Szakértői sor és ellenőrzés

A futás végén minden munkakör három állapot egyikében van: `GRAFBAN` (kész), `SZAKERTORE` (döntésre vár) vagy `ELVETVE`.

```bash
python -m gaphopper stats                 # gráfszámok + állapotok
python -m gaphopper ui                    # Szakértői sor a webfelületen
python -m gaphopper review --list         # ugyanez parancssorban
python -m gaphopper review --approve <qid>
python -m gaphopper review --reject <qid>
```

Szakértőhöz kerül, ha a bírálat után is sérül egy szabály (R1–R20; a táblázat a README 5. fejezetében), ha a bíráló EXPERT-et mond, vagy ha a P3 nem biztos egy egyesítésben. A 100 munkakör után nézd meg a neo4j Browserben:

```cypher
// munkakörönként hány TESZ-nivaló, TESZ-SZÍN és CORE TESZ-nivaló
MATCH (m:MunkaKor)-[r:TARTALMAZ_TESZT]->(t)-[:MAGJA]->(g)
RETURN m.label_hu, count(DISTINCT g) AS mag, count(t) AS tesz,
       count(DISTINCT CASE r.relevance WHEN 'CORE' THEN g END) AS core_mag ORDER BY mag;

// több munkakörben ismétlődő TESZ-nivalók: itt látszik, működik-e az egyesítés
MATCH (g:TeszMag)<-[:MAGJA]-()<-[:TARTALMAZ_TESZT]-(m)
WITH g, collect(DISTINCT m.label_hu) AS mk WHERE size(mk) > 1
RETURN g.label_hu, mk ORDER BY size(mk) DESC LIMIT 50;

// egy TESZ-nivaló mélyítési létrája
MATCH (g:TeszMag {id: $mag})<-[:MAGJA]-(t)-[:HELYZETBEN_VEGZENDO {primary: true}]->(h)
RETURN t.label_hu, h.id, h.fok ORDER BY h.tengely, h.fok;

// a leggyakoribb meghatározó SZER-ELEM-ek
MATCH (s:SzerElem)<-[:IRANYUL]-(g:TeszMag) RETURN s.label_hu, count(g) AS n ORDER BY n DESC LIMIT 30;
```

A küszöbök (`GH_SIM_HI=0.92`, `GH_SIM_LO=0.70`) kiinduló értékek. Ezek a TESZ-nivaló-egyezésre vonatkoznak; a SZÍN-egyezés determinisztikus. Ha sok a láthatóan azonos, de külön maradt TESZ-nivaló, csökkentsd a `GH_SIM_HI`-t; ha a rendszer különböző TESZ-eket vont össze, növeld.

## 6. KÉSZ-ségek (P4a)

A KÉSZ-ségek csak akkor futtathatók, ha a gráfban már elég munkakör van: egy SZÍN (KÖZ-eg × HELY-zet) akkor horgony, ha legalább 3 munkakörben és 5 TESZ-ben szerepel.

```bash
python -m gaphopper anchors                                    # az 1. kapu feletti horgonyok
python -m gaphopper p4a --type SZIN --value "KOZ_TARSAS|HELY_SZOLGALTATASI" --runs 3
python -m gaphopper p4a --type HELYZET --value HELY_RUTIN --runs 3
```

A P4a horgonyonként háromszor fut. A KÉSZ-ség ELLENŐRZÖTT lesz, ha legalább 2 futásban megjelenik és a horgony TESZ-einek legalább 70%-át lefedi; különben JELÖLT. Az ELFOGADOTT státusz szakértői döntés. A Top 100 után először a legtöbb TESZ-t lefedő 5–10 horgonyt futtasd, és nézd át az eredményt, mielőtt a többire is elindítod.

Ha egy új munkakör-csomag után változik a gráf, az érintett horgonyokra futtasd újra a P4a-t.

## 6a. Újraépítés (sémaváltás vagy új promptverzió után)

```bash
python -m gaphopper db-reset --yes   # mentés: runs/backup-<dátum>/, majd neo4j és state.sqlite ürítése
python -m gaphopper init
python -m gaphopper import data/job_100.jsonl
python -m gaphopper run --dry-run --job <3 referencia-munkakör>   # kalibráció
python -m gaphopper run --workers 8
python -m gaphopper generalize       # P6: túl szűk TESZ-nivaló-nevek általánosítása (előtte: --dry-run)
python -m gaphopper certify          # P5: TESZ-nivaló-rubrika + változatonként igazolás és SZÍN-rubrika
```

A `db-reset` a neo4j-t Cypherrel üríti; a Docker-kötet mappáját ne töröld fájlrendszerből.

## 7. Hibaelhárítás és újrafuttatás

| Tünet | Ok | Teendő |
| --- | --- | --- |
| `Az Ollama nem érhető el itt: …` | nem fut az Ollama, vagy más a cím | `ollama serve`; `OLLAMA_HOST` a `.env`-ben |
| `Ollama hiba (404) … model not found` | a `GH_EMBED_MODEL` név nem egyezik | a név pontosan az `ollama list` szerint |
| vektorindex-hiba íráskor (dimenzió) | modellcsere a vektorindex után | `DROP INDEX tesz_vec` (és `szer_vec`, `munkakor_vec`, `keszseg_vec`), `MATCH (n) REMOVE n.embedding`, majd `init` és `embed backfill` |
| `ServiceUnavailable` / `AuthError` | neo4j leállt, vagy rossz jelszó | indítsd a neo4j-t; `NEO4J_PASSWORD` |
| egy munkakör `HIBA` vagy vissza `NYERS` | sémahiba vagy API-hiba | `show --job <id>`: a `P1_ERR`/`P2_ERR` mutatja; a következő `run` vagy `batch submit` a hibaüzenettel próbálja újra |
| sok `SZAKERTORE` | szigorú szabály vagy gyenge bemenet | `review --list`: melyik szabály; küszöbök a `config/vocab.yaml`-ban |
| `429` / rate limit | túl sok párhuzamos hívás | kisebb `--workers` |
| köteg `P1_KOTEGBEN`-ben ragadt | a `collect` megszakadt | `batch status`, majd `batch collect --wait` |

Újrafuttatás:

```bash
python -m gaphopper run --job <id> --force             # egy munkakör elölről
python -m gaphopper run --job <id> --force --until p2  # gráf nélkül, összevetéshez
```

A `--force` a munkakör régi kapcsolatait törli a gráfból, a TESZ-eket és SZER-ELEM-eket nem, mert azokat más munkakörök is használhatják. Teljesen tiszta újrakezdéshez állítsd le a programot, töröld a `runs/state.sqlite` fájlt, ürítsd a neo4j adatbázist (`MATCH (n) DETACH DELETE n`), és kezdd a 2. lépéstől.

A webfelületről a szolgáltatói kötegelt mód kikerült. A **Feldolgozás indítása** a lépésenként beállított modelleket használja; a párhuzamos hívások száma továbbra is állítható.
