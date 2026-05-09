"""AI-generated sector rotation & market analysis using TWSE sector indices."""
import logging
import os
import re as _re

logger = logging.getLogger(__name__)

# Number of top/bottom sectors to include in the prompt
TOP_N = 8


def _build_prompt(sectors: list[dict], taiex_data: dict, date_str: str) -> str:
    gainers = sectors[:TOP_N]
    losers  = list(reversed(sectors[-TOP_N:]))  # worst first

    taiex_close  = taiex_data.get("close", 0)
    taiex_pct    = taiex_data.get("change_pct", 0)
    taiex_pts    = taiex_data.get("change_pts", 0)
    taiex_dir    = "上漲" if taiex_pct >= 0 else "下跌"
    trading_amt  = taiex_data.get("trading_amount_yi", 0)

    lines = [
        f"台灣股市 {date_str} 收盤資料：",
        f"加權指數 {taiex_close:,.2f} 點，{taiex_dir} {abs(taiex_pts):.2f} 點（{taiex_pct:+.2f}%），成交金額 {trading_amt:,.0f} 億元",
        "",
        f"=== 今日上市類股漲幅前 {TOP_N} ===",
    ]
    for i, s in enumerate(gainers, 1):
        lines.append(f"{i}. {s['name_short']}  {s['change_pct']:+.2f}%")

    lines.append("")
    lines.append(f"=== 今日上市類股跌幅前 {TOP_N} ===")
    for i, s in enumerate(losers, 1):
        lines.append(f"{i}. {s['name_short']}  {s['change_pct']:+.2f}%")

    lines.append("")
    lines.append("=== 所有類股完整排行 ===")
    for s in sectors:
        marker = "▲" if s["change_pct"] > 0 else ("▼" if s["change_pct"] < 0 else "─")
        lines.append(f"  {marker} {s['name_short']:14s}  {s['change_pct']:+.2f}%")

    lines.append("")
    lines.append(
        "請根據以上台灣股市類股漲跌幅排行資料，用繁體中文撰寫「類股與大盤分析」。\n"
        "必須嚴格使用以下格式，每個區塊空一行：\n\n"
        "【大盤概況】\n"
        "一段話：說明大盤指數強弱、市場今日主要走向。\n\n"
        "【強勢族群】\n"
        "• 族群名稱（漲幅%）：說明今日表現原因或特徵，點出可能的驅動因素（如政策、題材、資金輪動等）\n"
        "• （列出前3-4名，每項30-50字）\n\n"
        "【弱勢族群】\n"
        "• 族群名稱（跌幅%）：說明今日表現原因或特徵\n"
        "• （列出後3-4名）\n\n"
        "【族群輪動觀察】\n"
        "• 觀察今日哪些類型族群（防禦型/成長型/景氣循環型）相對抗跌或領漲\n"
        "• 是否有明顯的資金從強勢類股轉移至其他類股的跡象\n\n"
        "注意：語氣客觀專業，不提供投資建議，不加任何額外說明文字。"
    )
    return "\n".join(lines)


def build(sectors: list[dict], taiex_data: dict, date_str: str) -> dict:
    """Call Claude API and return structured sector analysis."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY environment variable not set")

    import anthropic

    prompt = _build_prompt(sectors, taiex_data, date_str)
    client = anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=900,
        messages=[{"role": "user", "content": prompt}],
    )
    text = msg.content[0].text.strip()
    # Strip any markdown heading
    text = _re.sub(r'^#+\s*.+?\n+', '', text).strip()

    logger.info(f"Sector analysis generated ({msg.usage.input_tokens} in / {msg.usage.output_tokens} out tokens)")
    return {
        "summary":       text,
        "model":         msg.model,
        "input_tokens":  msg.usage.input_tokens,
        "output_tokens": msg.usage.output_tokens,
        "sectors":       sectors,   # pass through for potential future use
    }
