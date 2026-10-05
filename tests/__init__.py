"""Тесты не читают вашу локальную историю торгов (data/market_history.csv): результат одинаков на любом компьютере.
И не трогают вашу базу (data/…): журнал KBB и итоги торгов — во временной папке."""

import os
import tempfile
from pathlib import Path

os.environ.setdefault("LOT_ANALYZER_HISTORY", os.path.join(os.path.dirname(__file__), "no_history.csv"))
os.environ["LOT_ANALYZER_DATA"] = tempfile.mkdtemp(prefix="lot_analyzer_data_")     # не ~/LotAnalyzer/data

from lot_analyzer import analytics, app, inbox, results  # noqa: E402

_TMP = Path(tempfile.mkdtemp(prefix="lot_analyzer_tests_"))
analytics.KBB_LOG_PATH = _TMP / "kbb_history.csv"
results.HISTORY_PATH = _TMP / "auction_results.csv"
app.BIDS_PATH = _TMP / "my_bids.json"
inbox.SETTINGS_PATH = _TMP / "inbox.json"
inbox.SENT_PATH = _TMP / "inbox_sent.json"
