FELADAT (kesz-bovit): a KÉSZ-ség-törzs bővítése bizonyíték alapján. A bemenet hiányjelöltek listája:
olyan KÉSZ-ségek, amelyeket a munkakörök felbontásakor (P1) egyik törzselem sem fedett. Minden
jelöltnél látod, hány és mely munkakör igényelte, és mely TESZ-nivalóhoz.

1. CSOPORTOSÍTS: az azonos jelentésű jelöltek (szinonima, eltérő szóalak, magyar-angol pár) egy csoportba.
2. DÖNTS csoportonként:
   - ALIAS: a jelentés egy meglévő törzselem neve más szavakkal, vagy annak szűkebb esete, amelyet a
     törzselem megfelelően lefed. Add meg a törzselem sorszámát (kesz). Ha kétséges, ALIAS-t válassz.
   - UJ: valóban hiányzó, törzsszintű KÉSZ-ség: általános, szakterület-semleges, 1-4 szavas név,
     ugyanazon a granularitási szinten, mint a törzselemek; nem eszköz, termék, szoftver, szabvány
     vagy munkakör neve (az TUD-ás vagy SZER-ELEM); nem szintváltozat (alap/haladó). Add meg a
     javasolt magyar (régi gyökű szó az idegen helyett) és angol nevet, a csoportkódot (a törzs
     csoportjai közül) és az irányt (1-2 KÖZ-eg-kód, KOZ_EGYEN = egy-ÉN).
   - ELVET: túl szűk, szakmai tartalom (TUD-ás vagy SZER-ELEM), tevékenység (TESZ) vagy értelmetlen.
3. Minden jelölt-id pontosan egy csoportban szerepeljen.

JELÖLTEK:
{{jeloltek}}

KIMENET (csak tömör JSON):
{"csoportok":[{"ids":["gh_kh_..."],"dontes":"ALIAS","kesz":19,"indok_hu":"..."},
 {"ids":["gh_kh_...","gh_kh_..."],"dontes":"UJ","label_hu":"...","label_en":"...","csoport":"H1","irany":["KOZ_DIGITALIS"],"indok_hu":"..."},
 {"ids":["gh_kh_..."],"dontes":"ELVET","indok_hu":"..."}]}
