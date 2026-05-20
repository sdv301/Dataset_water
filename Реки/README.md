# Папка «Реки» — структура данных

Данные по гидропостам и метеостанциям Якутии для прогноза паводков.

## Три хранилища (не смешивать температуру и уровни)

| Файл | Назначение |
|------|------------|
| [`data/reki/rivers_catalog.json`](../data/reki/rivers_catalog.json) | Каталог рек и постов для выбора в UI (канонические имена) |
| `hydro_meteo.db` | Температуры гидропостов и метеонаблюдения |
| [`data/reki/water_levels.db`](../data/reki/water_levels.db) | Только уровни воды (разреженные ряды из «данные январь») |

## Содержимое папки

| Папка / файл | Описание |
|--------------|----------|
| `Гидропосты_по_рекам/<название реки>/*.csv` | Температура по гидропостам (после сортировки по рекам) |
| `Свод - гидропосты.xlsx` | Справочник: `gidro_num` → река (`namewater`), название поста (`offname`) |
| `Метеостанции_по_типам/` | CSV метеонаблюдений (по типам станций) |
| `данные январь/` | Уровни воды, кодбуки, доп. Excel |
| `prepare_and_build_db.py` | Сортировка CSV + первичный импорт в `hydro_meteo.db` |

## Сборка данных

```powershell
cd <корень проекта>

# 1. Сортировка и проверка папок-рек
python scripts/organize_reki.py --check
python scripts/organize_reki.py --normalize

# 2. SQLite температур (если ещё нет hydro_meteo.db — долго)
python scripts/build_reki_database.py
python scripts/build_reki_database.py --merge-rivers

# 3. JSON-каталог рек для выбора в приложении
python scripts/build_rivers_catalog.py

# 4. Отдельная БД уровней воды
python scripts/build_water_levels_db.py --rebuild

# Статистика
python scripts/build_reki_database.py --stats
```

В приложении: `streamlit run main.py` → **Река и модель** → выбрать реку из каталога → **Собрать все посты** → **Аналитика** / **Прогноз**.

Таблицы в `hydro_meteo.db`: `rivers`, `hydro_stations`, `hydro_temperatures`, `meteo_*`, вид `v_hydro_daily`.  
Уровни **не** пишутся в `hydro_meteo.db` — только в `water_levels.db`.

## Как пользоваться в приложении

1. Запустите: `streamlit run main.py`
2. **Река и модель** — список рек из `rivers_catalog.json`; три датасета: температура, уровень, совместный (merge по дате без записи в одну БД)
3. **База Реки** — посты, предпросмотр температуры
4. **Каталог Реки** — точечная загрузка CSV / Excel из `данные январь`

## Формат CSV гидропоста

Колонки: `gidro_num`, `identifier`, `dt`, `temp`, `temptype`  
В приложении строится **дневной ряд температуры**. Уровни подгружаются отдельно из `water_levels.db`.
