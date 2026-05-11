import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime

from config import REPORTS_DIR, FORCE_REBUILD
from fetcher import twse_client, tpex_client
from fetcher.market_calendar import roc_to_date
from processor import index_stats, market_breadth, movers, institutional, foreign_trades, trust_trades, combined_inst, dealer_trades, ai_summary, sector_inst
from generator import renderer, index_builder, today_builder

logger = logging.getLogger(__name__)


def _safe(fn, *args, **kwargs) -> dict:
    try:
        return {"ok": True, "data": fn(*args, **kwargs)}
    except Exception as e:
        logger.error(f"{fn.__name__} failed: {e}", exc_info=True)
        return {"ok": False, "error": str(e)}


def _build_price_lookup(stock_list: list[dict], code_field: str, price_field: str) -> dict:
    result = {}
    for s in stock_list:
        try:
            code = str(s.get(code_field, "")).strip()
            val  = str(s.get(price_field, "0")).replace(",", "").strip()
            result[code] = float(val) if val and val not in ("---", "--", "") else 0.0
        except (ValueError, TypeError):
            pass
    return result


def _parallel_fetch(tasks: dict) -> dict:
    raw = {}
    with ThreadPoolExecutor(max_workers=5) as pool:
        future_map = {pool.submit(fn, *args): key for key, (fn, args) in tasks.items()}
        for future in as_completed(future_map, timeout=60):
            key = future_map[future]
            try:
                raw[key] = future.result(timeout=30)
            except Exception as e:
                logger.error(f"Fetch {key} failed: {e}")
                raw[key] = None
    return raw


def _detect_data_date(twse_stocks: list | None, twse_index: list | None) -> date | None:
    """Read actual data date from STOCK_DAY_ALL or MI_INDEX response."""
    for source in (twse_stocks, twse_index):
        if source:
            raw_date = source[0].get("Date") or source[0].get("日期")
            if raw_date:
                try:
                    return roc_to_date(str(raw_date).strip())
                except Exception:
                    pass
    return None


def build(target_date: date) -> None:
    logger.info(f"Fetching market data (requested date: {target_date.isoformat()})")

    today_str = target_date.strftime("%Y%m%d")

    # ── Fetch all endpoints in parallel ────────────────────────────────────
    # All TWSE legacy endpoints return current-day data without a date param.
    raw = _parallel_fetch({
        "twse_index":  (twse_client.fetch_taiex_index,  [today_str]),
        "twse_stocks": (twse_client.fetch_stock_day_all, [today_str]),
        "twse_bfi82u": (twse_client.fetch_bfi82u,        []),
        "twse_t86":    (twse_client.fetch_t86,           []),
        "twse_twt84u":    (twse_client.fetch_twt84u,         [today_str]),
        "tpex_daily":     (tpex_client.fetch_daily_quotes,   [target_date]),
        "tpex_highlight": (tpex_client.fetch_highlight,       [target_date]),
        "tpex_3sum":   (tpex_client.fetch_3insti_summary,[target_date]),
        "tpex_qfii":   (tpex_client.fetch_3insti_qfii,   [target_date]),
        "tpex_trust":  (tpex_client.fetch_3insti_trust,  [target_date]),
        "tpex_all":    (tpex_client.fetch_3insti_all,    [target_date]),
        "tpex_esb":    (tpex_client.fetch_esb_quotes,    [target_date]),
    })

    # Determine actual data date from STOCK_DAY_ALL / MI_5MINS_HIST response
    actual_date = _detect_data_date(raw.get("twse_stocks"), raw.get("twse_index"))
    if actual_date is None:
        actual_date = target_date
        logger.warning("Could not detect data date from API; using requested date")
    else:
        logger.info(f"Actual data date from API: {actual_date.isoformat()}")

    # Cross-check TPEX data date against TWSE actual_date
    tpex_date = tpex_client.get_tpex_data_date(raw.get("tpex_daily") or [])
    if tpex_date and tpex_date != actual_date:
        logger.warning(f"TPEX data date mismatch: TWSE={actual_date.isoformat()}, TPEX={tpex_date.isoformat()} — TPEX may not have updated yet")

    # ── Completeness gate ───────────────────────────────────────────────────
    # Institutional data (T86, BFI82U, TPEX 3insti) is typically available ~17:00.
    # If any key source is missing, write today.json (partial) and skip full report.
    missing = [k for k in ("twse_t86", "twse_bfi82u", "tpex_3sum", "tpex_all") if not raw.get(k)]
    if missing:
        logger.info(f"Institutional data not yet available ({', '.join(missing)}), writing partial today.json")
        updated = today_builder.build(actual_date, raw, complete=False)
        if updated:
            renderer.copy_static()
            index_builder.rebuild()
        return "partial" if updated else False

    output_path = REPORTS_DIR / f"{actual_date.isoformat()}.html"
    if output_path.exists() and not FORCE_REBUILD:
        logger.info(f"Report already exists: {output_path}, skipping (set FORCE_REBUILD=True to override)")
        return False

    # ── Price lookups ───────────────────────────────────────────────────────
    twse_prices = _build_price_lookup(raw.get("twse_stocks") or [], "Code", "ClosingPrice")
    tpex_prices = _build_price_lookup(raw.get("tpex_daily") or [],  "SecuritiesCompanyCode", "Close")

    # ── Process sections ────────────────────────────────────────────────────
    sections = {}

    sections["taiex"] = _safe(
        index_stats.build,
        raw.get("twse_index") or [],
        raw.get("twse_stocks") or [],
    )
    sections["breadth"] = _safe(
        market_breadth.build,
        raw.get("twse_stocks") or [],
        raw.get("tpex_daily") or [],
        raw.get("tpex_esb") or [],
        raw.get("twse_twt84u") or {},
        raw.get("tpex_highlight") or {},
    )
    sections["institutional"] = _safe(
        institutional.build,
        raw.get("twse_bfi82u") or {},
        raw.get("tpex_3sum") or [],
    )
    sections["movers"] = _safe(
        movers.build,
        raw.get("twse_stocks") or [],
        raw.get("tpex_daily") or [],
    )
    sections["foreign"] = _safe(
        foreign_trades.build,
        raw.get("twse_t86") or {},
        raw.get("tpex_qfii") or [],
        twse_prices,
        tpex_prices,
    )
    sections["trust"] = _safe(
        trust_trades.build,
        raw.get("twse_t86") or {},
        raw.get("tpex_trust") or [],
        twse_prices,
        tpex_prices,
    )
    sections["combined"] = _safe(
        combined_inst.build,
        raw.get("twse_t86") or {},
        raw.get("tpex_all") or [],
        twse_prices,
        tpex_prices,
    )
    sections["dealer"] = _safe(
        dealer_trades.build,
        raw.get("twse_t86") or {},
        raw.get("tpex_all") or [],
        twse_prices,
        tpex_prices,
    )
    sections["sector_inst"] = _safe(
        sector_inst.build,
        sections["foreign"].get("data", {})  if sections["foreign"]["ok"]  else {},
        sections["trust"].get("data", {})    if sections["trust"]["ok"]    else {},
        sections["combined"].get("data", {}) if sections["combined"]["ok"] else {},
        sections["dealer"].get("data", {})   if sections["dealer"]["ok"]   else {},
    )
    sections["sector_summary"] = _safe(
        sector_inst.build_summary,
        sections["combined"].get("data", {}) if sections["combined"]["ok"] else {},
    )
    sections["ai_summary"] = _safe(
        ai_summary.build,
        sections,
        actual_date.isoformat(),
    )

    # ── Render & save ───────────────────────────────────────────────────────
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    renderer.render_report(actual_date, sections, output_path, generated_at)
    today_builder.build(actual_date, raw, complete=True)
    renderer.copy_static()
    index_builder.rebuild()

    logger.info(f"Report saved: {output_path}")
    return True
