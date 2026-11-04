# Gaphopper TESZ-felbontó

MUNKA-KÖR → általános TESZ-MAG-ok (munkaminta × SZER-ELEM-fajta, 2–4 KÉSZ-ség-törzselemmel) → SZÍN-ezett változataik, a
TESZ-nivalók (MAG × SZÍN, SZÍN = ⟨KÖZ-eg, HELY-zet⟩) → helyi neo4j. A tervezés indoklása:
[docs/TESZ-MAG es SZIN - granularitas.md](docs/TESZ-MAG%20es%20SZIN%20-%20granularitas.md).
A feldolgozás lépései: E0 kinyerés, P1 felbontás, P2 bírálat, P3 egyesítés, P4 készséghorgonyzás és EQF, P5 tanúsítás.

Ez a teljes beállítási referencia. A [futtatási segédlet](docs/Gaphopper%20TESZ%20–%20futtatási%20segédlet.md) a napi munkamenetet, a [CLAUDE.md](CLAUDE.md) a fejlesztési szabályokat írja le.

```
gaphopper-tesz/
├─ config/vocab.yaml       zárt szótárak (TESZ-típus, KÖZ-eg, HELY-zet, SZER-ELEM) + szabálykuszöbök
├─ config/kesz_torzs.yaml  KÉSZ-ség-törzs: 275 elem (gh_c_<n>, GH_competency_list), Keszseg-csomópontként
├─ prompts/                E0 kinyerés, P0 rendszerprompt, P1 felbontás, P2 bírálat, P3 egyesítés, P4a/P4b
├─ gaphopper/              Python-csomag (CLI: python -m gaphopper)
│   ├─ schemas.py          a promptkimenetek pydantic-sémái
│   ├─ llm.py              Anthropic / Gemini / helyi OpenAI-kompatibilis (pl. Qwen a DGX Sparkon) / kézi / teszt
│   ├─ report.py           tokenek, idő és költség a futásnaplóból
│   ├─ rules.py            R1–R20 determinisztikus szabályok (MAG- és SZÍN-szinten, G1–G4 általánosság)
│   ├─ merge.py            P3: MAG-egyezés (alias → hasonlóság → kétes esetek egy LLM-hívásban),
│   │                      SZÍN-egyezés determinisztikusan; az írandók egy WritePlan-be gyűlnek
│   ├─ graph.py            neo4j-séma, kötegelt (UNWIND) írás egy tranzakcióban
│   ├─ store.py            SQLite: állapot, napló, köztes eredmények, alias-index, kötegek
│   ├─ extract.py          0. lépés: dokumentumokból jobs.jsonl
│   ├─ batch.py            kötegelt (Anthropic) és párhuzamos futtatás
│   └─ pipeline.py         a sor vezérlése
├─ gaphopper/web.py + web/ webes felület (JavaScript, külön csomag nélkül): futás, adatbázis, modellek, promptok, feltöltés
├─ config/models.yaml      a felületen mentett modellbeállítás (ha létezik, erősebb a .env-nél)
├─ data/sample_jobs.jsonl  három minta (angol katalógus, magyar, angol hirdetés)
└─ tests/                  offline tesztek (neo4j és API-kulcs nélkül)
```

## 1. Telepítés

```bash
cd gaphopper-tesz
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# első telepítéskor: cp .env.example .env
# töltsd ki a neo4j adatait és a használt szolgáltatók API-kulcsait
```

A helyi neo4j 5.x legyen elindítva (Desktop vagy Docker). A vektorindexhez 5.11 vagy újabb kell.

## 2. Első futtatás

### 0. lépés: dokumentumokból jobs.jsonl

Munkaköri leírások, álláshirdetések, katalógusok (PDF, DOCX, TXT, MD, HTML, ODT, RTF), akár egész mappa:

```bash
python -m gaphopper extract ~/Dokumentumok/munkakori_leirasok/ --out data/jobs.jsonl --import
```

- A szöveget kód olvassa ki (szkennelt PDF-hez előbb OCR kell), 24 000 karakteres, átfedő részletekre vágja.
- Részletenként az E0 prompt gyűjti ki a munkaköröket: cím, feladatok, elvárások, körülmények, kódok (FEOR, ISCO, ESCO, O\*NET, ha a szöveg megadja), szó szerinti forrásidézet. Nem egészít ki szakmai tudásból.
- Kód fésüli össze a részletek határán szétvágott és az ismétlődő munkaköröket (a szintjelzők, pl. „senior”, nem különböztetnek).
- Azonosító: álláshirdetés → `ad:…`, katalógus → `cat:…`, munkaköri leírás → `doc:…`. A kimenet hozzáfűződik a meglévő `jobs.jsonl`-hez; azonos azonosítónál a régi sor cserélődik.
- `--import` azonnal betölti a feldolgozó sorba; anélkül előbb átnézheted a fájlt, és utána `python -m gaphopper import data/jobs.jsonl`.
- Csonka vagy bizonytalan kinyerésnél figyelmeztetést ír ki.

### 1–3. lépés: felbontás, bírálat, gráf

```bash
python -m gaphopper init                                  # séma + zárt szótárak a gráfban
python -m gaphopper import data/sample_jobs.jsonl         # vagy a munkakör-táblázat .xlsx-e
python -m gaphopper run --job onet:29-1141.00 --until p2  # P1 + P2, gráfba írás nélkül
python -m gaphopper show --job onet:29-1141.00            # eredmények
python -m gaphopper run --job onet:29-1141.00             # P3 + gráfba írás
python -m gaphopper certify                               # P5: rubrika és igazolási mondat a gráf új TESZ-eihez
python -m gaphopper ui                                    # webes felület: http://localhost:8765
```

Táblázatból: `python -m gaphopper import ../Gaphopper_top100_TESZ_SZERELEM_seed_v01.xlsx`
(a `Munkakörök` és `Forrás` lapot olvassa; a kész TESZ-lapokat nem). Ugyanez előre elkészítve: `data/job_100.jsonl`.

O\*NET-feladatlisták a `tasks` mezőbe: töltsd le a [O\*NET adatbázis](https://www.onetcenter.org/database.html) „Task Statements” és „Task Ratings” fájlját (szöveg vagy Excel), majd
`python -m gaphopper onet-tasks "../Task Statements.txt" --ratings "../Task Ratings.txt" --release 31.0`.
A fő (Core) feladatok fontossági sorrendben a `tasks`, a kiegészítők a `tasks_supplemental` mezőbe kerülnek; a változott rekordot az újabb `import` visszaállítja NYERS-re. JSONL/CSV mezők: `job_id, title, field, description,
tasks (;-vel elválasztva), requirements, conditions, entry_route, source, locale`.
Hirdetésnél a `job_id` legyen `ad:` előtagú: ilyenkor a rendszer alias alapján keresi a kanonikus munkakört.

## 3. Tömeges futtatás

**Kötegelt mód (Anthropic Message Batches API)** – sok munkakörre a legolcsóbb: a kérések aszinkron, kedvezményes áron futnak (a feltételeket a szolgáltató aktuális dokumentációjában nézd meg).

```bash
python -m gaphopper batch submit --step P1            # minden NYERS rekord (--limit N, --job ID)
python -m gaphopper batch status
python -m gaphopper batch collect --wait              # percenként ellenőriz, amíg minden köteg kész
python -m gaphopper batch submit --step P2            # a kész P1-ekre
python -m gaphopper batch collect --wait
python -m gaphopper run                               # P3 + gráfba írás helyben (P2_ELFOGADVA rekordok)
```

- Beküldéskor a rekordok `P1_KOTEGBEN` / `P2_KOTEGBEN` állapotba kerülnek, így nem futnak kétszer.
- Begyűjtéskor minden választ ugyanaz a séma- és szabályellenőrzés vizsgál, mint egyenként. A hibás rekord visszaáll, és a hibaüzenet a következő `submit` promptjába kerül; elég újra beküldeni.
- Egy köteg legfeljebb `--max-per-batch` (alapból 2000) kérés; több rekordnál több köteg jön létre.
- A kötegben alapból nem küldünk hőmérsékletet (egyes modellek nem fogadják); `GH_BATCH_TEMPERATURE=1` bekapcsolja.
- A P2 kötegelt módja is Anthropic-ot használ: ehhez a P2 szolgáltatója legyen `anthropic`.

**Párhuzamos mód (bármely szolgáltató, pl. Gemini vagy a helyi Qwen)** – azonnali válasz, normál áron:

```bash
python -m gaphopper run --workers 8                  # P1 és P2 párhuzamosan, a gráfba írás egy szálon
```

## 4. Három futtatási mód

| Mód | Beállítás | Mikor |
| --- | --- | --- |
| API | `anthropic`, `gemini` vagy `local` (lépésenként) | rendes futtatás |
| Kézi | `GH_PROVIDER=manual` | prompt-finomítás Claude Code-ban vagy chatben, API-kulcs nélkül |
| Teszt | `FakeLLM` a tesztekben | fejlesztés |

Kézi módban a `run` a `runs/manual/<job>/` mappába írja a promptot (`P1_system.md`, `P1_user.md`).
A választ JSON-fájlba mentve: `python -m gaphopper manual-submit --job <id> --step P1 --file válasz.json`,
ezután ugyanígy a P2. A P3 kézi módban csak pontos alias-egyezést használ; a kétes egyesítések szakértői sorba kerülnek.

## 5. Mit csinál egy futás

1. **P1** – a modell általános TESZ-MAG-okra bontja a munkakört (v2: 6–12 MAG, 4–9 CORE). A MAG munkaminta ×
   SZER-ELEM-fajta, legfeljebb 6 szó, szakterület-, célcsoport- és SZÍN-semleges (G1–G4); a szakmai specifikum a
   `kontextus_hu` mezőbe kerül (a MunkaKor–TESZ élen), a MAG-hoz 2–4 KÉSZ-ség-törzselem (`kesz_ids`) tartozik.
   A P1 rendszerpromptjának gyorsítótárazott része a KÉSZ-ség-törzs és a MAG-törzs (a gráf ugyanezzel a
   szótárverzióval készült, legtöbbet használt ≤80 MAG-ja); illeszkedő törzs-MAG-nál a P1 csak a kódot írja
   (`mag_ref`), a MAG mezőit a kód tölti ki, a P3 hívás nélkül oldja fel. Minden MAG-hoz pontosan egy
   ALAP-SZÍN változatot ad, további változatot csak az M2 (új vagy szigorúbb értékelési kritérium) és az M3
   (a munkakörben rendszeres) próba szerint, MAG-onként legfeljebb 3-at (összesen 8–18 TESZ-nivaló); a
   változatnak nincs saját címkéje (G3). CORE MAG-on
   legfeljebb 1 mélyítési jelölt (a szakmában ismert, itt nem jellemző nehezebb SZÍN). A változat nem ismétli a
   MAG mezőit (`tesz_type`, `primary_szer`, `outcome_hu`, `relevance`, `kor_id`): a séma a MAG-ról tölti ki őket.
   Igazolási mondat, rubrika és indoklás nincs a kimenetben. Sémahibánál egyszer újrapróbál a hibaüzenettel; a
   gyakori, egyértelmű értékeltérések (pl. `QUARTERLY` → `MONTHLY`, `PERIPHERAL` → `SUPPORTING`) a
   `vocab.yaml` `alias_ertek` táblája szerint újrahívás nélkül javulnak.
2. **Szabályok** (küszöbök: `config/vocab.yaml` → `rules`):

   | Kód | Szint | Mit jelez |
   | --- | --- | --- |
   | R1 | MAG + változat | MAG 6–12, TESZ-nivaló 8–18, CORE MAG 4–9 |
   | R4 | MAG (+ változat mintái) | MAG-címke 3–6 szó (G1), tiltott címkeminták |
   | R5 | MAG | EXECUTE-arány > 30% |
   | R6 | ALAP-SZÍN | a HELY-zet a TESZ-típus „természetes párja” > 60% |
   | R7 | változat | Kritikus HELY-zet aránya (magas kockázatú szakterületen 40%, egyébként 25%) |
   | R8 | változat | azonos ⟨típus, SZER-ELEM, KÖZ-eg, HELY-zet⟩ négyes |
   | R9 | MAG | MAG nélküli feladatkör |
   | R10 | változat | alacsony bizonyosság (szakértőhöz) |
   | R11 | MAG | két MAG azonos ⟨típus, meghatározó SZER-ELEM⟩ párral – egy MAG két SZÍN-e? |
   | R12 | MAG | több mint 3 változat |
   | R13 | változat | nem ALAP-SZÍN változat érdemi `szin_kulonbseg_hu` nélkül (M2) |
   | R14 | MAG | SZÍN-jelző a MAG címkéjében (`szin_jelzok` szólista) |
   | R15 | séma | MAG-onként pontosan egy ALAP-SZÍN (a P1-nél sémahiba, a P2 után a kód javítja) |
   | R16 | változat | csak KÖZ-egben eltér, azonos kísérő SZER-ELEM-ekkel – valószínű kísérő KÖZ-eg |
   | R17 | MAG | munkakör- vagy szakszó (gépész, villamos …) a MAG-címkében vagy a meghatározó SZER-ELEM-ben (G1); általános célcsoport („hallgatóknak”) maradhat |
   | R18 | MAG | G4-összevonási jelölt: azonos TESZ-típus és ≥ 2/3 KÉSZ-ség-átfedés |
   | R19 | MAG | ≥ 50 elemű MAG-törzs mellett az új MAG-ok aránya > 50% |
   | R20 | MAG | 2-nél kevesebb `kesz_ids` (G2) |
3. **P2** – csak akkor fut, ha kell: szabályjelzésnél, és a minőség követésére egy véletlen mintán (alapból 15%,
   `GH_P2_MODE=auto|always|never`, `GH_P2_SAMPLE`). A bíráló a P1 tömörített, MAG-onként csoportosított nézetét
   kapja (indoklás, angol címke és definíció nélkül), és csak az eltéréseket adja vissza: FIX (változaton vagy
   `mag_id`-vel a MAG-on), DROP, MERGE, SPLIT (változat vagy MAG), ADD (új MAG-gal is), REMAG (változatok egy MAG
   alá), FOLD (változat kísérő SZÍN-né). A kód alkalmazza őket. Ha a szabályok utána is sérülnek, vagy a bíráló
   EXPERT-et mond → szakértői sor.
4. **P3 + gráf** – két lépcsőben. *MAG-egyezés*: alias-találat (magyarul és angolul is) → hasonlósági keresés a
   MAG-címkén és az `outcome_hu`-n → a kétes MAG- és SZER-ELEM-esetek munkakörönként **egyetlen** LLM-hívásban
   (SAME / NARROWER / BROADER / NEW; a KÖZ-eg és a HELY-zet nem számít). *SZÍN-egyezés*: determinisztikus, a
   TESZ-nivaló kiléte `stable_id(gh_tesz, mag_id, kozeg, helyzet)`; ha a MAG-on már van ilyen SZÍN, SAME, különben új
   változat. Az írás egy tranzakcióban, típusonként egy `UNWIND`-lekérdezéssel megy. A szinonimák az elem
   `aliases` / `alias_keys` tulajdonságaiba kerülnek, nem külön csomópontba.
5. **P5 (`certify`)** – halmozható tanúsítvány: MAG-onként 3–6 alapkritérium (`rubric_alap_hu`), változatonként
   igazolási mondat és 1–3 kiegészítő SZÍN-kritérium (`rubric_szin_hu`, az ALAP-SZÍN-nél üres). Egyszer készül,
   a gráfba írás után, hívásonként 6 MAG a változataival.
6. **Napló** – minden LLM-hívás nyers válasza, modellje, promptverziója, tokenje és ideje a `runs/state.sqlite`-ban. Összesítés: `python -m gaphopper report` (promptverziónként, így a régi és az új futás összevethető).

## 5a. Modellek és költség

Alapértékek (a `.env`, illetve a felület Modellek oldala felülírja):

| Lépés | Modell | Gondolkodás |
| --- | --- | --- |
| E0, P1 | `claude-sonnet-5-5` | `effort=low` |
| P2, P3, P5 | `claude-haiku-5-5` | `effort=low` |
| P4 | `claude-sonnet-5-5` | `effort=medium` |

**Helyi modell (pl. Qwen a DGX Sparkon):** bármely OpenAI-kompatibilis szerver (vLLM, SGLang, llama.cpp, Ollama) használható, külön Python-csomag nélkül:

```
GH_LOCAL_BASE_URL=http://<spark-cím>:8000/v1
GH_LOCAL_MODEL=<a szerver /v1/models listája szerinti név>
GH_PROVIDER_P2=local
GH_PROVIDER_P3=local
GH_PROVIDER_P5=local
GH_THINKING_P3=off          # Qwen: a chat-sablon gondolkodása ki (enable_thinking=false)
GH_LOCAL_JSON_MODE=object   # object | schema (kötött dekódolás, vLLM) | none
```

`python -m gaphopper models --check-local` kiírja a lépésenként érvényes beállítást, és lekéri a helyi szerver modelljeit.
A helyi modell lassabban ír, mint az API, ezért a rövid kimenetű lépésekre (P2 eltérések, P3, P5) érdemes; a P1 hosszú kimenete a Sonneten gyorsabb.

A futás idempotens: a kész munkakör nem fut újra (`--force` kell hozzá), a változatlan rekord importja nem állítja vissza.

## 5b. A gráf sémája (TESZ-MAG × SZÍN)

```
(:TeszMag {id, label_hu, label_en, outcome_hu, aliases, embedding, status, gen, rubric_alap_hu})
   -[:TIPUSA]->(:TeszTipus)   -[:IRANYUL {role}]->(:SzerElem)          // a meghatározó SZER-ELEM a MAG-on
   -[:IGENYEL]->(:Keszseg {id: "gh_c_<n>", label_en, csoport, fajta})  // 2–4 KÉSZ-ség-törzselem (G2)
(:TeszNivalo {id, label_hu, alap_szin, szin_kulonbseg_hu, attestation_hu, rubric_szin_hu})
   -[:MAGJA]->(:TeszMag)   -[:SZINBEN]->(:Szin)
   -[:KOZEGBEN_ZAJLIK {primary}]->(:Kozeg)   -[:HELYZETBEN_VEGZENDO {primary}]->(:Helyzet)
   -[:HASZNAL {role}]->(:SzerElem)                                     // kísérő, közegfüggő SZER-ELEM
(:Szin {id: "KOZ_FIZIKAI|HELY_KRITIKUS"})-[:KOZEGE]->(:Kozeg), -[:HELYZETE]->(:Helyzet)   // 5 × 11 rögzített
(:Helyzet {tengely, fok})-[:MELYEBB]->(:Helyzet)   // KISZ: TANULASI 0 → RUTIN 1 → VALTOZO 2 → PROBLEMA 3 → KRITIKUS 4
(:TeszMag)-[:MELYITESI_JELOLT {indok_hu, forras_munkakor}]->(:Szin)
(:MunkaKor)-[:TARTALMAZ_TESZT {relevance, frequency, expected_eqf, alap_szin, kontextus_hu, ...}]->(:TeszNivalo)
```

Kilét: `mag_id = stable_id("gh_mag", tesz_type, primary_szer_id, label_hu)`,
`tesz_id = stable_id("gh_tesz", mag_id, kozeg, helyzet)`. A HELY-zet SZEREP tengelyén (DÖNTÉSI, ALKOTÓ,
EGYÜTTMŰKÖDÉSI, SZOLGÁLTATÁSI, IRÁNYÍTÁSI, TANÍTÓ) nincs fok. A `HELY_TANULASI` a betanulás (a személy maga
tanul), a tanítás `HELY_TANITO`. A `MELYITI` / `ATVISZI` viszony nincs tárolva: a MAG és a `Helyzet.fok`
lekérdezéssel megadja (példák a tervezési doksi 5.4 fejezetében és a felület Adatbázis oldalán).

## 5c. Próbafutás (dry run)

```bash
python -m gaphopper run --dry-run --job onet:29-1141.00            # a rekord a state.sqlite-ból (csak olvasva)
python -m gaphopper run --dry-run --input data/job_100.jsonl --job onet:47-2111.00 --workers 3
python -m gaphopper run --dry-run --chain --no-graph --job A --job B --job C   # lánc: a korábbi munkakörök
                                     # új MAG-jai a következő MAG-törzsébe kerülnek (a közös MAG-ok mérése)
```

A P1, a (szükség szerinti) P2 és az egyesítés P3-hívása lefut, de **sem a gráf, sem a `state.sqlite` nem
változik**: a napló és a köztes eredmények memóriában élnek, az egyesítés csak olvas. A kimenet a MAG-ok és
változataik táblázata (● ALAP-SZÍN, ○ mélyítő/átvivő, ◇ mélyítési jelölt, egyesítési döntés), a
szabályjelzések, lépésenként a tokenek, a becsült költség és az idő; mentés: `runs/dryrun/<job>.json`. A
webes felületen a Futás oldal „Próbafutás (dry run)” kapcsolójával, illetve egy munkakör részleteinél a
„Próbafutás” gombbal indítható; az eredmény a munkakör részleteinél látszik.

## 5d. Újraépítés (új séma vagy új promptverzió után)

```bash
python -m gaphopper db-reset --yes   # mentés: runs/backup-<dátum>/ (state.sqlite + neo4j-export.json), majd
                                     # minden neo4j-csomópont, index és constraint és a state.sqlite törlése
python -m gaphopper init             # új séma: constraint-ek, indexek, szótárak, 55 Szin, MELYEBB-lánc
python -m gaphopper import data/job_100.jsonl
python -m gaphopper run --dry-run --job <id>   # kalibráció
python -m gaphopper run --workers 4
python -m gaphopper certify
```

A Docker-kötet mappáját nem kell (és nem szabad) fájlrendszerből törölni: a `db-reset` Cypherrel ürít.

## 6. Hasonlóságkeresés (helyi Ollama)

Alapból (üres `GH_EMBED_MODEL`) a neo4j teljes szöveges indexe adja a jelölteket, és a P3 dönt.
Pontosabb és olcsóbb nagy tömegnél a beágyazás. Helyi Ollama-val nem kell hozzá extra Python-csomag:

```bash
ollama list                                   # a modell neve, pl. bge-m3
# .env:
#   GH_EMBED_PROVIDER=ollama
#   GH_EMBED_MODEL=bge-m3
#   OLLAMA_HOST=http://localhost:11434
python -m gaphopper embed check               # dimenzió + magyar–angol hasonlósági próba
python -m gaphopper init                      # létrehozza a vektorindexeket a modell dimenziójával
python -m gaphopper embed backfill            # a már a gráfban lévő elemek vektorai
```

- Magyar és angol munkakörökhöz **többnyelvű** modell kell (pl. `bge-m3`). Az `embed check` megmutatja: a „szoftverfejlesztő” – „software developer” párnak jóval hasonlóbbnak kell lennie, mint az „ápoló” – „software developer” párnak. Csak angolra tanított modellnél (pl. `nomic-embed-text`) a két nyelv nem kerül egymás közelébe.
- Ha a modell feladat-előtagot vár, add meg: `GH_EMBED_PREFIX="search_document: "`.
- A vektorindex dimenziója a modellhez kötött. Modellcserénél töröld a vektorindexeket (`DROP INDEX tesz_vec` stb.) és a régi vektorokat (`MATCH (n) REMOVE n.embedding`), majd `init` és `embed backfill`.
- Bekapcsolt beágyazással `GH_SIM_HI` felett automatikusan azonos (ha a típus, a meghatározó SZER-ELEM és a HELY-zet is egyezik), `GH_SIM_LO` alatt automatikusan új; csak a köztes sáv megy LLM-hez. A küszöbök modellfüggők: az első 50–100 munkakör után nézd meg a P3-döntéseket, és állítsd be őket.

## 7. KÉSZ-ségek (P4a)

```bash
python -m gaphopper anchors                       # az 1. kapu (≥3 munkakör, ≥5 TESZ) feletti SZÍN-ek (a Szin csomópontból)
python -m gaphopper p4a --type HELYZET --value HELY_RUTIN --runs 3
python -m gaphopper p4a --type SZIN --value "KOZ_TARSAS|HELY_SZOLGALTATASI"
```

SZÍN-horgonynál a KÉSZ-ség a `Szin` csomóponthoz köt (`HORGONYOZ`, `ASPEKTUSA`), ugyanahhoz, amelyhez a
TESZ-nivaló `SZINBEN` éle: a TESZ és a KÉSZ-ség mélyülése egy gráfúton látszik. A P4a háromszor fut; ELLENŐRZÖTT státuszt kap a KÉSZ-ség, ha legalább 2 futásban megjelenik, és a horgony
TESZ-einek legalább 70%-át lefedi; különben JELÖLT. Az ELFOGADOTT státusz szakértői döntés.

## 8. Claude Code és neo4j MCP

A `CLAUDE.md` leírja Claude Code-nak a projektet. A gráf közvetlen lekérdezéséhez másold a `.mcp.json.example`
fájlt `.mcp.json` néven (a neo4j MCP szerver `uvx`-szel indul; a pontos csomagnevet és változókat a
neo4j-contrib/mcp-neo4j leírásában ellenőrizd).

Hasznos lekérdezések:

```cypher
// egy munkakör TESZ-ei tripletekkel
MATCH (m:MunkaKor {id:'onet:29-1141.00'})-[r:TARTALMAZ_TESZT]->(t)-[i:IRANYUL]->(s)
MATCH (t)-[:TIPUSA]->(ty)
RETURN t.label_hu, ty.id, s.label_hu, i.kozeg_id, i.helyzet_id, r.relevance, r.expected_eqf;

// több munkakörben ismétlődő TESZ-ek
MATCH (t:TeszNivalo)<-[:TARTALMAZ_TESZT]-(m) WITH t, collect(m.label_hu) AS mk
WHERE size(mk) > 1 RETURN t.label_hu, mk ORDER BY size(mk) DESC;
```

## 9. Tesztek

```bash
pip install pytest && python -m pytest -q
GH_TEST_XLSX=../Gaphopper_top100_TESZ_SZERELEM_seed_v01.xlsx python -m pytest -q   # Excel-import is
```

## 10. Webes felület

```bash
python -m gaphopper ui                 # http://localhost:8765, megnyitja a böngészőt
python -m gaphopper ui --port 9000 --no-browser
python -m gaphopper ui --host 0.0.0.0  # a helyi hálózatról is elérhető (jelszó nincs rajta!)
```

Egyetlen JavaScript-oldal (`web/`), a Python oldalon csak a standard könyvtár fut (`gaphopper/web.py`); külön csomag nem kell.

- **Futás:** a „Próbafutás (dry run)” kapcsolóval a kijelölt munkakörök P1–P3 lépése lefut, de semmi nem íródik (lásd 5c). Felül a fázisok (E0 kinyerés → P1 felbontás → P2 bírálat → P3 gráfba írás → neo4j → P5 tanúsítás), mindegyiknél hány munkakör kész, kihagyott, hibás, várakozó vagy fut éppen. Alatta munkakörönként ugyanez egy sorban, és külön oszlopban, hogy a munkakör ténylegesen benne van-e a neo4j-ben (a gráfból ellenőrizve, nem az állapotmezőből). Indítás: kijelöltek / következő N / minden várakozó, meddig, hány szálon; a futó parancs kimenete élőben, leállítható. Egy munkakörre kattintva: fázisonként a modell, a tokenek, az idő és a költség, a hiba szövege, a P2 módosításai, és MAG-onként csoportosítva a változatok (● ALAP-SZÍN, ○ mélyítő/átvivő, ◇ mélyítési jelölt), hogy bekerültek-e a gráfba (új elemként vagy egy meglévőhöz kapcsolva), illetve mit vett ki a P2. A szakértői jóváhagyás és elutasítás is itt van.
- **Adatbázis:** összevetés: hány munkakör van a gráfban, hány nincs, melyik „kész” munkakör hiányzik a gráfból, mi van csak a gráfban, hány TESZ-nivalónak nincs még igazolása. Kész lekérdezések: MAG-ok mélyítési létrája, mélyítési jelöltek, TESZ-típus × SZÍN. A SQLite állapotai és a neo4j csomópontjai, kapcsolatai; csak olvasó Cypher- és SQL-lekérdezés.
- **Modellek:** lépésenként szolgáltató és modell; API-ból frissíthető modellválaszték; OpenAI-kompatibilis API és beágyazó modell beállítása és ellenőrzése; szerkeszthető egységárak; P2-mód. A mentés a `config/models.yaml`-ba kerül, és erősebb, mint a `.env`.
- **Promptok:** a lépés sorrendjében, melyik lépés használja; szerkesztés, eltérés mentés előtt, korábbi változatok (`prompts/.history/`) betöltése.
- **Feltöltés:** dokumentumok behúzása és kinyerése (E0, utána rögtön import), rekordfájlok előnézete (új / változott / változatlan) és importja.

A felületről indított parancsok a háttérben futnak (`python -m gaphopper ...`), a kimenetük a `runs/ui_tasks/` mappába kerül, így a felület újraindítása után is visszanézhetők.

## Következő lépések (még nincs benne)

- Gemini kötegelt mód (most a Gemini párhuzamos módban fut);
- újrahasznosítás munkakörök között: a P1 megkapja a rokon munkakörök meglévő TESZ-eit, és azonosítóval hivatkozik rájuk;
- P4b (EQF-szintleírók) futtató parancsa; a prompt kész (`prompts/p4b_eqf.md`);
- munkakör-összevonás LLM-mel hirdetéseknél (most: alias-egyezés vagy új munkakör);
- crawler és PostgreSQL, amikor a hirdetések száma a százezres tartományba lép.

## Modellválasztás, kapcsolatellenőrzés és fogyasztás

Az elsődleges felület: `python -m gaphopper ui`, cím: `http://localhost:8765`.
Az `app.py`, `review_app.py` és `ui/` régi, opcionális Streamlit-felület; a jelen útmutató a beépített webfelületre vonatkozik, amelyhez Streamlit nem szükséges.

A **Modellek** oldalon válassz szolgáltatót minden lépéshez (E0, P1–P5), majd nyomd meg a szolgáltató **modelllista frissítése** gombját. A lista az API által az adott kulccsal elérhető modelleket mutatja; a kiválasztott nevek és a mentett modelllista később is láthatók. Egyedi modellazonosító is beírható. A listában szereplés önmagában nem garantál JSON- vagy gondolkodásiparaméter-támogatást. Szolgáltatóváltáskor válaszd ki az új modellt, majd **Mentés**. A következő indított futás használja az új beállítást; a már futó folyamat nem vált modellt.

A **Promptok** oldalon a lépés melletti hivatkozás a modellbeállításokhoz vezet. A modell a feldolgozási lépéshez tartozik: például a P3 rendszer- és feladatpromptja közösen használja a P3 modelljét; a közös P0 rendszerpromptot több lépés használja.

- Anthropic: `ANTHROPIC_API_KEY`; Gemini: `GEMINI_API_KEY` vagy `GOOGLE_API_KEY` a `.env` fájlban. A szolgáltatói Python-csomagokat a `requirements.txt` tartalmazza.
- Helyi generatív modellhez nincs külön beállítópanel vagy előbeállítás az elsődleges webfelületen. A régi `local` konfiguráció CLI-kompatibilitása megmaradt; meglévő ilyen lépésnél válassz másik szolgáltatót. Az Ollama-beágyazás külön funkcióként megmarad.
- OpenAI-kompatibilis API: `openai`. Külön base URL (alapérték: `https://api.openai.com/v1`), kulcsváltozó neve (alapérték: `OPENAI_API_KEY`), JSON-mód. Más szolgáltatóhoz is megadható saját URL és kulcsváltozó. A külön **Kapcsolati próba** gomb a beírt, még nem mentett beállításokkal lekéri a `/models` végpontot. Siker vagy hiba helyben megjelenik; a próba nem ment és nem indít szöveggenerálást, ezért annak jogosultságát nem igazolja. A kulcs értéke a `.env` fájlban marad, nem kerül a modellkonfigurációba vagy az API-válaszba.
- Beágyazás: `ollama` vagy az opcionálisan telepített `sentence-transformers`. Modell, Ollama-cím és szövegelőtag menthető. A **Beágyazás ellenőrzése** tényleges vektorokat kér, majd dimenziót és magyar–angol hasonlóságot mutat. Üres modell kikapcsolja a beágyazást. A próba sentence-transformers esetén letöltést is indíthat. Modellcsere után a meglévő vektorok és indexek migrációja külön feladat; lásd a futtatási segédlet hibaelhárítását.

A konfiguráció erősorrendje: `config/models.yaml` → `.env` / környezet → kód alapértékei. A felületi lépésbeállítás teljes lépéskonfigurációként érvényesül. A jelenlegi effektív értékeket a `python -m gaphopper models` mutatja, ezért a dokumentált alapérték helyett ezt ellenőrizd futtatás előtt. A felületi beállítás törlése a teljes `models.yaml` fájlt törli (árakat és mentett modelllistákat is).

A **Futás** oldalon az összes naplózott hívás bemeneti, cache-olvasási és kimeneti tokenje, valamint az árazott fogyasztás látható; futás közben automatikusan frissül. A részletes táblázat lépésenként, a munkakör részletei munkakörönként mutatják a fogyasztást. Parancssorban: `python -m gaphopper report`.
Az egységárak a **Modellek** oldalon USD / 1M tokenben szerkeszthetők (bemenet, kimenet, cache olvasás). Új modellhez az üres sort töltsd ki, ments, majd újabb sorhoz töltsd újra az oldalt. A költség becslés a beállított árakkal; ismeretlen árú hívások külön jelennek meg. A helyi modellek API-költsége 0, a hardver és áram nincs benne. A számláló a naplózott válaszokat és sémajavítási próbákat összesíti; válasz nélküli hálózati hibák és a beágyazás tokenjei nem részei. Az API-válaszok alapján a Gemini gondolkodási tokenjei a kimenethez számítanak.


A Gemini választó tíz előre betöltött szöveges modellt tartalmaz (2.5 Pro/Flash/Flash-Lite,
3 Flash Preview, 3.1 Pro Preview/Flash-Lite, 3.5 Flash-Lite, 3.6/3.8 Flash, 4 Argon).
Az API-listafrissítés további, a kulccsal elérhető azonosítókat adhat hozzá; az előre betöltött lista
önmagában nem igazol hozzáférést. A [Google díjszabásából](https://ai.google.dev/gemini-api/docs/pricing)
2026-10-21-én ellenőrzött Standard szöveges árak a választóban is látszanak.
A 2.5 és 3.1 Pro hosszúkontextus-díjsávja hívásonként érvényesül. A 4 Argon kedvezményes tarifája
2026-11-30-ig, a 3.6/3.8 Flash tarifája 2026-12-31-ig érvényes; lejáratkor az árakat frissíteni kell.
A díjak fizetős használatra vonatkozó becslések, a szabad kvótát és a cache tárolási díját nem számolják.


A webfelületen nincs szolgáltatói kötegelt mód: a **Feldolgozás indítása** a lépésenként
kiválasztott szolgáltatóval és modellel, a megadott párhuzamossággal dolgozik.
A régi Anthropic-kötegek kezelése csak a `batch` CLI-parancsokkal érhető el.

A **Jóváhagyás és gráfba írás** háttérfeladatként fut. A gomb mellett és a P3 fázisnál
folyamatjelzés látszik; a Futás oldalon a napló is követhető. A kérés sikeres gráfírás után
záródik le, hiba esetén nyitva marad, és a hiba a munkakör részleteinél olvasható.

A **Komponensek** oldalon külön ellenőrizhető a Neo4j Docker-konténer, a Neo4j
adatbázis-kapcsolat (hitelesítéssel és olvasási próbával), a helyi beágyazás és az
OpenAI-kompatibilis API modelllistája. A beágyazáspróba egy rövid szövegből vektort
készít; kikapcsolt beágyazásnál ezt külön jelzi. A sentence-transformers modellt
itt nem tölti be automatikusan: annak próbája a Modellek oldalon érhető el.
Az ellenőrzések kézzel frissíthetők, a szerver gépéről futnak.

A **Neo4j Docker indítása** gomb a meglévő, leállított `neo4j_gh` konténert indítja,
hiányzó konténernél pedig ezt futtatja:

```bash
docker run -d --rm -i --name neo4j_gh -p 7474:7474 -p 7687:7687 -v /mnt/data/project_data/gh:/data neo4j
```

Ez a helyi `neo4j` image-et és a megadott adatkönyvtárat használja. A terminált
igénylő `-t` kapcsoló a webes indításból kimarad. A konténer nevét a `.env`
`GH_NEO4J_CONTAINER` változója írhatja felül. A Dockernek futnia kell, és a webes
szerver felhasználójának hozzá kell férnie. A futó konténert nem indítja újra.
Indítás után a Neo4j felállása eltarthat néhány másodpercig; utána frissítsd az állapotot.

P3 közben a kereséshez kiszámított beágyazást a mentés is újrahasználja; a már
lekért TESZ-kontextusokat munkakörönként gyorsítótárazza. A P3 naplója és mentett
összesítése külön időt mutat az előkészítésre (`planning_seconds`), döntésre
(`decision_seconds`) és gráfírásra (`writing_seconds`); modellhívás esetén a
`model_seconds` is szerepel. Ezekkel elkülöníthető az API és a gráfműveletek ideje.

A P1 tömör JSON-t kér (formázó behúzás nélkül), az üres listák és null értékű
opcionális mezők elhagyhatók. A séma ezeket visszaállítja, így a mentett eredmény
és a következő lépések formátuma változatlan. Az indoklások célhossza 8–12 szó,
legfeljebb 20; a 12–25 TESZ, a kétnyelvű címkék és a szakmai szabályok megmaradnak.
A bemeneti rekord teljes tartalma tömör JSON-ként megy a modellnek. Ez új
promptverziót eredményez; a korábbi eredményeket nem futtatja automatikusan újra.

Ha a P1 válaszában kizárólag hibás `relevance` értékek vannak, az egy javító
újrapróbálkozás csak ezekhez a TESZ-indexekhez kér `CORE` / `SUPPORTING` döntést.
A kód csak a kért mezőket módosítja, majd a teljes P1-et ismét validálja. Hiányzó,
dupla vagy nem kért index, illetve érvénytelen besorolás hibát eredményez; nincs
csendes automatikus átminősítés. Más sémahibák esetén a teljes válasz javítását kéri.
