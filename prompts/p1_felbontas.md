FELADAT (P1): Bontsd fel az alábbi MUNKA-KÖR-t általános TESZ-nivalókra és azok SZÍN-ezett
változataira (TESZ-SZÍN-ekre).

MUNKAMENET (fejben, ebben a sorrendben; csak a JSON-t írd ki):
1. FELADATKÖRÖK: a munkakör célja egy mondatban; 3-6 feladatkör (kor_id) becsült
   munkaidő-aránnyal (összesen 90-110%). A feladatkör rendező szint, nem TESZ.
2. TESZ-NIVALÓK: összesen 6-12 TESZ-nivaló (ebből 4-9 CORE), feladatkörönként 1-3. Forrás: tasks,
   description, requirements, conditions és a szakma ismert gyakorlata a locale-ban.
   a) TÖRZS ELŐBB: a TESZ-TÖRZS elemét akkor válaszd, ha azonos a TESZ-típus, a meghatározó
      SZER-ELEM fajtája és az ERED-mény, és a munkakör szakembere a törzsbeli névből ráismer a saját
      tevékenységére (G5) – akkor is, ha a szakterület más (pl. ápolási és szerelési nyilvántartás
      vezetése = ugyanaz a DOCUMENT típusú TESZ-nivaló); a különbség a kontextus_hu-ba és a kísérő
      SZER-ELEM-be kerül. Ha a törzsbeli név túl elvont a munkakör CORE tevékenységére, új TESZ-nivaló kell.
      Alak: {"nivalo_id","nivalo_ref":"T07","szer":"<a te meghatározó SZER-ELEM-fajtád neve>","kor_id",
      "relevance","kesz_ids","kontextus_hu"}: a kesz_ids
      ennek a munkakörnek a 2-5 legjellemzőbb KÉSZ-sége ehhez a TESZ-nivalóhoz (a gráf munkakörönként
      súlyozza őket); a többi TESZ-nivaló-mezőt ne
      írd ki. Újat csak akkor alkoss, ha egyik sem illik.
   b) ÚJ TESZ-nivaló: munkamintából, a SZER-ELEM fajtájával (G1-G5); label_hu 2-6 szó, label_en is,
      a névben benne van a meghatározó SZER-ELEM neve vagy töve,
      szakterület, szak és SZÍN nélkül, általános célcsoport maradhat ("hallgatóknak", nem
      "gépész hallgatóknak"); a primary_szer is fajta szintű; tesz_type (az ERED-mény fajtája szerint) és runner_up;
      primary_szer (a fajta, definíció egy rövid mondat); outcome_hu (a közös ERED-mény egy
      mondatban, minőségi küszöbbel); relevance (CORE, ha a munkakör nélküle nem az, ami);
      kesz_ids: 2-5 sorszám a KÉSZ-SÉG-TÖRZSből (G2): csak a legjellemzőbbek, a legfontosabb
      elöl; ne sorold fel az összes kapcsolódót.
   c) kontextus_hu: a munkakör-specifikus tartalom röviden (szakterület, szak, tantárgy,
      helyszín), legfeljebb 8 szó – ide kerül minden, ami a TESZ-nivaló-címkéből kimaradt.
   d) kesz_hiany (TESZ-nivalónként legfeljebb 2): ha a TESZ-nivalóhoz lényeges KÉSZ-séget egy törzselem
      csak részben fedi, vagy a legjobb illesztés is távoli (a kesz_ids-be ilyenkor is a legjobb kerül).
      Törzsszintű legyen:
      általános, szakterület-semleges, 1-4 szavas név; nem eszköz, termék, szoftver, szabvány vagy
      munkakör neve (az TUD-ás vagy SZER-ELEM), nem szintváltozat (alap/haladó), nem törzselem szinonimája.
      Alak: {"label_hu","label_en","csoport":"<a törzs csoportkódja>","irany":["<KÖZ-eg-fejléc>"]}.
   Összevonás (G4): azonos típus, azonos ERED-mény-fajta és 2/3 KÉSZ-ség-átfedés esetén egy TESZ-nivaló
   (pl. szakdolgozat- és TDK-témavezetés; ipari és kutatási partnerkapcsolat).
3. SZÍN-EZÉS: minden TESZ-nivalóhoz pontosan egy ALAP-SZÍN változat (alap_szin true): a munkakörben
   leggyakoribb ⟨KÖZ-eg, HELY-zet⟩. További változat (alap_szin false) csak az M2 és M3 próbával;
   TESZ-nivalónként legfeljebb 3, összesen 8-18 TESZ-SZÍN. A változatnak nincs saját címkéje (G3):
   a SZÍN a kozeg/helyzet mezőben, a különbség egy mondatban a szin_kulonbseg_hu-ban (pl. online
   tanítás: KOZ_DIGITALIS/HELY_TANITO). Ami nem éri el az M2 küszöböt, kísérő SZÍN:
   kozeg_secondary (0-2) / helyzet_secondary (0-1). Egy TESZ-nivaló változatainak SZÍN-e mind különböző;
   a nyelv, a célcsoport vagy a téma nem SZÍN. Tanításnál a HELY-zet HELY_TANITO; a
   HELY_TANULASI a személy saját betanulása.
4. MÉLYÍTÉSI JELÖLT: ha egy CORE TESZ-nivalónak van a szakmában ismert nehezebb SZÍN-e, amely ebben a
   munkakörben NEM jellemző: melyites_jeloltek (legfeljebb 1; nem TESZ!).
5. ÖNELLENŐRZÉS: T1-T6 a változatokra, M1 és G1-G5 a TESZ-nivalókra; a munkakör fő tevékenységei
   (pl. szoftverfejlesztőnél kódolás és tesztelés) felismerhető néven szerepelnek. Készség jellegű elem a
   skill_mentions listába. KÉSZ-ség-lefedettség TESZ-nivalónként: fedik-e a kesz_ids a TESZ-nivaló lényeges
   KÉSZ-ségeit? Ha nem (csak részben vagy távolról), kesz_hiany (2d).
6. VÁLTOZATONKÉNT: kozeg, helyzet, frequency, expected_eqf (1-8), confidence (0.6 alatt csak
   valódi, szakértőt igénylő bizonytalanságnál, open_question-nel); 0-4 kísérő SZER-ELEM
   (secondary_szer, definíció nélkül) – a szakmai és közegfüggő eszközök itt vannak; a meghatározó
   SZER-ELEM szűkebb fajtája is ide kerül "fajtaja": true jelöléssel (pl. {"label_hu":"Higiéniai
   előírás","kind":"RULE","role":"GUIDED_BY","fajtaja":true}).
7. MUNKAKÖR-NEVEK: magyar és angol preferált név, a látott vagy szokásos alternatív nevek
   (title_aliases) nyelvjelöléssel.

TÖMÖRSÉG:
- Tömör JSON, behúzás és sortörés nélkül. Ne írj elemzést, indoklást, igazolási mondatot vagy
  rubrikát (ezek a P5-höz tartoznak).
- A változat NEM ismétli a TESZ-nivaló mezőit és a címkét. A nivalo_ref-es TESZ-nivaló csak a kódot és a munkaköri
  mezőket írja.
- Üres listát, null értéket és alapértéket ne írj ki (title_aliases, skill_mentions,
  secondary_szer, kozeg_secondary, helyzet_secondary, melyites_jeloltek, kesz_hiany, open_question).

BEMENET:
{{job_record_json}}

KIMENET (csak tömör JSON; a minta olvashatóság miatt tördelt):
{"job_id":"...","job_label_hu":"...","job_label_en":"...",
 "title_aliases":[{"text":"...","lang":"hu|en"}],
 "purpose_hu":"...","eqf_band":{"min":6,"max":7,"rationale_hu":"..."},
 "tesz_korok":[{"kor_id":"K1","name_hu":"...","time_share_pct":40}],
 "nivalok":[
  {"nivalo_id":"N01","kor_id":"K1","label_hu":"Tanóra vagy foglalkozás megtartása",
   "label_en":"Deliver a lesson or session","outcome_hu":"...",
   "tesz_type":"SUPPORT_SERVE","runner_up":"COMMUNICATE",
   "primary_szer":{"label_hu":"Foglalkozásterv","label_en":"Session plan","kind":"PLAN","role":"GUIDED_BY","definition_hu":"..."},
   "relevance":"CORE","kesz_ids":[246,239,163],"kontextus_hu":"gépész hallgatók, labor és gyakorlat",
   "kesz_hiany":[{"label_hu":"Egyéni differenciálás","label_en":"Individual differentiation","csoport":"L1","irany":["KOZ_TARSAS"]}],
   "melyites_jeloltek":[{"kozeg":"KOZ_FIZIKAI","helyzet":"HELY_KRITIKUS","indok_hu":"..."}]},
  {"nivalo_id":"N02","nivalo_ref":"T07","szer":"Hiba","kor_id":"K2","relevance":"CORE","kesz_ids":[19,67,5],"kontextus_hu":"..."}],
 "szinek":[
  {"cand_id":"C01","nivalo_id":"N01","alap_szin":true,"kozeg":"KOZ_TARSAS","helyzet":"HELY_TANITO",
   "secondary_szer":[{"label_hu":"...","label_en":"...","kind":"TOOL","role":"USES"}],
   "frequency":"WEEKLY","expected_eqf":6,"confidence":0.8},
  {"cand_id":"C02","nivalo_id":"N01","alap_szin":false,"kozeg":"KOZ_DIGITALIS","helyzet":"HELY_TANITO",
   "szin_kulonbseg_hu":"...","frequency":"MONTHLY","expected_eqf":6,"confidence":0.7}],
 "skill_mentions":[{"text":"...","related_cand_ids":["C01"]}]}
