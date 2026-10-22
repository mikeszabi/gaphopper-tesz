FELADAT (P3): Minden ESETRE döntsd el, hogy a JELÖLT azonos-e valamelyik TALÁLATTAL.

TESZ-NIVALÓ AZONOS, ha mind teljesül:
- ugyanaz a TESZ-típus és az elsődleges HELY-zet;
- a meghatározó SZER-ELEM azonos vagy egymás aliasa;
- az ERED-mény lényegében ugyanaz;
- egy mikrotanúsítvány mindkettőt igazolná.
SZER-ELEM AZONOS, ha a definíciója, példája és ellenpéldája kölcsönösen érvényes.
A nyelv (magyar / angol), a ragozás, a rövidítés és az írásváltozat NEM különbség:
ilyenkor SAME, és a jelölt neve aliasként kerül a törzselemhez.
A puszta névhasonlóság nem elég.

DÖNTÉS:
SAME        - azonos -> target_id a törzselem id-je
VARIANT     - TESZ: ugyanaz a mag, de más HELY-zet vagy lényegesen más ERED-mény -> target_id
NARROWER    - SZER-ELEM: a jelölt a törzselem szűkebb fajtája -> target_id
BROADER     - a jelölt tágabb; ha a törzselem elég pontos, használd azt -> target_id
NEW         - nincs megfelelő -> target_id null
Ha a jelölt neve jobb, mint a törzselemé, add meg az upgrade_proposal mezőben.

ESETEK:
{{cases_json}}

KIMENET (csak JSON; minden case_id-ra pontosan egy döntés):
{"decisions": [{"case_id": "S1", "decision": "SAME|VARIANT|NARROWER|BROADER|NEW",
                "target_id": "...|null", "new_aliases": [{"text": "...", "lang": "hu|en"}],
                "upgrade_proposal": null, "note_hu": "", "confidence": 0.0}]}
