FELADAT (P3): Minden ESETRE döntsd el, hogy a JELÖLT azonos-e valamelyik TALÁLATTAL.

TESZ-MAG AZONOS, ha mind teljesül (a KÖZ-eg és a HELY-zet NEM számít, azt a kód kezeli):
- ugyanaz a TESZ-típus;
- a meghatározó SZER-ELEM azonos vagy egymás aliasa;
- az ERED-mény fajtája lényegében ugyanaz (egy MAG-rubrika mindkettőt igazolná).
SZER-ELEM AZONOS, ha a definíciója, példája és ellenpéldája kölcsönösen érvényes.
A nyelv, a ragozás, a rövidítés és az írásváltozat NEM különbség: ilyenkor SAME, és a jelölt
neve aliasként kerül a törzselemhez. A puszta névhasonlóság nem elég.

DÖNTÉS:
SAME     - azonos -> target_id a törzselem id-je
NARROWER - SZER-ELEM: a jelölt a törzselem szűkebb fajtája -> target_id
BROADER  - a jelölt tágabb; ha a törzselem elég pontos, használd azt -> target_id
NEW      - nincs megfelelő -> target_id null
Ha a jelölt neve jobb, mint a törzselemé, add meg az upgrade_proposal mezőben.

ESETEK:
{{cases_json}}

KIMENET (csak tömör JSON; minden case_id-ra pontosan egy döntés, üres mezőt ne írj ki):
{"decisions":[{"case_id":"S1","decision":"SAME|NARROWER|BROADER|NEW","target_id":"...",
  "new_aliases":[{"text":"...","lang":"hu|en"}],"confidence":0.9}]}
