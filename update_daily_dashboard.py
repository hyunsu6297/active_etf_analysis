from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parent
SEOUL = ZoneInfo("Asia/Seoul")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download one day of ETF holdings data and rebuild the static dashboard."
    )
    parser.add_argument(
        "--date",
        default=datetime.now(SEOUL).date().isoformat(),
        help="Target date in YYYY-MM-DD format. Defaults to today's date in Asia/Seoul.",
    )
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="Only rebuild the dashboard from existing parquet data.",
    )
    return parser.parse_args()


def run_step(command: list[str], *, allow_failure: bool = False) -> None:
    print(f"+ {' '.join(command)}", flush=True)
    completed = subprocess.run(command, cwd=ROOT)
    if completed.returncode and not allow_failure:
        raise SystemExit(completed.returncode)
    if completed.returncode:
        print(
            f"WARNING: command exited with {completed.returncode}; "
            "continuing with existing data.",
            flush=True,
        )


def main() -> None:
    args = parse_args()
    target_date = date.fromisoformat(args.date)

    if args.skip_download:
        print("Skipping download by request.", flush=True)
    elif target_date.weekday() >= 5:
        print(f"{target_date} is a weekend; rebuilding from existing data.", flush=True)
    else:
        run_step(
            [
                sys.executable,
                "etf_holdings_pipeline.py",
                "--start",
                target_date.isoformat(),
                "--end",
                target_date.isoformat(),
                "--skip-existing",
            ],
            allow_failure=True,
        )

    run_step([sys.executable, "build_etf_dashboard.py"])

    source = ROOT / "etf_active_weight_dashboard.html"
    target = ROOT / "index.html"
    shutil.copyfile(source, target)
    print(f"Updated {target.name} from {source.name}.", flush=True)


if __name__ == "__main__":
    main()
