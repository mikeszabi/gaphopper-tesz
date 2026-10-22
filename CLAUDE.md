# Gaphopper TESZ-felbontó – útmutató Claude Code-nak

## Cél
MUNKA-KÖR-ök felbontása elemi TESZ-nivalókra (egy TESZ = egy mikrotanúsítvány), mindegyik egy
TESZ-típussal és egy meghatározó triplettel ⟨SZER-ELEM, KÖZ-eg, HELY-zet⟩, majd betöltés a helyi neo4j-be.
A szakmai szabályok forrása a `prompts/p0_system.md` és a `config/vocab.yaml`; ezeket ne írd át
kérés nélkül, mert minden változás új `prompt_version`-t és újrafuttatást jelent.

## Dokumentáció és felület
A teljes konfigurációs referencia a [README.md](README.md); a napi munkamenet a
[futtatási segédlet](docs/Gaphopper%20TESZ%20–%20futtatási%20segédlet.md).
Az elsődleges UI: `python -m gaphopper ui`, http://localhost:8765 (`gaphopper/web.py` + `web/`).
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
- `python -m gaphopper show --job <id>` – köztes eredmények
- `python -m gaphopper review` – szakértői sor; `--approve <qid>` gráfba írja
- `python -m gaphopper stats`, `anchors`, `p4a --type ... --value ...`
- `python -m gaphopper onet-tasks <Task Statements> [--ratings <Task Ratings>]` – O*NET-feladatlisták a `tasks` mezőbe
- `python -m gaphopper embed check|backfill` – helyi Ollama beágyazás próbája, hiányzó vektorok pótlása
- `python -m gaphopper certify` – P5: igazolási mondat és rubrika a rubrika nélküli TESZ-ekhez
- `python -m gaphopper report` – tokenek, idő, költség promptverziónként; `models` – érvényes modellbeállítás
- `python -m gaphopper ui` – webes felület (http://localhost:8765; `gaphopper/web.py` + `web/`)
- tesztek: `python -m pytest -q`

## Kézi mód (amikor te vagy a modell)
Ha `GH_PROVIDER=manual`, a `run` a `runs/manual/<job>/` mappába írja a promptot.
1. Olvasd el a `P1_system.md` és `P1_user.md` fájlt, és pontosan a kért JSON-sémában válaszolj.
2. Mentsd a választ `runs/manual/<job>/P1_answer.json` néven.
3. `python -m gaphopper manual-submit --job <id> --step P1 --file runs/manual/<job>/P1_answer.json`
4. Ugyanígy a P2-vel (ha a szabályok jeleznek vagy mintába esik). A P2-nél légy szigorú, független bíráló:
   ne a saját P1-edet védd, és csak az eltéréseket add vissza.

## Kézi kinyerés (E0)
Kézi módban az `extract` a `runs/manual/extract/<fájlnév>/` mappába írja a részletek promptjait
(`E0_system.md`, `E0_<n>_user.md`). A választ `E0_<n>_answer.json` néven mentsd ugyanoda, és futtasd
újra ugyanazt az `extract` parancsot. Csak a szövegben szereplő adatot gyűjtsd ki, ne egészítsd ki.

## Szabályok munka közben
- Ne hozz létre külön csomópontot szinonimáknak: a nevek az elem `aliases` tulajdonságába kerülnek.
- A magyar név az elsődleges, az angol a megfeleltetés; régi gyökű magyar szót válassz az idegen helyett.
- Ha egy kimenet sémahibás, ne kerüld meg a validálást: javítsd a választ vagy a promptot.
- Promptváltoztatást előbb a tesztekkel és legalább 3 munkakörön ellenőrizz, és írd le, mi változott.

A fő UI nem kínál szolgáltatói batch módot vagy batch API-útvonalat; a normál futtatás modellfüggetlen.
A `batch` CLI régi kötegek kezelésére továbbra is elérhető.
