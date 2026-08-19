# Шаблоны импорта CSV (utf-8-sig)

`observations_template.csv` — `river,post,date,water_level_cm,temp_min,temp_mean,temp_max,precip_mm,snow_pct_norm,ice_thickness_cm`. Кодировка `utf-8-sig` (BOM), `POST /api/import/observations`.
`stations_template.csv` — `river,post,lat,lon,low_oya,critical_oya,name_ru`. `POST /api/import/stations`.
Совместимы с `hydro_service._EXPECTED_DAILY_FEATURES_COLUMNS` и `api_server._parse_csv_upload` (upsert по river/post/date).
Примеры: `python_code/sample_data_minimal.csv` (5 строк), `sample_data_full.csv` (6 строк).
