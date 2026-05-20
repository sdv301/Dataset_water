import os
import pandas as pd
import numpy as np
import datetime
from datetime import timedelta
import joblib
from xgboost import XGBRegressor
import optuna
from sklearn.model_selection import TimeSeriesSplit, train_test_split
import warnings

warnings.filterwarnings('ignore')

class FloodPredictor:
    """
    Класс для вероятностного прогнозирования уровня воды.
    """
    def __init__(self, models_dir="models"):
        self.models_dir = models_dir
        self.horizons = [1, 3, 7, 14, 30, 60, 90, 180, 365]
        self.quantiles = [0.5, 0.9, 0.95]
        self.models = {}
        self.features = []
        
        if not os.path.exists(self.models_dir):
            os.makedirs(self.models_dir)

    def _prepare_data(self, data, target_col, horizon):
        df = data.copy()
        if 'date' in df.columns:
            df = df.set_index('date')
            
        df = df.sort_index()
        
        # Сдвиг целевой переменной на горизонт прогнозирования
        df[f'target_h{horizon}'] = df[target_col].shift(-horizon)
        
        # Удаление NaNs, которые образовались из-за сдвига
        df = df.dropna(subset=[f'target_h{horizon}'])
        
        X = df.drop(columns=[target_col, f'target_h{horizon}'])
        y = df[f'target_h{horizon}']
        
        return X, y

    def _calculate_sample_weights(self, y):
        # Взвешивание экстремальных пиков: вес x3 для значений > 90-го перцентиля
        p90 = np.percentile(y, 90)
        weights = np.where(y > p90, 3.0, 1.0)
        return weights

    def _optimize_params(self, X, y, q):
        def objective(trial):
            params = {
                'n_estimators': trial.suggest_int('n_estimators', 50, 500),
                'max_depth': trial.suggest_int('max_depth', 3, 10),
                'learning_rate': trial.suggest_float('learning_rate', 1e-3, 0.3, log=True),
                'subsample': trial.suggest_float('subsample', 0.5, 1.0),
                'colsample_bytree': trial.suggest_float('colsample_bytree', 0.5, 1.0),
                'min_child_weight': trial.suggest_int('min_child_weight', 1, 10),
                'reg_alpha': trial.suggest_float('reg_alpha', 1e-8, 1.0, log=True),
                'reg_lambda': trial.suggest_float('reg_lambda', 1e-8, 1.0, log=True),
                'objective': 'reg:quantileerror',
                'quantile_alpha': q,
                'n_jobs': -1,
                'random_state': 42
            }
            
            # Адаптивная TimeSeriesSplit
            n_samples = len(X)
            if n_samples < 365:
                # Мало данных, простой сплит
                X_train, X_val, y_train, y_val = train_test_split(X, y, test_size=0.2, shuffle=False)
                model = XGBRegressor(**params)
                weights = self._calculate_sample_weights(y_train)
                model.fit(X_train, y_train, sample_weight=weights, eval_set=[(X_val, y_val)], verbose=False)
                preds = model.predict(X_val)
                score = -np.mean(np.where(y_val >= preds, q * (y_val - preds), (1 - q) * (preds - y_val))) # pinball loss
            else:
                n_splits = min(5, n_samples // 180)
                tscv = TimeSeriesSplit(n_splits=n_splits)
                scores = []
                for train_idx, val_idx in tscv.split(X):
                    X_train, X_val = X.iloc[train_idx], X.iloc[val_idx]
                    y_train, y_val = y.iloc[train_idx], y.iloc[val_idx]
                    weights = self._calculate_sample_weights(y_train)
                    
                    model = XGBRegressor(**params)
                    model.fit(X_train, y_train, sample_weight=weights, verbose=False)
                    preds = model.predict(X_val)
                    loss = np.mean(np.where(y_val >= preds, q * (y_val - preds), (1 - q) * (preds - y_val)))
                    scores.append(-loss)
                score = np.mean(scores)
                
            return score

        study = optuna.create_study(direction='maximize')
        study.optimize(objective, n_trials=20, timeout=300) # Лимиты: 20 итераций или 5 минут
        return study.best_params

    def train(self, data: pd.DataFrame, target_col: str = "water_level_cm"):
        """
        Обучает модели для всех горизонтов и квантилей.
        """
        self.features = [c for c in data.columns if c not in [target_col, 'date']]
        
        for h in self.horizons:
            print(f"Обучение для горизонта {h} дней...")
            if len(data) <= h + 30: # Требуем хотя бы 30 дней валидации
                print(f"Недостаточно данных для горизонта {h}. Пропуск.")
                continue
                
            X, y = self._prepare_data(data, target_col, h)
            self.models[h] = {}
            
            for q in self.quantiles:
                print(f"  Обучение квантиля {q}...")
                best_params = self._optimize_params(X, y, q)
                best_params['objective'] = 'reg:quantileerror'
                best_params['quantile_alpha'] = q
                
                model = XGBRegressor(**best_params)
                weights = self._calculate_sample_weights(y)
                model.fit(X, y, sample_weight=weights)
                
                self.models[h][q] = model
                
                # Сохранение модели
                joblib.dump(model, os.path.join(self.models_dir, f'model_h{h}_q{int(q*100)}.joblib'))
                
        # Сохраняем список фичей
        joblib.dump(self.features, os.path.join(self.models_dir, 'features.joblib'))
        print("Обучение завершено.")

    def _get_latest_features(self, date: datetime):
        # Заглушка: в реальности здесь логика получения фичей на конкретную дату.
        # Для простоты возвращаем нули или средние.
        return pd.DataFrame([[0]*len(self.features)], columns=self.features)

    def predict(self, date: datetime.date, horizon: int, warning_level=None, danger_level=None):
        """
        Прогноз на конкретный горизонт от заданной даты.
        """
        if horizon not in self.models:
            return None
        
        X = self._get_latest_features(date)
        
        preds = {}
        for q in self.quantiles:
            model = self.models[horizon][q]
            preds[f'q{int(q*100)}'] = model.predict(X)[0]
            
        result = {
            'date': date + timedelta(days=horizon),
            'median': preds['q50'],
            'q90': preds['q90'],
            'q95': preds['q95'],
        }
        
        if warning_level:
            # Аппроксимация вероятности:
            if preds['q50'] >= warning_level: prob_warn = 0.99
            elif preds['q90'] >= warning_level: prob_warn = 0.50 + 0.40 * (preds['q90'] - warning_level) / (preds['q90'] - preds['q50'] + 1e-5)
            elif preds['q95'] >= warning_level: prob_warn = 0.10 + 0.40 * (preds['q95'] - warning_level) / (preds['q95'] - preds['q90'] + 1e-5)
            else: prob_warn = 0.05 * (preds['q95'] / warning_level)
            result['prob_warning'] = min(max(prob_warn, 0), 1)
            
        if danger_level:
             # Аналогичная грубая аппроксимация для опасного
            if preds['q50'] >= danger_level: prob_dang = 0.99
            elif preds['q90'] >= danger_level: prob_dang = 0.50 + 0.40 * (preds['q90'] - danger_level) / (preds['q90'] - preds['q50'] + 1e-5)
            elif preds['q95'] >= danger_level: prob_dang = 0.10 + 0.40 * (preds['q95'] - danger_level) / (preds['q95'] - preds['q90'] + 1e-5)
            else: prob_dang = 0.05 * (preds['q95'] / danger_level)
            result['prob_danger'] = min(max(prob_dang, 0), 1)
            
        return result

    def predict_month(self, year: int, month: int, warning_level=None, danger_level=None):
        start_date = datetime.date(year, month, 1)
        if month == 12:
            end_date = datetime.date(year+1, 1, 1) - timedelta(days=1)
        else:
            end_date = datetime.date(year, month+1, 1) - timedelta(days=1)
            
        results = []
        current_date = start_date
        base_date = start_date - timedelta(days=1) # Прогноз от вчера
        
        while current_date <= end_date:
            h = (current_date - base_date).days
            # Ищем ближайший доступный горизонт "снизу вверх" для аппроксимации если точного нет
            available_h = [x for x in self.horizons if x >= h]
            use_h = available_h[0] if available_h else self.horizons[-1]
            
            res = self.predict(base_date, use_h, warning_level, danger_level)
            if res:
                res['date'] = current_date # Overwrite for display
                results.append(res)
            current_date += timedelta(days=1)
            
        return pd.DataFrame(results)

    def predict_year(self, year: int, warning_level=None, danger_level=None):
        start_date = datetime.date(year, 1, 1)
        end_date = datetime.date(year, 12, 31)
        
        results = []
        current_date = start_date
        base_date = start_date - timedelta(days=1)
        
        # Для года можно шагать неделями или собирать все дни и агрегировать
        while current_date <= end_date:
            h = (current_date - base_date).days
            available_h = [x for x in self.horizons if x >= h]
            use_h = available_h[0] if available_h else self.horizons[-1]
            
            res = self.predict(base_date, use_h, warning_level, danger_level)
            if res:
                res['date'] = current_date
                results.append(res)
            current_date += timedelta(days=7) # Недельный шаг для года
            
        df = pd.DataFrame(results)
        if len(df) == 0: return df
        df['month'] = pd.to_datetime(df['date']).dt.month
        monthly_agg = df.groupby('month').agg({
            'median': 'mean',
            'q95': 'max',
            'prob_warning': 'max',
            'prob_danger': 'max'
        }).reset_index()
        return monthly_agg
