# Referencia-táblák a `job-all` (J0) lépéshez

- `feor08_hierarchy.csv`, `isco08_feor08_crosswalk.csv`: a `../esco/data/FEOR/feor08.xls` két lapja CSV-ben
  (a projekt környezete nem olvas .xls-t).
  Kézi javítások a hierarchiában (a forrásban összecsúszott vagy hiányzó sorok):
  33 és 331 szétválasztva; 3711 és 3712 szétválasztva; 3722 nevéből a 373 csoport neve törölve;
  021 és 613 alcsoport pótolva (a gyermekelemük nevéből).
  A crosswalk 263, 43, 63, 632, 634 kódjai a FEOR-08-ban nem léteznek; a J0 kihagyja őket.
- `job_all_llm_decisions.json`: a J0 LLM-döntéseinek gyorsítótára (ESCO-pótlás, FEOR-választás).
