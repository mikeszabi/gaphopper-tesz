FELADAT (kesz-irany): a KÉSZ-ség-törzs elemeinek irányultsága. Az irány az a KÖZ-eg, amelyre a
KÉSZ-ség gyakorlása irányul (amit alakít, figyel vagy kezel):
KOZ_EGYEN egy-ÉN: önmaga (önszabályozás, tanulás, testi-lelki állapot, hozzáállás) |
KOZ_TARSAS másik ember, csoport, beszélgetés | KOZ_SZERVEZETI szabály, folyamat, szerep, intézmény,
üzlet | KOZ_DIGITALIS szoftver, adat, hálózat, digitális rendszer | KOZ_TUDOMANYOS elmélet, modell,
módszer, kísérlet, bizonyíték, tudományos és gondolati érvelés | KOZ_NYELVI szöveg, beszéd, nyelvi forma,
fordítás |
KOZ_FIZIKAI test, szerszám, gép, anyag, épített tér | KOZ_TERMESZET élőlény, természeti jelenség.

Minden elemnek van egy alapiránya (a csoportjából). Vizsgáld meg elemenként:
- ha az alapirány jó: NE írd ki az elemet;
- ha más az irány, vagy két irány egyformán lényeges (legfeljebb 2, a fő elöl): írd ki;
- ha nem tudsz egyértelműen dönteni: írd ki "biztos": false jelöléssel (szakértő dönt).
A szakmai terület nem irány: a "Pénzügyi elemzés" az adatra (KOZ_DIGITALIS) irányul, nem a szervezetre,
ha az eredménye adat-értelmezés; a "Hipotézisvizsgálat" és a "Tanuláselméletek ismerete" a tudományos
közegre (KOZ_TUDOMANYOS); a "Szövegértés" és a "Fordítás" a nyelvire (KOZ_NYELVI); az "Empátia" a társra;
a "Stressztűrés" az egy-ÉN-re.

BEMENET (sorszám|név|csoport|alapirány|leírás):
{{elemek}}

KIMENET (csak tömör JSON):
{"elemek":[{"n":12,"irany":["KOZ_TARSAS"],"indok_hu":"..."},{"n":40,"irany":["KOZ_DIGITALIS","KOZ_FIZIKAI"],"biztos":false,"indok_hu":"..."}]}
