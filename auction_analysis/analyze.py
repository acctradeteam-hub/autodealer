"""Разбор итогов аукциона по файлу логгера auction_all_<дата>_HHMM.csv.

Запуск:
    python3 -m auction_analysis путь/к/auction_all_2026-11-10_1326.csv

Результат в out/auctions/<дата>/: report.md (разбор текстом) и tables.xlsx (таблицы по разделам).
Порядок разбора всегда один и тот же:
  0. Полнота данных — можно ли доверять цифрам
  1. Сводка по аукционам
  2. Наше участие
  3. Конкуренты (список в config.yaml)
  4. Ваш сегмент: модели до $10k, с KBB — разница KBB Private Party − цена
  5. Все покупатели и андербиддеры
  6. Цены по рынку
  7. Лоты вашего сегмента, ушедшие дёшево, и непроданные
  8. Сравнение с прошлыми аукционами (история копится в out/auctions/<дата>/cars.csv)
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
    if box.get("models"): m &= df["Модель"].str.lower().isin([x.lower() for x in box["models"]])
    return df[m]


# --------------------------------------------------------------------------- KBB
def load_kbb(path: Path | None) -> pd.Series:
    """Файл с KBB Private Party: любая таблица (CSV/XLSX), где есть колонка с VIN и колонка с KBB."""
    if not path:
        return pd.Series(dtype=float)
    t = pd.read_excel(path, dtype=str) if path.suffix.lower() in (".xlsx", ".xls") else \
        pd.read_csv(path, dtype=str, encoding="utf-8-sig", sep=None, engine="python")
    vin = next((c for c in t.columns if "vin" in c.lower()), None)
    val = next((c for c in t.columns if "kbb" in c.lower() or "private" in c.lower()), None)
    if not vin or not val:
        raise SystemExit(f"В {path.name} нужны колонки с VIN и с KBB; есть: {', '.join(t.columns)}")
    v = pd.to_numeric(t[val].str.replace(r"[^\d.]", "", regex=True), errors="coerce")
    return pd.Series(v.values, index=t[vin].str.strip().str.upper()).dropna().groupby(level=0).last()


# --------------------------------------------------------------------------- наш сегмент
def our_segment(cars: pd.DataFrame, box: dict, kbb: pd.Series, top: int) -> tuple[str, pd.DataFrame, pd.DataFrame]:
    sold = cars[cars["Результат"] == SOLD].copy()
    sold["KBB PP"] = sold["VIN"].str.upper().map(kbb)
    sold["KBB − цена"] = sold["KBB PP"] - sold["Цена продажи"]
    if kbb.empty:
        seg = in_box(sold, box)
        note = (f"KBB по лотам пока нет, поэтому сегмент — продажи до {money(box.get('price_max'))}"
                f"{' среди ваших моделей' if box.get('models') else ''}: {len(seg)} машин из {len(sold)}. "
                "С файлом KBB (`--kbb`) сегмент считается по KBB Private Party и появляется разница KBB − цена.")
    else:
        b = {k: v for k, v in box.items() if k not in ("price_min", "price_max")}
        base = in_box(sold, b)
        main_ = base["KBB PP"] <= box.get("kbb_max", 10000)
        stretch = (base["KBB PP"] <= box.get("kbb_stretch", 15000)) & (base["KBB − цена"] >= box.get("min_spread_stretch", 5000))
        seg = base[main_ | stretch]
        note = (f"KBB найден для {sold['KBB PP'].notna().sum()} из {len(sold)} проданных машин. "
                f"В сегменте (KBB до {money(box.get('kbb_max'))}, до {money(box.get('kbb_stretch'))} "
                f"при разнице от {money(box.get('min_spread_stretch'))}): {len(seg)} машин.")
    by_model = (seg.groupby("Модель").agg(
        Продано=("Лот", "count"), Медиана=("Цена продажи", "median"), Мин=("Цена продажи", "min"),
        Макс=("Цена продажи", "max"), Пробег=("Пробег, mi", "median"), KBB=("KBB PP", "median"),
        Разница=("KBB − цена", "median"),
        Аукционы=("Аукцион", lambda x: ", ".join(sorted(set(map(short, x))))))
        .query("Продано >= 2").sort_values("Продано", ascending=False).head(top).reset_index())
    for c in ("Медиана", "Мин", "Макс", "KBB", "Разница"):
        by_model[c] = by_model[c].map(money)
    by_model["Пробег"] = by_model["Пробег"].map(lambda v: "—" if pd.isna(v) else f"{v:,.0f}")
    if kbb.empty:
        by_model = by_model.drop(columns=["KBB", "Разница"])
    best = pd.DataFrame()
    if not kbb.empty:
        best = seg.sort_values("KBB − цена", ascending=False).head(top)
        best = pd.DataFrame({
            "Лот": best["Лот"], "Машина": best["Год"].map(lambda y: f"{y:.0f}") + " " + best["Марка / модель"],
            "Пробег": best["Пробег, mi"].map(lambda v: "—" if pd.isna(v) else f"{v:,.0f}"),
            "Цена": best["Цена продажи"].map(money), "KBB PP": best["KBB PP"].map(money),
            "KBB − цена": best["KBB − цена"].map(money), "Купил": best["Покупатель"]})
    return note, by_model, best


# --------------------------------------------------------------------------- конкуренты
def competitors(cars: pd.DataFrame, bids: pd.DataFrame, comp: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows, buys = [], []
    sold = cars[cars["Результат"] == SOLD]
    for name, pats in (comp or {}).items():
        rx = "|".join(re.escape(p) for p in pats)
        b = bids[bids["Дилер"].str.contains(rx, case=False, regex=True)]
        won = sold[sold["Покупатель"].str.contains(rx, case=False, regex=True)]
        second = sold[sold["2-й участник (андербиддер)"].str.contains(rx, case=False, regex=True)]
        rows.append({
            "Конкурент": name,
            "Имя на аукционе": ", ".join(sorted(set(b["Дилер"]) | set(won["Покупатель"]))) or "не встречался",
            "Лотов со ставками": b.groupby(KEY).ngroups, "Ставок": len(b), "Купил": len(won),
            "Потрачено": money(won["Цена продажи"].sum()) if len(won) else "—",
            "Средняя": money(won["Цена продажи"].mean()) if len(won) else "—",
            "Был вторым": len(second), "Аукционы": ", ".join(sorted(set(map(short, won["Аукцион"])))),
        })
        for _, c in won.iterrows():
            buys.append({"Конкурент": name, "Лот": c["Лот"], "Машина": f"{c['Год']:.0f} {c['Марка / модель']}",
                         "Пробег": f"{c['Пробег, mi']:,.0f}" if pd.notna(c["Пробег, mi"]) else "—",
                         "Цена": money(c["Цена продажи"]), "Второй": c["2-й участник (андербиддер)"],
                         "Дилеров": c["Кол-во дилеров"]})
    return pd.DataFrame(rows), pd.DataFrame(buys)


# --------------------------------------------------------------------------- история
HIST_COLS = ["Дата торгов", "Аукцион", "Лейн", "Лот #", "Год", "Марка / модель", "Модель", "VIN", "Пробег, mi",
             "Результат", "Цена продажи", "Покупатель", "2-й участник (андербиддер)", "Кол-во дилеров",
             "Asking (старт)", "По дороге ≈, mi"]


def history(cars: pd.DataFrame, hist_dir: Path, day: str, comp: dict, top: int) -> tuple[str, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Сохраняет итоги дня в hist_dir/<день>/cars.csv и сравнивает с предыдущими днями."""
    cars[HIST_COLS].to_csv(hist_dir / day / "cars.csv", index=False, encoding="utf-8-sig")
    prev = []
    for f in sorted(hist_dir.glob("*/cars.csv")):
        if f.parent.name < day:
            prev.append(pd.read_csv(f, encoding="utf-8-sig", keep_default_na=False,
                                    dtype={"Покупатель": str, "2-й участник (андербиддер)": str}))
    if not prev:
        return ("Прошлых аукционов в истории пока нет: каждый разбор сохраняет итоги дня, "
                "со следующего аукциона здесь появится сравнение.", pd.DataFrame(), pd.DataFrame(), pd.DataFrame())
    h = pd.concat(prev, ignore_index=True)
    for c in ("Цена продажи", "Кол-во дилеров", "Asking (старт)"):
        h[c] = pd.to_numeric(h[c], errors="coerce")
    days = sorted(h["Дата торгов"].astype(str).unique())
    sold_t, sold_h = cars[cars["Результат"] == SOLD], h[h["Результат"] == SOLD]

    a = []
    for auc in sorted(set(cars["Аукцион"]) | set(h["Аукцион"])):
        t, p = sold_t[sold_t["Аукцион"] == auc], sold_h[sold_h["Аукцион"] == auc]
        nd = p["Дата торгов"].nunique()
        a.append({"Аукцион": short(auc), "Продано сегодня": len(t), "В среднем раньше": round(len(p) / nd) if nd else "—",
                  "Медиана сегодня": money(t["Цена продажи"].median()), "Медиана раньше": money(p["Цена продажи"].median()),
                  "Цена/asking сегодня": f"{(t['Цена продажи'] / t['Asking (старт)']).median():.2f}×" if t["Asking (старт)"].notna().any() else "—",
                  "Цена/asking раньше": f"{(p['Цена продажи'] / p['Asking (старт)']).median():.2f}×" if p["Asking (старт)"].notna().any() else "—"})
    m = []
    for model, t in sold_t.groupby("Модель"):
        p = sold_h[sold_h["Модель"] == model]
        if len(t) >= 3 and len(p) >= 3:
            mt, mp = t["Цена продажи"].median(), p["Цена продажи"].median()
            m.append({"Модель": model, "Сегодня": len(t), "Медиана сегодня": money(mt), "Раньше": len(p),
                      "Медиана раньше": money(mp), "Изменение": f"{100 * (mt / mp - 1):+.0f}%", "_d": abs(mt / mp - 1)})
    models = pd.DataFrame(m).sort_values("_d", ascending=False).drop(columns="_d").head(top) if m else pd.DataFrame()
    c = []
    for name, pats in (comp or {}).items():
        rx = "|".join(re.escape(x) for x in pats)
        row = {"Конкурент": name}
        for d in days + [day]:
            src = sold_h[sold_h["Дата торгов"].astype(str) == d] if d != day else sold_t
            w = src[src["Покупатель"].astype(str).str.contains(rx, case=False, regex=True)]
            row[d] = f"{len(w)} / {money(w['Цена продажи'].sum())}" if len(w) else "—"
        c.append(row)
    return (f"Сравнение с прошлыми аукционами: {', '.join(days)}.", pd.DataFrame(a), models, pd.DataFrame(c))


# --------------------------------------------------------------------------- отчёт
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("csv", type=Path, nargs="+", help="один или несколько файлов логгера за один день")
    ap.add_argument("--kbb", type=Path, default=None, help="таблица с VIN и KBB Private Party")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)
    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}
    top, box, comp = cfg.get("top_n", 15), cfg.get("buy_box") or {}, cfg.get("competitors") or {}

    cars, bids = load_many(args.csv)
    day = cars["Дата торгов"].iloc[0]
    hist_dir = ROOT / "out" / "auctions"
    out = args.out or hist_dir / day
    out.mkdir(parents=True, exist_ok=True)
    (hist_dir / day).mkdir(parents=True, exist_ok=True)

    cov, cov_notes = coverage(cars, bids, cfg.get("gap_minutes", 4))
    ov = overview(cars)
    mine, mine_note = our_activity(cars, bids, cfg.get("us", {}))
    comp_t, comp_buys = competitors(cars, bids, comp)
    seg_note, seg_models, seg_best = our_segment(cars, box, load_kbb(args.kbb), top)
    top_b, under, b_note = buyers(cars, top)
    by_price, by_age, models = segments(cars, top)
    cheap, unsold = cheap_and_unsold(cars, cfg.get("cheap_vs_median", 0.8), cfg.get("min_peers", 3),
                                     {k: v for k, v in box.items() if k in ("price_max", "models", "makes", "year_min", "miles_max", "max_road_miles")})
    h_note, h_auc, h_models, h_comp = history(cars, hist_dir, day, comp, top)

    sold = cars[cars["Результат"] == SOLD]
    md = [f"# Аукцион {day}: разбор\n",
          f"Файлы: {', '.join(f'`{p.name}`' for p in args.csv)}. Аукционов: {cars['Аукцион'].nunique()}, лейнов: {len(cov)}, "
          f"машин в списках: {len(cars)}, продано: {len(sold)} на {money(sold['Цена продажи'].sum())}.\n",
          "## 0. Полнота данных\n", "\n".join(f"- {n}" for n in cov_notes) + "\n\n", md_table(cov),
          "\n## 1. Сводка по аукционам\n", md_table(ov),
          "\n## 2. Наше участие\n", mine_note + "\n\n", md_table(mine),
          "\n## 3. Конкуренты\n", md_table(comp_t), "\n### Что купили\n", md_table(comp_buys, 50),
          "\n## 4. Ваш сегмент\n", seg_note + "\n\n### Модели сегмента: почём уходили\n", md_table(seg_models),
          *(["\n### Самая большая разница KBB − цена\n", md_table(seg_best)] if not seg_best.empty else []),
          "\n## 5. Все покупатели\n", b_note + "\n\n", md_table(top_b),
          "\n### Кто чаще всего был вторым (поднимает цену)\n", md_table(under),
          "\n## 6. Цены по рынку\n### По цене\n", md_table(by_price),
          "\n### По году выпуска\n", md_table(by_age), "\n### Самые частые модели\n", md_table(models),
          f"\n## 7. Ушли дешевле похожих (ниже {cfg.get('cheap_vs_median', 0.8):.0%} медианы той же модели: год ±1, пробег ±40 тыс. миль)\n",
          "Только машины вашего сегмента. Состояния (condition report) в данных нет: часть ушла дёшево из-за проблем.\n\n",
          md_table(cheap), "\n### Не проданы: сколько не дотянули до floor\n", md_table(unsold),
          "\n## 8. Сравнение с прошлыми аукционами\n", h_note + "\n\n",
          *(["### По аукционам\n", md_table(h_auc), "\n### Модели: изменение медианы цены\n", md_table(h_models),
             "\n### Конкуренты по дням (машин / сумма)\n", md_table(h_comp)] if not h_auc.empty else [])]
    (out / "report.md").write_text("".join(md), encoding="utf-8")

    with pd.ExcelWriter(out / "tables.xlsx") as xw:
        for name, t in [("Полнота", cov), ("Сводка", ov), ("Наши ставки", mine), ("Конкуренты", comp_t),
                        ("Покупки конкурентов", comp_buys), ("Сегмент модели", seg_models), ("Сегмент KBB", seg_best),
                        ("Покупатели", top_b), ("Андербиддеры", under), ("По цене", by_price), ("По году", by_age),
                        ("Модели", models), ("Дёшево ушли", cheap), ("Не проданы", unsold),
                        ("История аукционы", h_auc), ("История модели", h_models), ("История конкуренты", h_comp)]:
            (t if not t.empty else pd.DataFrame({"": ["нет данных"]})).to_excel(xw, sheet_name=name, index=False)
    print(f"Готово: {out / 'report.md'}\n        {out / 'tables.xlsx'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
