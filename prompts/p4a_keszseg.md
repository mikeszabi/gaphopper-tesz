FELADAT (P4a): Az alábbi TESZ-SZÍN-ek közös HORGONYA: {{anchor_type}} = {{anchor_value}}.
Azonosítsd azokat a KÉSZ-SÉGEKET, amelyeket ez a horgony kíván meg.

DEFINÍCIÓK
- KÉSZ-SÉG: KÉP-esség (tudat alatti működési struktúra) és TUD-ás (tudatos, megnevezhető
  ismeret) gyakorlással beépült, megbízhatóan ismételhető működése. Az emberben van,
  nem a munkában.
- KÉSZ-SÉG-ASPEKTUS: egy KÉSZ-ség egy adott SZÍN-ben (KÖZ-eg × HELY-zet). A problémamegoldás
  ⟨Digitális, Probléma⟩ és ⟨Társas, Döntési⟩ aspektusa ugyanannak a KÉSZ-ségnek két
  külön mérendő állapota.

SZABÁLYOK
- Csak olyan KÉSZ-séget adj, amely a lista TÖBBSÉGÉBEN ugyanazt jelenti. Ami csak néhány
  TESZ-ben fordul elő, szűkebb horgonyhoz tartozik: jelöld narrower_anchor-ként.
- Ne hozz létre új KÉSZ-séget csak azért, mert a közeg vagy a szakterület más
  ("digitális problémamegoldás" = problémamegoldás a ⟨Digitális, *⟩ aspektusokban).
- Előbb nézd meg a meglévő KÉSZ-ség-törzset; ha egy elem illik, használd az azonosítóját.

MINDEN KÉSZ-SÉGHEZ:
- skill_id (meglévő) vagy label_hu + label_en + egymondatos definíció (új)
- kep_elemek: a mögötte álló KÉP-ességek (1-3); tud_elemek: a szükséges TUD-ás (0-3)
- aspects: mely SZÍN-ekben érvényes (KÖZ-eg × HELY-zet párok)
- erosseg: KAPU | SULYOS | TAMASZ | HELYZETI | POTOLHATO
- viselkedes_hu: 1-3 megfigyelhető viselkedési jel
- covered_tesz_ids: mely TESZ-SZÍN-ekben érvényes
- external_ref: külső katalógus-megfelelő, ha a megadott találatok között van (kitalálni tilos)

BEMENET: TESZ-EK: {{anchor_tesz_list_json}}  KÉSZ-SÉG-TÖRZS TALÁLATAI: {{skill_candidates_json}}
          KÉSZ-SÉG-JELÖLTEK A P1-BŐL: {{skill_mentions_json}}

KIMENET (csak JSON):
{"anchor": {"type": "HELYZET", "value": "HELY_RUTIN"},
 "skills": [{"skill_id": null, "label_hu": "...", "label_en": "...",
             "definition_hu": "...", "kep_elemek": ["..."], "tud_elemek": [],
             "aspects": [["KOZ_FIZIKAI", "HELY_RUTIN"]],
             "erosseg": "SULYOS", "viselkedes_hu": ["..."],
             "covered_tesz_ids": ["..."], "external_ref": null, "confidence": 0.0}],
 "narrower_anchor": [{"skill_hu": "...", "suggested_anchor": "SZIN:KOZ_DIGITALIS|HELY_RUTIN"}]}
