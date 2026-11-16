# Gaphopper kompetenciaszolgáltatások

Elkülönített, állapotmentes FastAPI alkalmazás saját böngészős felülettel. A meglévő generáló alkalmazást nem módosítja. A `gaphopper.config` ontológiáját és a `gaphopper.llm` JSON-ellenőrző rétegét használja; a pipeline-t, SQLite állapotot és a fejlesztői `config/models.yaml` fájlt nem tölti be. A munkakör- és kompetenciagráfot csak olvassa; a felhasználókat, munkameneteket és modellbeállításokat az elkülönített `GHServiceState` szolgáltatáscsomópontban tartja. Az elemzési kérés eredménye a válaszban él; a személyes térképeket nem menti automatikusan a közös törzsbe.

## Helyi indítás

A repository gyökeréből:

```bash
python -m pip install -r services/requirements.txt
# A már meglévő .env mellé add meg a szolgáltatás alapmodelljét, vagy válaszd ki a felületen.
python -m uvicorn services.api.main:app --env-file .env --host 127.0.0.1 --port 8080
```

Felület: http://localhost:8080 · Bejelentkezés: http://localhost:8080/login.html · Konfiguráció: http://localhost:8080/config.html · OpenAPI/Swagger: http://localhost:8080/docs · séma: http://localhost:8080/openapi.json

**Alapfelhasználó: `gaphopper`, jelszó: `gaphopper`, szerepkör: adminisztrátor.** Az első bejelentkezés létrehozza a szolgáltatásállapotot. A konfigurációs oldalon menthető az alapmodell, létrehozható adminisztrátor vagy normál felhasználó, módosítható a jelszó és a szerepkör, valamint letiltható egy fiók. Az utolsó aktív adminisztrátor nem tiltható le és nem fokozható le.

A szolgáltatás induláskor automatikusan betölti a projekt gyökerében lévő `.env` fájlt is; a már létező környezeti változókat nem írja felül. A `--env-file .env` továbbra is használható. A konfigurációs oldal jelzi a szolgáltatói kulcsok betöltöttségét, értéküket sosem adja vissza. Hiányzó modellnél az API `ai_model_missing`, hiányzó kulcsnál `ai_key_missing` kódot és konkrét teendőt ad.

A `services/.env.example` felsorolja a használt változókat. A Neo4j címe, felhasználója és jelszava a generáló alkalmazáséval egyezhet. Az AI-kulcsok csak a szerveren vannak. Az OpenAI szolgáltatás alapértelmezése a hivatalos `https://api.openai.com/v1`; a meglévő fejlesztői LAN-végpontot nem veszi át. Egyedi OpenAI-kompatibilis végponthoz szerveroldalon állítható `OPENAI_BASE_URL`.

A konfigurációs oldalon mentett szolgáltató és modell az elsődleges. Amíg nincs mentett beállítás, a gyökér `.env` fájlból betöltött `SERVICE_AI_PROVIDER` és `SERVICE_AI_MODEL` ad alapértéket. Minden elemzési kérésben felülírhatók:

```json
{"ai":{"provider":"anthropic","model":"AZ_ELÉRHETŐ_MODELL_AZONOSÍTÓJA"}}
```

A támogatott szolgáltatók: `anthropic` (Claude), `gemini`, `openai`. A konfigurációs oldalon a modellek az aktuális API-kulccsal lekért listából választhatók; nincs beégetett tartaléklista vagy kézi modellnév-megadás. A lista csak szöveges feldolgozásra alkalmasnak azonosított modelleket mutat. Mentéskor egy rövid, személyes adat nélküli JSON-generálási próba is lefut; ha nincs hozzáférés, kvóta, vagy a modell nem kompatibilis a használt generálási végponttal, a korábbi beállítás változatlan marad. A próba szolgáltatói díjat használhat. A listázás nem generál tartalmat. Alap kompetenciatérkép olvasásához és azonosító alapú összehasonlításhoz nem szükséges AI-hívás.

## API

| Metódus | Útvonal | Eredmény |
|---|---|---|
| GET | `/healthz` | Folyamat életjel, Neo4j nélkül is |
| GET | `/readyz` | Neo4j olvasási kapcsolat ellenőrzése |
| POST | `/api/v1/auth/login` | Bejelentkezés, HttpOnly munkamenet-cookie |
| GET | `/api/v1/auth/me` | Aktuális felhasználó |
| POST | `/api/v1/auth/logout` | Kijelentkezés |
| GET | `/api/v1/models?provider=gemini` | Élő API-modelllista, admin jogosultsággal |
| GET / PUT | `/api/v1/config` | Beállítások olvasása / adminisztrátori mentése |
| GET / POST | `/api/v1/admin/users` | Felhasználók listázása / létrehozása |
| PATCH | `/api/v1/admin/users/{username}` | Jelszó, szerepkör vagy aktív állapot módosítása |
| GET | `/api/v1/jobs?q=&offset=0&limit=50` | Munkakörkeresés és lapozás (limit ≤ 200) |
| GET | `/api/v1/competencies` | Neo4j kompetenciatörzs, kiválasztható azonosítókkal |
| POST | `/api/v1/job-maps` | Munkaköri kompetenciatérkép |
| POST | `/api/v1/comparisons` | Egyéni és munkaköri térkép összehasonlítása |
| POST | `/api/v1/career-suggestions` | SAFE, DEVELOP, ALTERNATE ajánlások |
| POST | `/api/v1/identify-job` | Munkakör azonosítása szövegből vagy dokumentumból |
| POST | `/api/v1/identify-competencies` | Személyes kompetenciák azonosítása szövegből vagy CV-ből |
| GET | `/api/v1/lab/jobs?q=` | Kompetencialabor: a felbontott (profillal bíró) munkakörök, tipikus EQF-szinttel |
| GET | `/api/v1/lab/competencies` | KÉSZ-ség-törzs kategóriával és csoporttal |
| POST | `/api/v1/lab/job-profiles` | Munkaköri profilok (legfeljebb 6): TESZ-SZÍN-ek, kompetenciák fontossággal és elvárt EQF-szinttel |
| POST | `/api/v1/lab/gap` | Egyén × munkakörök: kompetenciánkénti állapot, távolság, lefedettség, EQF-eltérés |
| POST | `/api/v1/lab/recommendations` | Minden felbontott munkakör rangsora az egyénhez, hiányokkal (opcionális EQF-ablak) |
| POST | `/api/v1/lab/identify-person` | Egyéni profil öt forrásból (explicit, implicit, munkakör, képzés, stílus), EQF-becsléssel |
| POST | `/api/v1/lab/space` | Kompetenciatér: 2D-elrendezés együttes előfordulás alapján, klaszterek, illeszkedés, horgonypontok |
| POST | `/api/v1/lab/identify-job` | Munkakör azonosítása dokumentumból, csak a felbontott munkakörök közül |

Munkaköri térkép:

```json
{
  "job_title": "ápoló",
  "tasks": ["Betegadatok rögzítése", "Kapcsolattartás a betegekkel"],
  "ai": {"provider": "gemini", "model": "AZ_ELÉRHETŐ_MODELL_AZONOSÍTÓJA"}
}
```

A `job_id` pontosan azonosít; a `job_title` név és alias alapján keres. Több egyezésnél 409 és választható azonosítók érkeznek. Ismeretlen explicit `job_id` 404. Egyedi cím és feladatlista, valamint önmagában feladatlista is elfogadott. Üres feladatlista hibás; a mező elhagyása az alapfeladatlistát jelenti. Az API mezőnevei kisbetűs snake_case alakban követik a specifikáció USER_JOB neveit.

A válasz `user_job_1_difference` mezője tartalmazza a hozzáadott, elhagyott és változatlan feladatokat. Az egyezés Unicode-normalizálás, kisbetűsítés és whitespace-egységesítés után, sorrendtől függetlenül értendő. Szemantikailag átfogalmazott feladatot eltérőnek tekintünk, és új térképet generálunk. A `changed: null` azt jelenti, hogy nem volt összehasonlítható alapfeladatlista.

A `user_job_1_triplets` a TESZ-nivaló × KÖZ-eg × HELY-zet kapcsolatokat tartalmazza. Generáláskor minden triplethez feladatindexek, kompetenciaazonosítók és indoklás tartozik. Minden bemeneti feladatot le kell fedni; az ontológiai és kompetenciaazonosítók ellenőrzöttek, hibás AI-válasz egyszer javíttatható. Az alap térkép a meglévő gráf TESZ–SZÍN csomópontjaira hivatkozik. A `user_job_1_competencies[].evidence` a tripletek azonosítóira mutat.

Összehasonlítás:

```json
{
  "job_id": "onet:29-1141.00",
  "user_competency_map": ["gh_c_1", "gh_c_2"]
}
```

A személyes térkép jelenleg kompetenciaazonosítók listája, nem szintértékelés. A listák ismétlései nem növelik az egyezést. Kimenet: `user_job_competency_matches`, `user_job_competency_missing`, `user_competency_extra`, valamint `coverage` (egyezők / elvárt kompetenciák). Feladatlista és AI-választás az előző kéréshez hasonlóan adható meg. A megadott kompetenciaazonosítóknak létezniük kell a Neo4j törzsben.

Karrierajánló:

```json
{
  "user_competency_map": ["gh_c_1", "gh_c_2"],
  "user_work_experience": [
    {"job_id": "onet:29-1141.00", "years": 3, "sector": "Egészségügy"}
  ],
  "per_category": 2,
  "ai": {"provider": "openai", "model": "AZ_ELÉRHETŐ_MODELL_AZONOSÍTÓJA"}
}
```

Minden feldolgozott, kompetenciákhoz kapcsolt munkakör részt vesz a rangsorolásban. A szűrés magyarázható heurisztika:

- **SAFE:** legnagyobb kompetencialefedettség, egyezésnél azonosító szerinti stabil sorrend. Korábbi munkakör is ajánlható.
- **DEVELOP:** a tapasztalati munkakörrel egyező munkakör, közös besorolási felmenő vagy közös szektor; legalább egy hiányzó kompetencia. SAFE munkakör nem ismétlődhet.
- **ALTERNATE:** nincs közös besorolás/szektor a tapasztalattal, de van legalább egy egyező kompetencia. SAFE munkakör nem ismétlődhet.

A közelség jelenleg legfeljebb öt `RESZE` élen elérhető közös besorolás vagy közös ágazat. Ez tág közelséget is jelenthet, nem kalibrált karriertávolság. A szolgáltató a teljes rangsor első SAFE elemeit kapja, DEVELOP és ALTERNATE kategóriánként a legjobb 12 jelöltből választ és indokol. A `years` az indoklás kontextusa, nem rejtett pontszorzó. A szolgáltatás ellenőrzi a kategóriák jelöltazonosítóit, darabszámát és az ismétlésmentességet. Ha nincs elég megfelelő jelölt, kevesebb ajánlás és figyelmeztetés érkezik. A hiányos taxonómia korlátozza a közelség megállapítását.

## Kompetencialabor (fő felület)

A http://localhost:8080 felülete öt funkciót ad. A felső sáv mutatja a kiválasztott munkaköröket (legfeljebb 6) és az egyéni profilt; a 3. és 4. funkció ezekből dolgozik. A kiválasztás és a profil csak a böngészőben marad meg (JSON-ként menthető és betölthető).

1. **Munkakör-profil:** munkakör listából, álláshirdetésből vagy szövegből (AI-azonosítás a felbontott munkakörök közül). A kompetenciák a TESZ-nivalókon keresztül, fontossággal és elvárt EQF-szinttel jelennek meg; több munkakörnél összevető mátrix.
2. **Egyéni profil:** CV-ből vagy szövegből; a kompetenciának nem kell szó szerint szerepelnie. Öt forrás (`services/api/person.py`):
   - **explicit:** a szöveg kimondja; **implicit:** a leírt tevékenységből, felelősségből következik (mindkettő szó szerinti idézettel);
   - **munkakör:** az AI kigyűjti a munkatapasztalatokat, és ha egy felbontott munkakörnek egyértelműen megfelel, a program a gráfprofil alap (CORE) TESZ-SZÍN-jei által igényelt kompetenciákat örökíti, az elvárt EQF-szinten (1 évnél rövidebb tapasztalatnál egy szinttel lejjebb; bizonyosság 0,35 + 0,35 × fontosság, 2 év felett +0,1); megfelelő munkakör nélkül az AI következtet;
   - **képzés:** a végzettségekből és képzésekből, a képzés EQF-szintjével;
   - **stílus:** a dokumentum felépítéséből és fogalmazásából, idézet nélkül is, legfeljebb 0,6 bizonyossággal, szintbecslés nélkül.

   Több forrásból jövő kompetencia egy sor: bizonyosság 1 − Π(1 − cᵢ), szint a nem stílus alapú források maximuma. Forrástípusonként ki-be kapcsolható, mi számítson a 3. és 4. funkcióba (pl. a korábbi munkakörökből örökölt kompetenciák nélkül). Minden szint kézzel módosítható, kompetencia kézzel is felvehető.
3. **Munkakör-ajánló:** az egyéni profil illeszkedése minden felbontott munkakörhöz; szórásdiagram (illeszkedés × EQF) és rangsor a legfontosabb hiányokkal.
4. **Illeszkedés és hiány:** távolság a kiválasztott munkakörökhöz, kompetencia-mátrix (✓ megvan, ▽ szint alatt, ✕ hiányzik) és munkakörönként részletes EQF-összevetés.
5. **Kompetenciatér:** a kiválasztott munkakörök és az egyén kompetenciái egy térben (`services/api/space.py`). Két kompetencia közel kerül, ha a TESZ-SZÍN-ek gyakran együtt igénylik (koszinusz-hasonlóság az összes felbontott munkakörön, 25% súllyal a KÉSZ-hierarchia mint előzetes); távolság: legrövidebb út a 6-szomszédos hasonlósági hálón; elrendezés: klasszikus MDS három dimenzióba; klaszterek: átlagos kapcsolású hierarchikus csoportosítás; klaszterszétválasztás: a klaszterközéppontok 2,2-szeresre tolódnak, a tagok 0,7-szeresre húzódnak a saját középpontjukhoz. Megjelenítés Three.js-sel (r160, helyben: `services/web/vendor/three/`, MIT): gömbök (méret = fontosság, szín = állapot a választott nézőpont-munkakörhöz), áttetsző klaszterburkok, a munkakörök és az egyén a kompetenciáik súlypontjában álló horgonypontok; klaszterenként illeszkedési sáv mutatja, hol sűrűsödnek a hiányok. Forgatás, nagyítás (görgő, két ujj), mozgatás; ütköző címkék elrejtése; WebGL nélkül táblázatos nézet.

A 3. és 4. funkció determinisztikus, AI-hívás nélkül számol a gráfból (`services/api/lab.py`):

- TESZ-SZÍN súlya: relevancia (CORE 1, SUPPORTING 0,5) × gyakoriság (DAILY 1, WEEKLY 0,85, MONTHLY 0,7, RARE 0,5).
- Munkakör EQF-szintje: a `TARTALMAZ_TESZT.expected_eqf` értékek súlyozott mediánja; kompetencia elvárt szintje: az őt igénylő CORE TESZ-SZÍN-ek legmagasabb EQF-je.
- Hiány: (elvárt − saját) / elvárt, hiányzó kompetenciánál 1; távolság = a hiányok súlyozott átlaga, illeszkedés = 1 − távolság.

Csak a felbontott munkakörök (a gráfban `elozetes` jelölés nélkül) vesznek részt; az előzetes, csak besorolt munkakörökhöz nincs profil.

## Feladatlista-pillanatkép

A jelenlegi gráf `MunkaKor` csomópontjai nem tárolják a nyers feladatlistát. A szolgáltatás ezért `job_id` alapján a külön exportált `services/data/jobs.json` állományból olvassa az alapfeladatokat, a kompetenciatérképet pedig a Neo4j-ből. Az export 923 munkakört tartalmaz; frissíteni kell, amikor az alap katalógus változik:

```bash
python -m services.export_catalog
```

A pillanatkép nem a Neo4j élő feladatlista-verziója. A gráf és a katalógus tartalmi összhangját kiadáskor biztosítani kell. Hiányzó alaplista esetén a rendszer ezt jelzi; egyedi feladatlista esetén generál, nem feltételez egyezést. Meglévő alap kompetenciatérképet feladatlista nélkül továbbra is visszaad.

## Konténer és Google Cloud Run

A repository gyökeréből:

```bash
docker build -f services/Dockerfile -t gaphopper-services:local .
docker run --rm --env-file .env -p 8080:8080 gaphopper-services:local
```

Konténerből a `localhost` a konténert jelenti. Helyi Neo4j-hez állíts be elérhető hostcímet vagy közös Docker-hálózatot; Linuxon használható a `--add-host=host.docker.internal:host-gateway` és `NEO4J_URI=bolt://host.docker.internal:7687` páros.

A Docker build kontextus engedélylistás: a gyökér `.env`, a generálási futások, a SQLite fájlok és a helyi modellkonfiguráció nem kerülnek a képbe. A folyamat nem rootként fut, a `PORT` változót követi. Nincs kötelező helyi írás vagy háttérfolyamat. Az API és a felület egy originről működik, nincs szükség külön CORS-konfigurációra.

**A Neo4j tartós adatbázist ne ugyanebben a Cloud Run konténerben futtasd.** A Cloud Run példányok fájlrendszere nem tartós. Az alkalmazás Cloud Run-ra kerülhet; a gráfhoz Neo4j AuraDB a Google Cloudon, vagy külön Compute Engine/GKE alapú tartós Neo4j szükséges.

Források: [Cloud Run futtatási szerződés](https://docs.cloud.google.com/run/docs/container-contract), [Neo4j felhős telepítési lehetőségek](https://neo4j.com/docs/operations-manual/current/cloud-deployments/).

Telepítési előkészítés:

1. Hozz létre cél Neo4j példányt. A migrációt a választott Neo4j verzióhoz és célhoz illeszkedő backup/import eljárással végezd, majd hasonlítsd össze a munkakörök, TESZ–SZÍN-ek, kompetenciák és kapcsolatok darabszámát. Az alkalmazás nem migrálja vagy írja felül automatikusan az adatbázist.
2. Engedélyezd a Cloud Run, Artifact Registry és Secret Manager API-kat a célprojektben. Készíts Docker repositoryt és külön futtatási service accountot.
3. A Neo4j-jelszót és az AI-kulcsokat tárold Secret Managerben; a futtatási service account a szükséges titkokra kapjon `roles/secretmanager.secretAccessor` szerepet. A `deploy/cloud-run.yaml` mindhárom szolgáltatói titkot hivatkozza; csak a ténylegesen használt kulcsok hivatkozásait tartsd meg.
4. A munkakör- és kompetenciagráf lekérdezései olvasók. A bejelentkezéshez és adminisztrációhoz a Neo4j fióknak a `GHServiceState` csomópontra írási jog, az első használathoz egyediségkényszer-létrehozási jog is szükséges. Az egyediségkényszert üzemeltetőként előre is létrehozhatod: `CREATE CONSTRAINT gh_service_state_id IF NOT EXISTS FOR (n:GHServiceState) REQUIRE n.id IS UNIQUE`. Aura esetén a kapott `neo4j+s://...` URI-t használd. Privát VM/GKE esetén Cloud Run VPC-kapcsolat és megfelelő tűzfalszabály szükséges.
5. Építsd és töltsd fel a képet, majd a YAML `REPLACE_...` értékeit cseréld ki konkrét képre, service accountra, Neo4j URI-ra és modellre. Telepítéskor lehetőleg a képdigestet rögzítsd.

Példa parancsok (helyőrzők kitöltése után, a repository gyökeréből):

```bash
docker build -f services/Dockerfile -t REGION-docker.pkg.dev/PROJECT/REPOSITORY/gaphopper-services:VERSION .
docker push REGION-docker.pkg.dev/PROJECT/REPOSITORY/gaphopper-services:VERSION
gcloud run services replace services/deploy/cloud-run.yaml --region REGION --project PROJECT
# Privát Cloud Run szolgáltatás helyi elérése a saját IAM-jogosultságoddal:
gcloud run services proxy gaphopper-services --port 8080 --region REGION --project PROJECT
```

Az IAM-hozzáférést a célprojektben külön ellenőrizd: az új szolgáltatás maradjon privát, az invoker jogokat célzottan add meg. Az API-k bejelentkezett felhasználót igényelnek. Automatizált elemzési hívásokhoz opcionálisan `SERVICE_API_TOKEN` Bearer-token állítható be; ez nem jogosít fel felhasználókezelésre vagy konfiguráció módosítására. Az admin API-k kizárólag adminisztrátori munkamenettel működnek. A statikus oldalak és az OpenAPI séma nyilvánosan olvashatók, üzleti adatot csak hitelesítve adnak az API-k. A felhős YAML alapból nem ad publikus hozzáférést; egy már létező szolgáltatás IAM-beállításait viszont a YAML lecserélése nem állítja vissza.

A YAML 600 másodperces kéréshatárt, példányonként 4 párhuzamos kérést és legfeljebb 3 példányt állít be. A hosszú elemzések szinkron HTTP-kérések: a kliensbontás nem garantálja a szolgáltatói hívás megszakítását. Nagy terheléshez tartós feladatsor és eredménytár további fejlesztésként szükséges. A CV, álláshirdetés, személyes kompetenciák és munkatapasztalat a kiválasztott AI-szolgáltatóhoz kerülhet; az alkalmazás ezeket nem naplózza és nem menti a default gráfba.

## Ellenőrzés

```bash
python -m pip install pytest httpx
python -m pytest tests services/tests -q
node --check services/web/app.js
```

A szolgáltatástesztek fake adatbázissal és fake AI-val futnak, fizetős hívás nélkül. Ellenőrzik a feladatlista-eltérést, az alap térkép újrafelhasználását, az egyedi térképet, a halmazalapú összehasonlítást, a kategóriákat, a bemeneti hibákat és a hozzáférésvédelmet. Az élő AI-minőséget és a hiányos gráfadatok hatását külön, kiválasztott valós munkakörökön kell értékelni.


## Bejelentkezés és tartós konfiguráció

A fő felületről az adminisztrátori **Konfiguráció** hivatkozás nyitja a külön beállítási oldalt. Az alapmodellt itt állítsd be; a fő munkafelületen csak az aktív modell neve jelenik meg. Programozott API-hívásban az `ai` mező továbbra is lehet kérésenkénti felülírás.

A jelszavak egyedi sóval, scrypt hashként tárolódnak. A véletlen munkamenet-tokenből csak SHA-256 hash kerül az adatbázisba; a böngésző HttpOnly, SameSite=Strict cookie-t kap, nyolcórás lejárattal. A jelszó vagy jogosultság módosítása az érintett összes munkamenetét visszavonja. Felhasználónként legfeljebb 10 munkamenet marad aktív; 10 hibás belépés után ötperces zárolás lép életbe. Módosító, cookie-alapú hívásoknál a `X-Gaphopper-Client: web` fejléc kötelező; a beépített felület ezt elküldi.

Az első állapotlétrehozásnál a `SERVICE_BOOTSTRAP_PASSWORD` felülírhatja az alap `gaphopper` jelszót. Később a jelszó a felhasználókezelőben módosítható; a környezeti változó nem írja felül a már létező fiókot. A `SERVICE_COOKIE_SECURE=1` HTTPS-re korlátozza a cookie-t; Cloud Runon ez automatikusan bekapcsol a `K_SERVICE` változó alapján, a telepítési YAML is rögzíti. Helyi HTTP-futtatáshoz hagyd kikapcsolva. Cloud Run proxyval végzett helyi tesztnél is vedd figyelembe a HTTPS-cookie követelményt.

A `GHServiceState {id:'main'}` csomópont csak a szolgáltatás felhasználóit, hash-eit, munkameneteit, belépési hibaszámlálóit és alapmodelljét tárolja JSON-ként. A módosítások Neo4j tranzakciós zárral sorosítottak, ezért több Cloud Run példány ugyanazt az állapotot látja. Ez kis és közepes tesztterheléshez készült; nagy felhasználószámhoz külön identitásszolgáltató és dedikált session store célszerű. A csomópontot és egyediségkényszerét a Neo4j migrációval együtt kell átvinni, ha a felhasználókat és a beállításokat is meg akarod tartani. A felhasználókezelés nem használ helyi JSON- vagy SQLite fájlt.

## Dokumentumos azonosító API-k

Mindkét végpont ugyanazt a két bemeneti módot fogadja:

- `application/json`: `{"text":"Legalább húsz karakteres dokumentumszöveg…", "ai":{"provider":"gemini","model":"MODELL"}}`. Az `ai` elhagyható, ekkor a konfigurációs oldal alapmodellje érvényes.
- `multipart/form-data`: `file` fájlmező **vagy** `text` szövegmező, opcionálisan `provider` és `model` együtt. Pontosan egy szöveget vagy egy fájlt adj meg.

Támogatott formátumok: szöveges PDF, DOCX (bekezdések és táblázatok), TXT, MD, HTML. Fájlméret: legfeljebb 8 MB; kinyert szöveg: 20–60 000 karakter; PDF: legfeljebb 80 oldal. A túl hosszú szöveget nem csonkítjuk észrevétlenül, hanem 413 hibával jelezzük. Szkennelt PDF-hez előzetes OCR kell. Sérült/jelszóvédett dokumentum 422, nem támogatott formátum 415 választ ad. A feltöltött fájlt és a kinyert szöveget nem őrizzük meg; a HTTP-keretrendszer a kérés feldolgozása közben ideiglenes fájlt használhat, amit a kérés végén bezár.

A `/api/v1/identify-job` a Neo4j munkakörcímekből, angol címekből és aliasokból választ legfeljebb három találatot, és kinyeri a leírt munkaköri feladatokat. A visszaadott címek mindig az adatbázisból származnak. A `selected_job_id` a legnagyobb AI-bizonyosságú javaslat, nem szakértőileg jóváhagyott besorolás. A felületen minden jelölt külön átvehető kompetenciatérkép-készítéshez, a kinyert feladatokkal együtt.

A `/api/v1/identify-competencies` a teljes Neo4j kompetenciatörzsből keres, és visszaadja a későbbi összehasonlításba vagy karrierajánlóba közvetlenül átadható `user_competency_map` azonosítólistát. Nem feltételezi automatikusan egy korábbi munkakör minden kompetenciájának meglétét.

Mindkét válaszban a `matches` elemei: `id`, adatbázisból feloldott `label`, `confidence`, `evidence` és `reason`. Az `evidence` rövid forrásidézetek listája; az API ellenőrzi, hogy az idézet ténylegesen szerepel-e a bemenetben, az azonosító létezik-e a megfelelő adatbázisban, és nincs-e ismétlés. A confidence AI-becslés, nem kalibrált valószínűség. Megalapozott találat hiányában üres lista és figyelmeztetés érkezik.

Példa helyi belépésre és dokumentumküldésre (a fájlnév a saját dokumentumodra cserélendő):

```bash
curl -c /tmp/gaphopper-cookies.txt -H 'Content-Type: application/json' -H 'X-Gaphopper-Client: web' \
  -d '{"username":"gaphopper","password":"gaphopper"}' http://localhost:8080/api/v1/auth/login
curl -b /tmp/gaphopper-cookies.txt -H 'X-Gaphopper-Client: web' \
  -F 'file=@cv.pdf' http://localhost:8080/api/v1/identify-competencies
curl -b /tmp/gaphopper-cookies.txt -H 'Content-Type: application/json' -H 'X-Gaphopper-Client: web' \
  -d '{"text":"Álláshirdetés: betegadatok rögzítése és betegekkel való kapcsolattartás."}' \
  http://localhost:8080/api/v1/identify-job
```

A TESZ-felbontó webes és opcionális Streamlit modelloldala is ugyanazt a `gaphopper.model_catalog` modult használja. A korábban mentett vagy árlistában szereplő modellneveket nem keveri az elérhető modellek közé. Új listázáshoz vagy a végpont módosítása után frissítés szükséges; sikertelen listázás üres választékot és hibajelzést ad. A szolgáltatói hozzáférés és kvóta a sikeres próba után is változhat.
