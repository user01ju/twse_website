"""Section 4 盤勢總覽的圖表資料：直接吃 sections，不經 AI 轉述。

選股/選族群規則跟 ai_summary prompt 餵的那幾組一致（同一批大票、同一個對作門檻），
所以 AI 寫的一句話結論跟圖講的是同一群標的。各塊資料不足時回 None，模板整塊不畫。
寬度一律回 0~100 的百分比、SVG 座標在這裡算好，模板只負責排版。
"""

_BULL = ("主流延續", "拉回續買")
_FLIP = ("追漲回補", "低接轉買")


def _w(v, vmax) -> float:
    return round(min(abs(v) / vmax * 100, 100), 1) if vmax else 0.0


def _ticks(lo: float, hi: float, n: int = 5) -> list[float]:
    """lo~hi 之間取「好看」的刻度（1/2/5 × 10^k）。"""
    raw = (hi - lo) / n
    mag = 10 ** len(str(int(raw))) / 10 if raw >= 1 else 1
    step = next(s * mag for s in (1, 2, 5, 10) if s * mag >= raw)
    t, out = (lo // step) * step, []
    while t <= hi:
        if t >= lo:
            out.append(t)
        t += step
    return out


def _market(sections: dict) -> dict | None:
    if not sections.get("taiex", {}).get("ok"):
        return None
    t = sections["taiex"]["data"]
    out = {"close": t["close"], "chg": t["change_pts"], "pct": t["change_pct"],
           "amt": t["trading_amount_yi"], "avg": t.get("avg20_amount_yi")}
    if out["avg"]:
        scale = max(out["amt"], out["avg"]) * 1.15
        out |= {"amt_w": _w(out["amt"], scale), "avg_w": _w(out["avg"], scale),
                "amt_vs": round((out["amt"] / out["avg"] - 1) * 100, 1)}

    b = sections.get("breadth", {})
    if b.get("ok"):
        bd = b["data"]
        out["breadth"] = [{"label": lbl, "up": m["up"], "down": m["down"], "flat": m["flat"],
                           "up_w": _w(m["up"], m["up"] + m["down"] + m["flat"]),
                           "flat_w": _w(m["flat"], m["up"] + m["down"] + m["flat"])}
                          for k, lbl in (("twse", "上市"), ("tpex", "上櫃")) if (m := bd.get(k))]
        out["limit_up"] = sum(bd.get(k, {}).get("limit_up", 0) for k in ("twse", "tpex"))
        out["limit_down"] = sum(bd.get(k, {}).get("limit_down", 0) for k in ("twse", "tpex"))

    mt = sections.get("market_trend", {})
    if mt.get("ok") and not mt["data"].get("degraded"):
        ma, nh = mt["data"].get("above_ma20", {}), mt["data"].get("new_high_low", {})
        if ma.get("total"):
            out["ma20"] = ma.get("pct", 0)
            hi, lo = nh.get("new_high", 0), nh.get("new_low", 0)
            out["nhl"] = {"hi": hi, "lo": lo, "net": hi - lo, "hi_w": _w(hi, hi + lo)}

    inst = sections.get("institutional", {})
    if inst.get("ok"):
        rows = inst["data"].get("twse", [])
        pick = lambda pre: sum(r["net_yi"] for r in rows if not r.get("is_total") and r["name"].startswith(pre))
        total = lambda k: next((r["net_yi"] for r in inst["data"].get(k, []) if r.get("is_total")), None)
        items = [("外資", pick("外資")), ("投信", pick("投信")), ("自營商", pick("自營商")),
                 ("上市合計", total("twse")), ("上櫃合計", total("tpex"))]
        items = [(n, v) for n, v in items if v is not None]
        vmax = max((abs(v) for _, v in items), default=0)
        out["inst"] = [{"name": n, "v": v, "w": _w(v, vmax)} for n, v in items]
    return out


def _sector_tag(r: dict) -> tuple[str, str]:
    up1, m20 = r["med1"] > 0, r.get("med20")
    if m20 is None:
        return ("強勢", "pos") if up1 else ("弱勢", "neg")
    if up1:
        return ("延續強勢", "pos") if m20 > 0 else ("剛轉強", "warn")
    return ("由強轉弱", "warn") if m20 > 0 else ("延續弱勢", "neg")


def _sectors(sections: dict) -> list[dict] | None:
    sb = sections.get("sector_breadth", {})
    d = sb.get("data") or {}
    rows = [r for r in d.get("rows") or [] if r["n"] >= 3]
    if not sb.get("ok") or d.get("degraded") or len(rows) < 8:
        return None
    sel = rows[:4] + rows[-4:]
    vmax = max(abs(r["med1"]) for r in sel) or 1
    out = []
    for r in sel:
        tag, tone = _sector_tag(r)
        movers = r["leaders"] if r["med1"] > 0 else r["laggards"]
        out.append({"sector": r["sector"], "n": r["n"], "med1": r["med1"], "med5": r.get("med5"),
                    "med20": r.get("med20"), "up": r["up"], "down": r["down"], "flat": r["flat"],
                    "w": _w(r["med1"], vmax), "tag": tag, "tone": tone,
                    "movers": movers[:2]})
    return out


def _scatter(pts: list[dict]) -> dict | None:
    """錢價背離散佈圖：x = 20 日漲跌%、y = 5 日淨額億、半徑 = |現價 vs 法人成本|。"""
    if len(pts) < 2:
        return None
    W, H, L, R, T, B = 640, 300, 48, 20, 20, 36
    xs, ys = [p["ret20"] for p in pts] + [0], [p["net5"] for p in pts] + [0]
    x0, x1 = min(xs) - 4, max(xs) + 6
    y0, y1 = min(ys) * 1.2 - 3, max(ys) * 1.2 + 3
    X = lambda v: round(L + (v - x0) / (x1 - x0) * (W - L - R), 1)
    Y = lambda v: round(T + (y1 - v) / (y1 - y0) * (H - T - B), 1)
    return {
        "w": W, "h": H, "l": L, "r": W - R, "t": T, "b": H - B, "x0": X(0), "y0": Y(0),
        "xt": [{"v": v, "x": X(v)} for v in _ticks(x0, x1)],
        "yt": [{"v": v, "y": Y(v)} for v in _ticks(y0, y1)],
        "pts": [dict(p, x=X(p["ret20"]), y=Y(p["net5"]),
                     rad=round(6 + min(abs(p["vs_cost"] or 0), 30) * 0.5, 1)) for p in pts],
    }


def _dumbbell(rows: list[dict]) -> dict | None:
    """20 日淨額 → 5 日淨額。"""
    if not rows:
        return None
    W, L, R, T, B, RH = 640, 64, 16, 8, 28, 28
    H = T + len(rows) * RH + B
    vals = [v for r in rows for v in (r["net5"], r["net20"])] + [0]
    x0, x1 = min(vals) * 1.1, max(vals) * 1.1
    X = lambda v: round(L + (v - x0) / (x1 - x0) * (W - L - R), 1)
    return {
        "w": W, "h": H, "t": T, "b": H - B, "l": L,
        "xt": [{"v": v, "x": X(v)} for v in _ticks(x0, x1)],
        "rows": [dict(r, xa=X(r["net20"]), xb=X(r["net5"]), y=T + i * RH + RH // 2)
                 for i, r in enumerate(rows)],
    }


def _stock(r: dict) -> dict:
    keys = ("code", "name", "net5", "net20", "ret20", "pos52", "vs_cost", "streak", "accel_tag", "f5", "t5", "pattern")
    return {k: r.get(k) for k in keys}


def _flows(sections: dict) -> dict | None:
    sf = sections.get("sector_flow", {})
    if not sf.get("ok") or sf["data"].get("degraded"):
        return None
    fd = sf["data"].get("four_dim") or {}
    big = fd.get("big") or []
    if not big:
        return None
    summ = {c["p"]: c for c in fd["summary"]}
    # ETF（00 開頭）只留在連賣天數：0050/0056 動輒上百億，放進個股圖會把比例尺壓扁
    stocks = [r for r in big if not r["code"].startswith("00")]
    by = lambda pats: [r for r in stocks if r["pattern"] in pats]

    # 主流 vs 棄守
    bull_n = sum(summ[p]["n"] for p in _BULL if p in summ)
    bull_f5 = sum(summ[p]["f5"] for p in _BULL if p in summ)
    bear = summ.get("棄守", {"n": 0, "f5": 0})
    vmax = max(abs(bull_f5), abs(bear["f5"])) or 1
    bull_big = sorted(by(_BULL), key=lambda r: -r["net5"])
    top8 = sum(r["net5"] for r in bull_big[:8])
    lead = [r for r in bull_big if r.get("pos52") is not None and r["pos52"] >= -3][:4]
    main = {
        "bull": {"n": bull_n, "f5": bull_f5, "w": _w(bull_f5, vmax)},
        "bear": {"n": bear["n"], "f5": bear["f5"], "w": _w(bear["f5"], vmax)},
        "top8_pct": round(top8 / bull_f5 * 100) if bull_f5 > 0 else None,
        "top1": _stock(bull_big[0]) if bull_big else None,
        "lead": [dict(_stock(r), who=("外資投信同買" if r["f5"] > 0 and r["t5"] > 0 else
                                      "外資買・投信賣" if r["f5"] > 0 else "投信買・外資賣"))
                 for r in lead],
    }

    # 錢價背離
    # 三組各自保留名額：漲多轉賣金額動輒是拉高調節的好幾倍，混排會把後者整組擠掉。
    # 拉高調節看「出貨結構」→ 現價高於法人成本越多越優先，不是看金額
    has_ret = lambda pats: [r for r in by(pats) if r.get("ret20") is not None]
    lag = sorted(has_ret(("買不漲",)), key=lambda r: -r["net5"])[:3]
    dump = sorted(has_ret(("漲多轉賣",)), key=lambda r: r["net5"])[:2]
    pull = sorted(has_ret(("拉高調節",)), key=lambda r: (r["vs_cost"] is None, -(r["vs_cost"] or 0)))[:2]
    div = _scatter([_stock(r) for r in lag + dump + pull])

    # 賣壓主體
    sells = sorted(by(("棄守",)), key=lambda r: r["net5"])[:6]
    smax = abs(sells[0]["net5"]) if sells else 1
    sell = [dict(_stock(r), w=_w(r["net5"], smax)) for r in sells]
    streaks = sorted((r for r in big if (r.get("streak") or 0) <= -8), key=lambda r: r["streak"])[:6]

    # 翻轉與分歧（對作門檻同 ai_summary._four_dim_lines）
    flip = _dumbbell([_stock(r) for r in sorted(by(_FLIP), key=lambda r: -r["net5"])[:7]])
    split = sorted((r for r in stocks if r.get("f5", 0) * r.get("t5", 0) < 0
                    and min(abs(r["f5"]), abs(r["t5"])) >= max(5, 0.2 * max(abs(r["f5"]), abs(r["t5"])))),
                   key=lambda r: -min(abs(r["f5"]), abs(r["t5"])))[:3]
    dmax = max((max(abs(r["f5"]), abs(r["t5"])) for r in split), default=1)
    duel = [dict(_stock(r), fw=_w(r["f5"], dmax), tw=_w(r["t5"], dmax)) for r in split]

    return {"main": main, "div": div, "sell": sell,
            "streaks": [_stock(r) for r in streaks], "flip": flip, "duel": duel}


def build(sections: dict) -> dict:
    return {"market": _market(sections), "sectors": _sectors(sections), "flows": _flows(sections)}
