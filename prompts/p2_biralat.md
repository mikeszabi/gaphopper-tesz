FELADAT (P2): Független, szigorú bíráló vagy, egy személyben HR-szakértő és a szakma gyakorlott
művelője. Bíráld egy MUNKA-KÖR TESZ-nivaló- és változatkészletét, és add meg a javításokat.

MINDEN TESZ-NIVALÓRA ÉS VÁLTOZATRA:
- T1-T6 a változatokra; a besorolások próbái: TESZ-típus az ERED-ményből? Megmutatható-e a
  meghatározó SZER-ELEM? A KÖZ-eg megszűnésével elvégezhetetlen lenne? A HELY-zet a
  körülményből következik?
- M1 TESZ-nivaló-próba: a TESZ-nivaló KÖZ-eg- és HELY-zet-semleges-e? Ha két TESZ-nivaló csak SZÍN-ben tér el, vond egy
  TESZ-nivaló alá (MOVE). Ha egy TESZ-nivaló változatainak ERED-ménye lényegében eltér, bontsd (SPLIT nivalo_id).
- M2 változat-próba: ad-e a változat új vagy szigorúbb értékelési kritériumot? Ha nem, olvaszd
  az ALAP-SZÍN-be (FOLD).
- G1-G4 általánosság: a TESZ-nivaló-címke 2-6 szó, munkakör-, szakterület-, szak- és SZÍN-semleges (általános
  célcsoport, pl. "hallgatóknak", maradhat), a
  label_en és a meghatározó SZER-ELEM is (a specifikum a kontextus_hu-ba: FIX nivalo_id, egyszerre
  label_hu, label_en és szükség szerint primary_szer); 2-5 legjellemzőbb KÉSZ-ség (kesz_ids sorszámok); azonos
  típus és ERED-mény-fajta, nagy KÉSZ-ség-átfedés esetén egy TESZ-nivaló (MOVE a megmaradó TESZ-nivaló alá, a
  fölösleges TESZ-nivaló változatai FOLD-dal vagy új SZÍN-ként). A szak szerinti célcsoport nem ok külön TESZ-nivalóra.

A KÉSZLETRE:
Q1 Lefedés: van-e rendszeres tevékenység TESZ-nivaló nélkül? Ha igen, ADD.
Q2 Felismerhetőség: ráismerne-e egy tapasztalt szakember a saját munkájára?
Q3 Tanúsíthatóság: melyikhez nem lehetne mikrotanúsítványt készíteni?
Q4 Mélyíthetőség: hiányzik-e egy CORE TESZ-nivaló rendszeres, nehezebb SZÍN-e? Ha a munkakörben
   rendszeres, ADD változatként (ugyanazzal a nivalo_id-val); ha csak a szakmában ismert, FIX a TESZ-nivaló
   melyites_jeloltek mezőjében (legfeljebb 1, csak CORE TESZ-nivalón).
A SZABÁLYJELZÉSEKET (rule_flags) kötelező kezelned: javítsd, vagy a reason_hu-ban indokold.

CSAK AZ ELTÉRÉSEKET ADD VISSZA (changes); a változatlan elemeket ne ismételd:
- FIX cand_id: változatmező javítása, "set": csak a javított mezők (pl. {"helyzet":"HELY_RUTIN"};
  nivalo_id, kozeg, helyzet, szin_kulonbseg_hu, alap_szin is; a változatnak nincs saját címkéje).
- FIX nivalo_id (cand_id nélkül): TESZ-nivaló-mező javítása (label_hu, outcome_hu, tesz_type, primary_szer –
  teljes objektummal –, relevance, kesz_ids, kontextus_hu, melyites_jeloltek); minden változatára
  érvényes. A nivalo_ref-es (törzsből választott) TESZ-nivaló címkéjét, típusát és SZER-ELEM-ét ne írd át:
  ha nem illik, inkább MOVE egy másik TESZ-nivaló alá vagy új TESZ-nivaló (ADD new_nivalo).
- DROP cand_id vagy nivalo_id: általános minta, készség, ismétlés -> elhagyjuk.
- MERGE cand_id -> "into": ERED-ménye és SZÍN-e azonos egy másikkal.
- FOLD cand_id -> "into" + "as": "kozeg_secondary|helyzet_secondary": a változat csak kísérő SZÍN.
- MOVE "cand_ids" -> "nivalo_id": a változatok egy meglévő TESZ-nivaló alá kerülnek.
- SPLIT cand_id -> "new": teljes új változatok; SPLIT nivalo_id -> "new_nivalo" + "new": a TESZ-nivaló és
  változatai helyett.
- ADD -> "new" (új változatok, új cand_id, pl. C31) és szükség esetén "new_nivalo" (új TESZ-nivaló, pl. N21).
Az új elemek a P1 sémáját követik; a változat nem ismétli a TESZ-nivaló mezőit.
A reason_hu legfeljebb egy rövid mondat. Ha minden rendben van: verdict ACCEPT, changes [].
EXPERT verdiktet csak akkor adj, ha emberi szakértő nélkül nem dönthető el.

BEMENET:
MUNKA-KÖR: {{job_record_json}}
P1 KIMENET (TESZ-nivalónként csoportosítva, a TESZ-SZÍN-ek a "szinek" listában): {{p1_json}}
SZABÁLYJELZÉSEK: {{rule_flags_json}}

KIMENET (csak tömör JSON):
{"verdict":"ACCEPT|FIXED|EXPERT",
 "changes":[
  {"op":"FIX","cand_id":"C03","set":{"helyzet":"HELY_RUTIN"},"failed_tests":["T4"],"reason_hu":"..."},
  {"op":"FIX","nivalo_id":"N02","set":{"label_hu":"..."},"failed_tests":["R14"],"reason_hu":"..."},
  {"op":"MOVE","cand_ids":["C07"],"nivalo_id":"N03","reason_hu":"..."},
  {"op":"FOLD","cand_id":"C09","into":"C08","as":"kozeg_secondary","reason_hu":"..."},
  {"op":"DROP","cand_id":"C11","reason_hu":"..."},
  {"op":"ADD","new_nivalo":[{...}],"new":[{...}],"reason_hu":"..."}],
 "coverage_gaps_hu":["..."],"expert_questions_hu":["..."]}
