FELADAT (E0 – kinyerés): Az alábbi dokumentumrészletből gyűjtsd ki a benne leírt MUNKA-KÖRÖKET
egységes rekordokba. A dokumentum lehet munkaköri leírás, álláshirdetés, képzési vagy
foglalkozási katalógus, szervezeti szabályzat vagy bármilyen más szöveg; magyar vagy angol.

SZABÁLYOK
1. Csak azt írd le, ami a szövegben szerepel vagy egyértelműen következik belőle. Ne egészítsd
   ki szakmai tudásból: a felbontás egy későbbi lépés dolga. Hiányzó mező = üres szöveg vagy [].
2. Egy MUNKA-KÖR = egy személyhez rendelt, megnevezett munkakör. Ha a részlet több munkakört ír le,
   mindegyik külön rekord. Ha egy sem szerepel benne (pl. tartalomjegyzék, jogi záradék, céges
   bemutatkozás), adj vissza üres jobs listát.
3. tasks: a feladatok listája, a szöveghez hűen, egy feladat egy elem. Ha a szöveg folyó prózában
   írja le a feladatokat, bontsd mondatokra; ne fogalmazd át szakmailag.
4. requirements: végzettség, képesítés, tapasztalat, eszköz- és nyelvismeret – ahogy a szöveg írja.
5. conditions: munkakörülmények (műszak, távmunka, terep, utazás, fizikai igénybevétel, kockázat).
6. Kódok: ha a szöveg FEOR-, ISCO-, ESCO- vagy O*NET-kódot ad meg, írd a codes mezőbe; ne találd ki.
7. doc_type: JOB_DESCRIPTION (munkaköri leírás) | JOB_AD (álláshirdetés) | CATALOG (foglalkozási
   vagy képzési katalógus) | OTHER.
8. Ha a munkakör leírása a részlet határán megszakad, jelöld truncated=true-val; az összefésülés
   a kód dolga.
9. source_quote: legfeljebb 200 karakteres szó szerinti idézet, amely alapján a munkakört
   azonosítottad (a visszakereséshez).

DOKUMENTUM: {{file_name}}  (részlet {{chunk_no}}/{{chunk_total}})
---
{{chunk_text}}
---

KIMENET (csak JSON):
{"doc_type": "JOB_DESCRIPTION|JOB_AD|CATALOG|OTHER",
 "jobs": [
   {"title": "...", "title_en": "", "lang": "hu|en",
    "codes": {"feor": "", "isco": "", "esco": "", "onet": ""},
    "employer": "", "field": "", "description": "",
    "tasks": ["..."], "requirements": "", "conditions": "", "entry_route": "",
    "truncated": false, "source_quote": "...", "confidence": 0.0}
 ]}
