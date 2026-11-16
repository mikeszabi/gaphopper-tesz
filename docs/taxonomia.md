# Besorolási és ágazati taxonómiák a gráfban

Ez a leírás azt mutatja be, hogyan kerül a munkakörök foglalkozási besorolása (O\*NET/SOC, ESCO, ISCO-08,
FEOR-08) és ágazati besorolása (ISIC, WEF) a neo4j-be, hogyan kapcsolódnak hozzá a munkakörök, és hogyan
lehet rajta lekérdezni. Két lépés tartozik hozzá:

| Lépés | Parancs | Mit csinál |
|---|---|---|
| J0 | `python -m gaphopper job-all` | `data/job_all.jsonl`: minden O\*NET-munkakör ESCO-, ISCO- és FEOR-hivatkozással |
| T0 | `python -m gaphopper taxonomy` | a teljes taxonómiák, megfeleltetések és ágazatok a gráfba; a meglévő munkakörök bekötése |

A T0 LLM nélkül fut, néhány másodperc, és bármikor újrafuttatható (például `db-reset --graph-only` után).
A J0 csak a nem egyértelmű megfeleltetéseknél hív LLM-et, és a döntéseit gyorsítótárba menti.

## 1. A munkakör-rekord hivatkozásai (job_all.jsonl)

Egy rekord egy feladatlistával rendelkező O\*NET-foglalkozás (`job_id: onet:<kód>`, 923 rekord). A
hivatkozások két helyen vannak:

- `codes`: az elsődleges kódok laposan: `onet`, `soc`, `isco`, `esco` (URI), `esco_code`, `feor`.
- `refs`: rendszerenként (`onet`, `esco`, `isco`, `feor`, és ha van, `crawl`) a hivatkozások listája,
  minden elem azonos szerkezetű:

```json
{"system": "feor", "code": "8425", "uri": "", "name_en": "", "name_hu": "Targoncavezető",
 "match": "", "method": "crosswalk+llm", "primary": true, "confidence": 0.85,
 "path": [{"level": 1, "level_name": "főcsoport", "code": "8", "name_en": "", "name_hu": "GÉPKEZELŐK, …"},
          {"level": 2, "code": "84", …}, {"level": 3, "code": "842", …}, {"level": 4, "code": "8425", …}]}
```

| Mező | Jelentés |
|---|---|
| `match` | a viszony típusa: `exactMatch`, `closeMatch`, `broadMatch` (a hivatkozott elem tágabb), `narrowMatch` |
| `method` | `crosswalk` (hivatalos megfeleltetés), `crosswalk+llm` (több jelöltből az LLM választott), `llm` (nem volt megfeleltetés, az LLM keresett), `manual`, `source` |
| `primary` | rendszerenként pontosan egy elsődleges hivatkozás van |
| `confidence` | csak az LLM-döntéseknél (0–1) |
| `path` | a hierarchia a gyökértől a hivatkozott elemig |

A megfeleltetés forrásai: O\*NET–ESCO a hivatalos ESCO–O\*NET crosswalkból (v1, 2022), ISCO az ESCO-foglalkozás
csoportjából, FEOR-08 az ISCO-08–FEOR-08 megfeleltetésből. Ahol ez nem egyértelmű, az LLM a jelöltek közül
választ:

| Eset | Darab | Módszer |
|---|---|---|
| ESCO: egy pontos egyezés vagy egyetlen jelölt | 546 | `crosswalk` |
| ESCO: több jelölt, nincs egyetlen pontos egyezés | 358 | `crosswalk+llm` |
| ESCO: nincs megfeleltetés | 19 | `llm` (szöveges hasonlóság + ISCO-csoport jelöltjeiből) |
| FEOR: egy jelölt | 713 | `crosswalk` |
| FEOR: több jelölt | 210 | `crosswalk+llm` |

A hivatalos crosswalk rangsora sokszor félrevezető, ezért nem bíztuk rá az elsődleges ESCO kiválasztását:
például az *Industrial Truck and Tractor Operators* szoros párja ott a *moving truck driver*, a *forklift
operator* csak tág egyezés. Az elsődleges ESCO adja a rekord magyar címét is.

A 0,6 alatti bizonyosságú döntések (kb. 200) a `data/job_all_review.csv`-ben vannak átnézésre. A döntések
gyorsítótára: `data/ref/job_all_llm_decisions.json`; egy tétel törlése után a `job-all` csak azt kérdezi újra.

## 2. A gráfmodell

### Csomópontok

Minden besorolási elem `Besorolas`, minden ágazat `Agazat` címkét kap, mellette a rendszer címkéjét. A kulcs
mindenhol `id = "<rendszer>:<kód>"`.

| Címke | Rendszer | Darab | Szintek |
|---|---|---|---|
| `Besorolas:Isco` | ISCO-08 | 619 | 1 major group → 2 sub-major → 3 minor → 4 unit group |
| `Besorolas:Esco` | ESCO v1.1 foglalkozás | 3 039 | 5 (és mélyebb, ha az ESCO-foglalkozás beágyazott) |
| `Besorolas:Feor` | FEOR-08 | 654 | 1 főcsoport → 2 csoport → 3 alcsoport → 4 foglalkozás |
| `Besorolas:Soc` | SOC 2018 | 1 447 | 1 major → 2 minor → 3 broad → 4 detailed |
| `Besorolas:Onet` | O\*NET-SOC 2019 | 1 016 | 5 |
| `Agazat:Isic` | ISIC Rev.4 szekció | 21 | A–U |
| `Agazat:Wef` | WEF-szektor (GH-kód) | 27 | |

Tulajdonságok: `id, rendszer, kod, szint, szint_nev, label_hu, label_en, uri` (az ágazatnál `leiras_hu,
leiras_en` is). A `label_hu`/`label_en` a TESZ-gráf többi csomópontjával azonos elnevezés.

### Élek

```
(gyerek:Besorolas)-[:RESZE]->(szülő:Besorolas)
(a)-[:MEGFELEL {forras, match, method, primary, confidence}]->(b)
(x)-[:AGAZATBAN {forras, primary, arany, db}]->(:Agazat)
(m:MunkaKor)-[:BESOROLVA {match, method, primary, confidence}]->(:Besorolas)
```

**RESZE** – a hierarchia rendszeren belül (6 731 él). Az ESCO-foglalkozások az ISCO-csoportjuk (beágyazott
ESCO-foglalkozás a szülő ESCO-foglalkozás) alá kerülnek, így az ISCO és az ESCO egy fát alkot. Az O\*NET-foglalkozás
a SOC detailed kódja alá kerül. A FEOR és a SOC/O\*NET külön fa.

**MEGFELEL** – rendszerek közötti megfeleltetés; a `forras` mondja meg, honnan jön:

| `forras` | Irány | Darab | Tartalom |
|---|---|---|---|
| `esco-onet-crosswalk` | Esco/Isco → Onet | 4 250 | a hivatalos megfeleltetés minden sora, `match` típussal |
| `isco08-feor08` | Isco → Feor | 809 | a hivatalos megfeleltetés |
| `job_all` | Onet → Esco/Isco/Feor | 7 490 | a J0 döntései, `primary` és `confidence` jelöléssel |
| `gh-isic-wef` | Isic → Wef | 21 | ISIC-szekcióhoz rendelt WEF-szektor |

**AGAZATBAN** – foglalkozás vagy csoport ágazata. A `primary` az elsődleges ágazat, az `arany` és a `db` a
származtatott éleknél a megoszlás.

| `forras` | Kiinduló csomópont | Számítás |
|---|---|---|
| `esco-isic` | Isco, Esco | az `ESCO/occupation_isic.csv` közvetlenül (több kód esetén az első az elsődleges) |
| `job_all-esco-isic` | Onet (a job_all munkakörei) | az O\*NET-foglalkozás ESCO-hivatkozásainak ISIC-kódjai; elsődleges az elsődleges ESCO első kódja, `arany` = előfordulási arány |
| `soc-major-aggregate` | Soc főcsoport (23, a legfelső szint) | a főcsoport O\*NET-foglalkozásainak elsődleges ágazata szerinti megoszlás |
| `…+isic-wef` | ugyanezek | az ISIC→WEF megfeleltetésen át közvetlenül a WEF-szektorhoz; ha több ISIC-szekció ugyanarra a WEF-szektorra mutat, egy él, összeadott aránnyal |

**BESOROLVA** – egy feldolgozott munkakör (`MunkaKor`) hivatkozásai, a rekord `refs`-éből: minden hivatkozott
O\*NET-, ESCO-, ISCO- és FEOR-elemhez egy él. A felsőbb szinteket a `RESZE` bejárása adja, ezeket nem tároljuk
munkakörönként. A `MunkaKor` az elsődleges kódokat laposan is hordozza (`kod_onet`, `kod_isco`, `kod_esco_code`,
`kod_feor`).

Mikor íródik: a P3 a munkakör írásakor (`graph.write_plan`), ha a rekordnak van `refs` mezője (job_all-rekord);
a `taxonomy` parancs pedig a gráfban már meglévő munkakörökre is megírja (a tárolt rekord `refs`-éből, ennek
hiányában a `data/job_all.jsonl` azonos `job_id`-jú rekordjából).

A besorolás LLM nélküli, a TESZ-felbontástól független lépés: a P1 és a P2 bemenetéből a `refs`, `codes`, `sets`,
`source`, `source_url`, `fetched_at` és `tasks_source` mező kimarad (`pipeline.llm_job_view`), így a besorolási
hivatkozások nem növelik a promptot és nem befolyásolják a felbontást.

## 3. Futtatás

```bash
conda activate gaphopper
python -m gaphopper job-all              # data/job_all.jsonl (LLM csak a nyitott döntésekre, gyorsítótárral)
python -m gaphopper taxonomy --dry-run   # összesítés, a gráf nem változik
python -m gaphopper taxonomy             # betöltés + a meglévő munkakörök BESOROLVA élei
python -m gaphopper taxonomy --munkakorok  # + minden job_all-munkakör előzetes MunkaKor-csomópontként
```

| Kapcsoló | Alapérték | Jelentés |
|---|---|---|
| `--esco-dir` | `../esco/data` | `ESCO/`, `ONET/`, `ISIC/`, `WEF/` almappák |
| `--ref-dir` | `data/ref` | `feor08_hierarchy.csv`, `isco08_feor08_crosswalk.csv` |
| `--jobs` | `data/job_all.jsonl` | a J0-döntések és az ágazat-származtatás forrása |
| `--no-link` | | a munkakörök BESOROLVA éleit nem írja |
| `--munkakorok` | | a `--jobs` minden rekordjához `MunkaKor`-csomópont, TESZ-felbontás nélkül (lásd lent) |

**Előzetes munkakörök (`--munkakorok`).** A besorolás így a TESZ-felbontástól függetlenül, LLM nélkül a gráfba
kerül: minden job_all-rekordból `MunkaKor` lesz (`label_hu` = `title`, `label_en` = `title_en`, `field`, `kod_*`,
`elozetes: true`) a `BESOROLVA` éleivel. A már felbontott munkakörök nevét és TESZ-éleit nem írja felül, csak a
kódjaikat. Amikor egy előzetes munkakör a P3-on átmegy, a P1 neve felülírja a címet, és az `elozetes` jelölés
törlődik. A fő UI az előzetes munkakört nem számolja „gráfban lévőnek”, és nem jelzi árvának; a `stats`
`munkakor_elozetes` értéke a számukat adja. Felbontott munkakörök: `MATCH (m:MunkaKor) WHERE m.elozetes IS NULL`.

Az írás egy tranzakcióban, UNWIND-dal történik (`graph.write_taxonomy`). Újrafuttatáskor a csomópontok
frissülnek (MERGE), a `RESZE` élek és a betöltött források `MEGFELEL`/`AGAZATBAN` élei újraíródnak; a TESZ-gráfot
nem érinti. A `db-reset --graph-only` után: `init`, majd `taxonomy`.

## 4. Lekérdezési példák

```cypher
// Egy munkakör teljes FEOR-útja
MATCH (:MunkaKor {id: "onet:29-1141.00"})-[:BESOROLVA {primary: true}]->(f:Feor)-[:RESZE*0..]->(x:Feor)
RETURN x.szint, x.kod, x.label_hu ORDER BY x.szint

// Munkakörök egy FEOR-főcsoportban (bármely mélységben)
MATCH (m:MunkaKor)-[:BESOROLVA {primary: true}]->(:Feor)-[:RESZE*0..]->(:Feor {kod: "2"})
RETURN m.id, m.label_hu

// TESZ-nivalók ISIC-ágazatonként
MATCH (m:MunkaKor)-[:BESOROLVA {primary: true}]->(:Onet)-[:AGAZATBAN {primary: true}]->(s:Isic)
MATCH (m)-[:TARTALMAZ_TESZT]->(:TeszSzin)-[:SZINEZI]->(n:TeszNivalo)
RETURN s.label_hu AS agazat, count(DISTINCT m) AS munkakor, count(DISTINCT n) AS nivalo ORDER BY munkakor DESC

// Egy SOC-főcsoport WEF-ágazati megoszlása
MATCH (:Soc {kod: "53-0000"})-[a:AGAZATBAN]->(w:Wef) RETURN w.label_hu, a.arany ORDER BY a.arany DESC

// Két munkakör legközelebbi közös őse az ISCO/ESCO-fában
MATCH (:MunkaKor {id: $a})-[:BESOROLVA {primary: true}]->(:Esco)-[:RESZE*0..]->(k),
      (:MunkaKor {id: $b})-[:BESOROLVA {primary: true}]->(:Esco)-[:RESZE*0..]->(k)
RETURN k.id, k.label_hu ORDER BY k.szint DESC LIMIT 1

// Átjárás: ESCO-foglalkozásból a lehetséges FEOR-kódok
MATCH (:Esco {kod: "8344.1"})-[:RESZE*0..]->(:Isco {szint: 4})-[:MEGFELEL {forras: "isco08-feor08"}]->(f:Feor)
RETURN f.kod, f.label_hu

// Lefedettség: FEOR-foglalkozások, amelyekhez még nincs feldolgozott munkakör
MATCH (f:Feor {szint: 4}) WHERE NOT (f)<-[:BESOROLVA]-(:MunkaKor) RETURN count(f)

// Alacsony bizonyosságú LLM-döntések a gráfban
MATCH (o:Onet)-[r:MEGFELEL {forras: "job_all", primary: true}]->(x) WHERE r.confidence < 0.6
RETURN o.kod, o.label_en, labels(x), x.label_hu, r.confidence ORDER BY r.confidence
```

A fák legfeljebb 6 szint mélyek, a változó hosszú `RESZE*` bejárás olcsó. A `besorolas_text` fulltext index
(`label_hu`, `label_en`, `kod`) névre keresésre való:
`CALL db.index.fulltext.queryNodes("besorolas_text", "targonca*") YIELD node, score RETURN node.id, node.label_hu`.

## 5. Új forrás felvétele

Egy új besorolás (például KSH-ágazat, ESCO-készségcsoport, egy cég saját munkakörkatalógusa) felvétele nem
igényel sémaváltozást:

1. A `job_all` rekordokban új kulcs a `refs`-ben (`"ksh": [{…ugyanazok a mezők…, "path": […]}]`), elsődleges
   kód a `codes`-ban.
2. A `taxonomy.py`-ban a csomópontok (`rendszer`, `kod`, `szint`, `label_hu`, `_parent`) és a megfeleltetés
   (`MEGFELEL` új `forras` értékkel) előállítása; a címke a `SYSTEM_LABEL`/`SECTOR_LABEL` és a
   `graph.TAXONOMY_LABELS` szótárba kerül.
3. A `REF_SYSTEMS` bővítése, hogy a `BESOROLVA` élek is létrejöjjenek.

## 6. Adatminőség, ismert korlátok

- **FEOR-08 forrásfájl**: összecsúszott és hiányzó sorokat javítottunk (`data/ref/README.md`). A crosswalk
  263, 43, 63, 632, 634 kódjai a FEOR-08-ban nem léteznek, kimaradnak; a `0021` FEOR-kód egy nem létező `002`
  szülőre hivatkozik, ezért nincs fölötte hierarchia.
- **ESCO-fájl**: a fegyveres erők ISCO-kódjairól lemaradt a vezető nulla (`110`); betöltéskor 4 jegyre pótoljuk.
- **O\*NET-ágazat**: az ESCO-n át származtatott, nem hivatalos adat. Ha egy O\*NET-foglalkozás ESCO-párjai több
  ágazatban vannak, az `arany` a megoszlást mutatja, nem a foglalkoztatási súlyt.
- **SOC-főcsoport ágazata**: a foglalkozások száma szerinti megoszlás, nem a létszám szerinti.
- **ISIC→WEF**: szekciónként egy WEF-szektor (`ISIC_WEF_sectors.xlsx`); több WEF-szektor (Autóipar,
  Elektronika, Olaj és gáz stb.) ISIC-szekció szintjén nem különíthető el, ezekhez nem fut él.
- **93 O\*NET-foglalkozás** („All Other” gyűjtőkategóriák) feladatlista nélkül van: a taxonómiában szerepel
  (`Besorolas:Onet`), munkakör-rekord nincs hozzá.
