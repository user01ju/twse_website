"""本地強制重建指定日期的報告（含完整 today.json）。

GitHub Actions 的 update.py 以「最新交易日」為目標，盤後資料還沒發布時
只會寫空的 partial。本腳本可指定確切日期、強制重建完整報告，並覆蓋
本地 output/（報告、index、today.json、價格快取）。

用法:
  python rebuild_local.py                # 重建最新交易日
  python rebuild_local.py 2026-06-18     # 重建指定日期
  python rebuild_local.py 20260618       # 同上，YYYYMMDD 亦可
"""
import logging
import os
import sys
from datetime import date

# 必須在 import config 之前設好，FORCE_REBUILD 才會生效
os.environ["FORCE_REBUILD"] = "true"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)


def _parse_date(s: str) -> date:
    s = s.strip().replace("/", "-")
    if "-" in s:
        return date.fromisoformat(s)
    if len(s) == 8 and s.isdigit():           # YYYYMMDD
        return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    raise ValueError(f"無法解析日期: {s}")


def main():
    from fetcher.market_calendar import get_latest_trading_day
    from generator import report_builder

    if len(sys.argv) > 1:
        target = _parse_date(sys.argv[1])
    else:
        target = get_latest_trading_day()

    print(f"強制重建報告: {target.isoformat()} (FORCE_REBUILD=true)")
    result = report_builder.build(target)

    if result is True:
        print(f"[OK] 完成: output/reports/{target.isoformat()}.html + today.json")
        sys.exit(0)
    elif result == "partial":
        print("[WARN] 只寫了 partial — 該日盤後資料（法人/個股）尚未發布，稍後再試")
        sys.exit(2)
    else:
        print("[WARN] 未產生報告（非交易日、或資料日期與目標差太多）")
        sys.exit(2)


if __name__ == "__main__":
    main()
