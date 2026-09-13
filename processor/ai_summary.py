"""AI-generated 今日盤勢總覽 using Claude API."""
import logging
import os

logger = logging.getLogger(__name__)


_GROUPS = (("主流延續",), ("拉回續買",), ("追漲回補", "低接轉買"), ("買不漲",),
           ("漲多轉賣", "停損轉賣"), ("拉高調節", "反彈續賣"), ("棄守",))


def _fd_stock(r: dict) -> str:
    """一檔個股的四維一行：名稱(代號) 5日/20日淨額、漲跌、位階、成本、連續、外資投信。"""
    bits = [f"{r['name']}({r['code']}) {r['sector']} 5日{r['net5']:+.0f}億/20日{r['net20']:+.0f}億"]
    if r.get("net5_pct") is not None:
        bits.append(f"5日佔市值{r['net5_pct']:+.2f}%")
    if r.get("ret5") is not None and r.get("ret20") is not None:
        bits.append(f"漲跌5日{r['ret5']:+.1f}%/20日{r['ret20']:+.1f}%")
    if r.get("pos52") is not None:
        bits.append(f"距52週高{r['pos52']:+.0f}%")
    if r.get("vs_cost") is not None:
        bits.append(f"現價vs法人成本{r['vs_cost']:+.0f}%")
    if r.get("streak"):
        bits.append(f"連{'買' if r['streak'] > 0 else '賣'}{abs(r['streak'])}天")
    if r.get("accel_tag"):
        bits.append(r["accel_tag"])
    bits.append(f"外資{r.get('f5', 0):+.0f}/{r.get('f20', 0):+.0f} 投信{r.get('t5', 0):+.0f}/{r.get('t20', 0):+.0f}")
    return " ".join(bits)


def _four_dim_lines(fd: dict, short: int, long: int) -> list[str]:
    """個股層四維分析餵 prompt：型態分布 + 七組大票 + 連續進出 + 外資投信分歧。"""
    big = fd.get("big") or []
    if not big:
        return []
    lines = [
        f"（以下是近 {long} 個交易日的個股層「四維型態」分析：型態 = ({short}日流向, {long}日流向) × "
        f"({short}日漲跌, {long}日漲跌)。續進/續出 = 兩窗口同號，轉買/轉賣 = 異號；主軸看 {long} 日漲跌。"
        f"樣本 {fd['total']} 檔，法人淨額合計 {short}日{fd['total_net5']:+.0f}億/{long}日{fd['total_net20']:+.0f}億，"
        f"個股漲跌中位數 {short}日{fd['median_ret5']:+.2f}%/{long}日{fd['median_ret20']:+.2f}%。"
        f"「大票」= |{short}日| ≥ 5 億或 |{long}日| ≥ 15 億，共 {len(big)} 檔）"
    ]
    lines.append("【型態分布（檔數/5日合計億/20日合計億）】" + "；".join(
        f"{c['p']} {c['n']}檔 {c['f5']:+d}/{c['f20']:+d}" for c in fd["summary"]))
    for pats in _GROUPS:
        sel = sorted((r for r in big if r["pattern"] in pats), key=lambda r: -abs(r["net5"]))[:8]
        if sel:
            lines.append(f"【{'/'.join(pats)} 大票 Top{len(sel)}】" + "；".join(_fd_stock(r) for r in sel))
    streaks = sorted((r for r in big if abs(r.get("streak", 0)) >= 8), key=lambda r: -abs(r["streak"]))[:8]
    if streaks:
        lines.append("【連續進出 ≥8 天】" + "；".join(
            f"{r['name']}({r['code']}) 連{'買' if r['streak'] > 0 else '賣'}{abs(r['streak'])}天 {r['pattern']}"
            for r in streaks))
    # 對作要兩邊都有份量：小邊 ≥ 5 億且 ≥ 大邊的 20%，不然台積電「外資 +223 / 投信 -5」也會被算進來
    split = sorted((r for r in big if r.get("f5", 0) * r.get("t5", 0) < 0
                    and min(abs(r["f5"]), abs(r["t5"])) >= max(5, 0.2 * max(abs(r["f5"]), abs(r["t5"])))),
                   key=lambda r: -min(abs(r["f5"]), abs(r["t5"])))[:8]
    if split:
        lines.append("【外資投信 5 日對作】" + "；".join(
            f"{r['name']}({r['code']}) 外資{r['f5']:+.0f}億 投信{r['t5']:+.0f}億 {r['pattern']}"
            for r in split))
    return lines


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

    # 個股層四維分析（5/20 日窗口）— 帶時間維度的資料，上面每一段都只有當日。
    # degraded（快取缺天）時整段不進 prompt，規則跟 market_trend 一致：窗口沒湊滿時
    # 5/20 日累計會安靜偏小，錯的數字會污染摘要敘事。
    sf = sections.get("sector_flow", {})
    fd_lines = []
    if sf.get("ok") and not sf["data"].get("degraded"):
        d = sf["data"]
        fd_lines = _four_dim_lines(d.get("four_dim") or {}, d.get("short", 5), d.get("long", 20))
    lines.extend(fd_lines)

    if fd_lines:
        fmt = (
            "必須嚴格使用以下格式輸出，共五個區塊，每個區塊之間空一行：\n\n"
            "【大盤概況】\n"
            "一段話說明指數漲跌、成交量、市場廣度（漲跌家數、漲跌停家數）、法人合計。\n\n"
            "【主流與集中度】\n"
            "• 主流延續/拉回續買裡「錢＋價＋位階」三合一的是誰（附代號、5/20 日金額、距 52 週高），外資投信是否同買\n"
            "• 多頭型態的檔數與金額佔比，多頭是分散還是集中在少數幾檔\n\n"
            "【錢價背離】\n"
            "• 買不漲：法人續進但 20 日跌的，點名最大的 2-3 檔（附代號），現價 vs 法人成本\n"
            "• 漲多轉賣/拉高調節：錢在出但價還撐著的，點名 2-3 檔，說明是獲利了結還是出貨結構\n\n"
            "【賣壓主體】\n"
            "• 棄守組：哪些族群/個股（附代號與金額）、5 日是否仍在加速、法人成本相對現價 → 是認賠還是獲利了結\n"
            "• 連賣最久的個股\n\n"
            "【翻轉與分歧】\n"
            "• 低接轉買/追漲回補：20 日賣、5 日翻買的是誰（附代號），是族群級還是個股級\n"
            "• 外資投信對作最大的 2-3 檔，誰接誰倒\n\n"
            "注意：每個 • 項目約 40-70 字，只用上面提供的數據與型態詞，沒有的不要編；"
            "語氣客觀專業，不提供投資建議，不加任何額外說明文字。"
        )
    else:
        fmt = (
            "必須嚴格使用以下格式輸出，只有一個區塊：\n\n"
            "【大盤概況】\n"
            "一段話說明指數漲跌、成交量、市場廣度（漲跌家數、漲跌停家數）、法人合計。\n\n"
            "注意：語氣客觀專業，不提供投資建議，不加任何額外說明文字。"
        )
    lines.append("\n請根據以上數據，用繁體中文撰寫「今日盤勢總覽」。\n" + fmt)

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
