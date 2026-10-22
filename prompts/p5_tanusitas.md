FELADAT (P5): Az alábbi, már egyesített TESZ-MAG-okhoz és változataikhoz (TESZ-nivalókhoz) írd
meg a halmozható mikrotanúsítvány szövegeit.

- MAG (csak ahol "kell_alap": true): rubric_alap_hu – 3-6 megfigyelhető, egymástól független
  alapkritérium az ERED-ményről vagy a végrehajtásról, amely minden SZÍN-en érvényes. Ne ismételd
  a címkét, ne írj általánosságot ("pontos munka") konkrétum nélkül.
- VÁLTOZAT: attestation_hu – pontosan ebben a formában: "A személy önállóan [ige] [mit]
  [milyen körülmények között], [milyen eredménnyel]."; a KÖZ-eg és a HELY-zet legyen benne.
  rubric_szin_hu – az ALAP-SZÍN változatnál üres lista; a többinél 1-3 KIEGÉSZÍTŐ kritérium,
  amely a szin_kulonbseg_hu szerint új vagy szigorúbb (időkorlát, hibatűrés, címzett), és nem
  ismétli a MAG alapkritériumait.
A régi gyökű magyar szót válaszd az idegen helyett, ha a szakmai jelentés nem sérül.

TESZ-MAG-OK ÉS VÁLTOZATOK:
{{tesz_list_json}}

KIMENET (csak tömör JSON; minden kért MAG-ra és minden változatra pontosan egy elem):
{"magok":[{"id":"...","rubric_alap_hu":["...","...","..."]}],
 "valtozatok":[{"id":"...","attestation_hu":"A személy önállóan ...","rubric_szin_hu":["..."]}]}
