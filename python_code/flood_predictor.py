# -*- coding: utf-8 -*-
"""
Модуль прогнозирования паводков (HydroPredict).

Вероятностное прогнозирование уровня воды методами квантильной регрессии
с использованием XGBoost и опционально CatBoost.
"""

import os
import json
import sqlite3
import datetime
import warnings
from pathlib import Path
from datetime import timedelta
from typing import Optional, Dict, List, Any, Tuple

import joblib
import numpy as np
import pandas as pd
import optuna
from xgboost import XGBRegressor
from sklearn.model_selection import TimeSeriesSplit, train_test_split
from sklearn.metrics import mean_squared_error, mean_absolute_error

warnings.filterwarnings("ignore")
optuna.logging.set_verbosity(optuna.logging.WARNING)


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

def _pinball_loss(y_true: np.ndarray, y_pred: np.ndarray, q: float) -> float:
    """Вычисление pinball loss (квантильная функция потерь)."""
    delta = y_true - y_pred
    return float(np.mean(np.where(delta >= 0, q * delta, (q - 1) * delta)))


def _lazy_import_catboost():
    """Ленивый импорт CatBoost — не падаем, если пакет не установлен."""
    try:
        from catboost import CatBoostRegressor
        return CatBoostRegressor
    except ImportError:
        return None


# ---------------------------------------------------------------------------
# Основной класс
# ---------------------------------------------------------------------------

class FloodPredictor:
    """
    Класс для вероятностного прогнозирования уровня воды.

    Поддерживает:
    - XGBoost quantile regression (reg:quantileerror)
    - CatBoost quantile regression (опционально)
    - Оптимизацию гиперпараметров через Optuna
    - Загрузку данных из SQLite-базы
    - Сохранение/загрузку моделей с манифестом
    """

    # Целевые горизонты прогноза (дни)
    DEFAULT_HORIZONS: List[int] = [1, 3, 7, 14, 30]

    # Целевые квантили
    DEFAULT_QUANTILES: List[float] = [0.1, 0.5, 0.9, 0.95]

    # Признаки, ожидаемые в обучающем DataFrame
    EXPECTED_FEATURES: List[str] = [
        "temp_min", "temp_mean", "temp_max", "precip_mm",
        "snow_pct_norm", "ice_thickness_cm",
        "level_lag_1", "level_lag_3", "level_lag_7", "level_lag_14",
        "level_ma7", "level_ma14", "level_ma30",
        "delta_1d", "delta_3d", "delta_7d",
        "day_of_year", "month", "sin_doy", "cos_doy",
        "precip_sum_3d", "precip_sum_7d", "precip_sum_14d",
        "temp_anomaly",
    ]

    def __init__(
        self,
        models_dir: str = "models",
        db_path: str = "data/ml_features.db",
        backend: str = "xgboost",
        horizons: Optional[List[int]] = None,
        quantiles: Optional[List[float]] = None,
    ):
        """
        Инициализация предиктора.

        Args:
            models_dir: Корневая директория для хранения моделей.
            db_path: Путь к SQLite-базе с фичами.
            backend: «xgboost» или «catboost».
            horizons: Горизонты прогноза (дни).
            quantiles: Целевые квантили.
        """
        self.models_dir = models_dir
        self.db_path = db_path
        self.backend = backend.lower()
        self.horizons = horizons or self.DEFAULT_HORIZONS
        self.quantiles = quantiles or self.DEFAULT_QUANTILES

        self.models: Dict[int, Dict[float, Any]] = {}
        self.features: List[str] = []
        self.metrics: Dict[str, Any] = {}

        # Текущая река / пост (устанавливается при загрузке данных)
        self._river: Optional[str] = None
        self._post: Optional[str] = None

    # ------------------------------------------------------------------
    # Загрузка данных
    # ------------------------------------------------------------------

    def load_station_data(self, river: str, post: str) -> pd.DataFrame:
        """
        Загрузка фичей станции из SQLite-базы.

        Args:
            river: Название реки (например, «Лена»).
            post: Название поста (например, «Якутск»).

        Returns:
            DataFrame с признаками и целевой переменной.

        Raises:
            FileNotFoundError: Если файл БД не найден.
            ValueError: Если данных для станции нет.
        """
        if not os.path.exists(self.db_path):
            raise FileNotFoundError(
                f"База данных не найдена: {self.db_path}"
            )

        self._river = river
        self._post = post

        conn = sqlite3.connect(self.db_path)
        try:
            query = (
                "SELECT * FROM daily_features "
                "WHERE river = ? AND post = ? "
                "ORDER BY date"
            )
            df = pd.read_sql_query(query, conn, params=(river, post))
        finally:
            conn.close()

        if df.empty:
            raise ValueError(
                f"Нет данных для станции: река='{river}', пост='{post}'"
            )

        # Приведение типов
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])
            
        for col in df.columns:
            if col not in ["date", "river", "post"]:
                df[col] = pd.to_numeric(df[col], errors='coerce').astype(float)

        return df

    # ------------------------------------------------------------------
    # Подготовка данных
    # ------------------------------------------------------------------

    def _prepare_data(
        self,
        data: pd.DataFrame,
        target_col: str,
        horizon: int,
    ) -> Tuple[pd.DataFrame, pd.Series]:
        """
        Подготовка обучающей выборки: сдвиг целевой переменной на горизонт.

        Args:
            data: Исходный DataFrame.
            target_col: Название целевой колонки.
            horizon: Горизонт прогноза в днях.

        Returns:
            Кортеж (X, y).
        """
        df = data.copy()

        if "date" in df.columns:
            df = df.set_index("date")

        # Удаляем служебные колонки, которые не являются признаками
        drop_cols = ["river", "post"]
        for col in drop_cols:
            if col in df.columns:
                df = df.drop(columns=[col])

        df = df.sort_index()

        # Сдвиг целевой переменной на горизонт прогнозирования
        target_name = f"target_h{horizon}"
        df[target_name] = df[target_col].shift(-horizon)

        # Удаление NaN из-за сдвига
        df = df.dropna(subset=[target_name])

        exclude = [target_col, target_name]
        feature_cols = [c for c in df.columns if c not in exclude]
        X = df[feature_cols]
        y = df[target_name]

        return X, y

    def _calculate_sample_weights(self, y: pd.Series, X: pd.DataFrame) -> np.ndarray:
        """
        Расчёт весов обучающих примеров.

        Стратегия взвешивания:
        - Весенние месяцы (апрель-июнь): вес ×2
        - Экстремальные значения (>90-го перцентиля): вес ×3
        - Оба условия складываются мультипликативно.

        Args:
            y: Целевая переменная.
            X: Признаки (нужен month или индекс-дата).

        Returns:
            Массив весов.
        """
        weights = np.ones(len(y), dtype=np.float64)

        # Весенний коэффициент: апрель (4), май (5), июнь (6) — вес ×2
        if "month" in X.columns:
            month_vals = X["month"].values
        elif hasattr(X.index, "month"):
            month_vals = X.index.month
        else:
            month_vals = None

        if month_vals is not None:
            spring_mask = np.isin(month_vals, [4, 5, 6])
            weights[spring_mask] *= 2.0

        # Экстремальные значения: >90-го перцентиля — вес ×3
        p90 = np.percentile(y, 90)
        extreme_mask = y.values > p90
        weights[extreme_mask] *= 3.0

        return weights

    # ------------------------------------------------------------------
    # Оптимизация гиперпараметров
    # ------------------------------------------------------------------

    def _optimize_params(
        self,
        X: pd.DataFrame,
        y: pd.Series,
        q: float,
        n_trials: int = 15,
        timeout: int = 180,
    ) -> Dict[str, Any]:
        """
        Оптимизация гиперпараметров через Optuna.

        Args:
            X: Признаки.
            y: Целевая переменная.
            q: Квантиль.
            n_trials: Максимальное число итераций Optuna.
            timeout: Таймаут в секундах.

        Returns:
            Лучшие гиперпараметры.
        """
        use_catboost = self.backend == "catboost"
        CatBoostRegressor = None
        if use_catboost:
            CatBoostRegressor = _lazy_import_catboost()
            if CatBoostRegressor is None:
                print("  [!] CatBoost не установлен, используем XGBoost.")
                use_catboost = False

        sample_weights_full = self._calculate_sample_weights(y, X)

        def objective(trial: optuna.Trial) -> float:
            if use_catboost:
                params = {
                    "iterations": trial.suggest_int("iterations", 100, 800),
                    "depth": trial.suggest_int("depth", 3, 10),
                    "learning_rate": trial.suggest_float(
                        "learning_rate", 1e-3, 0.3, log=True
                    ),
                    "l2_leaf_reg": trial.suggest_float(
                        "l2_leaf_reg", 1e-3, 10.0, log=True
                    ),
                    "subsample": trial.suggest_float("subsample", 0.5, 1.0),
                    "loss_function": f"Quantile:alpha={q}",
                    "random_seed": 42,
                    "verbose": 0,
                }
            else:
                params = {
                    "n_estimators": trial.suggest_int("n_estimators", 50, 500),
                    "max_depth": trial.suggest_int("max_depth", 3, 10),
                    "learning_rate": trial.suggest_float(
                        "learning_rate", 1e-3, 0.3, log=True
                    ),
                    "subsample": trial.suggest_float("subsample", 0.5, 1.0),
                    "colsample_bytree": trial.suggest_float(
                        "colsample_bytree", 0.5, 1.0
                    ),
                    "min_child_weight": trial.suggest_int(
                        "min_child_weight", 1, 10
                    ),
                    "reg_alpha": trial.suggest_float(
                        "reg_alpha", 1e-8, 1.0, log=True
                    ),
                    "reg_lambda": trial.suggest_float(
                        "reg_lambda", 1e-8, 1.0, log=True
                    ),
                    "objective": "reg:quantileerror",
                    "quantile_alpha": q,
                    "n_jobs": -1,
                    "random_state": 42,
                }

            # Адаптивная кросс-валидация
            n_samples = len(X)
            if n_samples < 365:
                # Мало данных — простой hold-out
                X_tr, X_val, y_tr, y_val = train_test_split(
                    X, y, test_size=0.2, shuffle=False
                )
                idx_tr = X_tr.index
                w_tr = self._calculate_sample_weights(y_tr, X_tr)

                if use_catboost:
                    model = CatBoostRegressor(**params)
                    model.fit(X_tr, y_tr, sample_weight=w_tr)
                else:
                    model = XGBRegressor(**params)
                    model.fit(
                        X_tr, y_tr,
                        sample_weight=w_tr,
                        eval_set=[(X_val, y_val)],
                        verbose=False,
                    )

                preds = model.predict(X_val)
                score = -_pinball_loss(y_val.values, preds, q)
            else:
                n_splits = min(5, n_samples // 180)
                tscv = TimeSeriesSplit(n_splits=max(n_splits, 2))
                scores = []
                for train_idx, val_idx in tscv.split(X):
                    X_tr, X_val = X.iloc[train_idx], X.iloc[val_idx]
                    y_tr, y_val = y.iloc[train_idx], y.iloc[val_idx]
                    w_tr = self._calculate_sample_weights(y_tr, X_tr)

                    if use_catboost:
                        model = CatBoostRegressor(**params)
                        model.fit(X_tr, y_tr, sample_weight=w_tr)
                    else:
                        model = XGBRegressor(**params)
                        model.fit(
                            X_tr, y_tr,
                            sample_weight=w_tr,
                            verbose=False,
                        )

                    preds = model.predict(X_val)
                    loss = _pinball_loss(y_val.values, preds, q)
                    scores.append(-loss)

                score = float(np.mean(scores))

            return score

        study = optuna.create_study(direction="maximize")
        study.optimize(objective, n_trials=n_trials, timeout=timeout)
        return study.best_params

    # ------------------------------------------------------------------
    # Обучение
    # ------------------------------------------------------------------

    def train(
        self,
        data: pd.DataFrame,
        target_col: str = "water_level_cm",
        n_trials: int = 15,
        timeout: int = 180,
    ) -> None:
        """
        Обучение моделей для всех горизонтов и квантилей.

        Args:
            data: DataFrame с признаками и целевой переменной.
            target_col: Название колонки с уровнем воды.
            n_trials: Число итераций Optuna.
            timeout: Таймаут Optuna (секунды).
        """
        self.features = [
            c for c in data.columns
            if c not in [target_col, "date", "river", "post"]
        ]
        self.metrics = {}

        use_catboost = self.backend == "catboost"
        CatBoostRegressor = None
        if use_catboost:
            CatBoostRegressor = _lazy_import_catboost()
            if CatBoostRegressor is None:
                print("[!] CatBoost не установлен, переключаемся на XGBoost.")
                use_catboost = False
                self.backend = "xgboost"

        for h in self.horizons:
            print(f"  Горизонт {h} дн.:")
            if len(data) <= h + 30:
                print(f"    ⚠ Недостаточно данных (нужно >{h + 30} строк). Пропуск.")
                continue

            X, y = self._prepare_data(data, target_col, h)
            self.models[h] = {}
            self.metrics[h] = {}

            for q in self.quantiles:
                print(f"    Квантиль {q} ...", end=" ", flush=True)
                best_params = self._optimize_params(X, y, q, n_trials, timeout)

                # Финальная тренировка на всех данных
                if use_catboost:
                    best_params["loss_function"] = f"Quantile:alpha={q}"
                    best_params["random_seed"] = 42
                    best_params["verbose"] = 0
                    model = CatBoostRegressor(**best_params)
                else:
                    best_params["objective"] = "reg:quantileerror"
                    best_params["quantile_alpha"] = q
                    best_params["n_jobs"] = -1
                    best_params["random_state"] = 42
                    model = XGBRegressor(**best_params)

                weights = self._calculate_sample_weights(y, X)
                model.fit(X, y, sample_weight=weights)

                self.models[h][q] = model

                # Расчёт метрик на обучающей выборке (in-sample)
                preds = model.predict(X)
                rmse = float(np.sqrt(mean_squared_error(y, preds)))
                mae = float(mean_absolute_error(y, preds))
                pbl = _pinball_loss(y.values, preds, q)

                self.metrics[h][q] = {
                    "rmse": round(rmse, 2),
                    "mae": round(mae, 2),
                    "pinball_loss": round(pbl, 4),
                    "params": best_params,
                }

                print(f"RMSE={rmse:.1f}, MAE={mae:.1f}, PBL={pbl:.4f}")

                # Сохранение модели
                self._save_model(model, h, q, best_params)

        # Сохраняем список фичей
        model_dir = self._get_model_dir()
        os.makedirs(model_dir, exist_ok=True)
        joblib.dump(self.features, os.path.join(model_dir, "features.joblib"))

        # Сохраняем манифест
        self._save_manifest()

        print("  ✓ Обучение завершено.")

    # ------------------------------------------------------------------
    # Оценка (Evaluate)
    # ------------------------------------------------------------------

    def evaluate(
        self,
        data: pd.DataFrame,
        target_col: str = "water_level_cm",
        test_size: float = 0.2,
    ) -> Dict[str, Any]:
        """
        Оценка моделей на hold-out выборке.

        Args:
            data: Полный DataFrame.
            target_col: Целевая колонка.
            test_size: Доля тестовой выборки (от конца временного ряда).

        Returns:
            Словарь с метриками по горизонтам и квантилям.
        """
        eval_metrics: Dict[str, Any] = {}

        for h in self.horizons:
            if h not in self.models:
                continue

            X, y = self._prepare_data(data, target_col, h)
            split_idx = int(len(X) * (1 - test_size))
            X_test = X.iloc[split_idx:]
            y_test = y.iloc[split_idx:]

            eval_metrics[h] = {}

            for q in self.quantiles:
                if q not in self.models[h]:
                    continue

                model = self.models[h][q]
                preds = model.predict(X_test)

                rmse = float(np.sqrt(mean_squared_error(y_test, preds)))
                mae = float(mean_absolute_error(y_test, preds))
                pbl = _pinball_loss(y_test.values, preds, q)

                eval_metrics[h][q] = {
                    "rmse": round(rmse, 2),
                    "mae": round(mae, 2),
                    "pinball_loss": round(pbl, 4),
                    "n_test_samples": len(y_test),
                }

        return eval_metrics

    # ------------------------------------------------------------------
    # Прогнозирование
    # ------------------------------------------------------------------

    def _load_features_from_db(self, date: datetime.date) -> Optional[pd.DataFrame]:
        """
        Загрузка реальных фичей из БД для заданной даты.

        Args:
            date: Дата, для которой нужны признаки.

        Returns:
            DataFrame с одной строкой признаков или None.
        """
        if not os.path.exists(self.db_path):
            return None

        conn = sqlite3.connect(self.db_path)
        try:
            query = (
                "SELECT * FROM daily_features "
                "WHERE river = ? AND post = ? AND date = ? "
                "LIMIT 1"
            )
            date_str = date.isoformat()
            df = pd.read_sql_query(
                query, conn,
                params=(self._river, self._post, date_str),
            )
        finally:
            conn.close()

        if df.empty:
            return None

        # Оставляем только нужные фичи
        available = [c for c in self.features if c in df.columns]
        return df[available]

    def predict(
        self,
        date: datetime.date,
        horizon: Optional[int] = None,
        warning_level: Optional[float] = None,
        danger_level: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Прогноз на конкретный горизонт от заданной даты.

        Args:
            date: Базовая дата прогноза.
            horizon: Горизонт (дни). Если None — прогноз по всем горизонтам.
            warning_level: Уровень НЯ (повышенный).
            danger_level: Уровень ОЯ (опасный).

        Returns:
            Словарь с прогнозом или None.
        """
        horizons_to_predict = [horizon] if horizon else self.horizons

        results = []
        for h in horizons_to_predict:
            if h not in self.models:
                continue

            # Попытка загрузить реальные фичи из БД
            X = self._load_features_from_db(date)
            if X is None:
                # Заглушка: нулевые фичи
                X = pd.DataFrame(
                    [[0] * len(self.features)], columns=self.features
                )

            preds = {}
            for q in self.quantiles:
                if q not in self.models[h]:
                    continue
                model = self.models[h][q]
                preds[f"q{int(q * 100)}"] = float(model.predict(X)[0])

            result: Dict[str, Any] = {
                "date": date + timedelta(days=h),
                "horizon": h,
            }

            # Добавляем квантильные прогнозы
            if "q10" in preds:
                result["q10"] = preds["q10"]
            if "q50" in preds:
                result["median"] = preds["q50"]
            if "q90" in preds:
                result["q90"] = preds["q90"]
            if "q95" in preds:
                result["q95"] = preds["q95"]

            # Расчёт вероятностей превышения критических уровней
            if warning_level is not None and "q50" in preds:
                result["prob_warning"] = self._estimate_exceedance_prob(
                    preds, warning_level
                )
            if danger_level is not None and "q50" in preds:
                result["prob_danger"] = self._estimate_exceedance_prob(
                    preds, danger_level
                )

            results.append(result)

        if horizon is not None:
            return results[0] if results else None
        return results if results else None

    @staticmethod
    def _estimate_exceedance_prob(
        preds: Dict[str, float], threshold: float
    ) -> float:
        """
        Аппроксимация вероятности превышения заданного уровня
        на основе квантильных прогнозов.

        Args:
            preds: Словарь квантильных прогнозов.
            threshold: Пороговое значение.

        Returns:
            Вероятность превышения [0, 1].
        """
        q50 = preds.get("q50", 0)
        q90 = preds.get("q90", q50)
        q95 = preds.get("q95", q90)

        if q50 >= threshold:
            prob = 0.99
        elif q90 >= threshold:
            denom = q90 - q50 + 1e-5
            prob = 0.50 + 0.40 * (q90 - threshold) / denom
        elif q95 >= threshold:
            denom = q95 - q90 + 1e-5
            prob = 0.10 + 0.40 * (q95 - threshold) / denom
        else:
            prob = 0.05 * (q95 / (threshold + 1e-5))

        return round(min(max(prob, 0.0), 1.0), 4)

    # ------------------------------------------------------------------
    # Важность признаков
    # ------------------------------------------------------------------

    def get_feature_importance(
        self,
        horizon: Optional[int] = None,
        quantile: float = 0.5,
    ) -> Dict[str, float]:
        """
        Возвращает важность признаков для указанной модели.

        Args:
            horizon: Горизонт прогноза. Если None, берётся первый доступный.
            quantile: Квантиль модели.

        Returns:
            Словарь {имя_признака: важность}.

        Raises:
            ValueError: Если модель не найдена.
        """
        if horizon is None:
            if not self.models:
                raise ValueError("Модели не обучены.")
            horizon = list(self.models.keys())[0]

        if horizon not in self.models or quantile not in self.models[horizon]:
            raise ValueError(
                f"Модель не найдена: горизонт={horizon}, квантиль={quantile}"
            )

        model = self.models[horizon][quantile]

        # XGBoost
        if hasattr(model, "feature_importances_"):
            importances = model.feature_importances_
            feature_names = self.features
            if len(feature_names) != len(importances):
                feature_names = [f"f{i}" for i in range(len(importances))]
            return dict(
                sorted(
                    zip(feature_names, importances.tolist()),
                    key=lambda x: x[1],
                    reverse=True,
                )
            )

        # CatBoost
        if hasattr(model, "get_feature_importance"):
            importances = model.get_feature_importance()
            feature_names = self.features
            if len(feature_names) != len(importances):
                feature_names = [f"f{i}" for i in range(len(importances))]
            return dict(
                sorted(
                    zip(feature_names, importances.tolist()),
                    key=lambda x: x[1],
                    reverse=True,
                )
            )

        return {}

    # ------------------------------------------------------------------
    # Метрики
    # ------------------------------------------------------------------

    def get_metrics(self) -> Dict[str, Any]:
        """
        Возвращает метрики обучения (RMSE, MAE, Pinball Loss).

        Returns:
            Словарь вида {horizon: {quantile: {rmse, mae, pinball_loss}}}.
        """
        return self.metrics

    # ------------------------------------------------------------------
    # Сохранение / загрузка моделей
    # ------------------------------------------------------------------

    def _get_model_dir(self) -> str:
        """Формирование пути к директории моделей текущей станции."""
        if self._river and self._post:
            return os.path.join(self.models_dir, self._river, self._post)
        return self.models_dir

    def _save_model(
        self,
        model: Any,
        horizon: int,
        quantile: float,
        params: Dict[str, Any],
    ) -> str:
        """
        Сохранение одной модели в файл joblib.

        Args:
            model: Обученная модель.
            horizon: Горизонт.
            quantile: Квантиль.
            params: Гиперпараметры модели.

        Returns:
            Путь к сохранённому файлу.
        """
        model_dir = self._get_model_dir()
        os.makedirs(model_dir, exist_ok=True)

        filename = f"model_h{horizon}_q{int(quantile * 100)}.joblib"
        filepath = os.path.join(model_dir, filename)
        joblib.dump(model, filepath)

        return filepath

    def _save_manifest(self) -> str:
        """
        Сохранение манифеста обучения (JSON) с датой, метриками, параметрами.

        Returns:
            Путь к файлу манифеста.
        """
        model_dir = self._get_model_dir()
        os.makedirs(model_dir, exist_ok=True)

        manifest = {
            "training_date": datetime.datetime.now().isoformat(),
            "river": self._river,
            "post": self._post,
            "backend": self.backend,
            "horizons": self.horizons,
            "quantiles": self.quantiles,
            "features": self.features,
            "metrics": {},
        }

        # Конвертация ключей словаря метрик в строки для JSON
        for h, q_dict in self.metrics.items():
            manifest["metrics"][str(h)] = {}
            for q, m in q_dict.items():
                manifest["metrics"][str(h)][str(q)] = m

        filepath = os.path.join(model_dir, "manifest.json")
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)

        return filepath

    def load_models(self, river: str, post: str) -> None:
        """
        Загрузка ранее обученных моделей с диска.

        Args:
            river: Название реки.
            post: Название поста.
        """
        self._river = river
        self._post = post
        model_dir = self._get_model_dir()

        if not os.path.exists(model_dir):
            raise FileNotFoundError(
                f"Директория моделей не найдена: {model_dir}"
            )

        # Загрузка фичей
        features_path = os.path.join(model_dir, "features.joblib")
        if os.path.exists(features_path):
            self.features = joblib.load(features_path)

        # Загрузка манифеста
        manifest_path = os.path.join(model_dir, "manifest.json")
        if os.path.exists(manifest_path):
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            self.horizons = manifest.get("horizons", self.DEFAULT_HORIZONS)
            self.quantiles = manifest.get("quantiles", self.DEFAULT_QUANTILES)

        # Загрузка моделей
        self.models = {}
        for h in self.horizons:
            self.models[h] = {}
            for q in self.quantiles:
                filename = f"model_h{h}_q{int(q * 100)}.joblib"
                filepath = os.path.join(model_dir, filename)
                if os.path.exists(filepath):
                    self.models[h][q] = joblib.load(filepath)

        print(
            f"Загружены модели для {river} / {post}: "
            f"{sum(len(v) for v in self.models.values())} шт."
        )
