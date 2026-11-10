"""Разбор итогов аукциона по файлу логгера auction_all_<дата>_HHMM.csv.

Запуск:
    python3 -m auction_analysis путь/к/auction_all_2026-11-10_1326.csv

Результат в out/auctions/<дата>/: report.md (разбор текстом) и tables.xlsx (таблицы по разделам).
Порядок разбора всегда один и тот же:
  0. Полнота данных — можно ли доверять цифрам
  1. Сводка по аукционам
  2. Наше участие
  3. Покупатели и конкуренты
  4. Цены по сегментам и моделям
  5. Лоты, ушедшие дёшево, и непроданные
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG = Path(__file__).with_name("config.yaml")

SOLD = "Продано"
NO_DATA = "Не выставлялась / нет данных"


def load(path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    df = pd.read_csv(path, encoding="utf-8-sig", dtype=str, keep_default_na=False)
    num = ["По прямой от 92620, mi", "По дороге ≈, mi", "Лот #", "Год", "Пробег, mi", "Цена продажи",
           "Ставка андербиддера", "Макс. ставка дилера", "Кол-во ставок", "Кол-во дилеров",
           "Asking (старт)", "Floor (последний)", "Шаг №", "Сумма ставки"]
    for c in num:
        df[c] = pd.to_numeric(df[c].str.replace(",", ""), errors="coerce")
    cars = df[df["Строка"] == "ИТОГ"].copy()
    bids = df[df["Строка"] == "ставка"].copy()
    cars["Лот"] = cars["Аукцион"].map(short) + " " + cars["Лейн"] + "-" + cars["Лот #"].astype("Int64").astype(str)
    cars["Модель"] = cars["Марка / модель"].map(base_model)
    return cars, bids


KEY = ["Аукцион", "Лейн", "Лот #"]


def load_many(paths: list[Path]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Несколько файлов одного дня (например, утренняя запись и повторный снимок) → один набор.

    По каждой машине берётся самая полная строка: сначала с известным итогом, затем со ставками.
    Ставки объединяются без повторов.
    """
    parts = [load(p) for p in paths]
    cars = pd.concat([c for c, _ in parts], ignore_index=True)
    bids = pd.concat([b for _, b in parts], ignore_index=True)
    final = (cars["Результат"] != NO_DATA) & (cars["Результат"] != "Не продано / нет финала")
    cars["_score"] = final.astype(int) * 2 + cars["Кол-во ставок"].notna().astype(int)
    cars = cars.sort_values("_score", ascending=False, kind="stable").drop(columns="_score")
    # лучшая строка по машине; пустые поля (штат, покупатель, лесенка…) дополняются из других файлов
    text = cars.columns[cars.dtypes == object]
    cars[text] = cars[text].replace("", pd.NA)
    cars = cars.groupby(KEY, sort=True, dropna=False).first().reset_index()
    cars[text] = cars[text].fillna("")
    # «прошла до запуска логгера» не про машину, у которой покупатель нашёлся в другом файле
    cars.loc[(cars["Покупатель"] != "") & (cars["Примечание"] == "прошла до запуска логгера"), "Примечание"] = ""
    cars = cars[[c for c in parts[0][0].columns]]
    bids = bids.drop_duplicates(KEY + ["Время ставки", "Тип ставки", "Бейдж", "Сумма ставки", "Текст"])
    return cars, bids


def short(name: str) -> str:
    return re.sub(r"^CarMax\s+|\s+Auction Center$", "", name).strip()


TWO_WORD = {"Model", "Grand", "Santa", "Range", "Town", "Land", "Rover", "Sport", "Transit", "Express", "Wrangler"}
TWO_WORD_MAKES = {"Land", "Alfa", "Aston"}


def base_model(s: str) -> str:
    """«Honda CR-V SE» → «Honda CR-V», «Tesla Model 3 Long Range» → «Tesla Model 3»."""
    parts = s.split()
    n = 2
    if parts and parts[0] in TWO_WORD_MAKES:
        n += 1
    if len(parts) > n and parts[n - 1] in TWO_WORD:
        n += 1
    return " ".join(parts[:n])


def money(v) -> str:
    return "—" if pd.isna(v) else f"${v:,.0f}"


def pct(a, b) -> str:
    return "—" if not b else f"{100 * a / b:.0f}%"


def md_table(df: pd.DataFrame, max_rows: int = 30) -> str:
    if df.empty:
        return "_нет данных_\n"
    df = df.head(max_rows)
    cols = [str(c) for c in df.columns]
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for row in df.itertuples(index=False):
        lines.append("| " + " | ".join("" if pd.isna(v) else str(v) for v in row) + " |")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- 0. полнота данных
def coverage(cars: pd.DataFrame, bids: pd.DataFrame, gap_min: int) -> tuple[pd.DataFrame, list[str]]:
    rows, notes = [], []
    for (auc, lane), g in cars.groupby(["Аукцион", "Лейн"]):
        # только ставки дилеров: Asking/Floor появляются до старта лейна и дают ложные «дыры»
        b = bids[(bids["Аукцион"] == auc) & (bids["Лейн"] == lane) & (bids["Тип ставки"] == "Дилер")]
        t = (pd.to_datetime(b["Время ставки"], format="%H:%M:%S", errors="coerce")
             .dropna().sort_values().reset_index(drop=True))
        d = t.diff()
        gaps = [f"{t[i - 1]:%H:%M}–{t[i]:%H:%M}" for i in d[d > pd.Timedelta(minutes=gap_min)].index]
        recorded = g["Результат"] != NO_DATA
        with_bids = g["Кол-во ставок"].fillna(0) > 0
        last_rec = g.loc[recorded, "Лот #"].max()
        not_run = g[(g["Результат"] == NO_DATA) & (g["Лот #"] > last_rec)] if pd.notna(last_rec) else g
        holes = g[(g["Результат"] == NO_DATA) & (g["Лот #"] < last_rec)] if pd.notna(last_rec) else g.iloc[0:0]
        rows.append({
            "Аукцион": short(auc), "Лейн": lane, "Машин в списке": len(g),
            "Итог известен": int(recorded.sum()), "Продано": int((g["Результат"] == SOLD).sum()),
            "Ставки записаны": int(with_bids.sum()),
            "Без итога внутри прогона": len(holes), "Не дошли / после записи": len(not_run),
            "Запись с": t.min().strftime("%H:%M") if len(t) else "—",
            "Запись по": t.max().strftime("%H:%M") if len(t) else "—",
            "Дыры в записи": ", ".join(gaps) or "нет",
        })
    cov = pd.DataFrame(rows)
    total, rec = cov["Машин в списке"].sum(), cov["Итог известен"].sum()
    wb, holes = cov["Ставки записаны"].sum(), cov["Без итога внутри прогона"].sum()
    notes.append(f"Итог известен по {rec} машинам из {total} ({pct(rec, total)}), "
                 f"ставки записаны по {wb} ({pct(wb, total)}).")
    if holes:
        notes.append(f"**{holes} машин без итога внутри прогона**: лейн шёл, а логгер их не видел. "
                     "Цифры ниже — только по машинам с известным итогом.")
    if wb < rec:
        notes.append(f"По {rec - wb} машинам есть только цена из списка лейна, без ставок и покупателя. "
                     "Чаще всего это значит, что страница Velocicast потеряла связь и перестала обновляться: "
                     "если в окошке логгера «Последнее событие» долго не меняется во время торгов, нажмите F5 "
                     "и вставьте скрипт заново. Покупатели, андербиддеры и ставки ниже посчитаны только по машинам "
                     "со ставками.")
    nobuyer = cars[(cars["Результат"] == SOLD) & (cars["Примечание"] != "")]
    if len(nobuyer):
        notes.append(f"У {len(nobuyer)} проданных машин покупатель определён неточно "
                     f"({', '.join(sorted(set(nobuyer['Примечание'])))}).")
    return cov, notes


# --------------------------------------------------------------------------- 1. сводка
def overview(cars: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for auc, g in cars.groupby("Аукцион"):
        run = g[g["Результат"] != NO_DATA]
        sold = g[g["Результат"] == SOLD]
        rows.append({
            "Аукцион": short(auc), "Штат": g["Штат"].iloc[0], "Дорога ≈ mi": g["По дороге ≈, mi"].iloc[0],
            "Итог известен": len(run), "Продано": len(sold), "Продаваемость": pct(len(sold), len(run)),
            "Оборот": money(sold["Цена продажи"].sum()), "Средняя": money(sold["Цена продажи"].mean()),
            "Медиана": money(sold["Цена продажи"].median()),
            "Дилеров на лот (мед.)": sold["Кол-во дилеров"].median(),
            "Цена / asking": f"{(sold['Цена продажи'] / sold['Asking (старт)']).median():.2f}×"
            if sold["Asking (старт)"].notna().any() else "—",
        })
    return pd.DataFrame(rows).sort_values("Дорога ≈ mi")


# --------------------------------------------------------------------------- 2. наше участие
def our_activity(cars: pd.DataFrame, bids: pd.DataFrame, us: dict) -> tuple[pd.DataFrame, str]:
    pat = "|".join(re.escape(n) for n in us.get("names", [])) or "$^"
    mine = bids[bids["Дилер"].str.contains(pat, case=False, regex=True) |
                bids["Бейдж"].isin([str(b) for b in us.get("badges", [])])]
    if mine.empty:
        return pd.DataFrame(), "Ваших ставок в записанных лотах нет."
    rows = []
    for (auc, lane, lot), g in mine.groupby(["Аукцион", "Лейн", "Лот #"]):
        car = cars[(cars["Аукцион"] == auc) & (cars["Лейн"] == lane) & (cars["Лот #"] == lot)].iloc[0]
        our_max = g["Сумма ставки"].max()
        won = bool(re.search(pat, car["Покупатель"], re.I))
        rows.append({
            "Лот": car["Лот"], "Машина": f"{car['Год']:.0f} {car['Марка / модель']}", "Пробег": car["Пробег, mi"],
            "Наш макс.": money(our_max), "Ставок": len(g), "Итог": "куплено" if won else car["Результат"].lower(),
            "Цена": money(car["Цена продажи"]), "Купил": "" if won else car["Покупатель"],
            "Не хватило": "" if won or pd.isna(car["Цена продажи"]) else money(car["Цена продажи"] - our_max),
        })
    t = pd.DataFrame(rows)
    badges = sorted(set(mine["Бейдж"]))
    won = (t["Итог"] == "куплено").sum()
    return t, f"Ставили на {len(t)} лотов (бейдж {', '.join(badges)}), куплено {won}."


# --------------------------------------------------------------------------- 3. покупатели
def buyers(cars: pd.DataFrame, top: int) -> tuple[pd.DataFrame, pd.DataFrame, str]:
    sold = cars[(cars["Результат"] == SOLD) & (cars["Покупатель"] != "")]
    b = (sold.groupby("Покупатель")
         .agg(Машин=("Лот", "count"), Потрачено=("Цена продажи", "sum"), Средняя=("Цена продажи", "mean"),
              Аукционы=("Аукцион", lambda s: ", ".join(sorted(set(map(short, s))))),
              Модели=("Модель", lambda s: ", ".join(s.value_counts().head(3).index)))
         .sort_values(["Машин", "Потрачено"], ascending=False).reset_index())
    share = b.head(5)["Машин"].sum() / max(len(sold), 1)
    b["Потрачено"] = b["Потрачено"].map(money)
    b["Средняя"] = b["Средняя"].map(money)
    u = (sold[sold["2-й участник (андербиддер)"] != ""].groupby("2-й участник (андербиддер)")
         .agg(Раз_вторым=("Лот", "count"), Подняли_до=("Цена продажи", "median"))
         .sort_values("Раз_вторым", ascending=False).reset_index()
         .rename(columns={"2-й участник (андербиддер)": "Дилер", "Раз_вторым": "Раз вторым",
                          "Подняли_до": "Медиана цены"}))
    u["Медиана цены"] = u["Медиана цены"].map(money)
    note = (f"Покупателей: {len(b)}. Топ-5 забрали {pct(share * len(sold), len(sold))} проданных машин. "
            f"Однократных покупателей: {(b['Машин'] == 1).sum()}.")
    return b.head(top), u.head(top), note


# --------------------------------------------------------------------------- 4. сегменты
def segments(cars: pd.DataFrame, top: int) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    sold = cars[cars["Результат"] == SOLD].copy()
    sold["Цена"] = pd.cut(sold["Цена продажи"], [0, 5000, 10000, 15000, 20000, 30000, 1e9],
                          labels=["до $5k", "$5–10k", "$10–15k", "$15–20k", "$20–30k", "$30k+"])
    sold["Возраст"] = pd.cut(sold["Год"], [0, 2012, 2016, 2019, 2022, 3000],
                             labels=["≤2012", "2013–16", "2017–19", "2020–22", "2023+"])

    def seg(col):
        g = sold.groupby(col, observed=True).agg(
            Продано=("Лот", "count"), Медиана=("Цена продажи", "median"),
            Дилеров=("Кол-во дилеров", "median"), Пробег=("Пробег, mi", "median")).reset_index()
        g["Медиана"] = g["Медиана"].map(money)
        g["Пробег"] = g["Пробег"].map(lambda v: "—" if pd.isna(v) else f"{v:,.0f}")
        return g

    models = (sold.groupby("Модель").agg(
        Продано=("Лот", "count"), Медиана=("Цена продажи", "median"), Мин=("Цена продажи", "min"),
        Макс=("Цена продажи", "max"), Год_мед=("Год", "median"), Дилеров=("Кол-во дилеров", "median"))
        .sort_values("Продано", ascending=False).head(top).reset_index())
    for c in ("Медиана", "Мин", "Макс"):
        models[c] = models[c].map(money)
    models = models.rename(columns={"Год_мед": "Год (мед.)"})
    return seg("Цена"), seg("Возраст"), models


# --------------------------------------------------------------------------- 5. дешёвые и непроданные
def cheap_and_unsold(cars: pd.DataFrame, ratio: float, min_peers: int, box: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    sold = cars[cars["Результат"] == SOLD]
    rows = []
    for _, c in in_box(sold, box).iterrows():
        peers = sold[(sold["Модель"] == c["Модель"]) & ((sold["Год"] - c["Год"]).abs() <= 1)
                     & ((sold["Пробег, mi"] - c["Пробег, mi"]).abs() <= 40000) & (sold.index != c.name)]
        if len(peers) < min_peers:
            continue
        med = peers["Цена продажи"].median()
        if c["Цена продажи"] < ratio * med:
            rows.append({"Лот": c["Лот"], "Машина": f"{c['Год']:.0f} {c['Марка / модель']}",
                         "Пробег": f"{c['Пробег, mi']:,.0f}" if pd.notna(c["Пробег, mi"]) else "—",
                         "Цена": money(c["Цена продажи"]), "Медиана похожих": money(med),
                         "Скидка": f"{100 * (1 - c['Цена продажи'] / med):.0f}%", "Похожих": len(peers),
                         "Дилеров": c["Кол-во дилеров"], "Купил": c["Покупатель"]})
    cheap = pd.DataFrame(rows)
    unsold = cars[cars["Результат"].str.startswith("Не продано")].copy()
    # «нет финала» при ставке не ниже floor — почти всегда продажа, которую логгер не застал
    missed = (unsold["Результат"] == "Не продано / нет финала") & (unsold["Макс. ставка дилера"] >= unsold["Floor (последний)"])
    unsold["Результат"] = unsold["Результат"].where(~missed, "финал не записан (ставка ≥ floor)")
    unsold = unsold.assign(
        Машина=unsold["Год"].map(lambda y: f"{y:.0f}") + " " + unsold["Марка / модель"],
        **{"Макс. ставка": unsold["Макс. ставка дилера"].map(money), "Floor": unsold["Floor (последний)"].map(money),
           "До floor": (unsold["Floor (последний)"] - unsold["Макс. ставка дилера"]).map(money)},
    )[["Лот", "Машина", "Пробег, mi", "Результат", "Макс. ставка", "Floor", "До floor", "Кол-во дилеров"]]
    return cheap, unsold


def in_box(df: pd.DataFrame, box: dict) -> pd.DataFrame:
    m = pd.Series(True, index=df.index)
    if box.get("price_min"): m &= df["Цена продажи"] >= box["price_min"]
    if box.get("price_max"): m &= df["Цена продажи"] <= box["price_max"]
    if box.get("year_min"): m &= df["Год"] >= box["year_min"]
    if box.get("miles_max"): m &= df["Пробег, mi"] <= box["miles_max"]
    if box.get("max_road_miles"): m &= df["По дороге ≈, mi"] <= box["max_road_miles"]
    if box.get("makes"): m &= df["Марка / модель"].str.split().str[0].str.lower().isin([x.lower() for x in box["makes"]])
    return df[m]


# --------------------------------------------------------------------------- отчёт
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("csv", type=Path, nargs="+", help="один или несколько файлов логгера за один день")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)
    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}

    cars, bids = load_many(args.csv)
    day = cars["Дата торгов"].iloc[0]
    out = args.out or ROOT / "out" / "auctions" / day
    out.mkdir(parents=True, exist_ok=True)

    cov, cov_notes = coverage(cars, bids, cfg.get("gap_minutes", 4))
    ov = overview(cars)
    mine, mine_note = our_activity(cars, bids, cfg.get("us", {}))
    top_b, under, b_note = buyers(cars, cfg.get("top_n", 15))
    by_price, by_age, models = segments(cars, cfg.get("top_n", 15))
    cheap, unsold = cheap_and_unsold(cars, cfg.get("cheap_vs_median", 0.8), cfg.get("min_peers", 3),
                                     cfg.get("buy_box") or {})

    sold = cars[cars["Результат"] == SOLD]
    md = [f"# Аукцион {day}: разбор\n",
          f"Файлы: {', '.join(f'`{p.name}`' for p in args.csv)}. Аукционов: {cars['Аукцион'].nunique()}, лейнов: {len(cov)}, "
          f"машин в списках: {len(cars)}, продано (записано): {len(sold)} на {money(sold['Цена продажи'].sum())}.\n",
          "## 0. Полнота данных\n", "\n".join(f"- {n}" for n in cov_notes) + "\n\n", md_table(cov),
          "\n## 1. Сводка по аукционам\n", md_table(ov),
          "\n## 2. Наше участие\n", mine_note + "\n\n", md_table(mine),
          "\n## 3. Покупатели\n", b_note + "\n\n", md_table(top_b),
          "\n### Кто чаще всего был вторым (поднимает цену)\n", md_table(under),
          "\n## 4. Цены по сегментам\n### По цене\n", md_table(by_price),
          "\n### По году выпуска\n", md_table(by_age), "\n### Самые частые модели\n", md_table(models),
          f"\n## 5. Ушли дешевле похожих (ниже {cfg.get('cheap_vs_median', 0.8):.0%} медианы той же модели: год ±1, пробег ±40 тыс. миль)\n",
          md_table(cheap), "\n### Не проданы: сколько не дотянули до floor\n", md_table(unsold)]
    (out / "report.md").write_text("".join(md), encoding="utf-8")

    with pd.ExcelWriter(out / "tables.xlsx") as xw:
        for name, t in [("Полнота", cov), ("Сводка", ov), ("Наши ставки", mine), ("Покупатели", top_b),
                        ("Андербиддеры", under), ("По цене", by_price), ("По году", by_age), ("Модели", models),
                        ("Дёшево ушли", cheap), ("Не проданы", unsold)]:
            (t if not t.empty else pd.DataFrame({"": ["нет данных"]})).to_excel(xw, sheet_name=name, index=False)
    print(f"Готово: {out / 'report.md'}\n        {out / 'tables.xlsx'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
