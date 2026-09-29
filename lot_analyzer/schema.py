"""Схема итоговой таблицы «Аналитик лотов».

Порядок колонок здесь задаёт порядок столбцов в TSV, XLSX и накопительной таблице.
Колонка с manual=True парсером не заполняется: её значение приходит из
valuations/manual_values.tsv либо вписывается вручную.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Column:
    key: str            # внутреннее имя поля
    title: str          # заголовок в таблице
    manual: bool = False    # заполняется вручную / на этапе 2
    numeric: bool = False   # числовая колонка (для формата XLSX)
    width: int = 18         # ширина столбца в XLSX


COLUMNS: tuple[Column, ...] = (
    Column("auction", "Аукцион", width=12),
    Column("lot_number", "Лот", width=14),
    Column("vin", "VIN", width=20),
    Column("vin_valid", "VIN корректен", width=14),
    Column("year", "Год", numeric=True, width=7),
    Column("make", "Марка", width=14),
    Column("model", "Модель", width=18),
    Column("trim", "Комплектация", width=16),
    Column("odometer_miles", "Пробег, мили", numeric=True, width=13),
    Column("odometer_brand", "Достоверность пробега", width=20),
    Column("title_type", "Тип титула", width=20),
    Column("title_state", "Штат титула", width=12),
    Column("damage_primary", "Первичное повреждение", width=22),
    Column("damage_secondary", "Вторичное повреждение", width=22),
    Column("defects", "Дефекты (сводно)", width=40),
    Column("keys_present", "Ключи", width=10),
    Column("run_and_drive", "На ходу", width=12),
    Column("location", "Локация", width=22),
    Column("sale_date", "Дата продажи", width=14),
    Column("current_bid_usd", "Текущая ставка, $", numeric=True, width=16),
    Column("transport_quote_usd", "Доставка (котировка), $", numeric=True, width=16),
    Column("acv_estimate_usd", "Оценка ACV, $", numeric=True, width=15),
    Column("condition_grade", "Оценка состояния (grade)", width=14),
    Column("wholesale_usd", "Опт — оценка аукциона, $", numeric=True, width=17),
    Column("auction_retail_usd", "Ритейл — оценка аукциона, $", numeric=True, width=18),
    # --- оценки: источники недоступны автоматически, см. README, раздел «Этап 2» ---
    Column("kbb_private_party_usd", "KBB Private Party, $", manual=True, numeric=True, width=19),
    Column("kbb_estimate_usd", "KBB PP (своя оценка), $", numeric=True, width=17),
    Column("kbb_estimate_source", "Откуда оценка KBB", width=22),
    Column("mmr_adjusted_usd", "Adjusted MMR, $", manual=True, numeric=True, width=17),
    Column("cargurus_retail_usd", "Ритейл CarGurus, $", manual=True, numeric=True, width=18),
    Column("cargurus_deal_rating", "Рейтинг CarGurus", manual=True, width=17),
    Column("max_bid_usd", "Максимальная ставка, $", manual=True, numeric=True, width=21),
    Column("retail_estimate_usd", "Цена продажи (моя оценка), $", manual=True, numeric=True, width=20),
    Column("recon_estimate_usd", "Ремонт (моя оценка), $", manual=True, numeric=True, width=17),
    Column("my_proxy_usd", "Мой прокси, $", manual=True, numeric=True, width=13),
    # --- расчёт потолка ставки (lot_analyzer/bid.py, настройки в config/costs.json) ---
    Column("calc_verdict", "Вердикт", width=40),
    Column("calc_max_bid_usd", "Потолок ставки (расчёт), $", numeric=True, width=18),
    Column("market_estimate_usd", "Рынок (обычно платят), $", numeric=True, width=17),
    Column("sale_estimate_usd", "Цена продажи (расчёт), $", numeric=True, width=17),
    Column("calc_costs_usd", "Расходы сверх ставки, $", numeric=True, width=17),
    Column("calc_profit_usd", "Прибыль при потолке, $", numeric=True, width=17),
    Column("calc_breakdown", "Расчёт по статьям", width=60),
    # --- история ---
    Column("lot_description", "Описание лота", width=50),
    Column("history_page", "История (со страницы)", width=40),
    Column("carfax_autocheck", "Carfax / AutoCheck", manual=True, width=30),
    Column("condition_report", "Condition Report", manual=True, width=30),
    # --- фото и происхождение строки ---
    Column("photo_count", "Кол-во фото", numeric=True, width=12),
    Column("photo_urls", "Ссылки на фото", width=40),
    Column("lot_url", "Ссылка на лот", width=40),
    Column("source_file", "Файл-источник", width=28),
    Column("parsed_at", "Дата разбора", width=17),
    Column("needs_review", "Проверить", width=45),
)

COLUMN_TITLES: tuple[str, ...] = tuple(c.title for c in COLUMNS)
COLUMN_KEYS: tuple[str, ...] = tuple(c.key for c in COLUMNS)
BY_KEY: dict[str, Column] = {c.key: c for c in COLUMNS}
BY_TITLE: dict[str, Column] = {c.title: c for c in COLUMNS}

# Колонки, которые перезаписывать при повторном разборе нельзя: в них ручной труд.
MANUAL_KEYS: tuple[str, ...] = tuple(c.key for c in COLUMNS if c.manual)

# Ключ строки в накопительной таблице.
IDENTITY_KEYS: tuple[str, ...] = ("vin", "auction", "lot_number")


def empty_row() -> dict[str, str]:
    """Пустая строка со всеми колонками схемы."""
    return {key: "" for key in COLUMN_KEYS}
