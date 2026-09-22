"""Источники рыночных данных.

Оценщик не привязан к конкретному источнику: он работает с провайдером,
который отдаёт объявления конкурентов (comps) и коридор рейтингов CarGurus.

Сегодня рабочий провайдер один — ManualCsvProvider (файлы, которые вы готовите).
CarGurusDealerProvider — слот под подключение дилерского доступа; он намеренно
не реализован, потому что без легального доступа к данным его нечем наполнить.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class MarketDataProvider(ABC):
    """Интерфейс источника рыночных данных."""

    name = "abstract"

    @abstractmethod
    def comps(self) -> list[dict]:
        """Объявления конкурентов: make, model, year, mileage, price, distance_miles, days_on_market."""

    @abstractmethod
    def deal_ranges(self) -> list[dict]:
        """Коридоры CarGurus: make, model, year_from, year_to, great/good/fair_deal_max."""


from .manual_csv import ManualCsvProvider  # noqa: E402  (после объявления интерфейса)
from .cargurus_dealer import CarGurusDealerProvider  # noqa: E402

__all__ = ["MarketDataProvider", "ManualCsvProvider", "CarGurusDealerProvider"]
