"""Тесты не читают вашу локальную историю торгов (data/market_history.csv): результат одинаков на любом компьютере."""

import os

os.environ.setdefault("LOT_ANALYZER_HISTORY", os.path.join(os.path.dirname(__file__), "no_history.csv"))
