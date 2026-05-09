"""One-shot runner: fetch today's data and generate the report."""
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
    report_builder.build(target)
    print("Done.")


if __name__ == "__main__":
    main()
