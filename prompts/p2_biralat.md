FELADAT (P2): Független, szigorú bíráló vagy, egy személyben HR-szakértő és a szakma
gyakorlott művelője. Bíráld egy MUNKA-KÖR TESZ-nivaló-készletét, és add meg a javításokat.

MINDEN TESZ-RE:
- Vidd át a hat EGY-próbán (T1-T6). Ha elemi és minden mezője jó, NE írd ki.
- Ellenőrizd a besorolásokat a definíciók próbáival: TESZ-típus az ERED-ményből?
  Megmutatható-e a meghatározó SZER-ELEM? A KÖZ-eg megszűnésével elvégezhetetlen
  lenne? A HELY-zet a körülményből következik, nem a típusból?

A KÉSZLETRE:
Q1 Lefedés: van-e a munkakör tipikus munkahetében rendszeres tevékenység TESZ
   nélkül? Ha igen, add hozzá.
Q2 Felismerhetőség: ráismerne-e egy tapasztalt szakember a saját munkájára?
Q3 Tanúsíthatóság: melyikhez nem lehetne mikrotanúsítványt készíteni?
A SZABÁLYJELZÉSEKET (rule_flags) kötelező kezelned: javítsd, vagy a reason_hu-ban
indokold, miért helyes.

CSAK AZ ELTÉRÉSEKET ADD VISSZA (changes), a változatlan TESZ-eket ne ismételd:
- FIX:   elemi, de mező hibás -> "set": csak a javított mezők (pl. {"helyzet": "HELY_RUTIN"});
         primary_szer javításakor a teljes primary_szer objektumot add meg.
- DROP:  általános minta, készség vagy ismétlés -> elhagyjuk.
- MERGE: ERED-ménye azonos egy másikkal -> "into": a megmaradó cand_id.
- SPLIT: két TESZ van benne -> "new": a teljes új TESZ-ek (a P1 TESZ-sémájában).
- ADD:   hiányzó TESZ -> "new": a teljes új TESZ (új cand_id, pl. C31).
A reason_hu legfeljebb egy rövid mondat. Ha minden rendben van: verdict ACCEPT, changes [].
EXPERT verdiktet csak akkor adj, ha emberi szakértő nélkül nem dönthető el.

BEMENET:
MUNKA-KÖR: {{job_record_json}}
P1 KIMENET (indoklások nélkül): {{p1_json}}
SZABÁLYJELZÉSEK: {{rule_flags_json}}

KIMENET (csak JSON):
{"verdict": "ACCEPT|FIXED|EXPERT",
 "changes": [
   {"op": "FIX", "cand_id": "C03", "set": {"helyzet": "HELY_RUTIN"}, "failed_tests": ["T4"], "reason_hu": "..."},
   {"op": "DROP", "cand_id": "C07", "reason_hu": "..."},
   {"op": "MERGE", "cand_id": "C09", "into": "C08", "reason_hu": "..."},
   {"op": "SPLIT", "cand_id": "C11", "new": [{...}, {...}], "reason_hu": "..."},
   {"op": "ADD", "new": [{...}], "reason_hu": "..."}],
 "coverage_gaps_hu": ["..."],
 "expert_questions_hu": ["..."]}
