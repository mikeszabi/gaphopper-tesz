FELADAT (kesz-bovit): a KÉSZ-ség-törzs bővítése bizonyíték alapján. A bemenet hiányjelöltek listája:
olyan KÉSZ-ségek, amelyeket a munkakörök felbontásakor (P1) egyik törzselem sem fedett. Minden
jelöltnél látod, hány és mely munkakör igényelte, és mely TESZ-nivalóhoz.

1. CSOPORTOSÍTS: az azonos jelentésű jelöltek (szinonima, eltérő szóalak, magyar-angol pár) egy csoportba.
2. DÖNTS csoportonként:
   - ALIAS: csak teljes jelentésfedésnél: a jelölt egy meglévő törzselem neve más szavakkal (szinonima,
     szóalak, fordítás). Add meg a törzselem sorszámát (kesz). Ha a törzselem csak részben vagy tágabban
     fedi (pl. "Egyéni differenciálás" ~ "Fejlődést segítő támogatás"), az nem ALIAS, hanem UJ.
   - UJ: hiányzó vagy csak részben fedett, törzsszintű KÉSZ-ség (a szakértő hagyja jóvá): általános, szakterület-semleges, 1-4 szavas név,
     ugyanazon a granularitási szinten, mint a törzselemek; nem eszköz, termék, szoftver, szabvány
     vagy munkakör neve (az TUD-ás vagy SZER-ELEM); nem szintváltozat (alap/haladó). Add meg a
     javasolt magyar (régi gyökű szó az idegen helyett) és angol nevet, a csoportkódot (a törzs
     csoportjai közül) és az irányt (1-2 KÖZ-eg-kód, KOZ_EGYEN = egy-ÉN).
     Az általános érzékszervi és testi képesség (ízlelés, szaglás, hallási megkülönböztetés, színlátás vagy
     vizuális megkülönböztetés, tapintás, egyensúly stb.) törzsszintű: csoport H10, irány KOZ_FIZIKAI, akkor
     is, ha egy szakmában jellemző; a neve az érzékre utaljon, ne a szakmára ("Ízérzékelés", nem "Konyhai
     ízlés"). A 31 "Érzékszervi észlelés feldolgozása" aliasa csak az, ami nem egy konkrét érzékre vonatkozik.
   - ELVET: túl szűk, szakmai tartalom (TUD-ás vagy SZER-ELEM), tevékenység (TESZ) vagy értelmetlen.
3. Minden jelölt-id pontosan egy csoportban szerepeljen.

JELÖLTEK:
{{jeloltek}}

KIMENET (csak tömör JSON):
{"csoportok":[{"ids":["gh_kh_..."],"dontes":"ALIAS","kesz":19,"indok_hu":"..."},
 {"ids":["gh_kh_...","gh_kh_..."],"dontes":"UJ","label_hu":"...","label_en":"...","csoport":"H1","irany":["KOZ_DIGITALIS"],"indok_hu":"..."},
 {"ids":["gh_kh_..."],"dontes":"ELVET","indok_hu":"..."}]}
