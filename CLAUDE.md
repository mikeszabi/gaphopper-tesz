# Gaphopper TESZ-felbontó – útmutató Claude Code-nak

## Cél
MUNKA-KÖR-ök felbontása általános TESZ-nivalókra (v2: munkaminta × SZER-ELEM-fajta + ERED-mény; legfeljebb 6
szó, szakterület-, célcsoport-, KÖZ-eg- és HELY-zet-semleges; 2–5 KÉSZ-ség-törzselem a `config/kesz_torzs.yaml`-ból (a gráfban súlyozva: hány munkakör igényli);
a munkaköri specifikum a `kontextus_hu`-ba) és azok SZÍN-ezett változataira, a TESZ-SZÍN-ekre (TESZ-nivaló × SZÍN ⟨KÖZ-eg, HELY-zet⟩; egy
TESZ-SZÍN = egy mikrotanúsítvány), majd betöltés a helyi neo4j-be. Minden TESZ-nivalónak pontosan egy ALAP-SZÍN-e
van; további változat csak az M2 (új/szigorúbb kritérium) és M3 (rendszeres) próbával. A tervezés:
`docs/TESZ-MAG es SZIN - granularitas.md` (7. fejezet: a felhasználó döntései, kötelezők).
A szakmai szabályok forrása a `prompts/p0_system.md` és a `config/vocab.yaml`; ezeket ne írd át
kérés nélkül, mert minden változás új `prompt_version`-t és újrafuttatást jelent.

## Dokumentáció és felület
A teljes konfigurációs referencia a [README.md](README.md); a napi munkamenet a
[futtatási segédlet](docs/Gaphopper%20TESZ%20–%20futtatási%20segédlet.md).
Az elsődleges UI: `python -m gaphopper ui`, http://localhost:8765 (`gaphopper/web.py` + `web/`).
A KÉSZ-ség, KÉSZ-ség-sor és Karbantartás oldal a `gaphopper/web_kesz.py`-ban van; írni csak CLI-parancson át írnak.
A szakértői kérések magyarázata (UI és `review` CLI) a `gaphopper/review_view.py`-ban, a szabálynevek a
`config/szabalyok.yaml`-ban vannak; új szabálynál ide is vedd fel a nevét és egymondatos jelentését.
A felhasználói leírás a `docs/hasznalati-segedlet.md` (a UI Használati segéd fülje ezt rendereli, képek: `docs/img/`);
új funkciónál ezt is frissítsd, a szabálytáblát a `review_view.help_markdown(sync=True)` írja.
A Streamlit-fájlok régi, opcionális felületet alkotnak. Új funkciót az elsődleges UI-ban vezess át.
Modellkonfiguráció: `config/models.yaml` → `.env` → kódalapérték; `models` mutatja az effektív értékeket.
A fő felület szolgáltatói: anthropic, gemini, openai (külön kapcsolati próbával), manual.
A local csak régi CLI-konfigurációhoz marad; a fő UI-n nincs helyi LLM-panel. Az Ollama embed megmarad.
A Modellek oldal API-ból kér modelllistát, beágyazást ellenőriz és egységárakat ment;
a Futás oldal naplózott tokeneket és becsült API-költséget mutat. Kulcsértéket ne ments a YAML-ba.

## Parancsok
- `python -m gaphopper init` – séma és szótárak
- `python -m gaphopper extract <fájl|mappa> --out data/jobs.jsonl [--import]` – 0. lépés: dokumentumokból munkakör-rekordok
- `python -m gaphopper batch submit|status|collect --step P1|P2` – kötegelt futtatás; `run --workers N` – párhuzamos
- `python -m gaphopper import <fájl>` – munkakörök beolvasása
- `python -m gaphopper run --job <id> [--until p1|p2]` – feldolgozás
- `python -m gaphopper run --dry-run --job <id> [--input f.jsonl]` – próbafutás: LLM-lépések lefutnak, a gráf
  és a state.sqlite nem változik; kimenet `runs/dryrun/<job>.json` (a fő UI Futás oldalán kapcsolóként is)
- `python -m gaphopper db-reset --yes [--graph-only]` – mentés (`runs/backup-<dátum>/`), majd neo4j és state.sqlite
  ürítése; `--graph-only`: csak a gráf, a P1/P2-eredmények maradnak (újraépítés LLM nélkül, csak P3); utána `init`
- `python -m gaphopper show --job <id>` – köztes eredmények
- `python -m gaphopper review` – szakértői sor; `--approve <qid>` gráfba írja
- `python -m gaphopper stats`, `anchors`, `p4a --type ... --value ...`
- `python -m gaphopper onet-tasks <Task Statements> [--ratings <Task Ratings>]` – O*NET-feladatlisták a `tasks` mezőbe
- `python -m gaphopper embed check|backfill` – helyi Ollama beágyazás próbája, hiányzó vektorok pótlása
- `python -m gaphopper generalize [--dry-run]` – P6 törzskarbantartás: a több munkakörben, eltérő kontextussal
  használt TESZ-nivalók túl szűk nevének általánosítása (a régi név alias), gyűjtő-nevek bontási javaslata (szakértőhöz)
- `python -m gaphopper split [--review QID]` – jóváhagyott bontási javaslat végrehajtása (szakértői sor: split_review)
- `python -m gaphopper kesz-irany [--dry-run]` – KÉSZ-ségek irányultsága (KÖZ-eg, KOZ_EGYEN = egy-ÉN) elemenként, Haiku
- `python -m gaphopper kesz-bovit [--dry-run | --from-dryrun f.json ...]` – KÉSZ-ség-törzs bővítése a P1 `kesz_hiany`
  jelöltjeiből (ALIAS azonnal; UJ szakértői sorba, legalább 2 munkakörnél; jóváhagyás: gh_c_292-től yaml + gráf)
- `python -m gaphopper certify` – P5: TESZ-nivaló-rubrika (`rubric_alap_hu`), változatonként igazolás + SZÍN-rubrika (`rubric_szin_hu`)
- `python -m gaphopper report` – tokenek, idő, költség promptverziónként; `models` – érvényes modellbeállítás
- `python -m gaphopper ui` – webes felület (http://localhost:8765; `gaphopper/web.py` + `web/`)
- tesztek: `python -m pytest -q`

## Kézi mód (amikor te vagy a modell)
Ha `GH_PROVIDER=manual`, a `run` a `runs/manual/<job>/` mappába írja a promptot.
1. Olvasd el a `P1_system.md` és `P1_user.md` fájlt, és pontosan a kért JSON-sémában válaszolj
   (`nivalok` + `szinek`; a változat nem ismétli a TESZ-nivaló mezőit).
2. Mentsd a választ `runs/manual/<job>/P1_answer.json` néven.
3. `python -m gaphopper manual-submit --job <id> --step P1 --file runs/manual/<job>/P1_answer.json`
4. Ugyanígy a P2-vel (ha a szabályok jeleznek vagy mintába esik). A P2-nél légy szigorú, független bíráló:
   ne a saját P1-edet védd, és csak az eltéréseket add vissza.

## Kézi kinyerés (E0)
Kézi módban az `extract` a `runs/manual/extract/<fájlnév>/` mappába írja a részletek promptjait
(`E0_system.md`, `E0_<n>_user.md`). A választ `E0_<n>_answer.json` néven mentsd ugyanoda, és futtasd
újra ugyanazt az `extract` parancsot. Csak a szövegben szereplő adatot gyűjtsd ki, ne egészítsd ki.

## Szabályok munka közben
- TESZ-nivaló-szintű mezők (`tesz_type`, `primary_szer`, `outcome_hu`, `relevance`, `kor_id`) csak a TESZ-nivalón élnek; a
  változatot a séma tölti ki (`schemas.link_nivalok`). A változat kiléte a gráfban a ⟨TESZ-nivaló, KÖZ-eg, HELY-zet⟩.
- A P3 LLM csak TESZ-nivaló- és SZER-ELEM-egyezésről dönt; a SZÍN-egyezés determinisztikus (`merge.py`).
- Gráfírás csak `WritePlan` + `graph.write_plan()` úton (UNWIND, egy tranzakció); soronkénti lekérdezést ne vezess be.
- A P1 rendszerpromptja: P0 + KÉSZ-ség-törzs | TESZ-törzs (`llm.CACHE_SPLIT` két cache-blokk); törzs-TESZ-nivalónál a
  P1 csak `nivalo_ref`-et ír (`Context.resolve_mag_refs`), a P3 hívás nélkül oldja fel. A törzs csak az aktuális
  szótárverzióval (`TeszNivalo.gen`) készült TESZ-nivalókból áll.
- A meghatározó SZER-ELEM fajta szintű; a szűkebb fajta a TESZ-SZÍN kísérő SZER-ELEM-e (`fajtaja: true` -> `FAJTAJA` él).
  Törzs-választásnál a P1 megadja a saját SZER-ELEM-fajtáját (`szer`); ha eltér a törzselemétől, átnézésre kerül.
- A KÉSZ-ség-törzsnek nincs típusmezője (TUD/KÉP/HOZZÁ-állás állapot, nem fajta); a hierarchia kategória → csoport →
  elem, az 1. tengely az irány (`irany`), a 2. (TESZ-típus-család, hordozhatóság) a gráfból számolt (`update_kesz_profil`).
  A `kesz_torzs.yaml`-t a `config.save_kesz_torzs` írja (soronként egy elem); kézi szerkesztésnél ezt az alakot tartsd.
- Szabályok: R1–R21 és R23 (`rules.py`; R23 = `kesz_hiany_guard`; G1–G5: R4, R17, R18, R20, R21; törzshasználat: R19; R17-hez nincs szólista), küszöbök a `vocab.yaml` `rules` részében; a zárt listák egyértelmű
  eltérései az `alias_ertek` táblában (új tételt csak egyértelmű megfeleltetésnél vegyél fel).
- Ne hozz létre külön csomópontot szinonimáknak: a nevek az elem `aliases` tulajdonságába kerülnek.
- A magyar név az elsődleges, az angol a megfeleltetés; régi gyökű magyar szót válassz az idegen helyett.
- Ha egy kimenet sémahibás, ne kerüld meg a validálást: javítsd a választ vagy a promptot.
- Promptváltoztatást előbb a tesztekkel és legalább 3 munkakörön ellenőrizz, és írd le, mi változott.

A fő UI nem kínál szolgáltatói batch módot vagy batch API-útvonalat; a normál futtatás modellfüggetlen.
A `batch` CLI régi kötegek kezelésére továbbra is elérhető.
