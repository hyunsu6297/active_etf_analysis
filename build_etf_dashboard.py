from __future__ import annotations

import argparse
import json
import re
from datetime import date, datetime
from pathlib import Path

import pandas as pd


DEFAULT_LIST = Path("\uC885\uBAA9\uB9AC\uC2A4\uD2B8.xlsx")
DEFAULT_OUTPUT = Path("etf_active_weight_dashboard.html")
ISSUER_SAMSUNG = "\uC0BC\uC131"
ISSUER_TIME = "\uD0C0\uC784"
TYPE_ACTIVE = "\uC561\uD2F0\uBE0C"
TYPE_PASSIVE = "\uD328\uC2DC\uBE0C"
SECURITY_ALIASES = {
    "GOOG US EQUITY": "GOOGL US EQUITY",
}
SECURITY_NAME_OVERRIDES = {
    "GOOGL US EQUITY": "ALPHABET INC",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build an ETF active weight dashboard.")
    parser.add_argument("--parquet", help="Defaults to an accumulated parquet built from all etf_holdings_YYYYMMDD_YYYYMMDD.parquet files.")
    parser.add_argument("--list-file", default=str(DEFAULT_LIST), help="ETF list workbook with BM column")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Output HTML path")
    return parser.parse_args()


def latest_holdings_parquet() -> Path:
    parquet_dir = Path("data/parquet")
    pattern = re.compile(r"^etf_holdings_(\d{8})_(\d{8})\.parquet$")
    candidates = []
    for path in parquet_dir.glob("etf_holdings_*.parquet"):
        match = pattern.match(path.name)
        if match:
            candidates.append((path.stat().st_mtime, path))
    if not candidates:
        raise FileNotFoundError("No data/parquet/etf_holdings_*.parquet files found.")

    frames = []
    for _mtime, path in sorted(candidates):
        frame = pd.read_parquet(path)
        frame["_source_mtime"] = path.stat().st_mtime
        frames.append(frame)

    combined = pd.concat(frames, ignore_index=True)
    key_columns = [
        "as_of_date",
        "issuer",
        "etf_type",
        "etf_name",
        "security_code",
        "ticker",
        "security_name",
    ]
    combined = (
        combined.sort_values("_source_mtime")
        .drop_duplicates(subset=[col for col in key_columns if col in combined.columns], keep="last")
        .drop(columns=["_source_mtime"])
        .sort_values(["as_of_date", "issuer", "etf_type", "etf_name", "holding_no"], na_position="last")
    )

    start = pd.to_datetime(combined["as_of_date"]).min().strftime("%Y%m%d")
    end = pd.to_datetime(combined["as_of_date"]).max().strftime("%Y%m%d")
    output = parquet_dir / f"etf_holdings_accumulated_{start}_{end}.parquet"
    combined.to_parquet(output, index=False)
    return output


def clean_key(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.strip().str.upper()


def display_code(code: str) -> str:
    text = str(code).strip().upper()
    text = re.sub(r"\s+US\s+EQUITY$", "", text)
    text = re.sub(r"\s+INDEX$", "", text)
    return text.strip()


def load_inputs(parquet_path: Path, list_file: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    holdings = pd.read_parquet(parquet_path)
    etfs = pd.read_excel(list_file).rename(
        columns={"\uC6B4\uC6A9\uC0AC": "issuer", "\uC720\uD615": "etf_type", "ETF\uBA85": "etf_name"}
    )
    required = {"issuer", "etf_type", "BM", "etf_name"}
    missing = required.difference(etfs.columns)
    if missing:
        raise ValueError(f"{list_file} is missing columns: {sorted(missing)}")

    holdings = holdings.merge(etfs[["issuer", "etf_type", "etf_name", "BM"]], on=["issuer", "etf_type", "etf_name"], how="left")
    if holdings["BM"].isna().any():
        missing_rows = holdings.loc[holdings["BM"].isna(), ["issuer", "etf_type", "etf_name"]].drop_duplicates()
        raise ValueError(f"Holdings without BM mapping:\n{missing_rows}")

    holdings["as_of_date"] = holdings["as_of_date"].astype(str)
    holdings["weight"] = pd.to_numeric(holdings["weight"], errors="coerce").fillna(0.0)
    holdings["security_key"] = clean_key(holdings["ticker"].fillna(holdings["security_code"]))
    holdings["security_key"] = holdings["security_key"].replace(SECURITY_ALIASES)
    holdings["display_code"] = holdings["security_key"].map(display_code)
    holdings["security_name"] = holdings["security_name"].fillna(holdings["security_key"]).astype(str).str.strip()
    holdings["security_name"] = holdings["security_key"].map(SECURITY_NAME_OVERRIDES).fillna(holdings["security_name"])
    return holdings, etfs


def aggregate_day(holdings: pd.DataFrame, bm: str, issuer: str, etf_type: str, date_value: str) -> pd.DataFrame:
    frame = holdings[
        (holdings["BM"] == bm)
        & (holdings["issuer"] == issuer)
        & (holdings["etf_type"] == etf_type)
        & (holdings["as_of_date"] == date_value)
    ]
    if frame.empty:
        return pd.DataFrame(columns=["security_key", "display_code", "security_name", "weight"])
    return (
        frame.groupby("security_key", as_index=False)
        .agg(display_code=("display_code", "first"), security_name=("security_name", "first"), weight=("weight", "sum"))
        .sort_values("weight", ascending=False)
    )


def available_reference_date(dates: list[str], current_date: str, days_back: int) -> str | None:
    target = pd.Timestamp(current_date) - pd.Timedelta(days=days_back)
    candidates = [d for d in dates if d < current_date and pd.Timestamp(d) <= target]
    return candidates[-1] if candidates else None


def active_table(
    holdings: pd.DataFrame,
    bm: str,
    issuer: str,
    current_date: str,
    dates: list[str],
    passive_weights: dict[str, float],
) -> list[dict]:
    current = aggregate_day(holdings, bm, issuer, TYPE_ACTIVE, current_date)
    references = [("d1", 1), ("w1", 7), ("m1", 30), ("m3", 91), ("m6", 182)]
    past_weights: dict[str, dict[str, float] | None] = {}
    past_dates: dict[str, str | None] = {}
    for key, days in references:
        ref_date = available_reference_date(dates, current_date, days)
        past_dates[key] = ref_date
        if ref_date is None:
            past_weights[key] = None
        else:
            past = aggregate_day(holdings, bm, issuer, TYPE_ACTIVE, ref_date)
            past_weights[key] = past.set_index("security_key")["weight"].to_dict()

    rows: list[dict] = []
    for row in current.itertuples(index=False):
        item = {
            "code": row.display_code,
            "fullCode": row.security_key,
            "name": row.security_name,
            "bmWeight": float(passive_weights.get(row.security_key, 0.0)),
            "weight": float(row.weight),
            "bmDiff": float(row.weight - passive_weights.get(row.security_key, 0.0)),
            "changes": {},
        }
        for key, _days in references:
            lookup = past_weights[key]
            if lookup is None:
                item["changes"][key] = {"kind": "na", "value": None, "date": None}
            elif row.security_key not in lookup:
                item["changes"][key] = {"kind": "new", "value": None, "date": past_dates[key]}
            else:
                item["changes"][key] = {
                    "kind": "delta",
                    "value": float(row.weight - lookup[row.security_key]),
                    "date": past_dates[key],
                }
        rows.append(item)
    return rows


def passive_compare_table(
    passive: pd.DataFrame,
    samsung_weights: dict[str, float],
    time_weights: dict[str, float],
) -> list[dict]:
    rows: list[dict] = []
    for row in passive.itertuples(index=False):
        passive_weight = float(row.weight)
        samsung_weight = float(samsung_weights.get(row.security_key, 0.0))
        time_weight = float(time_weights.get(row.security_key, 0.0))
        rows.append(
            {
                "code": row.display_code,
                "fullCode": row.security_key,
                "name": row.security_name,
                "passiveWeight": passive_weight,
                "samsungWeight": samsung_weight,
                "samsungDiff": samsung_weight - passive_weight,
                "timeWeight": time_weight,
                "timeDiff": time_weight - passive_weight,
            }
        )
    return rows


def build_series(holdings: pd.DataFrame, bm: str) -> list[dict]:
    frames = []
    specs = [
        (ISSUER_SAMSUNG, TYPE_PASSIVE, "p"),
        (ISSUER_SAMSUNG, TYPE_ACTIVE, "s"),
        (ISSUER_TIME, TYPE_ACTIVE, "t"),
    ]
    for issuer, etf_type, column in specs:
        part = holdings[(holdings["BM"] == bm) & (holdings["issuer"] == issuer) & (holdings["etf_type"] == etf_type)]
        grouped = (
            part.groupby(["as_of_date", "security_key"], as_index=False)
            .agg(display_code=("display_code", "first"), name=("security_name", "first"), weight=("weight", "sum"))
            .rename(columns={"weight": column})
        )
        frames.append(grouped)
    merged = frames[0]
    for frame in frames[1:]:
        merged = merged.merge(frame, on=["as_of_date", "security_key"], how="outer", suffixes=("", "_r"))
        merged["display_code"] = merged["display_code"].fillna(merged.pop("display_code_r"))
        merged["name"] = merged["name"].fillna(merged.pop("name_r"))
    for column in ["p", "s", "t"]:
        merged[column] = merged[column].fillna(0.0)
    merged = merged.sort_values(["security_key", "as_of_date"])
    return [
        {
            "d": row.as_of_date,
            "k": row.security_key,
            "c": row.display_code,
            "n": row.name,
            "p": float(row.p),
            "s": float(row.s),
            "t": float(row.t),
        }
        for row in merged.itertuples(index=False)
    ]


def rank_summary(passive: pd.DataFrame, active: pd.DataFrame) -> dict:
    passive_map = passive.set_index("security_key")[["display_code", "security_name", "weight"]]
    active_map = active.set_index("security_key")[["display_code", "security_name", "weight"]]
    keys = sorted(set(passive_map.index).union(active_map.index))
    rows = []
    for key in keys:
        passive_weight = float(passive_map.loc[key, "weight"]) if key in passive_map.index else 0.0
        active_weight = float(active_map.loc[key, "weight"]) if key in active_map.index else 0.0
        if key in active_map.index:
            code = active_map.loc[key, "display_code"]
            name = active_map.loc[key, "security_name"]
        else:
            code = passive_map.loc[key, "display_code"]
            name = passive_map.loc[key, "security_name"]
        diff = active_weight - passive_weight
        rel = ((active_weight / passive_weight) - 1) * 100 if passive_weight > 0 else None
        rows.append(
            {
                "code": code,
                "fullCode": key,
                "name": name,
                "activeWeight": active_weight,
                "passiveWeight": passive_weight,
                "diff": diff,
                "relativePct": rel,
            }
        )
    return {
        "over": sorted(rows, key=lambda item: item["diff"], reverse=True)[:15],
        "under": sorted(rows, key=lambda item: item["diff"])[:15],
    }


def build_payload(holdings: pd.DataFrame, etfs: pd.DataFrame, parquet_path: Path) -> dict:
    bm_values = [bm for bm in ["S&P500", "NASDAQ100"] if bm in set(etfs["BM"])]
    if not bm_values:
        bm_values = sorted(etfs["BM"].dropna().unique().tolist())

    bm_payload = {}
    for bm in bm_values:
        bm_holdings = holdings[holdings["BM"] == bm]
        dates = sorted(bm_holdings["as_of_date"].unique().tolist())
        current_date = dates[-1]
        passive_meta = etfs[(etfs["BM"] == bm) & (etfs["etf_type"] == TYPE_PASSIVE)].iloc[0]
        samsung_meta = etfs[(etfs["BM"] == bm) & (etfs["issuer"] == ISSUER_SAMSUNG) & (etfs["etf_type"] == TYPE_ACTIVE)].iloc[0]
        time_meta = etfs[(etfs["BM"] == bm) & (etfs["issuer"] == ISSUER_TIME) & (etfs["etf_type"] == TYPE_ACTIVE)].iloc[0]

        passive = aggregate_day(holdings, bm, ISSUER_SAMSUNG, TYPE_PASSIVE, current_date)
        samsung = aggregate_day(holdings, bm, ISSUER_SAMSUNG, TYPE_ACTIVE, current_date)
        time = aggregate_day(holdings, bm, ISSUER_TIME, TYPE_ACTIVE, current_date)
        passive_weights = passive.set_index("security_key")["weight"].to_dict()
        samsung_weights = samsung.set_index("security_key")["weight"].to_dict()
        time_weights = time.set_index("security_key")["weight"].to_dict()

        bm_payload[bm] = {
            "currentDate": current_date,
            "dateCount": len(dates),
            "firstDate": dates[0],
            "passiveEtf": passive_meta["etf_name"],
            "samsungEtf": samsung_meta["etf_name"],
            "timeEtf": time_meta["etf_name"],
            "passiveRows": passive_compare_table(passive, samsung_weights, time_weights),
            "samsungRows": active_table(holdings, bm, ISSUER_SAMSUNG, current_date, dates, passive_weights),
            "timeRows": active_table(holdings, bm, ISSUER_TIME, current_date, dates, passive_weights),
            "series": build_series(holdings, bm),
        }

    return {
        "defaultBm": "S&P500" if "S&P500" in bm_payload else bm_values[0],
        "bms": bm_payload,
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "today": date.today().isoformat(),
        "sourceParquet": str(parquet_path),
    }


def make_html(payload: dict) -> str:
    payload_json = json.dumps(payload, ensure_ascii=False)
    html = """<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ETF Weight Dashboard</title>
  <style>
    :root {
      --bg: #f4f6f8;
      --panel: #ffffff;
      --ink: #17202a;
      --muted: #647082;
      --line: #d7dde7;
      --head: #eef2f6;
      --over: #087f5b;
      --under: #c13b33;
      --new: #a26300;
      --blue: #225e9b;
      --gold: #a26300;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: Arial, "Malgun Gothic", sans-serif;
      letter-spacing: 0;
    }
    header {
      background: var(--panel);
      border-bottom: 1px solid var(--line);
      padding: 14px 22px;
    }
    .header-row { display: flex; align-items: center; gap: 18px; flex-wrap: wrap; }
    h1 { margin: 0; font-size: 23px; line-height: 1.2; }
    .bm-buttons { display: flex; gap: 6px; }
    .bm-button {
      height: 32px;
      padding: 0 14px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      color: var(--ink);
      font-size: 13px;
      font-weight: 700;
      cursor: pointer;
    }
    .bm-button.active { border-color: var(--blue); background: var(--blue); color: #fff; }
    label { display: grid; gap: 6px; color: #334155; font-size: 12px; font-weight: 700; }
    select, input {
      height: 38px;
      padding: 6px 10px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      color: var(--ink);
      font-size: 14px;
    }
    .meta {
      display: flex;
      flex-wrap: wrap;
      gap: 9px 16px;
      color: var(--muted);
      font-size: 12px;
      padding-bottom: 0;
    }
    main { padding: 16px; }
    .layout {
      display: grid;
      grid-template-columns: minmax(620px, 1fr) minmax(620px, 1fr);
      gap: 12px;
      align-items: start;
    }
    .lower {
      display: grid;
      grid-template-columns: minmax(620px, 1fr) minmax(620px, 1fr);
      gap: 12px;
      margin-bottom: 12px;
      align-items: start;
    }
    .lower > section { height: 465px; }
    section {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      min-width: 0;
      overflow: hidden;
    }
    .section-head {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 10px;
      align-items: baseline;
      padding: 12px 12px 10px;
      border-bottom: 1px solid var(--line);
    }
    h2 { margin: 0; font-size: 15px; line-height: 1.25; }
    .count { color: var(--muted); font-weight: 600; font-size: 12px; }
    .hint { color: var(--muted); font-size: 11px; text-align: right; }
    .table-wrap { max-height: calc(100vh - 154px); overflow: auto; }
    .lower .table-wrap { max-height: 420px; }
    table { width: 100%; border-collapse: separate; border-spacing: 0; font-size: 12px; }
    th, td {
      border-bottom: 1px solid #e8edf3;
      padding: 7px 8px;
      text-align: right;
      vertical-align: middle;
      white-space: nowrap;
      font-variant-numeric: tabular-nums;
    }
    th {
      position: sticky;
      top: 0;
      z-index: 2;
      background: var(--head);
      color: #3f4c5d;
      font-size: 11px;
      font-weight: 760;
    }
    th:nth-child(2), td:nth-child(2), th:nth-child(3), td:nth-child(3) { text-align: left; }
    .rank { text-align: center; color: var(--muted); font-weight: 700; }
    td.name { overflow: hidden; text-overflow: ellipsis; }
    .passive-table { table-layout: fixed; }
    .passive-table th:nth-child(1), .passive-table td:nth-child(1) { width: 42px; }
    .passive-table th:nth-child(2), .passive-table td:nth-child(2) { width: 74px; }
    .passive-table th:nth-child(3), .passive-table td:nth-child(3) { width: 102px; }
    .passive-table th:nth-child(4), .passive-table td:nth-child(4) { width: 82px; }
    .passive-table th:nth-child(n+5), .passive-table td:nth-child(n+5) { width: 92px; }
    .passive-table .group-head th { text-align: center; border-bottom: 1px solid #cbd5e1; }
    .passive-table .sub-head th { text-align: center; }
    .passive-table .group-box { border-top: 2px solid #222; border-left: 2px solid #222; border-right: 2px solid #222; }
    .passive-table .sub-head th:nth-child(1), .passive-table td:nth-child(5),
    .passive-table .sub-head th:nth-child(3), .passive-table td:nth-child(7) { border-left: 2px solid #222; }
    .passive-table .sub-head th:nth-child(2), .passive-table td:nth-child(6),
    .passive-table .sub-head th:nth-child(4), .passive-table td:nth-child(8) { border-left: 1px solid #dce4ee; border-right: 2px solid #222; }
    .passive-table .sub-head th { top: 28px; }
    .active-table { table-layout: fixed; font-size: 11px; }
    .active-table th, .active-table td { padding: 6px 4px; }
    .active-table th:nth-child(1), .active-table td:nth-child(1) { width: 42px; }
    .active-table th:nth-child(2), .active-table td:nth-child(2) { width: 68px; }
    .active-table th:nth-child(3), .active-table td:nth-child(3) { width: auto; }
    .active-table th:nth-child(4), .active-table td:nth-child(4) { width: 74px; }
    .active-table th:nth-child(5), .active-table td:nth-child(5) { width: 68px; }
    .active-table th:nth-child(6), .active-table td:nth-child(6) { width: 88px; }
    .active-table th:nth-child(n+7), .active-table td:nth-child(n+7) { width: 62px; }
    .active-table th:nth-child(n+4), .active-table td:nth-child(n+4) { border-left: 1px solid #edf1f5; }
    .code { font-weight: 700; color: #263241; overflow: hidden; text-overflow: ellipsis; }
    th.sortable { cursor: pointer; user-select: none; }
    th.sortable::after { content: ''; display: inline-block; width: 10px; color: #7a8798; font-size: 9px; }
    th.sortable.sort-desc::after { content: '\\25BC'; }
    th.sortable.sort-asc::after { content: '\\25B2'; }
    [data-chart-code] { cursor: pointer; }
    .pos { color: var(--over); font-weight: 700; }
    .neg { color: var(--under); font-weight: 700; }
    .new { color: var(--new); font-weight: 700; }
    .na { color: #98a2b3; }
    .weight { color: var(--blue); font-weight: 700; }
    .new-entry {
      text-align: center;
      background: rgba(8, 127, 91, 0.16);
      color: var(--over);
      font-weight: 800;
    }
    .absent {
      text-align: center;
      background: rgba(193, 59, 51, 0.16);
      color: var(--under);
      font-weight: 800;
    }
    .bar-cell { position: relative; overflow: hidden; text-align: center; }
    .bar-cell::before {
      content: '';
      position: absolute;
      top: 3px;
      bottom: 3px;
      width: var(--bar-width, 0%);
      background: var(--bar-color, rgba(100, 116, 139, 0.12));
      border-radius: 3px;
      pointer-events: none;
    }
    .bar-fill::before { left: 0; }
    .bar-center.pos::before { left: 50%; }
    .bar-center.neg::before { right: 50%; }
    .controls {
      display: grid;
      grid-template-columns: minmax(120px, 0.85fr) minmax(150px, 1fr) minmax(150px, 1fr) 64px;
      gap: 14px;
      padding: 12px;
      border-bottom: 1px solid var(--line);
      align-items: end;
    }
    button {
      height: 38px;
      border: 1px solid #1d4f87;
      background: var(--blue);
      color: #fff;
      border-radius: 6px;
      font-weight: 700;
      cursor: pointer;
    }
    .suggest-wrap { position: relative; }
    .suggestions {
      position: absolute;
      z-index: 10;
      top: 64px;
      left: 0;
      right: 0;
      display: none;
      max-height: 260px;
      overflow: auto;
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 6px;
      box-shadow: 0 8px 24px rgba(15, 23, 42, 0.12);
    }
    .suggestion {
      padding: 8px 10px;
      border-bottom: 1px solid #edf1f5;
      cursor: pointer;
      font-size: 12px;
    }
    .suggestion:last-child { border-bottom: 0; }
    .suggestion.active, .suggestion:hover { background: #eef6ff; }
    .suggestion strong { display: inline-block; min-width: 54px; color: var(--blue); }
    .chart-wrap { padding: 8px 12px 14px; }
    #trendChart { width: 100%; height: 310px; }
    .legend { display: flex; gap: 14px; padding: 0 12px 10px; color: var(--muted); font-size: 12px; }
    .swatch { display: inline-block; width: 18px; height: 3px; margin-right: 5px; vertical-align: middle; }
    @media (max-width: 1280px) {
      .layout, .lower { grid-template-columns: 1fr; }
      .table-wrap { max-height: 620px; }
    }
    @media (max-width: 720px) {
      header { padding: 14px; }
      main { padding: 10px; }
      .header-row, .controls { grid-template-columns: 1fr; }
      th, td { padding: 6px; }
    }
  </style>
</head>
<body>
  <header>
    <div class="header-row">
      <h1>ETF Weight Dashboard</h1>
      <div class="bm-buttons" id="bmButtons"></div>
      <div class="meta">
        <span id="dateMeta"></span>
        <span id="etfMeta"></span>
        <span id="sourceMeta"></span>
      </div>
    </div>
  </header>
  <main>
    <div class="lower">
      <section>
        <div class="section-head">
          <h2>BM \uB300\uBE44 \uC561\uD2F0\uBE0C ETF \uBE44\uC911 \uBD84\uC11D</h2>
          <span class="hint">\uBE44\uC911: %, BM \uB300\uBE44: %p</span>
        </div>
        <div class="table-wrap">
          <table class="passive-table">
            <thead>
              <tr class="group-head">
                <th rowspan="2" class="rank">\uC21C\uC704</th>
                <th rowspan="2" class="sortable" data-sort-table="passive" data-sort-key="code">Ticker</th>
                <th rowspan="2" class="sortable" data-sort-table="passive" data-sort-key="name">\uC885\uBAA9\uBA85</th>
                <th rowspan="2" class="sortable" data-sort-table="passive" data-sort-key="passiveWeight">BM(%)</th>
                <th class="group-box" colspan="2">\uC0BC\uC131</th>
                <th class="group-box" colspan="2">\uD0C0\uC784</th>
              </tr>
              <tr class="sub-head">
                <th class="sortable" data-sort-table="passive" data-sort-key="samsungWeight">\uBE44\uC911(%)</th>
                <th class="sortable" data-sort-table="passive" data-sort-key="samsungDiff">BM\uB300\uBE44(%p)</th>
                <th class="sortable" data-sort-table="passive" data-sort-key="timeWeight">\uBE44\uC911(%)</th>
                <th class="sortable" data-sort-table="passive" data-sort-key="timeDiff">BM\uB300\uBE44(%p)</th>
              </tr>
            </thead>
            <tbody id="passiveBody"></tbody>
          </table>
        </div>
      </section>

      <section>
        <div class="section-head">
          <h2>Ticker\uBCC4 \uD3B8\uC785 \uBE44\uC911 \uCD94\uC774</h2>
          <span class="hint">BM / \uC0BC\uC131 / \uD0C0\uC784</span>
        </div>
        <div class="controls">
          <label class="suggest-wrap">Ticker
            <input id="chartCode" value="NVDA">
            <div id="suggestions" class="suggestions"></div>
          </label>
          <label>\uC2DC\uC791\uC77C
            <input id="startDate" type="date" value="2026-01-01">
          </label>
          <label>\uC885\uB8CC\uC77C
            <input id="endDate" type="date">
          </label>
          <button id="chartButton">\uC870\uD68C</button>
        </div>
        <div class="legend">
          <span><i class="swatch" style="background:#087f5b"></i>BM</span>
          <span><i class="swatch" style="background:#225e9b"></i>\uC0BC\uC131</span>
          <span><i class="swatch" style="background:#c13b33"></i>\uD0C0\uC784</span>
          <span id="chartMeta"></span>
        </div>
        <div class="chart-wrap"><div id="trendChart"></div></div>
      </section>
    </div>

    <div class="layout">
      <section>
        <div class="section-head">
          <h2 id="samsungTitle">\uC0BC\uC131 \uC561\uD2F0\uBE0C</h2>
          <span class="hint">\uD604\uC7AC \uD3B8\uC785 \uBE44\uC911 \uB0B4\uB9BC\uCC28\uC21C</span>
        </div>
        <div class="table-wrap">
          <table class="active-table">
            <thead>
              <tr>
                <th class="rank">\uC21C\uC704</th>
                <th class="sortable" data-sort-table="samsungBody" data-sort-key="code">Ticker</th>
                <th class="sortable" data-sort-table="samsungBody" data-sort-key="name">\uC885\uBAA9\uBA85</th>
                <th class="sortable" data-sort-table="samsungBody" data-sort-key="weight">\uD3B8\uC785\uBE44(%)</th>
                <th class="sortable" data-sort-table="samsungBody" data-sort-key="bmWeight">BM(%)</th>
                <th class="sortable" data-sort-table="samsungBody" data-sort-key="bmDiff">BM\uB300\uBE44(%p)</th>
                <th class="sortable" data-sort-table="samsungBody" data-sort-key="changes.d1">\uC804\uC77C(%p)</th>
                <th class="sortable" data-sort-table="samsungBody" data-sort-key="changes.w1">\uC804\uC8FC(%p)</th>
                <th class="sortable" data-sort-table="samsungBody" data-sort-key="changes.m1">1\uAC1C\uC6D4(%p)</th>
                <th class="sortable" data-sort-table="samsungBody" data-sort-key="changes.m3">3\uAC1C\uC6D4(%p)</th>
                <th class="sortable" data-sort-table="samsungBody" data-sort-key="changes.m6">6\uAC1C\uC6D4(%p)</th>
              </tr>
            </thead>
            <tbody id="samsungBody"></tbody>
          </table>
        </div>
      </section>

      <section>
        <div class="section-head">
          <h2 id="timeTitle">\uD0C0\uC784 \uC561\uD2F0\uBE0C</h2>
          <span class="hint">\uD604\uC7AC \uD3B8\uC785 \uBE44\uC911 \uB0B4\uB9BC\uCC28\uC21C</span>
        </div>
        <div class="table-wrap">
          <table class="active-table">
            <thead>
              <tr>
                <th class="rank">\uC21C\uC704</th>
                <th class="sortable" data-sort-table="timeBody" data-sort-key="code">Ticker</th>
                <th class="sortable" data-sort-table="timeBody" data-sort-key="name">\uC885\uBAA9\uBA85</th>
                <th class="sortable" data-sort-table="timeBody" data-sort-key="weight">\uD3B8\uC785\uBE44(%)</th>
                <th class="sortable" data-sort-table="timeBody" data-sort-key="bmWeight">BM(%)</th>
                <th class="sortable" data-sort-table="timeBody" data-sort-key="bmDiff">BM\uB300\uBE44(%p)</th>
                <th class="sortable" data-sort-table="timeBody" data-sort-key="changes.d1">\uC804\uC77C(%p)</th>
                <th class="sortable" data-sort-table="timeBody" data-sort-key="changes.w1">\uC804\uC8FC(%p)</th>
                <th class="sortable" data-sort-table="timeBody" data-sort-key="changes.m1">1\uAC1C\uC6D4(%p)</th>
                <th class="sortable" data-sort-table="timeBody" data-sort-key="changes.m3">3\uAC1C\uC6D4(%p)</th>
                <th class="sortable" data-sort-table="timeBody" data-sort-key="changes.m6">6\uAC1C\uC6D4(%p)</th>
              </tr>
            </thead>
            <tbody id="timeBody"></tbody>
          </table>
        </div>
      </section>
    </div>
  </main>

  <script src="plotly-2.35.2.min.js"></script>
  <script>
    const DATA = __DATA__;
    let currentBm = DATA.defaultBm;
    let suggestions = [];
    let suggestionIndex = -1;
    let sortState = {};
    const fmtPct = v => Number.isFinite(v) ? (v * 100).toFixed(2) : '';
    const fmtPp = v => Number.isFinite(v) ? (v >= 0 ? '+' : '') + (v * 100).toFixed(1) : '';
    const fmtActivePp = v => Number.isFinite(v) ? (v >= 0 ? '+' : '') + (v * 100).toFixed(2) : '';
    const fmtChartPct = v => Number.isFinite(v) ? (v * 100).toFixed(1) + '%' : '';
    const fmtRel = v => Number.isFinite(v) ? (v >= 0 ? '+' : '') + v.toFixed(1) : 'N/A';
    const valueClass = v => v > 0 ? 'pos' : v < 0 ? 'neg' : 'na';
    const isZeroWeight = v => !Number.isFinite(v) || Math.abs(v) <= 0.0000001;
    const activeWeightText = v => isZeroWeight(v) ? '\uBBF8\uD3B8\uC785' : fmtPct(v);

    function escapeHtml(value) {
      return String(value ?? '').replace(/[&<>"']/g, ch => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
      }[ch]));
    }
    function normCode(value) {
      return String(value || '').trim().toUpperCase().replace(/\\s+US\\s+EQUITY$/, '').replace(/\\s+INDEX$/, '');
    }
    function allSecurities(item) {
      const map = new Map();
      item.series.forEach(row => {
        if (!map.has(row.c)) map.set(row.c, {code: row.c, fullCode: row.k, name: row.n});
      });
      return [...map.values()].sort((a, b) => a.code.localeCompare(b.code));
    }
    function setChartCode(code) {
      document.getElementById('chartCode').value = code;
      hideSuggestions();
      drawTrend();
      document.getElementById('chartCode').focus();
    }
    function changeCell(change) {
      if (!change || change.kind === 'na') return '<td class="na">-</td>';
      if (change.kind === 'new') return '<td class="new-entry">\uC2E0\uADDC\uD3B8\uC785</td>';
      return `<td class="${valueClass(change.value)}">${fmtActivePp(change.value)}</td>`;
    }
    function changeValue(change) {
      if (!change || change.kind !== 'delta' || !Number.isFinite(change.value)) return null;
      return change.value;
    }
    function maxAbs(values) {
      return Math.max(...values.filter(Number.isFinite).map(value => Math.abs(value)), 0.0001);
    }
    function maxVal(values) {
      return Math.max(...values.filter(Number.isFinite), 0.0001);
    }
    function barCell(value, scale, formatter = fmtPp, mode = 'center') {
      if (mode === 'fill' && isZeroWeight(value)) return '<td class="absent">\uBBF8\uD3B8\uC785</td>';
      const centered = mode === 'center';
      const widthBase = centered ? 50 : 100;
      const width = Math.min(widthBase, Math.abs(value) / scale * widthBase);
      const color = centered
        ? (value >= 0 ? 'rgba(8, 127, 91, 0.18)' : 'rgba(193, 59, 51, 0.18)')
        : 'rgba(34, 94, 155, 0.15)';
      const modeClass = centered ? 'bar-center' : 'bar-fill';
      return `<td class="bar-cell ${modeClass} ${valueClass(value)}" style="--bar-width:${width.toFixed(1)}%; --bar-color:${color};">${formatter(value)}</td>`;
    }
    function changeBarCell(change, scale) {
      const value = changeValue(change);
      if (value === null) return changeCell(change);
      return barCell(value, scale, fmtActivePp, 'center');
    }
    function changeColumnScale(rows, key) {
      return maxAbs(rows.map(row => changeValue(row.changes[key])));
    }
    function sortValue(row, key) {
      if (key.startsWith('changes.')) {
        const change = row.changes?.[key.split('.')[1]];
        if (!change) return null;
        if (change.kind === 'new') return Number.POSITIVE_INFINITY;
        return Number.isFinite(change.value) ? change.value : null;
      }
      return row[key];
    }
    function sortedRows(tableId, rows) {
      const state = sortState[tableId];
      if (!state) return rows;
      const {key, dir} = state;
      const sign = dir === 'asc' ? 1 : -1;
      return [...rows].sort((a, b) => {
        const av = sortValue(a, key);
        const bv = sortValue(b, key);
        const aEmpty = av === null || av === undefined || av === '';
        const bEmpty = bv === null || bv === undefined || bv === '';
        if (aEmpty && bEmpty) return 0;
        if (aEmpty) return 1;
        if (bEmpty) return -1;
        if (typeof av === 'number' && typeof bv === 'number') return (av - bv) * sign;
        return String(av).localeCompare(String(bv), 'ko') * sign;
      });
    }
    function updateSortHeaders() {
      document.querySelectorAll('th[data-sort-table]').forEach(th => {
        const state = sortState[th.dataset.sortTable];
        const active = state && state.key === th.dataset.sortKey;
        th.classList.toggle('sort-desc', active && state.dir === 'desc');
        th.classList.toggle('sort-asc', active && state.dir === 'asc');
      });
    }
    function renderPassive(rows) {
      const displayRows = sortedRows('passive', rows);
      const scale = {
        passiveWeight: maxVal(displayRows.map(row => row.passiveWeight)),
        samsungWeight: maxVal(displayRows.map(row => row.samsungWeight)),
        samsungDiff: maxAbs(displayRows.map(row => row.samsungDiff)),
        timeWeight: maxVal(displayRows.map(row => row.timeWeight)),
        timeDiff: maxAbs(displayRows.map(row => row.timeDiff))
      };
      document.getElementById('passiveBody').innerHTML = displayRows.map((row, idx) => `
        <tr>
          <td class="rank">${idx + 1}</td>
          <td class="code" title="${escapeHtml(row.fullCode)}" data-chart-code="${escapeHtml(row.code)}">${escapeHtml(row.code)}</td>
          <td class="name" title="${escapeHtml(row.name)}" data-chart-code="${escapeHtml(row.code)}">${escapeHtml(row.name)}</td>
          ${barCell(row.passiveWeight, scale.passiveWeight, fmtPct, 'fill')}
          ${barCell(row.samsungWeight, scale.samsungWeight, activeWeightText, 'fill')}
          ${barCell(row.samsungDiff, scale.samsungDiff)}
          ${barCell(row.timeWeight, scale.timeWeight, activeWeightText, 'fill')}
          ${barCell(row.timeDiff, scale.timeDiff)}
        </tr>
      `).join('');
    }
    function renderActive(bodyId, rows) {
      const displayRows = sortedRows(bodyId, rows);
      const scale = {
        weight: maxVal(displayRows.map(row => row.weight)),
        bmWeight: maxVal(displayRows.map(row => row.bmWeight)),
        bmDiff: maxAbs(displayRows.map(row => row.bmDiff)),
        d1: changeColumnScale(displayRows, 'd1'),
        w1: changeColumnScale(displayRows, 'w1'),
        m1: changeColumnScale(displayRows, 'm1'),
        m3: changeColumnScale(displayRows, 'm3'),
        m6: changeColumnScale(displayRows, 'm6')
      };
      document.getElementById(bodyId).innerHTML = displayRows.map((row, idx) => `
        <tr>
          <td class="rank">${idx + 1}</td>
          <td class="code" title="${escapeHtml(row.fullCode)}" data-chart-code="${escapeHtml(row.code)}">${escapeHtml(row.code)}</td>
          <td class="name" title="${escapeHtml(row.name)}" data-chart-code="${escapeHtml(row.code)}">${escapeHtml(row.name)}</td>
          ${barCell(row.weight, scale.weight, fmtPct, 'fill')}
          ${barCell(row.bmWeight, scale.bmWeight, fmtPct, 'fill')}
          ${barCell(row.bmDiff, scale.bmDiff)}
          ${changeBarCell(row.changes.d1, scale.d1)}
          ${changeBarCell(row.changes.w1, scale.w1)}
          ${changeBarCell(row.changes.m1, scale.m1)}
          ${changeBarCell(row.changes.m3, scale.m3)}
          ${changeBarCell(row.changes.m6, scale.m6)}
        </tr>
      `).join('');
    }
    function renderSuggestions() {
      const bm = currentBm || DATA.defaultBm;
      const item = DATA.bms[bm];
      const query = normCode(document.getElementById('chartCode').value);
      const box = document.getElementById('suggestions');
      if (!query) {
        hideSuggestions();
        return;
      }
      suggestions = allSecurities(item)
        .filter(row => row.code.includes(query) || row.fullCode.includes(query) || row.name.toUpperCase().includes(query))
        .sort((a, b) => {
          const rank = row => row.code === query ? 0 : row.code.startsWith(query) ? 1 : row.code.includes(query) ? 2 : row.fullCode.includes(query) ? 3 : 4;
          return rank(a) - rank(b) || a.code.localeCompare(b.code);
        })
        .slice(0, 12);
      suggestionIndex = suggestions.length ? 0 : -1;
      if (!suggestions.length) {
        hideSuggestions();
        return;
      }
      box.innerHTML = suggestions.map((row, idx) => `
        <div class="suggestion ${idx === suggestionIndex ? 'active' : ''}" data-index="${idx}">
          <strong>${escapeHtml(row.code)}</strong> ${escapeHtml(row.name)}
        </div>
      `).join('');
      box.style.display = 'block';
    }
    function hideSuggestions() {
      const box = document.getElementById('suggestions');
      box.style.display = 'none';
      box.innerHTML = '';
      suggestions = [];
      suggestionIndex = -1;
    }
    function moveSuggestion(delta) {
      if (!suggestions.length) return;
      suggestionIndex = (suggestionIndex + delta + suggestions.length) % suggestions.length;
      [...document.querySelectorAll('.suggestion')].forEach((node, idx) => {
        node.classList.toggle('active', idx === suggestionIndex);
      });
    }
    function chooseSuggestion() {
      if (suggestionIndex >= 0 && suggestions[suggestionIndex]) {
        setChartCode(suggestions[suggestionIndex].code);
      }
    }
    function drawTrend() {
      const bm = currentBm || DATA.defaultBm;
      const item = DATA.bms[bm];
      const query = normCode(document.getElementById('chartCode').value || 'NVDA');
      const start = document.getElementById('startDate').value || item.firstDate;
      const end = document.getElementById('endDate').value || item.currentDate;
      const rows = item.series
        .filter(row => row.d >= start && row.d <= end)
        .filter(row => normCode(row.c) === query || row.k.startsWith(query + ' ') || normCode(row.k) === query);
      const byDate = new Map();
      rows.forEach(row => byDate.set(row.d, row));
      const dates = [...byDate.keys()].sort();
      const svg = document.getElementById('trendChart');
      if (!dates.length) {
        if (window.Plotly) Plotly.purge(svg);
        svg.innerHTML = '<div class="na" style="display:grid;place-items:center;height:310px;">\uD45C\uC2DC\uD560 \uC2DC\uACC4\uC5F4\uC774 \uC5C6\uC2B5\uB2C8\uB2E4.</div>';
        document.getElementById('chartMeta').textContent = '';
        return;
      }
      const values = dates.map(d => byDate.get(d));
      if (!window.Plotly) {
        document.getElementById('trendChart').innerHTML = '<div class="na" style="display:grid;place-items:center;height:310px;">Plotly \uD30C\uC77C\uC744 \uBD88\uB7EC\uC624\uC9C0 \uBABB\uD588\uC2B5\uB2C8\uB2E4.</div>';
        return;
      }
      const trace = (label, key, color) => ({
        type: 'scatter',
        mode: 'lines+markers',
        name: label,
        x: dates,
        y: values.map(row => row[key]),
        line: {color, width: 2.5},
        marker: {color, size: 5},
        hovertemplate: `${label}, %{y:.1%}<extra></extra>`
      });
      const latest = values[values.length - 1];
      const labelAnnotation = (label, key, color, offset = 0) => ({
        x: 1.01,
        xref: 'paper',
        xanchor: 'left',
        y: latest[key],
        yref: 'y',
        yshift: offset,
        text: `${label}, ${fmtChartPct(latest[key])}`,
        showarrow: false,
        font: {size: 12, color},
        align: 'left'
      });
      Plotly.react(document.getElementById('trendChart'), [
        trace('BM', 'p', '#087f5b'),
        trace('\uC0BC\uC131', 's', '#225e9b'),
        trace('\uD0C0\uC784', 't', '#c13b33')
      ], {
        margin: {l: 48, r: 92, t: 8, b: 42},
        paper_bgcolor: '#ffffff',
        plot_bgcolor: '#ffffff',
        hovermode: 'x unified',
        showlegend: false,
        annotations: [
          labelAnnotation('BM', 'p', '#087f5b', -12),
          labelAnnotation('\uC0BC\uC131', 's', '#225e9b', 0),
          labelAnnotation('\uD0C0\uC784', 't', '#c13b33', 12)
        ],
        xaxis: {showgrid: false, tickfont: {color: '#647082'}, automargin: true},
        yaxis: {tickformat: '.2%', rangemode: 'tozero', gridcolor: '#edf1f5', tickfont: {color: '#647082'}, automargin: true}
      }, {displayModeBar: false, responsive: true});
      document.getElementById('chartMeta').textContent = `${values[0].c} - ${values[0].n}`;
      return;
    }
    function render() {
      const bm = currentBm || DATA.defaultBm;
      const item = DATA.bms[bm];
      document.getElementById('dateMeta').textContent = `\uAE30\uC900\uC77C ${item.currentDate} · \uC0AC\uC6A9 \uAC00\uB2A5 \uC77C\uC790 ${item.dateCount}\uAC1C`;
      document.getElementById('etfMeta').textContent = `BM ${item.passiveEtf} · \uC0BC\uC131 ${item.samsungEtf} · \uD0C0\uC784 ${item.timeEtf}`;
      document.getElementById('sourceMeta').textContent = `\uC0DD\uC131 ${DATA.generatedAt}`;
      document.getElementById('samsungTitle').innerHTML = `\uC0BC\uC131 \uC561\uD2F0\uBE0C: ${escapeHtml(item.samsungEtf)} <span class="count">(\uD3B8\uC785Ticker\uC218: ${item.samsungRows.length}\uAC1C)</span>`;
      document.getElementById('timeTitle').innerHTML = `\uD0C0\uC784 \uC561\uD2F0\uBE0C: ${escapeHtml(item.timeEtf)} <span class="count">(\uD3B8\uC785Ticker\uC218: ${item.timeRows.length}\uAC1C)</span>`;
      document.getElementById('endDate').value = item.currentDate;
      renderPassive(item.passiveRows);
      renderActive('samsungBody', item.samsungRows);
      renderActive('timeBody', item.timeRows);
      updateSortHeaders();
      drawTrend();
    }
    function renderBmButtons() {
      const box = document.getElementById('bmButtons');
      box.innerHTML = Object.keys(DATA.bms).map(bm => `
        <button class="bm-button ${bm === currentBm ? 'active' : ''}" data-bm="${bm}">
          ${bm === 'NASDAQ100' ? 'NASDAQ100' : bm}
        </button>
      `).join('');
    }
    document.getElementById('bmButtons').addEventListener('click', event => {
      const button = event.target.closest('[data-bm]');
      if (!button) return;
      currentBm = button.dataset.bm;
      renderBmButtons();
      render();
    });
    document.getElementById('chartButton').addEventListener('click', drawTrend);
    document.addEventListener('click', event => {
      const th = event.target.closest('th[data-sort-table]');
      if (!th) return;
      const table = th.dataset.sortTable;
      const current = sortState[table];
      const nextDir = current && current.key === th.dataset.sortKey && current.dir === 'desc' ? 'asc' : 'desc';
      sortState = {...sortState, [table]: {key: th.dataset.sortKey, dir: nextDir}};
      render();
    });
    ['chartCode', 'startDate', 'endDate'].forEach(id => document.getElementById(id).addEventListener('change', drawTrend));
    document.getElementById('chartCode').addEventListener('input', renderSuggestions);
    document.getElementById('chartCode').addEventListener('keydown', event => {
      if (event.key === 'ArrowDown') { event.preventDefault(); moveSuggestion(1); }
      if (event.key === 'ArrowUp') { event.preventDefault(); moveSuggestion(-1); }
      if (event.key === 'Enter' && suggestions.length) { event.preventDefault(); chooseSuggestion(); }
      if (event.key === 'Escape') hideSuggestions();
    });
    document.getElementById('suggestions').addEventListener('mousedown', event => {
      const item = event.target.closest('.suggestion');
      if (!item) return;
      const row = suggestions[Number(item.dataset.index)];
      if (row) setChartCode(row.code);
    });
    document.addEventListener('click', event => {
      const ticker = event.target.closest('[data-chart-code]');
      if (ticker) {
        setChartCode(ticker.dataset.chartCode);
        return;
      }
      if (!event.target.closest('.suggest-wrap')) hideSuggestions();
    });
    renderBmButtons();
    render();
  </script>
</body>
</html>
"""
    return html.replace("__DATA__", payload_json)


def main() -> None:
    args = parse_args()
    parquet_path = Path(args.parquet) if args.parquet else latest_holdings_parquet()
    holdings, etfs = load_inputs(parquet_path, Path(args.list_file))
    payload = build_payload(holdings, etfs, parquet_path)
    output = Path(args.output)
    output.write_text(make_html(payload), encoding="utf-8")
    print(f"Dashboard: {output}")
    print(f"Source parquet: {parquet_path}")
    print(f"BMs: {', '.join(payload['bms'].keys())}")
    for bm, data in payload["bms"].items():
        print(
            f"{bm}: {data['currentDate']} passive={len(data['passiveRows'])} "
            f"samsung={len(data['samsungRows'])} time={len(data['timeRows'])} series={len(data['series'])}"
        )


if __name__ == "__main__":
    main()
