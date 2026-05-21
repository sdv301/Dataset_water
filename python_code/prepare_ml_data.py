# -*- coding: utf-8 -*-
"""
Подготовка данных для машинного обучения (система прогнозирования паводков).
Скрипт собирает данные из разрозненных CSV файлов и базы hydro_meteo.db,
формируя единую SQLite базу `ml_features.db` с готовыми признаками для ML-моделей.
"""

import os
import sqlite3
import argparse
import numpy as np
import pandas as pd
from pathlib import Path
from tqdm import tqdm

# Определение путей
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DATA_DIR = PROJECT_ROOT / "data"
EXPORT_DIR = PROJECT_ROOT / "Реки" / "данные январь" / "export"

LEVELS_REPORT_CSV = EXPORT_DIR / "levels_wide_report.csv"
LEVELS_REPORT_EXCEL = PROJECT_ROOT / "Реки" / "данные январь" / "Уровни_воды.xlsx"
METEO_CSV = EXPORT_DIR / "dannie_meteo_codes.csv"
SNOW_CSV = EXPORT_DIR / "dannie_снегозапасы.csv"
ICE_CSV = EXPORT_DIR / "dannie_толщина_льда.csv"
STATIONS_CSV = EXPORT_DIR / "dannie_гмс.csv"

ML_FEATURES_DB = DATA_DIR / "ml_features.db"
HYDRO_METEO_DB = PROJECT_ROOT / "Реки" / "hydro_meteo.db"
if not HYDRO_METEO_DB.exists():
    HYDRO_METEO_DB = DATA_DIR / "hydro_meteo.db"

def safe_float(val):
    try:
        if pd.isna(val) or val == '*' or val == '':
            return np.nan
        return float(str(val).replace(',', '.'))
    except Exception:
        return np.nan

def print_step(msg):
    print(f"\n[STEP] {msg}...")

def prepare_data(river_filter=None, stats_only=False):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    
    if stats_only:
        print_stats()
        return

    # 1. Загрузка справочника станций
    print_step("Загрузка справочника ГМС")
    stations_df = pd.read_csv(STATIONS_CSV, encoding='utf-8')
    stations_df.rename(columns={
        'п/п': 'id',
        'Район': 'district',
        'Населенный пункт': 'settlement',
        'ОКТМО': 'oktmo',
        'Река': 'river',
        'ГМС': 'post',
        'Крит уровень ОЯ': 'critical_oya',
        'Низкий уровень ОЯ': 'low_oya',
        'Геопозиция (широта)': 'lat',
        'Геопозиция (долгота)': 'lon'
    }, inplace=True)
    
    # Очистка координат и уровней
    stations_df['lat'] = stations_df['lat'].apply(safe_float)
    stations_df['lon'] = stations_df['lon'].apply(safe_float)
    stations_df['critical_oya'] = stations_df['critical_oya'].apply(safe_float)
    stations_df['low_oya'] = stations_df['low_oya'].apply(safe_float)
    
    # 2. Парсинг уровней воды
    print_step("Парсинг уровней воды (levels_wide_report.csv)")
    # Указываем dtypes для ускорения и избежания предупреждений
    levels_df1 = pd.read_csv(
        LEVELS_REPORT_CSV, 
        encoding='utf-8',
        usecols=['Гидрометеорологический пост', 'Река', 'Район', 'Наименование населенного пункта', 'ОКТМО', 'Дата (дд.мм..гг)', 'Уровень_воды', 'Крит ОЯ', 'Низкий ОЯ']
    )
    
    print_step("Парсинг дополнительных уровней (Уровни_воды.xlsx)")
    try:
        levels_df2 = pd.read_excel(
            LEVELS_REPORT_EXCEL,
            usecols=['Гидрометеорологический пост', 'Река', 'Район', 'Наименование населенного пункта', 'ОКТМО', 'Дата (дд.мм..гг)', 'Уровень_воды', 'Крит ОЯ', 'Низкий ОЯ']
        )
        levels_df = pd.concat([levels_df1, levels_df2], ignore_index=True)
    except Exception as e:
        print(f"Ошибка загрузки Excel: {e}")
        levels_df = levels_df1
    levels_df.rename(columns={
        'Гидрометеорологический пост': 'post',
        'Река': 'river',
        'Район': 'district',
        'ОКТМО': 'oktmo',
        'Дата (дд.мм..гг)': 'date_raw',
        'Уровень_воды': 'water_level_cm',
        'Крит ОЯ': 'crit_oya',
        'Низкий ОЯ': 'low_oya_levels'
    }, inplace=True)
    
    levels_df['water_level_cm'] = levels_df['water_level_cm'].apply(safe_float)
    levels_df['crit_oya'] = levels_df['crit_oya'].apply(safe_float)
    levels_df['low_oya_levels'] = levels_df['low_oya_levels'].apply(safe_float)
    levels_df['date'] = pd.to_datetime(levels_df['date_raw'], errors='coerce').dt.date
    levels_df.dropna(subset=['date'], inplace=True)
    levels_df['date'] = pd.to_datetime(levels_df['date'])
    levels_df.drop_duplicates(subset=['river', 'post', 'date'], inplace=True)
    
    if river_filter:
        levels_df = levels_df[levels_df['river'].str.contains(river_filter, case=False, na=False)]
    
    # Создаем базу данных
    conn = sqlite3.connect(ML_FEATURES_DB)
    
    # Выделяем уникальные станции из уровней
    unique_stations = levels_df.groupby(['river', 'post']).agg(
        critical_oya=('crit_oya', 'first'),
        low_oya=('low_oya_levels', 'first'),
        date_start=('date', 'min'),
        date_end=('date', 'max'),
        records=('water_level_cm', 'count')
    ).reset_index()
    
    # Мержим с ГМС справочником для координат
    merged_stations = pd.merge(
        unique_stations,
        stations_df[['river', 'post', 'lat', 'lon', 'district', 'oktmo']],
        on=['river', 'post'],
        how='left'
    )
    # Запись в БД будет произведена после добавления метео-станций
    
    # 3. Парсинг метеоданных
    print_step("Парсинг метеоданных")
    meteo_df = pd.read_csv(METEO_CSV, encoding='utf-8')
    meteo_df.rename(columns={
        'Гидрометеорологический пост': 'post',
        'Год': 'year',
        'Месяц': 'month',
        'День': 'day',
        'Тмин': 'temp_min',
        'Тср': 'temp_mean',
        'Тмакс': 'temp_max',
        'Количество осадков': 'precip_mm'
    }, inplace=True)
    
    meteo_df['date'] = pd.to_datetime(meteo_df[['year', 'month', 'day']], errors='coerce')
    meteo_df.dropna(subset=['date'], inplace=True)
    meteo_df['temp_min'] = meteo_df['temp_min'].apply(safe_float)
    meteo_df['temp_mean'] = meteo_df['temp_mean'].apply(safe_float)
    meteo_df['temp_max'] = meteo_df['temp_max'].apply(safe_float)
    meteo_df['precip_mm'] = meteo_df['precip_mm'].apply(safe_float)
    
    # Усредняем дубликаты метеоданных на одну дату/станцию
    meteo_daily = meteo_df.groupby(['post', 'date']).agg({
        'temp_min': 'mean',
        'temp_mean': 'mean',
        'temp_max': 'mean',
        'precip_mm': 'sum'
    }).reset_index()
    
    # Находим станции, которые есть ТОЛЬКО в метео
    hydro_posts = set(levels_df['post'].unique())
    meteo_posts = set(meteo_daily['post'].unique())
    meteo_only = list(meteo_posts - hydro_posts)
    
    if meteo_only:
        mo_df = pd.DataFrame({'post': meteo_only})
        mo_df = pd.merge(mo_df, stations_df[['post', 'river', 'lat', 'lon', 'district', 'oktmo']].drop_duplicates('post'), on='post', how='left')
        mo_df['river'] = mo_df['river'].fillna('Метеостанции')
        mo_df['critical_oya'] = np.nan
        mo_df['low_oya'] = np.nan
        mo_df['date_start'] = None
        mo_df['date_end'] = None
        mo_df['records'] = 0
        merged_stations = pd.concat([merged_stations, mo_df], ignore_index=True)
        
    merged_stations.to_sql('stations', conn, if_exists='replace', index=False)
    
    # 4. Снегозапасы
    print_step("Парсинг снегозапасов")
    snow_df = pd.read_csv(SNOW_CSV, encoding='utf-8')
    snow_df.rename(columns={
        'Бассейн рек': 'river_basin',
        'Год': 'year',
        'Месяц': 'month',
        'День': 'day',
        'в % от нормы': 'snow_pct_norm'
    }, inplace=True)
    snow_df['date'] = pd.to_datetime(snow_df[['year', 'month', 'day']], errors='coerce')
    snow_df.dropna(subset=['date'], inplace=True)
    snow_df['snow_pct_norm'] = snow_df['snow_pct_norm'].apply(safe_float)
    snow_daily = snow_df.groupby(['river_basin', 'date']).agg({'snow_pct_norm': 'mean'}).reset_index()

    # 4b. Толщина льда
    print_step("Парсинг толщины льда")
    ice_daily = pd.DataFrame()
    if ICE_CSV.exists():
        ice_df = pd.read_csv(ICE_CSV, encoding='utf-8')
        ice_df.rename(columns={
            'Гидрометеорологический пост': 'post',
            'Наименование реки': 'river_ice',
            'Год': 'year',
            'Месяц': 'month',
            'День': 'day',
            'Толщина льда,см': 'ice_thickness_cm',
        }, inplace=True)
        ice_df['date'] = pd.to_datetime(ice_df[['year', 'month', 'day']], errors='coerce')
        ice_df.dropna(subset=['date'], inplace=True)
        ice_df['ice_thickness_cm'] = ice_df['ice_thickness_cm'].apply(safe_float)
        ice_daily = ice_df.groupby(['post', 'date']).agg({'ice_thickness_cm': 'mean'}).reset_index()

    # 4c. Температуры из hydro_meteo.db (опционально)
    hydro_temp_daily = pd.DataFrame()
    if HYDRO_METEO_DB.exists():
        print_step("Загрузка температур из hydro_meteo.db")
        try:
            hconn = sqlite3.connect(HYDRO_METEO_DB)
            hydro_temp_daily = pd.read_sql_query(
                """
                SELECT hs.river, hs.post_name AS post, ht.dt AS date, AVG(ht.temp) AS hydro_temp_mean
                FROM hydro_temperatures ht
                JOIN hydro_stations hs ON ht.gidro_num = hs.gidro_num
                GROUP BY hs.river, hs.post_name, ht.dt
                """,
                hconn,
            )
            hconn.close()
            if not hydro_temp_daily.empty:
                hydro_temp_daily['date'] = pd.to_datetime(hydro_temp_daily['date'], errors='coerce')
        except Exception as e:
            print(f"  [!] hydro_meteo.db: {e}")
    
    # 5. Обработка по станциям и генерация фичей
    print_step("Генерация признаков (Feature Engineering)")
    
    # Подготавливаем таблицу
    conn.execute("DROP TABLE IF EXISTS daily_features")
    conn.execute("""
        CREATE TABLE daily_features (
            river TEXT,
            post TEXT,
            date DATE,
            water_level_cm REAL,
            temp_min REAL,
            temp_mean REAL,
            temp_max REAL,
            precip_mm REAL,
            snow_pct_norm REAL,
            level_lag_1 REAL,
            level_lag_3 REAL,
            level_lag_7 REAL,
            level_lag_14 REAL,
            level_ma7 REAL,
            level_ma14 REAL,
            level_ma30 REAL,
            delta_1d REAL,
            delta_3d REAL,
            delta_7d REAL,
            day_of_year INTEGER,
            month INTEGER,
            sin_doy REAL,
            cos_doy REAL,
            precip_sum_3d REAL,
            precip_sum_7d REAL,
            precip_sum_14d REAL,
            ice_thickness_cm REAL,
            temp_anomaly REAL,
            level_vs_oya_pct REAL
        )
    """)
    
    for _, row in tqdm(merged_stations.iterrows(), total=len(merged_stations), desc="Обработка постов"):
        river = row['river']
        post = row['post']
        
        group = levels_df[(levels_df['river'] == river) & (levels_df['post'] == post)].copy()
        st_meteo = meteo_daily[meteo_daily['post'] == post].copy()
        
        if group.empty and st_meteo.empty:
            continue
            
        # Создаем полный непрерывный календарь дат
        if not group.empty:
            group = group.sort_values('date')
            min_date = group['date'].min()
            max_date = group['date'].max()
        else:
            st_meteo = st_meteo.sort_values('date')
            min_date = st_meteo['date'].min()
            max_date = st_meteo['date'].max()
            
        if pd.isna(min_date) or pd.isna(max_date):
            continue
            
        date_range = pd.date_range(start=min_date, end=max_date)
        ts_df = pd.DataFrame({'date': date_range})
        
        # Мержим уровни
        if not group.empty:
            st_data = group[['date', 'water_level_cm']].drop_duplicates('date')
            ts_df = pd.merge(ts_df, st_data, on='date', how='left')
        else:
            ts_df['water_level_cm'] = np.nan
        
        # Мержим метео (по названию поста)
        st_meteo = meteo_daily[meteo_daily['post'] == post]
        if st_meteo.empty:
            # Fallback - попытаться найти по реке (очень грубо, но лучше чем ничего)
            pass
        else:
            ts_df = pd.merge(ts_df, st_meteo[['date', 'temp_min', 'temp_mean', 'temp_max', 'precip_mm']], on='date', how='left')
            
        # Заполняем пропуски в метео
        for c in ['temp_min', 'temp_mean', 'temp_max', 'precip_mm']:
            if c in ts_df.columns:
                ts_df[c] = ts_df[c].interpolate(method='linear', limit=7)
                if c == 'precip_mm':
                    ts_df[c] = ts_df[c].fillna(0) # Осадки по дефолту 0
        
        # Мержим снег (по реке)
        st_snow = snow_daily[snow_daily['river_basin'].str.contains(river, case=False, na=False)] if not snow_daily.empty else pd.DataFrame()
        if not st_snow.empty:
            ts_df = pd.merge(ts_df, st_snow[['date', 'snow_pct_norm']], on='date', how='left')
            ts_df['snow_pct_norm'] = ts_df['snow_pct_norm'].ffill(limit=30)
        else:
            ts_df['snow_pct_norm'] = np.nan

        if not ice_daily.empty:
            st_ice = ice_daily[ice_daily['post'] == post]
            if not st_ice.empty:
                ts_df = pd.merge(ts_df, st_ice[['date', 'ice_thickness_cm']], on='date', how='left')
        if 'ice_thickness_cm' not in ts_df.columns:
            ts_df['ice_thickness_cm'] = np.nan

        if not hydro_temp_daily.empty:
            st_ht = hydro_temp_daily[
                (hydro_temp_daily['river'] == river) & (hydro_temp_daily['post'] == post)
            ]
            if not st_ht.empty:
                ts_df = pd.merge(ts_df, st_ht[['date', 'hydro_temp_mean']], on='date', how='left')
                if 'temp_mean' in ts_df.columns and 'hydro_temp_mean' in ts_df.columns:
                    ts_df['temp_mean'] = ts_df['temp_mean'].fillna(ts_df['hydro_temp_mean'])
            
        # Генерация фичей
        # 1. Лаги уровней
        ts_df['level_lag_1'] = ts_df['water_level_cm'].shift(1)
        ts_df['level_lag_3'] = ts_df['water_level_cm'].shift(3)
        ts_df['level_lag_7'] = ts_df['water_level_cm'].shift(7)
        ts_df['level_lag_14'] = ts_df['water_level_cm'].shift(14)
        
        # 2. Скользящие средние
        ts_df['level_ma7'] = ts_df['water_level_cm'].rolling(window=7, min_periods=1).mean()
        ts_df['level_ma14'] = ts_df['water_level_cm'].rolling(window=14, min_periods=1).mean()
        ts_df['level_ma30'] = ts_df['water_level_cm'].rolling(window=30, min_periods=1).mean()
        
        # 3. Скорость изменения
        ts_df['delta_1d'] = ts_df['water_level_cm'] - ts_df['level_lag_1']
        ts_df['delta_3d'] = ts_df['water_level_cm'] - ts_df['level_lag_3']
        ts_df['delta_7d'] = ts_df['water_level_cm'] - ts_df['level_lag_7']
        
        # 4. Сезонность
        ts_df['day_of_year'] = ts_df['date'].dt.dayofyear
        ts_df['month'] = ts_df['date'].dt.month
        ts_df['sin_doy'] = np.sin(2 * np.pi * ts_df['day_of_year'] / 365.25)
        ts_df['cos_doy'] = np.cos(2 * np.pi * ts_df['day_of_year'] / 365.25)
        
        # 5. Кумулятивные осадки
        if 'precip_mm' in ts_df.columns:
            ts_df['precip_sum_3d'] = ts_df['precip_mm'].rolling(window=3, min_periods=1).sum()
            ts_df['precip_sum_7d'] = ts_df['precip_mm'].rolling(window=7, min_periods=1).sum()
            ts_df['precip_sum_14d'] = ts_df['precip_mm'].rolling(window=14, min_periods=1).sum()
        else:
            ts_df['precip_mm'] = np.nan
            ts_df['temp_min'] = np.nan
            ts_df['temp_mean'] = np.nan
            ts_df['temp_max'] = np.nan
            ts_df['precip_sum_3d'] = np.nan
            ts_df['precip_sum_7d'] = np.nan
            ts_df['precip_sum_14d'] = np.nan

        # Аномалия температуры и уровень vs ОЯ
        if 'temp_mean' in ts_df.columns:
            doy_mean = ts_df.groupby(ts_df['date'].dt.dayofyear)['temp_mean'].transform('mean')
            ts_df['temp_anomaly'] = ts_df['temp_mean'] - doy_mean
        else:
            ts_df['temp_anomaly'] = np.nan

        crit = row.get('critical_oya')
        if crit and not pd.isna(crit) and crit != 0:
            ts_df['level_vs_oya_pct'] = 100.0 * ts_df['water_level_cm'] / float(crit)
        else:
            ts_df['level_vs_oya_pct'] = np.nan
            
        ts_df['river'] = river
        ts_df['post'] = post
        ts_df['date'] = ts_df['date'].dt.strftime('%Y-%m-%d')
        
        # Убираем строки без целевой переменной, ЕСЛИ это гидрологический пост.
        # Для чисто метео-постов оставляем данные как есть (water_level_cm будет NaN).
        if not group.empty:
            ts_df.dropna(subset=['water_level_cm'], inplace=True)
        else:
            # Если это метео-пост без уровней воды, удаляем пустые даты метео
            ts_df.dropna(subset=['temp_mean'], inplace=True)
        
        # Добавляем в БД
        ts_df.to_sql('daily_features', conn, if_exists='append', index=False)

    # Создание индексов для быстрого поиска
    print_step("Создание индексов БД")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_features_river_post ON daily_features (river, post, date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_stations_river ON stations (river)")
    conn.commit()
    conn.close()
    
    print("OK Подготовка данных успешно завершена. БД сохранена в:", ML_FEATURES_DB)

def print_stats():
    if not ML_FEATURES_DB.exists():
        print(f"ERROR База данных не найдена: {ML_FEATURES_DB}")
        return
        
    conn = sqlite3.connect(ML_FEATURES_DB)
    stations_count = conn.execute("SELECT COUNT(*) FROM stations").fetchone()[0]
    records_count = conn.execute("SELECT COUNT(*) FROM daily_features").fetchone()[0]
    rivers_count = conn.execute("SELECT COUNT(DISTINCT river) FROM stations").fetchone()[0]
    
    print(f"\nСтатистика базы данных ({ML_FEATURES_DB.name}):")
    print(f"  Реки: {rivers_count}")
    print(f"  Посты: {stations_count}")
    print(f"  Общее количество записей: {records_count:,}")
    conn.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Подготовка данных ML для HydroPredict')
    parser.add_argument('--stats', action='store_true', help='Только вывод статистики')
    parser.add_argument('--river', type=str, help='Обработать только указанную реку')
    args = parser.parse_args()
    
    prepare_data(river_filter=args.river, stats_only=args.stats)
