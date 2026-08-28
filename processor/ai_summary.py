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


def _flow_sector_line(g: dict, short: int, long: int) -> str:
    bits = [f"{g['sector']} {short}日{g['net5']:+.0f}億"]
    if g.get("net5_pct") is not None:
        bits.append(f"佔類股市值{g['net5_pct']:+.2f}%")
    bits.append(f"{long}日{g['net20']:+.0f}億")
    if g.get("streak"):
        bits.append(f"連{'買' if g['streak'] > 0 else '賣'}{abs(g['streak'])}天")
    if g.get("accel_tag"):
        bits.append(g["accel_tag"])
    bits.append(f"成分股買{g.get('up', 0)}/賣{g.get('down', 0)}家")
    if g.get("lead") is not None:
        # 一定要寫出是哪一檔：只給「最大一檔佔X%」的話模型會自己猜是誰，
        # 實測把 PCB-材料設備 的龍頭講成欣興(3037)，但欣興屬 ABF。
        who = f"（{g['lead_name']}{g['lead_code']}）" if g.get("lead_name") else ""
        bits.append(f"最大一檔{who}佔{g['lead']}%")
    return "、".join(bits)


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

    # 資金流向（5/20 日窗口）— 唯一帶時間維度的資料，上面每一段都只有當日。
    # degraded（快取缺天）時整段不進 prompt，規則跟 market_trend 一致：窗口沒湊滿時
    # 5/20 日累計會安靜偏小，錯的數字會污染摘要敘事。
    sf = sections.get("sector_flow", {})
    if sf.get("ok") and not sf["data"].get("degraded"):
        d = sf["data"]
        short, long = d.get("short", 5), d.get("long", 20)
        rows = (d.get("tabs") or {}).get("c") or []
        if rows:
            lines.append(f"（以下是近 {d.get('days')} 個交易日的三大法人累計流向，"
                         f"與上方單日數據互補）")
            lines.append("【資金流入族群 Top6】" +
                         "；".join(_flow_sector_line(g, short, long) for g in rows[:6]))
            out_rows = [g for g in rows[-5:] if g["net5"] < 0]
            if out_rows:
                lines.append("【資金流出族群】" +
                             "；".join(_flow_sector_line(g, short, long) for g in out_rows))

        srows = (d.get("stock_tabs") or {}).get("c") or []
        streaks = sorted((r for r in srows if abs(r.get("streak", 0)) >= 5),
                         key=lambda r: -abs(r["streak"]))[:6]
        if streaks:
            lines.append("【連續進出個股】" + "、".join(
                f"{r['name']}({r['code']}) 連{'買' if r['streak'] > 0 else '賣'}"
                f"{abs(r['streak'])}天/{short}日{r['net5']:+.0f}億"
                + (f"/外資投信{r['cons_tag']}" if r.get("cons_tag") else "")
                for r in streaks))
        flips = [r for r in srows if r.get("accel_tag") == "翻轉"][:4]
        if flips:
            lines.append("【資金翻轉個股】" + "、".join(
                f"{r['name']}({r['code']}) {long}日{r['net20']:+.0f}億"
                f"但{short}日{r['net5']:+.0f}億"
                for r in flips))

    lines.append(
        "\n請根據以上數據，用繁體中文撰寫「今日盤勢總覽」。\n"
        "必須嚴格使用以下格式輸出，共五個區塊，每個區塊之間空一行：\n\n"
        "【大盤概況】\n"
        "一段話說明指數漲跌、成交量、市場廣度（漲跌家數、漲跌停家數）。\n\n"
        "【熱門族群】\n"
        "• 族群名稱（如半導體、金融、航運、AI伺服器等）：說明今日動態，點名 2-3 支代表個股（附代號）\n"
        "• 族群名稱：...\n"
        "（列出 3-4 個今日最活躍或有明顯輪動跡象的族群）\n\n"
        "【法人動向】\n"
        "• 外資：買超哪些個股/族群（附代號與金額），賣超哪些（附代號與金額）\n"
        "• 投信：買超哪些個股（附代號），賣超哪些\n\n"
        "【資金趨勢】\n"
        "• 說明多日累計流向與單日的差異：哪些族群連續買超、哪些在加速或翻轉\n"
        "• 用「成分股買X/賣Y家」與「最大一檔佔Z%」判斷那是全族群買盤還是單一檔撐起來的，明講是哪一種\n"
        "（2-3 項；只根據上面提供的多日數據，沒有的不要編）\n\n"
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
        model="claude-opus-5",
        # Opus 5 預設開 adaptive thinking，max_tokens 是 thinking + 正文的共用上限。
        # 沿用舊的 1500 會讓摘要靜默截斷在區塊中間（不報錯，殘缺 HTML 直接上線）。
        # 不改成 thinking disabled：Opus 5 關思考時有 <thinking> 標籤洩漏進可見輸出的已知問題。
        max_tokens=5000,
        output_config={"effort": "medium"},
        messages=[{"role": "user", "content": prompt}],
    )
    # 安全分類器擋下時是 HTTP 200 + content: []，不是 exception → 沒 guard 會 IndexError
    if msg.stop_reason == "refusal" or not msg.content:
        raise RuntimeError(f"AI summary refused by model (stop_reason={msg.stop_reason})")
    # content[0] 不保證是文字：Opus 5 的 adaptive thinking 會把 ThinkingBlock 排在最前面，
    # 舊寫法 msg.content[0].text 這時候是 AttributeError。thinking 何時出現不固定，
    # 所以這是「有時候整份摘要不見」的隨機故障（_safe 接住，報告照出但少一塊）。
    text = next((b.text for b in msg.content if getattr(b, "type", "") == "text"), None)
    if text is None:
        kinds = [getattr(b, "type", "?") for b in msg.content]
        raise RuntimeError(f"AI summary 回應裡沒有 text block（收到 {kinds}）")
    text = text.strip()
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
