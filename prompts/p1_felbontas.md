FELADAT (P1): Bontsd fel az alábbi MUNKA-KÖR-t elemi TESZ-NIVALÓKRA.

MUNKAMENET (ebben a sorrendben):
1. FELADATKÖRÖK: írd le a MUNKA-KÖR célját egy mondatban, és határozz meg 4-8
   feladatkört (TESZ-kört) becsült munkaidő-aránnyal (összesen 90-110%). A feladatkör
   rendező szint, nem TESZ-nivaló.
2. TESZ-NIVALÓK: feladatkörönként 2-5, összesen 12-25 TESZ-nivaló. Forrás: tasks,
   description, requirements, conditions, és a szakma általánosan ismert gyakorlata
   a megadott locale-ban. Ne képezz TESZ-t általános mintából ("a munka tervezése",
   "dokumentálás"); csak MUNKA-KÖR-specifikus példányából ("ápolási dokumentáció
   vezetése elektronikus rendszerben").
3. ÖNELLENŐRZÉS (fejben): minden TESZ-t vigyél át a hat EGY-próbán és az igazolási
   mondat-próbán. Ami elbukik: bontsd, vond össze vagy hagyd el. A készség jellegű
   elemeket ("kommunikáció", "problémamegoldó képesség") tedd a skill_mentions listába.
4. TESZ-TÍPUS: az ERED-mény fajtája alapján, egy runner-up típussal.
5. SZER-ELEM: egy meghatározó (definícióval, legfeljebb egy rövid mondat) és 0-4 kísérő
   (definíció nélkül), fajtával és szereppel. Csak ami ténylegesen befolyásolja a
   teljesítést vagy az értékelést.
6. KÖZ-EG ÉS HELY-ZET: egy domináns KÖZ-eg (+0-2 kísérő) és egy elsődleges HELY-zet
   (+0-1 másodlagos). A kísérő SZER-ELEM-ek SZÍN-e alapértelmezés szerint a TESZ-é;
   kozeg/helyzet mezőt csak eltérésnél adj meg nekik.
7. MUNKAKÖRI SÚLY: relevance kizárólag CORE vagy SUPPORTING lehet; más érték
   (például PERIPHERAL) tilos. CORE, ha a MUNKA-KÖR nélküle nem az, ami (8-15 CORE);
   SUPPORTING, ha támogató vagy járulékos feladat.
   frequency, és expected_eqf (1-8) az elvárt önállóság és felelősség alapján.
8. MUNKAKÖR-NEVEK: add meg a munkakör magyar és angol preferált nevét, és a bemenetben
   látott vagy szokásos alternatív neveit (title_aliases), nyelvjelöléssel.

TÖMÖRSÉG ÉS KIMENETI KÖLTSÉG:
- Tömör JSON-t írj, behúzás és formázó sortörések nélkül. A szövegekben szükséges
  szóközöket tartsd meg. Ne írj munkamenetet, elemzést vagy magyarázó szöveget.
- Az outcome_hu egy rövid mondat a megfigyelhető eredménnyel és minőségi küszöbbel;
  ne ismételje meg a TESZ címét. A rationale_hu célhossza 8-12 szó (legfeljebb 20):
  csak a besorolás döntő okát írd, ne magyarázd újra a teljes triplettet.
- A primary_szer.definition_hu egyetlen rövid megkülönböztető mondat;
  a kísérő SZER-ELEM-ekhez definition_hu mezőt ne írj.
- Üres listát és null értéket ne írj ki: az alábbi mezők elhagyhatók, ha üresek:
  title_aliases, skill_mentions, secondary_szer, kozeg_secondary,
  helyzet_secondary, open_question. A hiányzó értékek listánál [], egyébként null.
  Ha van tartalmuk, add meg őket; a bizonytalanságot ne rejtsd el.
- A kísérő SZER-ELEM kozeg/helyzet mezője csak a TESZ-től való eltérésnél kell.
- Igazolási mondatot, rubrikát és kudarcmódot NE írj: ezek a P5-höz tartoznak.
- A tömörség nem csökkentheti a szakmai lefedettséget: a 12-25 TESZ és a hat
  EGY-próba követelménye változatlan. A magyar és angol címkéket is add meg.

BEMENET:
{{job_record_json}}

KIMENET (csak tömör JSON; az alábbi minta olvashatóság miatt tördelt):
{
  "job_id": "...",
  "job_label_hu": "...", "job_label_en": "...",
  "title_aliases": [{"text": "...", "lang": "hu|en"}],
  "purpose_hu": "...",
  "eqf_band": {"min": 0, "max": 0, "rationale_hu": "..."},
  "tesz_korok": [{"kor_id": "K1", "name_hu": "...", "time_share_pct": 0}],
  "tesz": [
    {"cand_id": "C01", "kor_id": "K1",
     "label_hu": "...", "label_en": "...",
     "outcome_hu": "...",
     "tesz_type": "VERIFY", "tesz_type_runner_up": "OBSERVE_MEASURE",
     "primary_szer": {"label_hu": "...", "label_en": "...", "kind": "RULE",
                      "role": "GUIDED_BY", "definition_hu": "..."},
     "secondary_szer": [{"label_hu": "...", "label_en": "...", "kind": "TOOL", "role": "USES"}],
     "kozeg": "KOZ_FIZIKAI",
     "helyzet": "HELY_KRITIKUS",
     "relevance": "CORE", "frequency": "DAILY",
     "expected_eqf": 5,
     "rationale_hu": "...",
     "confidence": 0.8}
  ],
  "skill_mentions": [{"text": "...", "related_cand_ids": ["C03"]}]
}
