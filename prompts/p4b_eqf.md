FELADAT (P4b): Írd le, hogyan néz ki az alábbi TESZ-nivaló elvégzése különböző
EQF-szinteken. Csak azokat a szinteket add meg, amelyeken ez a TESZ reálisan előfordul.

EQF-PILLÉREK: TUD-ás (ismeretek mélysége), KÉSZ-ség (műveletek összetettsége),
felelősség és önállóság (felügyelet alatt / önállóan / másokért / újítóként).
Minden szinthez: egy mondatos leírás, 2-3 megfigyelhető viselkedés, és egy tipikus
BIZ-onyíték (munkadarab, megfigyelés, szimuláció, igazolt gyakorlat).

BEMENET: {{tesz_json}}

KIMENET (csak JSON):
{"tesz_id": "...", "levels": [{"eqf": 4, "description_hu": "...",
  "behaviours_hu": ["..."], "evidence_hu": "..."}]}
