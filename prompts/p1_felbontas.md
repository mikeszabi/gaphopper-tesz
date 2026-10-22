FELADAT (P1): Bontsd fel az alábbi MUNKA-KÖR-t TESZ-MAG-okra és azok SZÍN-ezett változataira
(TESZ-nivalókra).

MUNKAMENET (fejben, ebben a sorrendben; csak a JSON-t írd ki):
1. FELADATKÖRÖK: a munkakör célja egy mondatban; 4-8 feladatkör (kor_id) becsült
   munkaidő-aránnyal (összesen 90-110%). A feladatkör rendező szint, nem TESZ.
2. TESZ-MAG-OK: feladatkörönként 2-4, összesen 10-18 MAG (ebből 8-15 CORE). Forrás: tasks,
   description, requirements, conditions és a szakma ismert gyakorlata a locale-ban. Általános
   mintából ne képezz MAG-ot, csak munkakör-specifikus példányából. A MAG címkéje SZÍN-semleges.
   A MAG-on: tesz_type (az ERED-mény fajtája szerint) és runner_up, primary_szer (definíció egy
   rövid mondat), outcome_hu (a közös ERED-mény egy mondatban, minőségi küszöbbel, a címke
   ismétlése nélkül), relevance (CORE, ha a munkakör nélküle nem az, ami; különben SUPPORTING).
3. SZÍN-EZÉS: minden MAG-hoz pontosan egy ALAP-SZÍN változat (alap_szin true): a munkakörben
   leggyakoribb ⟨KÖZ-eg, HELY-zet⟩. További változatot (alap_szin false) csak akkor adj, ha
   (M2) legalább egy új vagy szigorúbb értékelési kritériumot hoz, és (M3) ebben a munkakörben
   rendszeresen előfordul; MAG-onként legfeljebb 3 változat, összesen 12-25 TESZ-nivaló.
   A nem ALAP-SZÍN változat label_hu = MAG-címke + SZÍN-jelző (pl. "... sürgősségi helyzetben"),
   és szin_kulonbseg_hu: egy mondat arról, mitől nehezebb vagy más, mint az ALAP-SZÍN.
   Ami nem éri el az M2 küszöböt, az kísérő SZÍN: kozeg_secondary (0-2) / helyzet_secondary (0-1).
4. MÉLYÍTÉSI JELÖLT: ha egy CORE MAG-nak van a szakmában ismert nehezebb SZÍN-e, amely ebben a
   munkakörben NEM jellemző, add meg a MAG melyites_jeloltek listájában (legfeljebb 1; nem TESZ!).
5. ÖNELLENŐRZÉS: T1-T6 a változatokra, M1 a MAG-okra. Ha két MAG csak KÖZ-egben vagy HELY-zetben
   tér el: egy MAG két változata. Ha egy változat ERED-ménye vagy meghatározó SZER-ELEM-e eltér:
   külön MAG. Készség jellegű elem a skill_mentions listába.
6. VÁLTOZATONKÉNT: kozeg, helyzet, frequency, expected_eqf (1-8), confidence; 0-4 kísérő
   SZER-ELEM (secondary_szer, definíció nélkül; kozeg/helyzet csak eltérésnél) – a közegfüggő
   eszközök itt vannak.
7. MUNKAKÖR-NEVEK: magyar és angol preferált név, a látott vagy szokásos alternatív nevek
   (title_aliases) nyelvjelöléssel.

TÖMÖRSÉG:
- Tömör JSON, behúzás és sortörés nélkül. Ne írj elemzést, indoklást, igazolási mondatot vagy
  rubrikát (ezek a P5-höz tartoznak).
- A változat NEM ismétli a MAG mezőit (tesz_type, primary_szer, outcome_hu, relevance, kor_id).
  Az ALAP-SZÍN változatnál a label_hu/label_en elhagyható (a MAG-címke érvényes).
- Üres listát, null értéket és alapértéket ne írj ki (title_aliases, skill_mentions,
  secondary_szer, kozeg_secondary, helyzet_secondary, melyites_jeloltek, open_question).
- A tömörség nem csökkentheti a lefedettséget: a darabszámok és a próbák kötelezők.

BEMENET:
{{job_record_json}}

KIMENET (csak tömör JSON; a minta olvashatóság miatt tördelt):
{"job_id":"...","job_label_hu":"...","job_label_en":"...",
 "title_aliases":[{"text":"...","lang":"hu|en"}],
 "purpose_hu":"...","eqf_band":{"min":4,"max":5,"rationale_hu":"..."},
 "tesz_korok":[{"kor_id":"K1","name_hu":"...","time_share_pct":30}],
 "tesz_magok":[
  {"mag_id":"M01","kor_id":"K1","label_hu":"Gyógyszer beadása orvosi rendelés szerint",
   "label_en":"Administer medication as prescribed","outcome_hu":"...",
   "tesz_type":"EXECUTE","runner_up":"COMPLY",
   "primary_szer":{"label_hu":"Gyógyszerelési rendelés","label_en":"Medication order","kind":"RULE","role":"GUIDED_BY","definition_hu":"..."},
   "relevance":"CORE",
   "melyites_jeloltek":[{"kozeg":"KOZ_FIZIKAI","helyzet":"HELY_KRITIKUS","indok_hu":"..."}]}],
 "tesz":[
  {"cand_id":"C01","mag_id":"M01","alap_szin":true,"kozeg":"KOZ_FIZIKAI","helyzet":"HELY_RUTIN",
   "secondary_szer":[{"label_hu":"...","label_en":"...","kind":"TOOL","role":"USES"}],
   "frequency":"DAILY","expected_eqf":4,"confidence":0.8},
  {"cand_id":"C02","mag_id":"M01","alap_szin":false,
   "label_hu":"Gyógyszer beadása orvosi rendelés szerint állapotromlás idején",
   "label_en":"Administer medication as prescribed during deterioration",
   "kozeg":"KOZ_FIZIKAI","helyzet":"HELY_VALTOZO","szin_kulonbseg_hu":"...",
   "frequency":"WEEKLY","expected_eqf":5,"confidence":0.7}],
 "skill_mentions":[{"text":"...","related_cand_ids":["C01"]}]}
