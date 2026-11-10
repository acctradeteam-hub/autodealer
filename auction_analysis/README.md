# Разбор аукциона

```bash
python3 -m auction_analysis ~/Downloads/auction_all_2026-11-10_1326.csv
# несколько файлов одного дня склеиваются: утренняя запись + повторный снимок
python3 -m auction_analysis утро.csv снимок.csv
# с KBB Private Party: любая таблица CSV/XLSX с колонкой VIN и колонкой KBB
python3 -m auction_analysis утро.csv --kbb kbb_2026-11-10.xlsx
```

На вход — файлы логгера `auction_all_<дата>_HHMM.csv`. Результат — `out/auctions/<дата>/report.md`
и `tables.xlsx`, плюс `cars.csv` — итоги дня для сравнения со следующими аукционами.
Нужны `pandas`, `openpyxl`, `PyYAML`.

Разделы: 0. полнота данных · 1. сводка по аукционам · 2. наши ставки · 3. конкуренты ·
4. ваш сегмент (с KBB — разница KBB − цена) · 5. все покупатели · 6. цены по рынку ·
7. дёшево ушедшие и непроданные · 8. сравнение с прошлыми аукционами.

Настройки (наши имена, конкуренты, buy box, пороги) — в `config.yaml`.
