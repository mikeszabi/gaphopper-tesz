FELADAT (P1): Bontsd fel az alábbi MUNKA-KÖR-t általános TESZ-MAG-okra és azok SZÍN-ezett
változataira (TESZ-nivalókra).

MUNKAMENET (fejben, ebben a sorrendben; csak a JSON-t írd ki):
1. FELADATKÖRÖK: a munkakör célja egy mondatban; 3-6 feladatkör (kor_id) becsült
   munkaidő-aránnyal (összesen 90-110%). A feladatkör rendező szint, nem TESZ.
2. TESZ-MAG-OK: összesen 6-12 MAG (ebből 4-9 CORE), feladatkörönként 1-3. Forrás: tasks,
   description, requirements, conditions és a szakma ismert gyakorlata a locale-ban.
   a) TÖRZS ELŐBB: ha a MAG-TÖRZS valamelyik eleme illik (azonos TESZ-típus, munkaminta és
      ERED-mény-fajta), azt KELL választanod akkor is, ha a szakterület vagy a szak más (pl.
      ápolási és szerelési nyilvántartás rögzítése = ugyanaz a DOCUMENT-MAG; higiéniai és
      villamos biztonsági előírás betartása = ugyanaz a COMPLY-MAG); a különbség a kontextus_hu.
      Alak: {"mag_id","mag_ref":"T07","kor_id","relevance","kesz_ids","kontextus_hu"}: a kesz_ids
      ennek a munkakörnek a 2-5 legjellemzőbb KÉSZ-sége ehhez a MAG-hoz (a gráf munkakörönként
      súlyozza őket); a többi MAG-mezőt ne
      írd ki. Újat csak akkor alkoss, ha egyik sem illik.
   b) ÚJ MAG: munkamintából, a SZER-ELEM fajtájával (G1-G4); label_hu 2-6 szó, label_en is,
      szakterület, szak és SZÍN nélkül, általános célcsoport maradhat ("hallgatóknak", nem
      "gépész hallgatóknak"); a primary_szer is fajta szintű; tesz_type (az ERED-mény fajtája szerint) és runner_up;
      primary_szer (a fajta, definíció egy rövid mondat); outcome_hu (a közös ERED-mény egy
      mondatban, minőségi küszöbbel); relevance (CORE, ha a munkakör nélküle nem az, ami);
      kesz_ids: 2-5 sorszám a KÉSZ-SÉG-TÖRZSből (G2): csak a legjellemzőbbek, a legfontosabb
      elöl; ne sorold fel az összes kapcsolódót.
   c) kontextus_hu: a munkakör-specifikus tartalom röviden (szakterület, szak, tantárgy,
      helyszín), legfeljebb 8 szó – ide kerül minden, ami a MAG-címkéből kimaradt.
   Összevonás (G4): azonos típus, azonos ERED-mény-fajta és 2/3 KÉSZ-ség-átfedés esetén egy MAG
   (pl. szakdolgozat- és TDK-témavezetés; ipari és kutatási partnerkapcsolat).
3. SZÍN-EZÉS: minden MAG-hoz pontosan egy ALAP-SZÍN változat (alap_szin true): a munkakörben
   leggyakoribb ⟨KÖZ-eg, HELY-zet⟩. További változat (alap_szin false) csak az M2 és M3 próbával;
   MAG-onként legfeljebb 3, összesen 8-18 TESZ-nivaló. A változatnak nincs saját címkéje (G3):
   a SZÍN a kozeg/helyzet mezőben, a különbség egy mondatban a szin_kulonbseg_hu-ban (pl. online
   tanítás: KOZ_DIGITALIS/HELY_TANITO). Ami nem éri el az M2 küszöböt, kísérő SZÍN:
   kozeg_secondary (0-2) / helyzet_secondary (0-1). Egy MAG változatainak SZÍN-e mind különböző;
   a nyelv, a célcsoport vagy a téma nem SZÍN. Tanításnál a HELY-zet HELY_TANITO; a
   HELY_TANULASI a személy saját betanulása.
4. MÉLYÍTÉSI JELÖLT: ha egy CORE MAG-nak van a szakmában ismert nehezebb SZÍN-e, amely ebben a
   munkakörben NEM jellemző: melyites_jeloltek (legfeljebb 1; nem TESZ!).
5. ÖNELLENŐRZÉS: T1-T6 a változatokra, M1 és G1-G4 a MAG-okra. Készség jellegű elem a
   skill_mentions listába.
6. VÁLTOZATONKÉNT: kozeg, helyzet, frequency, expected_eqf (1-8), confidence (0.6 alatt csak
   valódi, szakértőt igénylő bizonytalanságnál, open_question-nel); 0-4 kísérő SZER-ELEM
   (secondary_szer, definíció nélkül) – a szakmai és közegfüggő eszközök itt vannak.
7. MUNKAKÖR-NEVEK: magyar és angol preferált név, a látott vagy szokásos alternatív nevek
   (title_aliases) nyelvjelöléssel.

TÖMÖRSÉG:
- Tömör JSON, behúzás és sortörés nélkül. Ne írj elemzést, indoklást, igazolási mondatot vagy
  rubrikát (ezek a P5-höz tartoznak).
- A változat NEM ismétli a MAG mezőit és a címkét. A mag_ref-es MAG csak a kódot és a munkaköri
  mezőket írja.
- Üres listát, null értéket és alapértéket ne írj ki (title_aliases, skill_mentions,
  secondary_szer, kozeg_secondary, helyzet_secondary, melyites_jeloltek, open_question).

BEMENET:
{{job_record_json}}

KIMENET (csak tömör JSON; a minta olvashatóság miatt tördelt):
{"job_id":"...","job_label_hu":"...","job_label_en":"...",
 "title_aliases":[{"text":"...","lang":"hu|en"}],
 "purpose_hu":"...","eqf_band":{"min":6,"max":7,"rationale_hu":"..."},
 "tesz_korok":[{"kor_id":"K1","name_hu":"...","time_share_pct":40}],
 "tesz_magok":[
  {"mag_id":"M01","kor_id":"K1","label_hu":"Tanóra vagy foglalkozás megtartása",
   "label_en":"Deliver a lesson or session","outcome_hu":"...",
   "tesz_type":"SUPPORT_SERVE","runner_up":"COMMUNICATE",
   "primary_szer":{"label_hu":"Foglalkozásterv","label_en":"Session plan","kind":"PLAN","role":"GUIDED_BY","definition_hu":"..."},
   "relevance":"CORE","kesz_ids":[246,239,163],"kontextus_hu":"gépész hallgatók, labor és gyakorlat",
   "melyites_jeloltek":[{"kozeg":"KOZ_FIZIKAI","helyzet":"HELY_KRITIKUS","indok_hu":"..."}]},
  {"mag_id":"M02","mag_ref":"T07","kor_id":"K2","relevance":"CORE","kesz_ids":[19,67,5],"kontextus_hu":"..."}],
 "tesz":[
  {"cand_id":"C01","mag_id":"M01","alap_szin":true,"kozeg":"KOZ_TARSAS","helyzet":"HELY_TANITO",
   "secondary_szer":[{"label_hu":"...","label_en":"...","kind":"TOOL","role":"USES"}],
   "frequency":"WEEKLY","expected_eqf":6,"confidence":0.8},
  {"cand_id":"C02","mag_id":"M01","alap_szin":false,"kozeg":"KOZ_DIGITALIS","helyzet":"HELY_TANITO",
   "szin_kulonbseg_hu":"...","frequency":"MONTHLY","expected_eqf":6,"confidence":0.7}],
 "skill_mentions":[{"text":"...","related_cand_ids":["C01"]}]}
