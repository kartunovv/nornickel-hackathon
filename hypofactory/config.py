"""Конфигурация: ключи API, пути, доменные константы."""
from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
ARTIFACTS_DIR = PROJECT_ROOT / "artifacts"
CACHE_DIR = ARTIFACTS_DIR / "cache"

ARTIFACTS_DIR.mkdir(exist_ok=True)
CACHE_DIR.mkdir(exist_ok=True)


def _load_dotenv() -> None:
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()

YC_API_KEY = os.environ.get("YC_API_KEY", "")
YC_FOLDER_ID = os.environ.get("YC_FOLDER_ID", "")

# Модели Yandex AI Studio
GPT_MODEL = os.environ.get("YC_GPT_MODEL", "yandexgpt/latest")
EMB_DOC_MODEL = "text-search-doc/latest"
EMB_QUERY_MODEL = "text-search-query/latest"

# Анонимизация в отчётах института: "Элемент 28" = Ni, "Элемент 29" = Cu
ELEMENT_MAP = {"28": "Ni", "29": "Cu"}

# Цены металлов для оценки ценности гипотез, $/т (настраиваемые)
METAL_PRICE_USD = {"Ni": 16500.0, "Cu": 9500.0}

# Веса критериев ранжирования по умолчанию (сумма = 1)
DEFAULT_WEIGHTS = {
    "value": 0.30,        # потенциальная ценность для KPI
    "feasibility": 0.20,  # реализуемость / риск внедрения
    "novelty": 0.15,      # новизна относительно известных решений
    "evidence": 0.20,     # обоснованность источниками и графом знаний
    "testability": 0.15,  # проверяемость в лабораторных условиях
}
