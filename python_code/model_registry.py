# -*- coding: utf-8 -*-
"""
Реестр моделей (Model Registry) для HydroPredict.

Управление сохранёнными моделями: регистрация, загрузка, перечисление,
получение манифестов и сводных метрик.
"""

import os
import json
import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import joblib


class ModelRegistry:
    """
    Реестр обученных моделей прогнозирования паводков.

    Структура хранения на диске:
        {models_dir}/{river}/{post}/model_h{horizon}_q{quantile}.joblib
        {models_dir}/{river}/{post}/manifest.json
        {models_dir}/{river}/{post}/features.joblib
    """

    def __init__(self, models_dir: str = "models"):
        """
        Инициализация реестра.

        Args:
            models_dir: Корневая директория с моделями.
        """
        self.models_dir = models_dir

    # ------------------------------------------------------------------
    # Регистрация модели
    # ------------------------------------------------------------------

    def register_model(
        self,
        river: str,
        post: str,
        horizon: int,
        quantile: float,
        model: Any,
        metrics: Dict[str, Any],
        params: Dict[str, Any],
    ) -> str:
        """
        Сохранение обученной модели и обновление манифеста.

        Args:
            river: Название реки.
            post: Название поста.
            horizon: Горизонт прогноза (дни).
            quantile: Квантиль.
            model: Обученный объект модели.
            metrics: Метрики обучения (rmse, mae, pinball_loss).
            params: Гиперпараметры модели.

        Returns:
            Путь к сохранённому файлу модели.
        """
        model_dir = self._model_dir(river, post)
        os.makedirs(model_dir, exist_ok=True)

        # Сохраняем модель
        filename = f"model_h{horizon}_q{int(quantile * 100)}.joblib"
        filepath = os.path.join(model_dir, filename)
        joblib.dump(model, filepath)

        # Обновляем манифест
        manifest = self._load_or_create_manifest(river, post)
        key = f"h{horizon}_q{int(quantile * 100)}"
        manifest["models"][key] = {
            "horizon": horizon,
            "quantile": quantile,
            "metrics": metrics,
            "params": self._serialize_params(params),
            "file": filename,
            "registered_at": datetime.datetime.now().isoformat(),
        }
        manifest["last_updated"] = datetime.datetime.now().isoformat()
        self._save_manifest(river, post, manifest)

        return filepath

    # ------------------------------------------------------------------
    # Загрузка модели
    # ------------------------------------------------------------------

    def load_model(
        self, river: str, post: str, horizon: int, quantile: float
    ) -> Any:
        """
        Загрузка модели с диска.

        Args:
            river: Название реки.
            post: Название поста.
            horizon: Горизонт прогноза.
            quantile: Квантиль.

        Returns:
            Объект модели.

        Raises:
            FileNotFoundError: Если файл модели не найден.
        """
        model_dir = self._model_dir(river, post)
        filename = f"model_h{horizon}_q{int(quantile * 100)}.joblib"
        filepath = os.path.join(model_dir, filename)

        if not os.path.exists(filepath):
            raise FileNotFoundError(
                f"Модель не найдена: {filepath}"
            )

        return joblib.load(filepath)

    # ------------------------------------------------------------------
    # Перечисление
    # ------------------------------------------------------------------

    def list_rivers(self) -> List[str]:
        """
        Список рек с обученными моделями.

        Returns:
            Список названий рек.
        """
        if not os.path.exists(self.models_dir):
            return []

        rivers = []
        for name in sorted(os.listdir(self.models_dir)):
            river_path = os.path.join(self.models_dir, name)
            if os.path.isdir(river_path):
                # Проверяем, что внутри есть хотя бы один пост с манифестом
                for post_name in os.listdir(river_path):
                    post_path = os.path.join(river_path, post_name)
                    manifest_path = os.path.join(post_path, "manifest.json")
                    if os.path.isdir(post_path) and os.path.exists(manifest_path):
                        rivers.append(name)
                        break

        return rivers

    def list_posts(self, river: str) -> List[str]:
        """
        Список постов для реки с обученными моделями.

        Args:
            river: Название реки.

        Returns:
            Список названий постов.
        """
        river_dir = os.path.join(self.models_dir, river)
        if not os.path.exists(river_dir):
            return []

        posts = []
        for name in sorted(os.listdir(river_dir)):
            post_path = os.path.join(river_dir, name)
            manifest_path = os.path.join(post_path, "manifest.json")
            if os.path.isdir(post_path) and os.path.exists(manifest_path):
                posts.append(name)

        return posts

    # ------------------------------------------------------------------
    # Манифест
    # ------------------------------------------------------------------

    def get_manifest(self, river: str, post: str) -> Dict[str, Any]:
        """
        Получение манифеста обучения для станции.

        Args:
            river: Название реки.
            post: Название поста.

        Returns:
            Словарь с манифестом.

        Raises:
            FileNotFoundError: Если манифест не найден.
        """
        manifest_path = os.path.join(
            self._model_dir(river, post), "manifest.json"
        )
        if not os.path.exists(manifest_path):
            raise FileNotFoundError(
                f"Манифест не найден: {manifest_path}"
            )

        with open(manifest_path, "r", encoding="utf-8") as f:
            return json.load(f)

    # ------------------------------------------------------------------
    # Сводные метрики
    # ------------------------------------------------------------------

    def get_all_metrics(self) -> List[Dict[str, Any]]:
        """
        Сводка метрик по всем обученным моделям.

        Returns:
            Список словарей с метриками по каждой станции/модели.
        """
        all_metrics: List[Dict[str, Any]] = []

        for river in self.list_rivers():
            for post in self.list_posts(river):
                try:
                    manifest = self.get_manifest(river, post)
                except FileNotFoundError:
                    continue

                models_info = manifest.get("models", {})
                for model_key, model_data in models_info.items():
                    entry = {
                        "river": river,
                        "post": post,
                        "horizon": model_data.get("horizon"),
                        "quantile": model_data.get("quantile"),
                        "registered_at": model_data.get("registered_at"),
                    }
                    # Добавляем метрики
                    metrics = model_data.get("metrics", {})
                    entry.update({
                        "rmse": metrics.get("rmse"),
                        "mae": metrics.get("mae"),
                        "pinball_loss": metrics.get("pinball_loss"),
                    })
                    all_metrics.append(entry)

        return all_metrics

    # ------------------------------------------------------------------
    # Вспомогательные методы
    # ------------------------------------------------------------------

    def _model_dir(self, river: str, post: str) -> str:
        """Путь к директории моделей станции."""
        return os.path.join(self.models_dir, river, post)

    def _load_or_create_manifest(
        self, river: str, post: str
    ) -> Dict[str, Any]:
        """Загрузка существующего манифеста или создание нового."""
        manifest_path = os.path.join(
            self._model_dir(river, post), "manifest.json"
        )

        if os.path.exists(manifest_path):
            with open(manifest_path, "r", encoding="utf-8") as f:
                return json.load(f)

        return {
            "river": river,
            "post": post,
            "created_at": datetime.datetime.now().isoformat(),
            "last_updated": datetime.datetime.now().isoformat(),
            "models": {},
        }

    def _save_manifest(
        self, river: str, post: str, manifest: Dict[str, Any]
    ) -> None:
        """Сохранение манифеста на диск."""
        model_dir = self._model_dir(river, post)
        os.makedirs(model_dir, exist_ok=True)

        manifest_path = os.path.join(model_dir, "manifest.json")
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)

    @staticmethod
    def _serialize_params(params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Сериализация гиперпараметров для JSON.
        Конвертирует numpy-типы в стандартные Python-типы.
        """
        serialized = {}
        for k, v in params.items():
            try:
                json.dumps(v)
                serialized[k] = v
            except (TypeError, ValueError):
                serialized[k] = str(v)
        return serialized
