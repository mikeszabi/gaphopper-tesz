FELADAT (P6, törzskarbantartás): Az alábbi TESZ-nivalókat több munkakör használja, eltérő szakmai
kontextussal. Mindegyikre döntsd el, hogy a NÉV minden kontextusra igaz-e.

- MARAD: a név minden kontextusra igaz, és a szakember felismeri benne a tevékenységet (G5).
- ATNEVEZ: a név valamelyik kontextusra nem igaz, mert túl szűk (szakterület, szak, közeg vagy egy szűkebb
  SZER-ELEM-fajta van benne, pl. "Higiéniai előírás betartása" villanyszerelőnél). Adj általánosabb,
  2-6 szavas nevet magyarul és angolul, a meghatározó SZER-ELEM fajtájával ("Előírás betartása és
  igazolása"). Ha a meghatározó SZER-ELEM neve is szűk, add meg az általános fajtáját is (szer_hu).
- BONT: a kontextusok valójában KÜLÖNBÖZŐ tevékenységek egy túl elvont név alatt (gyűjtő-TESZ-nivaló,
  pl. "Működés ellenőrzése kritérium szerint" szoftvertesztelésre és villamos mérésre). Adj 2-3
  felismerhető nevet, és mindegyikhez a munkakörök listáját. Ezt szakértő hagyja jóvá.
A TESZ-típust és az ERED-mény fajtáját ne változtasd. A régi gyökű magyar szót válaszd.
KORLÁTOK (a túláltalánosítás ellen):
- Csak szakterületi, szak- vagy közegszűkítést vegyél ki. Az általános célcsoport ("beteg", "hallgató",
  "ügyfél") maradhat a névben.
- Az új név is felismerhető legyen (G5): a szakma alapigéje + a SZER-ELEM fajtája, 2-6 szó. Csupasz,
  elvont név ("Terv készítése", "Címzett tájékoztatása", "Eredmény értékelése", "Fizikai segítség
  nyújtása") nem jó: ilyenkor inkább MARAD.
- Ha egy munkakör kontextusa valójában más tevékenységet takar (pl. "jogosultságok kezelése" a
  villanyszerelőnél hatósági engedélyt jelent, nem hozzáférési jogot), az BONT, nem ATNEVEZ.
- Kétség esetén MARAD.

TESZ-NIVALÓK (név, típus, meghatározó SZER-ELEM, munkakörök és kontextusaik, KÉSZ-ségek):
{{nivalok_json}}

KIMENET (csak tömör JSON, minden id-ra pontosan egy elem; MARAD-nál csak id és dontes):
{"nivalok":[{"id":"...","dontes":"MARAD"},
 {"id":"...","dontes":"ATNEVEZ","label_hu":"...","label_en":"...","szer_hu":"...","indok_hu":"..."},
 {"id":"...","dontes":"BONT","indok_hu":"...","bontas":[{"label_hu":"...","munkakorok":["..."]}]}]}
