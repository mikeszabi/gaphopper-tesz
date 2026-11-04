FELADAT (P5): Az alábbi, már egyesített TESZ-nivalókhoz és változataikhoz (TESZ-SZÍN-ekhez) írd
meg a halmozható mikrotanúsítvány szövegeit.

- TESZ-nivaló (csak ahol "kell_alap": true): rubric_alap_hu – 3-6 megfigyelhető, egymástól független
  alapkritérium az ERED-ményről vagy a végrehajtásról, amely minden SZÍN-en érvényes. Ne ismételd
  a címkét, ne írj általánosságot ("pontos munka") konkrétum nélkül. A kritérium a TESZ-nivaló általános
  szintjén szóljon; a "kontextusok" (munkakörök szakmai tartalma) csak zárójeles példaként
  jelenhet meg, legfeljebb egy kritériumban.
- TESZ-SZÍN: attestation_hu – pontosan ebben a formában: "A személy önállóan [ige] [mit]
  [milyen körülmények között], [milyen eredménnyel]."; a KÖZ-eg és a HELY-zet legyen benne.
  rubric_szin_hu – az ALAP-SZÍN változatnál üres lista; a többinél 1-3 KIEGÉSZÍTŐ kritérium,
  amely a szin_kulonbseg_hu (munkakörönként 1-2 mondat) szerint új vagy szigorúbb (időkorlát, hibatűrés, címzett), és nem
  ismétli a TESZ-nivaló alapkritériumait.
A régi gyökű magyar szót válaszd az idegen helyett, ha a szakmai jelentés nem sérül.

TESZ-NIVALÓK ÉS TESZ-SZÍN-EIK:
{{tesz_list_json}}

KIMENET (csak tömör JSON; minden kért TESZ-nivalóra és minden változatra pontosan egy elem):
{"nivalok":[{"id":"...","rubric_alap_hu":["...","...","..."]}],
 "szinek":[{"id":"...","attestation_hu":"A személy önállóan ...","rubric_szin_hu":["..."]}]}
