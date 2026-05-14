"""Generate output/today.json — lightweight market snapshot for homepage widget.

Called on every workflow run (even when institutional data is missing).
Returns True if the file was written/updated, False if nothing changed.
"""
import json
import logging
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

_TZ = ZoneInfo("Asia/Taipei")

from config import OUTPUT_DIR
from processor import index_stats, market_breadth, movers
from processor.movers import _normalize_twse, _normalize_tpex

logger = logging.getLogger(__name__)

TODAY_JSON = OUTPUT_DIR / "today.json"


def build(actual_date: date, raw: dict, complete: bool) -> bool:
    twse_stocks  = raw.get("twse_stocks") or []
    twse_index   = raw.get("twse_index")  or []
    tpex_stocks  = raw.get("tpex_daily")  or []
    esb_stocks   = raw.get("tpex_esb")    or []
    twt84u       = raw.get("twse_twt84u") or {}
    tpex_hl      = raw.get("tpex_highlight") or {}
    fmtqik       = raw.get("fmtqik")

    # ── taiex ───────────────────────────────────────────────────────────────
    try:
        taiex = index_stats.build(twse_index, twse_stocks, fmtqik)
    except Exception as e:
        logger.warning(f"today_builder: index_stats failed: {e}")
        taiex = {}   # allow writing a date-only placeholder when raw is empty

    # ── breadth ─────────────────────────────────────────────────────────────
    try:
        breadth = market_breadth.build(twse_stocks, tpex_stocks, esb_stocks, twt84u, tpex_hl)
    except Exception as e:
        logger.warning(f"today_builder: market_breadth failed: {e}")
        breadth = {}

    # ── top 5 gainers / losers ───────────────────────────────────────────────
    try:
        combined = [s for s in _normalize_twse(twse_stocks) + _normalize_tpex(tpex_stocks)
                    if abs(s["change_pct"]) < 50]
        top_gainers = sorted(combined, key=lambda s: s["change_pct"], reverse=True)[:5]
        top_losers  = sorted(combined, key=lambda s: s["change_pct"])[:5]
        mover_keys  = ("code", "name", "close", "change_pct", "market")
        top_gainers = [{k: s[k] for k in mover_keys} for s in top_gainers]
        top_losers  = [{k: s[k] for k in mover_keys} for s in top_losers]
    except Exception as e:
        logger.warning(f"today_builder: movers failed: {e}")
        top_gainers = top_losers = []

    payload = {
        "date":         actual_date.isoformat(),
        "generated_at": datetime.now(_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "complete":     complete,
        "taiex":        taiex,
        "breadth": {
            "twse": breadth.get("twse", {}),
            "tpex": breadth.get("tpex", {}),
        },
        "top_gainers": top_gainers,
        "top_losers":  top_losers,
    }

    new_text = json.dumps(payload, ensure_ascii=False, indent=2)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Never downgrade: if existing today.json is already complete for this date, keep it.
    if TODAY_JSON.exists():
        existing_text = TODAY_JSON.read_text(encoding="utf-8")
        if existing_text == new_text:
            return False
        try:
            existing = json.loads(existing_text)
            if (not complete
                    and existing.get("date") == actual_date.isoformat()
                    and existing.get("complete")):
                logger.info("today.json already complete for today — not downgrading to partial")
                return False
        except Exception:
            pass

    TODAY_JSON.write_text(new_text, encoding="utf-8")
    logger.info(f"today.json written (complete={complete})")
    return True
