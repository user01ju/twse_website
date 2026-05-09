"""Daily scheduler — runs update.py at 15:05 on weekdays."""
import logging
import sys
import time

import schedule

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

from update import main as run_update


def job():
    logging.info("Scheduled job triggered")
    try:
        run_update()
    except Exception as e:
        logging.error(f"Scheduled job failed: {e}", exc_info=True)


schedule.every().monday.at("15:05").do(job)
schedule.every().tuesday.at("15:05").do(job)
schedule.every().wednesday.at("15:05").do(job)
schedule.every().thursday.at("15:05").do(job)
schedule.every().friday.at("15:05").do(job)

if __name__ == "__main__":
    print("Scheduler running — will fetch data at 15:05 on weekdays.")
    print("Press Ctrl+C to stop.")
    while True:
        schedule.run_pending()
        time.sleep(30)
