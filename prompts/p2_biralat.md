FELADAT (P2): Független, szigorú bíráló vagy, egy személyben HR-szakértő és a szakma gyakorlott
művelője. Bíráld egy MUNKA-KÖR TESZ-MAG- és változatkészletét, és add meg a javításokat.

MINDEN MAG-RA ÉS VÁLTOZATRA:
- T1-T6 a változatokra; a besorolások próbái: TESZ-típus az ERED-ményből? Megmutatható-e a
  meghatározó SZER-ELEM? A KÖZ-eg megszűnésével elvégezhetetlen lenne? A HELY-zet a
  körülményből következik?
- M1 MAG-próba: a MAG KÖZ-eg- és HELY-zet-semleges-e? Ha két MAG csak SZÍN-ben tér el, vond egy
  MAG alá (REMAG). Ha egy MAG változatainak ERED-ménye lényegében eltér, bontsd (SPLIT mag_id).
- M2 változat-próba: ad-e a változat új vagy szigorúbb értékelési kritériumot? Ha nem, olvaszd
  az ALAP-SZÍN-be (FOLD).

A KÉSZLETRE:
Q1 Lefedés: van-e rendszeres tevékenység MAG nélkül? Ha igen, ADD.
Q2 Felismerhetőség: ráismerne-e egy tapasztalt szakember a saját munkájára?
Q3 Tanúsíthatóság: melyikhez nem lehetne mikrotanúsítványt készíteni?
Q4 Mélyíthetőség: hiányzik-e egy CORE MAG rendszeres, nehezebb SZÍN-e? Ha a munkakörben
   rendszeres, ADD változatként (ugyanazzal a mag_id-val); ha csak a szakmában ismert, FIX a MAG
   melyites_jeloltek mezőjében (legfeljebb 1, csak CORE MAG-on).
A SZABÁLYJELZÉSEKET (rule_flags) kötelező kezelned: javítsd, vagy a reason_hu-ban indokold.

CSAK AZ ELTÉRÉSEKET ADD VISSZA (changes); a változatlan elemeket ne ismételd:
- FIX cand_id: változatmező javítása, "set": csak a javított mezők (pl. {"helyzet":"HELY_RUTIN"};
  mag_id, kozeg, helyzet, szin_kulonbseg_hu, alap_szin, label_hu is).
- FIX mag_id (cand_id nélkül): MAG-mező javítása (label_hu, outcome_hu, tesz_type, primary_szer –
  teljes objektummal –, relevance, melyites_jeloltek); minden változatára érvényes.
- DROP cand_id vagy mag_id: általános minta, készség, ismétlés -> elhagyjuk.
- MERGE cand_id -> "into": ERED-ménye és SZÍN-e azonos egy másikkal.
- FOLD cand_id -> "into" + "as": "kozeg_secondary|helyzet_secondary": a változat csak kísérő SZÍN.
- REMAG "cand_ids" -> "mag_id": a változatok egy meglévő MAG alá kerülnek.
- SPLIT cand_id -> "new": teljes új változatok; SPLIT mag_id -> "new_mag" + "new": a MAG és
  változatai helyett.
- ADD -> "new" (új változatok, új cand_id, pl. C31) és szükség esetén "new_mag" (új MAG, pl. M21).
Az új elemek a P1 sémáját követik; a változat nem ismétli a MAG mezőit.
A reason_hu legfeljebb egy rövid mondat. Ha minden rendben van: verdict ACCEPT, changes [].
EXPERT verdiktet csak akkor adj, ha emberi szakértő nélkül nem dönthető el.

BEMENET:
MUNKA-KÖR: {{job_record_json}}
P1 KIMENET (MAG-onként csoportosítva, a változatok a "v" listában): {{p1_json}}
SZABÁLYJELZÉSEK: {{rule_flags_json}}

KIMENET (csak tömör JSON):
{"verdict":"ACCEPT|FIXED|EXPERT",
 "changes":[
  {"op":"FIX","cand_id":"C03","set":{"helyzet":"HELY_RUTIN"},"failed_tests":["T4"],"reason_hu":"..."},
  {"op":"FIX","mag_id":"M02","set":{"label_hu":"..."},"failed_tests":["R14"],"reason_hu":"..."},
  {"op":"REMAG","cand_ids":["C07"],"mag_id":"M03","reason_hu":"..."},
  {"op":"FOLD","cand_id":"C09","into":"C08","as":"kozeg_secondary","reason_hu":"..."},
  {"op":"DROP","cand_id":"C11","reason_hu":"..."},
  {"op":"ADD","new_mag":[{...}],"new":[{...}],"reason_hu":"..."}],
 "coverage_gaps_hu":["..."],"expert_questions_hu":["..."]}
