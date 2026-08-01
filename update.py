"""One-shot runner: fetch today's data and generate the report.

Exit codes:
  0 — new report generated, OR partial today.json written (deploy needed)
  2 — skipped (nothing new)
  1 — unexpected error
"""
import logging
import os
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

from fetcher.market_calendar import get_latest_trading_day
from generator import report_builder


def _export_report_date(target):
    """CI 用：把日期丟給後續 step 當 commit message（本機執行時 no-op）。"""
    github_env = os.environ.get("GITHUB_ENV")
    if github_env:
        with open(github_env, "a", encoding="utf-8") as f:
            f.write(f"REPORT_DATE={target.isoformat()}\n")


def main():
    target = get_latest_trading_day()
    print(f"Building report for trading day: {target.isoformat()}")
    try:
        generated = report_builder.build(target)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    if generated is True:
        _export_report_date(target)
        print("Done. New report generated.")
        sys.exit(0)
    elif generated == "partial":
        _export_report_date(target)
        print("Done. Partial today.json written (institutional data not yet available).")
        sys.exit(0)
    else:
        print("Done. Nothing new — skipped.")
        sys.exit(2)


if __name__ == "__main__":
    main()
