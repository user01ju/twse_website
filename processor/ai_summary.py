"""AI-generated 今日盤勢總覽 using Claude API."""
import logging
import os

logger = logging.getLogger(__name__)


def _fmt_stocks(stocks: list, n: int, show_yi: bool = True) -> str:
    parts = []
    for s in stocks[:n]:
        yi_str = f"/{s['net_yi']:.1f}億" if show_yi else ""
        parts.append(f"{s['name']}({s['code']}) {s['net_zhang']:+,}張{yi_str}")
    return "、".join(parts)


def _build_prompt(sections: dict, date_str: str) -> str:
    lines = [f"以下是台灣股市 {date_str} 的完整收盤資料：\n"]

    # TAIEX
    if sections.get("taiex", {}).get("ok"):
        t = sections["taiex"]["data"]
        dir_str = "上漲" if t["change_pts"] >= 0 else "下跌"
        lines.append(
            f"【加權指數】收盤 {t['close']:,.2f}，"
            f"{dir_str} {abs(t['change_pts']):.2f} 點（{t['change_pct']:+.2f}%），"
            f"成交金額 {t['trading_amount_yi']:,.0f} 億"
        )

    # Market breadth
    if sections.get("breadth", {}).get("ok"):
        b = sections["breadth"]["data"]
        for mkt, label in [("twse", "上市"), ("tpex", "上櫃")]:
            m = b.get(mkt, {})
            if m:
                lines.append(
                    f"【{label}】上漲 {m.get('up', 0)} / 下跌 {m.get('down', 0)} / 持平 {m.get('flat', 0)}，"
                    f"漲停 {m.get('limit_up', 0)} / 跌停 {m.get('limit_down', 0)}（共 {m.get('total', 0)} 支）"
                )

    # Market trend (20MA breadth / 52w new high-low)
    if sections.get("market_trend", {}).get("ok"):
        mt = sections["market_trend"]["data"]
        ma = mt.get("above_ma20", {})
        nh = mt.get("new_high_low", {})
        # degraded(cache 缺天) 時整段不進 prompt — 錯的趨勢數字會污染摘要敘事
        if ma.get("total") and not mt.get("degraded"):
            lines.append(
                f"【市場趨勢】收盤站上20日均線比例 {ma.get('pct', 0)}%（{ma.get('count', 0)}/{ma.get('total', 0)} 支），"
                f"創52週新高 {nh.get('new_high', 0)} 支 / 新低 {nh.get('new_low', 0)} 支（淨 {nh.get('net', 0):+d}）"
            )

    # Institutional aggregate
    if sections.get("institutional", {}).get("ok"):
        inst = sections["institutional"]["data"]
        for mkt_key, label in [("twse", "上市三大法人"), ("tpex", "上櫃三大法人")]:
            rows = inst.get(mkt_key, [])
            total = next((r for r in rows if r.get("is_total")), None)
            if total:
                sign = "買超" if total["net_yi"] >= 0 else "賣超"
                # Also show sub-components (外資、投信、自營商)
                details = []
                for r in rows:
                    if not r.get("is_total"):
                        s = "買超" if r["net_yi"] >= 0 else "賣超"
                        details.append(f"{r['name']}{s}{abs(r['net_yi']):.1f}億")
                lines.append(
                    f"【{label}合計】{sign} {abs(total['net_yi']):.1f} 億"
                    + (f"（{' / '.join(details)}）" if details else "")
                )

    # Foreign institutional — expand to top 10
    if sections.get("foreign", {}).get("ok"):
        fg = sections["foreign"]["data"]
        buy  = fg.get("buy_super",  [])[:10]
        sell = fg.get("sell_super", [])[:10]
        if buy:
            lines.append("【外資買超前10】" + _fmt_stocks(buy, 10))
        if sell:
            lines.append("【外資賣超前10】" + _fmt_stocks(sell, 10))

    # Trust — expand to top 10
    if sections.get("trust", {}).get("ok"):
        tr = sections["trust"]["data"]
        buy  = tr.get("buy_super",  [])[:10]
        sell = tr.get("sell_super", [])[:10]
        if buy:
            lines.append("【投信買超前10】" + _fmt_stocks(buy, 10, show_yi=False))
        if sell:
            lines.append("【投信賣超前10】" + _fmt_stocks(sell, 10, show_yi=False))

    # Top movers — expand to top 10
    if sections.get("movers", {}).get("ok"):
        mv = sections["movers"]["data"]
        gainers = mv.get("gainers", [])[:10]
        losers  = mv.get("losers",  [])[:10]
        if gainers:
            lines.append("【漲幅前10】" + "、".join(
                f"{s['name']}({s['code']}) {s['change_pct']:+.2f}%"
                for s in gainers
            ))
        if losers:
            lines.append("【跌幅前10】" + "、".join(
                f"{s['name']}({s['code']}) {s['change_pct']:+.2f}%"
                for s in losers
            ))

    lines.append(
        "\n請根據以上數據，用繁體中文撰寫「今日盤勢總覽」。\n"
        "必須嚴格使用以下格式輸出，共四個區塊，每個區塊之間空一行：\n\n"
        "【大盤概況】\n"
        "一段話說明指數漲跌、成交量、市場廣度（漲跌家數、漲跌停家數）。\n\n"
        "【熱門族群】\n"
        "• 族群名稱（如半導體、金融、航運、AI伺服器等）：說明今日動態，點名 2-3 支代表個股（附代號）\n"
        "• 族群名稱：...\n"
        "（列出 3-4 個今日最活躍或有明顯輪動跡象的族群）\n\n"
        "【法人動向】\n"
        "• 外資：買超哪些個股/族群（附代號與金額），賣超哪些（附代號與金額）\n"
        "• 投信：買超哪些個股（附代號），賣超哪些\n\n"
        "【弱勢警示】\n"
        "• 列出今日明顯下跌的族群或個股（附代號），說明原因或現象\n\n"
        "注意：每個 • 項目約 30-50 字，語氣客觀專業，不提供投資建議，不加任何額外說明文字。"
    )

    return "\n".join(lines)


def build(sections: dict, date_str: str) -> dict:
    """Call Claude API and return {summary, model, prompt_tokens, output_tokens}."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY environment variable not set")

    import anthropic  # lazy import — only needed when key is available

    prompt = _build_prompt(sections, date_str)
    client = anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model="claude-opus-4-8",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )
    text = msg.content[0].text.strip()
    # Strip any markdown heading Claude might prepend
    import re as _re
    text = _re.sub(r'^#+\s*今日盤勢總覽\s*\n+', '', text).strip()

    logger.info(
        f"AI summary generated ({msg.usage.input_tokens} in / {msg.usage.output_tokens} out tokens)"
    )
    return {
        "summary":       text,
        "model":         msg.model,
        "input_tokens":  msg.usage.input_tokens,
        "output_tokens": msg.usage.output_tokens,
    }
