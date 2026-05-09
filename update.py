"""One-shot runner: fetch today's data and generate the report.

Exit codes:
  0 — new report generated successfully
  2 — skipped (report already exists, or no trading data yet)
  1 — unexpected error
"""
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

from fetcher.market_calendar import get_latest_trading_day
from generator import report_builder


def main():
    target = get_latest_trading_day()
    print(f"Building report for trading day: {target.isoformat()}")
    try:
        generated = report_builder.build(target)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    if generated:
        print("Done. New report generated.")
        sys.exit(0)
    else:
        print("Done. Report already exists or no data — skipped.")
        sys.exit(2)


if __name__ == "__main__":
    main()
