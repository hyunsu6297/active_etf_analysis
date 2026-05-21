from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

import pandas as pd
import requests


WORKBOOK = "종목리스트.xlsx"
RAW_DIR = Path("data/raw")
PARQUET_DIR = Path("data/parquet")


@dataclass(frozen=True)
class EtfSource:
    issuer: str
    etf_type: str
    etf_name: str
    url_template: str


STANDARD_COLUMNS = [
    "as_of_date",
    "issuer",
    "etf_type",
    "etf_name",
    "holding_no",
    "security_code",
    "security_name",
    "isin",
    "ticker",
    "quantity",
    "weight",
    "market_value_krw",
    "current_price_krw",
    "price_change_krw",
    "source_url",
    "source_file",
    "downloaded_at",
]

NUMERIC_COLUMNS = [
    "holding_no",
    "quantity",
    "weight",
    "market_value_krw",
    "current_price_krw",
    "price_change_krw",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download ETF holding files and save standardized parquet output."
    )
    parser.add_argument("--list-file", default=WORKBOOK, help="ETF list workbook path")
    parser.add_argument("--start", help="Start date, YYYY-MM-DD. Defaults to today.")
    parser.add_argument("--end", help="End date, YYYY-MM-DD. Defaults to --start.")
    parser.add_argument("--raw-dir", default=str(RAW_DIR), help="Raw download directory")
    parser.add_argument("--parquet-dir", default=str(PARQUET_DIR), help="Parquet output directory")
    parser.add_argument("--skip-existing", action="store_true", help="Do not re-download existing files")
    return parser.parse_args()


def business_days(start: date, end: date) -> Iterable[date]:
    current = start
    while current <= end:
        if current.weekday() < 5:
            yield current
        current += timedelta(days=1)


def load_sources(path: Path) -> list[EtfSource]:
    df = pd.read_excel(path)
    required = {"운용사", "유형", "ETF명", "다운로드링크"}
    missing = required.difference(df.columns)
    if missing:
        raise ValueError(f"Missing columns in {path}: {sorted(missing)}")

    sources: list[EtfSource] = []
    for row in df.dropna(subset=["다운로드링크"]).itertuples(index=False):
        sources.append(
            EtfSource(
                issuer=str(getattr(row, "운용사")).strip(),
                etf_type=str(getattr(row, "유형")).strip(),
                etf_name=str(getattr(row, "ETF명")).strip(),
                url_template=str(getattr(row, "다운로드링크")).strip(),
            )
        )
    return sources


def render_url(template: str, target_date: date) -> str:
    return (
        template.replace("YYYY-MM-DD", target_date.strftime("%Y-%m-%d"))
        .replace("YYYYMMDD", target_date.strftime("%Y%m%d"))
    )


def safe_name(value: str) -> str:
    return re.sub(r"[^\w가-힣.-]+", "_", value, flags=re.UNICODE).strip("_")


def extension_from_response(response: requests.Response, url: str) -> str:
    disposition = response.headers.get("content-disposition", "")
    match = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)", disposition, flags=re.I)
    if match:
        suffix = Path(match.group(1)).suffix
        if suffix:
            return suffix.lower()

    content_type = response.headers.get("content-type", "").lower()
    if "spreadsheetml" in content_type or "xlsx" in content_type:
        return ".xlsx"
    if "excel" in content_type or "xls" in content_type:
        return ".xls"
    suffix = Path(url.split("?", 1)[0]).suffix
    return suffix.lower() if suffix else ".xls"


def download_file(
    source: EtfSource,
    target_date: date,
    raw_dir: Path,
    skip_existing: bool,
) -> tuple[Path, str]:
    url = render_url(source.url_template, target_date)
    date_dir = raw_dir / target_date.strftime("%Y-%m-%d")
    date_dir.mkdir(parents=True, exist_ok=True)

    stem = f"{safe_name(source.issuer)}_{safe_name(source.etf_type)}_{safe_name(source.etf_name)}_{target_date:%Y%m%d}"
    existing = sorted(date_dir.glob(f"{stem}.*"))
    if skip_existing and existing:
        return existing[0], url

    response = requests.get(url, timeout=60)
    response.raise_for_status()
    if not response.content:
        raise ValueError(f"Empty response for {source.etf_name} {target_date:%Y-%m-%d}")

    ext = extension_from_response(response, url)
    output_path = date_dir / f"{stem}{ext}"
    output_path.write_bytes(response.content)
    return output_path, url


def read_excel_any(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".xls":
        return pd.read_excel(path, header=None, engine="xlrd")
    return pd.read_excel(path, header=None, engine="openpyxl")


def clean_number(value) -> float | None:
    if pd.isna(value):
        return None
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        if text in {"", "-", "nan"}:
            return None
        value = text
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_weight(value, issuer: str) -> float | None:
    number = clean_number(value)
    if number is None:
        return None
    if issuer == "삼성":
        return number
    return number / 100


def find_header_row(frame: pd.DataFrame, labels: set[str]) -> int:
    for idx, row in frame.iterrows():
        values = {str(x).strip() for x in row.dropna().tolist()}
        if labels.issubset(values):
            return int(idx)
    raise ValueError(f"Could not find header row containing {sorted(labels)}")


def parse_samsung(
    frame: pd.DataFrame,
    source: EtfSource,
    target_date: date,
    source_url: str,
    file_path: Path,
    downloaded_at: str,
) -> pd.DataFrame:
    header_row = find_header_row(frame, {"번호", "종목명", "수량", "비중(%)"})
    header = [str(x).strip() if not pd.isna(x) else "" for x in frame.iloc[header_row].tolist()]
    data = frame.iloc[header_row + 1 :].copy()
    data.columns = header
    data = data.dropna(how="all")
    data = data[data["번호"].apply(clean_number).notna()]

    out = pd.DataFrame(
        {
            "as_of_date": target_date.isoformat(),
            "issuer": source.issuer,
            "etf_type": source.etf_type,
            "etf_name": source.etf_name,
            "holding_no": data.get("번호").apply(clean_number),
            "security_code": data.get("종목코드"),
            "security_name": data.get("종목명"),
            "isin": data.get("ISIN"),
            "ticker": data.get("종목코드"),
            "quantity": data.get("수량").apply(clean_number),
            "weight": data.get("비중(%)").apply(lambda x: normalize_weight(x, source.issuer)),
            "market_value_krw": data.get("평가금액(원)").apply(clean_number),
            "current_price_krw": data.get("현재가(원)").apply(clean_number)
            if "현재가(원)" in data
            else None,
            "price_change_krw": data.get("등락(원)").apply(clean_number) if "등락(원)" in data else None,
            "source_url": source_url,
            "source_file": str(file_path),
            "downloaded_at": downloaded_at,
        }
    )
    return out


def parse_time(
    frame: pd.DataFrame,
    source: EtfSource,
    target_date: date,
    source_url: str,
    file_path: Path,
    downloaded_at: str,
) -> pd.DataFrame:
    header_row = find_header_row(frame, {"종목코드", "종목명", "수량", "비중(%)"})
    header = [str(x).strip() if not pd.isna(x) else "" for x in frame.iloc[header_row].tolist()]
    data = frame.iloc[header_row + 1 :].copy()
    data.columns = header
    data = data.dropna(how="all")
    data = data[data["종목코드"].notna()]

    out = pd.DataFrame(
        {
            "as_of_date": target_date.isoformat(),
            "issuer": source.issuer,
            "etf_type": source.etf_type,
            "etf_name": source.etf_name,
            "holding_no": range(1, len(data) + 1),
            "security_code": data.get("종목코드"),
            "security_name": data.get("종목명"),
            "isin": None,
            "ticker": data.get("종목코드"),
            "quantity": data.get("수량").apply(clean_number),
            "weight": data.get("비중(%)").apply(lambda x: normalize_weight(x, source.issuer)),
            "market_value_krw": data.get("평가금액(원)").apply(clean_number),
            "current_price_krw": None,
            "price_change_krw": None,
            "source_url": source_url,
            "source_file": str(file_path),
            "downloaded_at": downloaded_at,
        }
    )
    return out


def standardize(
    file_path: Path,
    source: EtfSource,
    target_date: date,
    source_url: str,
    downloaded_at: str,
) -> pd.DataFrame:
    frame = read_excel_any(file_path)
    if source.issuer == "타임":
        out = parse_time(frame, source, target_date, source_url, file_path, downloaded_at)
    else:
        out = parse_samsung(frame, source, target_date, source_url, file_path, downloaded_at)

    out = out.reindex(columns=STANDARD_COLUMNS)
    out["security_code"] = out["security_code"].astype("string").str.strip()
    out["security_name"] = out["security_name"].astype("string").str.strip()
    out["isin"] = out["isin"].astype("string").str.strip()
    out["ticker"] = out["ticker"].astype("string").str.strip()
    for column in NUMERIC_COLUMNS:
        out[column] = pd.to_numeric(out[column], errors="coerce")
    return out


def main() -> None:
    args = parse_args()
    list_file = Path(args.list_file)
    raw_dir = Path(args.raw_dir)
    parquet_dir = Path(args.parquet_dir)
    start = datetime.strptime(args.start, "%Y-%m-%d").date() if args.start else date.today()
    end = datetime.strptime(args.end, "%Y-%m-%d").date() if args.end else start
    if end < start:
        raise ValueError("--end must be greater than or equal to --start")
    downloaded_at = datetime.now().isoformat(timespec="seconds")

    sources = load_sources(list_file)
    parquet_dir.mkdir(parents=True, exist_ok=True)

    all_frames: list[pd.DataFrame] = []
    failures: list[dict[str, str]] = []
    for target_date in business_days(start, end):
        for source in sources:
            try:
                raw_file, source_url = download_file(source, target_date, raw_dir, args.skip_existing)
                holdings = standardize(raw_file, source, target_date, source_url, downloaded_at)
                all_frames.append(holdings)
                print(
                    f"OK {target_date:%Y-%m-%d} {source.issuer} {source.etf_name}: "
                    f"{len(holdings)} rows -> {raw_file}"
                )
            except Exception as exc:
                failures.append(
                    {
                        "as_of_date": target_date.isoformat(),
                        "issuer": source.issuer,
                        "etf_name": source.etf_name,
                        "error": str(exc),
                    }
                )
                print(f"FAIL {target_date:%Y-%m-%d} {source.issuer} {source.etf_name}: {exc}")

    if not all_frames:
        raise SystemExit("No holdings were downloaded and standardized.")

    combined = pd.concat(all_frames, ignore_index=True)
    combined_path = parquet_dir / f"etf_holdings_{start:%Y%m%d}_{end:%Y%m%d}.parquet"
    combined.to_parquet(combined_path, index=False)

    for (as_of_date, issuer, etf_name), group in combined.groupby(["as_of_date", "issuer", "etf_name"]):
        part_dir = parquet_dir / f"as_of_date={as_of_date}" / f"issuer={safe_name(issuer)}"
        part_dir.mkdir(parents=True, exist_ok=True)
        group.to_parquet(part_dir / f"{safe_name(etf_name)}.parquet", index=False)

    if failures:
        failure_path = parquet_dir / f"download_failures_{start:%Y%m%d}_{end:%Y%m%d}.csv"
        pd.DataFrame(failures).to_csv(failure_path, index=False, encoding="utf-8-sig")
        print(f"Failures: {len(failures)} -> {failure_path}")

    print(f"Rows: {len(combined)}")
    print(f"Combined parquet: {combined_path}")


if __name__ == "__main__":
    main()
