#!/usr/bin/env python3
"""
fetch_macro.py — data layer for the macro desk site.

Sources (all keyless, verified 2026-10-14):
  1. TradingView economic calendar — per-release actual/forecast/previous, the
     same shape the EdgeFinder "Economic Data" charts show.
  2. FRED CSV — long-run continuous levels (rates, yields, dollar, housing).
  3. CFTC public reporting — COT positioning.
  4. CBOE daily market statistics — put/call ratios, call/put volume, open interest.
  5. FastBull — pending (resting) buy/sell orders.

Outputs into <repo>/data/. No API key, no secrets, read-only.
"""
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

REPO = os.environ.get("MACRO_REPO", "/root/macro-desk")
DATA = os.path.join(REPO, "data")

# NOTE: never send a browser User-Agent — fred.stlouisfed.org blackholes requests
# carrying a Chrome UA from this host (0 bytes, hangs to timeout).

# ------------------------------------------------------------------ countries
COUNTRIES = {
    "US": "United States", "EU": "Euro Area", "GB": "United Kingdom",
    "JP": "Japan", "CN": "China", "DE": "Germany", "CA": "Canada",
    "AU": "Australia", "CH": "Switzerland", "NZ": "New Zealand",
}
CAL_HISTORY_DAYS = 3 * 365      # ~3 years of releases for the bar charts
CAL_FUTURE_DAYS = 120
CHUNK_DAYS = 120                # TradingView is happy with ~4-month windows

TV_URL = ("https://economic-calendar.tradingview.com/events"
          "?from={f}&to={t}&countries={c}")
CAL_HEADERS = {"Origin": "https://www.tradingview.com",
               "Referer": "https://www.tradingview.com/"}


def http_get(url, headers=None, timeout=60, retries=3):
    """curl --http1.1: FRED cuts HTTP/2 mid-stream (curl rc=92)."""
    last = None
    for _ in range(retries):
        cmd = ["curl", "-sSL", "--http1.1", "--max-time", str(timeout)]
        for k, v in (headers or {}).items():
            cmd += ["-H", f"{k}: {v}"]
        cmd.append(url)
        p = subprocess.run(cmd, capture_output=True, text=True)
        if p.returncode == 0 and p.stdout.strip():
            return p.stdout
        last = f"curl rc={p.returncode} {(p.stderr or '')[:120]}"
    raise RuntimeError(last or "curl failed")


# ------------------------------------------------------------------- calendar
def fetch_calendar(country):
    now = datetime.now(timezone.utc)
    start, end = now - timedelta(days=CAL_HISTORY_DAYS), now + timedelta(days=CAL_FUTURE_DAYS)
    out, seen, cur, last = [], set(), start, None
    while cur < end:
        nxt = min(cur + timedelta(days=CHUNK_DAYS), end)
        url = TV_URL.format(f=cur.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                            t=nxt.strftime("%Y-%m-%dT%H:%M:%S.000Z"), c=country)
        raw = None
        for _ in range(3):                      # a few windows fail transiently
            try:
                raw = http_get(url, headers=CAL_HEADERS, timeout=45)
                d = json.loads(raw)
                break
            except Exception as e:                                 # noqa: BLE001
                d = None
                last = e
        if d is None:
            print(f"  {country} {cur:%Y-%m}: {type(last).__name__}", file=sys.stderr)
            cur = nxt
            continue
        for e in (d.get("result") or []):
            key = (e.get("id"), e.get("date"), e.get("title"))
            if key in seen or not e.get("date"):
                continue
            # drop noise: low-importance events that carry no numbers at all
            if not (e.get("actual") or e.get("forecast")) and (e.get("importance") or 0) < 0:
                continue
            seen.add(key)
            out.append({
                "title": e.get("title"), "country": e.get("country"),
                "indicator": e.get("indicator"), "period": e.get("period"),
                "importance": e.get("importance"), "unit": e.get("unit"),
                "actual": e.get("actual"), "forecast": e.get("forecast"),
                "previous": e.get("previous"), "date": e.get("date"),
            })
        cur = nxt
    out.sort(key=lambda x: x["date"])
    return out


# ----------------------------------------------------------------------- FRED
# Long-run continuous series (the "line chart" pages).  (series, label, unit, scale)
FRED_SERIES = [
    ("FEDFUNDS",   "Fed Funds Rate",             "%",   1),
    ("DGS3MO",     "3-Month Treasury",           "%",   1),
    ("DGS2",       "2-Year Treasury",            "%",   1),
    ("DGS10",      "10-Year Treasury",           "%",   1),
    ("DFII10",     "10Y Real Yield (TIPS)",      "%",   1),
    ("T10YIE",     "10Y Breakeven Inflation",    "%",   1),
    ("DTWEXBGS",   "Broad US Dollar Index",      "idx", 1),
    ("UNRATE",     "Unemployment Rate",          "%",   1),
    ("ICSA",       "Initial Jobless Claims",     "K",   0.001),
    ("UMCSENT",    "Consumer Confidence (Mich)", "idx", 1),
    ("TOTALSA",    "Total Vehicle Sales",        "M",   1),
    ("HOUST",      "Housing Starts",             "K",   1),
    ("CSUSHPINSA", "Case-Shiller Home Price",    "idx", 1),
    ("INDPRO",     "Industrial Production",      "idx", 1),
]


def fred_series(series):
    csv = http_get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}", timeout=45)
    rows = []
    for line in csv.strip().splitlines()[1:]:
        a = line.split(",")
        if len(a) != 2 or a[1].strip() in ("", ".", "NA"):
            continue
        try:
            rows.append((a[0].strip(), float(a[1])))
        except ValueError:
            continue
    return rows


def build_series():
    out, status = [], {}
    for series, label, unit, scale in FRED_SERIES:
        try:
            rows = fred_series(series)
        except Exception as e:                                     # noqa: BLE001
            status[series] = f"ERROR {type(e).__name__}: {str(e)[:80]}"
            continue
        hist = [(d, round(v * scale, 3)) for d, v in rows][-260:]
        if not hist:
            status[series] = "empty"
            continue
        out.append({"series": series, "label": label, "unit": unit,
                    "latest": hist[-1][1], "latest_date": hist[-1][0],
                    "prev": hist[-2][1] if len(hist) > 1 else None,
                    "history": hist})
        status[series] = "ok"
    return out, status


# ------------------------------------------------------------------------ COT
COT_MARKETS = [
    # (resource, market name, label, code, long_field, short_field)
    # The disaggregated "futures only" report (72hh-3qpy) carries m_money_* for
    # commodities; index futures live in the financial-futures report (gpe5-46if)
    # and use lev_money_* (leveraged funds) / asset_mgr_* instead.
    ("72hh-3qpy", "GOLD - COMMODITY EXCHANGE INC.", "Gold", "GC",
     "m_money_positions_long_all", "m_money_positions_short_all"),
    ("72hh-3qpy", "SILVER - COMMODITY EXCHANGE INC.", "Silver", "SI",
     "m_money_positions_long_all", "m_money_positions_short_all"),
    # NOTE: "CRUDE OIL, LIGHT SWEET - NYMEX" is a DEAD series (ends 2022-02) and
    # "WTI FINANCIAL CRUDE OIL" reports all-zero managed money.  WTI-PHYSICAL is
    # the live crude series with real positioning.
    ("72hh-3qpy", "WTI-PHYSICAL - NEW YORK MERCANTILE EXCHANGE", "WTI Crude", "CL",
     "m_money_positions_long_all", "m_money_positions_short_all"),
    ("gpe5-46if", "E-MINI S&P 500 - CHICAGO MERCANTILE EXCHANGE", "E-mini S&P 500", "ES",
     "lev_money_positions_long", "lev_money_positions_short"),
]


def fetch_cot():
    out = []
    for res, market, label, code, lf, sf in COT_MARKETS:
        # quote(safe="") is required: an unencoded "&" in a market name ("S&P 500")
        # splits the Socrata query into extra params and the request 400s.
        q = (f"https://publicreporting.cftc.gov/resource/{res}.json"
             "?$where=market_and_exchange_names='{m}'"
             "&$order=report_date_as_yyyy_mm_dd%20DESC&$limit=60").format(
                 m=quote(market, safe=""))
        try:
            body = http_get(q, timeout=45)
            rows = json.loads(body)
            if not isinstance(rows, list):
                raise RuntimeError(f"unexpected COT payload: {body[:120]}")
        except Exception as e:                                     # noqa: BLE001
            print(f"  COT {code}: {type(e).__name__} {str(e)[:120]}", file=sys.stderr)
            continue
        hist = []
        for r in rows:
            try:
                lng = float(r.get(lf) or 0)
                sht = float(r.get(sf) or 0)
            except (TypeError, ValueError):
                continue
            hist.append({"date": (r.get("report_date_as_yyyy_mm_dd") or "")[:10],
                         "long": lng, "short": sht, "net": lng - sht,
                         "oi": r.get("open_interest_all")})
        hist.reverse()
        # guard: a series that reports all-zero (or one flat value) positioning is
        # the wrong contract name — drop it rather than draw a meaningless chart.
        nets = {h["net"] for h in hist}
        if not hist or (len(nets) <= 1 and not any(nets)):
            print(f"  COT {code}: dropped (no usable positioning)", file=sys.stderr)
            continue
        out.append({"code": code, "label": label, "market": market, "history": hist})
    return out


# -------------------------------------------------------------------- options
def fetch_options():
    """CBOE daily market statistics.

    The page is a Next.js app: the numbers ride in the RSC flight stream as
    `self.__next_f.push([1,"..."])` chunks, split mid-object across chunks — so
    the chunks must be concatenated BEFORE parsing.  Single-level escaping only.
    """
    h = http_get("https://www.cboe.com/us/options/market_statistics/daily/", timeout=60)
    chunks = re.findall(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)', h, re.S)
    if not chunks:
        raise RuntimeError("CBOE flight chunks not found")
    text = "".join(json.loads(c) for c in chunks)
    i = text.find('{"data":{"optionsData"')
    if i < 0:
        raise RuntimeError("CBOE optionsData not found")
    depth, stop = 0, None
    for n, ch in enumerate(text[i:], start=i):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                stop = n + 1
                break
    d = json.loads(text[i:stop])["data"]["optionsData"]

    def pick(name):
        for r in d.get("ratios", []):
            if r["name"].startswith(name):
                return r["value"]
        return None

    def blk(name):
        vol = oi = None
        for r in d.get(name, []):
            if r["name"] == "VOLUME":
                vol = r
            elif r["name"] == "OPEN INTEREST":
                oi = r
        return {"volume": vol, "open_interest": oi}

    products = [{"name": k, **blk(k)} for k in d
                if k != "ratios" and isinstance(d[k], list) and blk(k)["volume"]]
    selected = re.search(r'"selectedDate":"([^"]+)"', text)
    return {
        "total_pc": pick("TOTAL PUT/CALL"), "index_pc": pick("INDEX PUT/CALL"),
        "equity_pc": pick("EQUITY PUT/CALL"), "etp_pc": pick("EXCHANGE TRADED PRODUCTS"),
        "vix_pc": pick("CBOE VOLATILITY"), "ratios": d.get("ratios", []),
        "products": products, "as_of": selected.group(1) if selected else None,
    }


# ------------------------------------------------------------- resting orders
WALL_PAIRS = [(28, "Gold", "XAUUSD"), (29, "Silver", "XAGUSD")]


def fetch_walls():
    """FastBull resting orders.  The payload is parallel arrays: `price` holds the
    level, `buyOrder`/`sellOrder` the size resting at that level.  A "wall" is the
    level carrying the biggest resting size."""
    out = []
    for pair_id, label, sym in WALL_PAIRS:
        u = (f"https://api.fastbull.com/fastbull-macro-data-service/api/"
             f"getWebPendingOrder?pairId={pair_id}")
        try:
            j = json.loads(http_get(u, timeout=45))
            body = json.loads(j.get("bodyMessage") or "[]")
        except Exception as e:                                     # noqa: BLE001
            print(f"  walls {sym}: {type(e).__name__}", file=sys.stderr)
            continue
        if not body:
            continue
        b = body[0]
        try:
            cur = float(b.get("currentPrice") or 0)
        except (TypeError, ValueError):
            cur = 0.0
        prices = b.get("price") or []
        buys = b.get("buyOrder") or []
        sells = b.get("sellOrder") or []
        levels = []
        for i, p in enumerate(prices):
            try:
                pf = float(p)
            except (TypeError, ValueError):
                continue
            # keep the book readable and the payload small: +/-6% around spot
            if cur and abs(pf / cur - 1) > 0.06:
                continue
            levels.append({"price": pf,
                           "buy": float(buys[i]) if i < len(buys) else 0.0,
                           "sell": float(sells[i]) if i < len(sells) else 0.0})
        if not levels:
            continue
        out.append({
            "symbol": sym, "label": label, "price": cur or None,
            "levels": levels,
            "buy_walls": sorted(levels, key=lambda x: -x["buy"])[:6],
            "sell_walls": sorted(levels, key=lambda x: -x["sell"])[:6],
            "as_of": b.get("createTime"),
        })
    return out


# ------------------------------------------------------------------- markets
SCAN_URL = "https://scanner.tradingview.com/global/scan"
SCAN_HEADERS = {"Origin": "https://www.tradingview.com",
                "Referer": "https://www.tradingview.com/",
                "Content-Type": "application/json"}
# (ticker, Bangla label, asset class) — first ticker that answers wins.
MARKET_TICKERS = [
    ("OANDA:XAUUSD", "Gold", "Metals"), ("OANDA:XAGUSD", "Silver", "Metals"),
    ("TVC:SILVER", "Silver", "Metals"), ("TVC:PLATINUM", "Platinum", "Metals"),
    ("COMEX:HG1!", "Copper", "Metals"), ("CAPITALCOM:COPPER", "Copper", "Metals"),
    ("NYMEX:CL1!", "WTI Crude", "Energy"), ("NYMEX:BZ1!", "Brent", "Energy"),
    ("ICEEUR:BRN1!", "Brent", "Energy"), ("NYMEX:NG1!", "Natural Gas", "Energy"),
    ("TVC:DXY", "US Dollar Index", "FX"),
    ("FX_IDC:EURUSD", "EUR/USD", "FX"), ("FX_IDC:USDJPY", "USD/JPY", "FX"),
    ("FX_IDC:GBPUSD", "GBP/USD", "FX"), ("FX_IDC:AUDUSD", "AUD/USD", "FX"),
    ("FX_IDC:USDCAD", "USD/CAD", "FX"), ("FX_IDC:USDCHF", "USD/CHF", "FX"),
    ("FX_IDC:NZDUSD", "NZD/USD", "FX"),
    ("SP:SPX", "S&P 500", "Indices"), ("NASDAQ:NDX", "Nasdaq 100", "Indices"),
    ("DJ:DJI", "Dow 30", "Indices"), ("TVC:NI225", "Nikkei 225", "Indices"),
    ("TVC:DEU40", "DAX 40", "Indices"), ("TVC:UKX", "FTSE 100", "Indices"),
    ("TVC:HSI", "Hang Seng", "Indices"),
    ("BINANCE:BTCUSDT", "Bitcoin", "Crypto"), ("BINANCE:ETHUSDT", "Ethereum", "Crypto"),
    ("TVC:US10Y", "US 10Y Yield", "Rates"), ("TVC:US02Y", "US 2Y Yield", "Rates"),
]
SCAN_COLS = ["name", "close", "change", "Volatility.D", "ATR", "Recommend.All",
             "Perf.W", "Perf.1M", "Perf.3M", "Perf.YTD"]


def fetch_market():
    """TradingView scanner — one POST returns close/change/vol/rating/performance
    for every ticker.  This is the market-heatmap + momentum + volatility source."""
    body = json.dumps({"symbols": {"tickers": [t for t, _, _ in MARKET_TICKERS],
                                   "query": {"types": []}},
                       "columns": SCAN_COLS})
    tmp = os.path.join(DATA, ".scan.json")
    open(tmp, "w").write(body)
    p = subprocess.run(["curl", "-sSL", "--http1.1", "--max-time", "45", "-X", "POST",
                        "-H", "Origin: https://www.tradingview.com",
                        "-H", "Referer: https://www.tradingview.com/",
                        "-H", "Content-Type: application/json",
                        "--data-binary", "@" + tmp, SCAN_URL],
                       capture_output=True, text=True)
    os.remove(tmp)
    if p.returncode != 0 or not p.stdout.strip():
        raise RuntimeError(f"scan curl rc={p.returncode} {(p.stderr or '')[:100]}")
    d = json.loads(p.stdout)
    got = {r["s"]: r["d"] for r in (d.get("data") or [])}
    seen, out = set(), []
    for ticker, label, klass in MARKET_TICKERS:
        if ticker not in got or label in seen:
            continue
        seen.add(label)
        v = got[ticker]
        out.append({"ticker": ticker, "label": label, "class": klass,
                    "close": v[1], "change": v[2], "vol": v[3], "atr": v[4],
                    "rating": v[5], "w": v[6], "m1": v[7], "m3": v[8], "ytd": v[9]})
    return out


# ---------------------------------------------------------------- seasonality
# Monthly history from FRED (long, free, keyless).  Gold/silver have no free
# multi-year daily series, so seasonality covers FX, energy, equities, crypto.
SEASON_SERIES = [
    ("DEXUSEU", "EUR/USD", False), ("DEXJPUS", "USD/JPY", True),
    ("DEXUSUK", "GBP/USD", False), ("DEXUSAL", "AUD/USD", False),
    ("DCOILWTICO", "WTI Crude", False), ("DCOILBRENTEU", "Brent", False),
    ("SP500", "S&P 500", False),
]


def fetch_season():
    out = []
    for series, label, invert in SEASON_SERIES:
        try:
            rows = fred_series(series)
        except Exception as e:                                         # noqa: BLE001
            print(f"  season {label}: {type(e).__name__}", file=sys.stderr)
            continue
        if invert:
            rows = [(d, 1.0 / v) for d, v in rows if v]
        # month-end close per (year, month)
        mm = {}
        for d, v in rows:
            mm[(int(d[:4]), int(d[5:7]))] = v
        keys = sorted(mm)
        buckets = {m: [] for m in range(1, 13)}
        for i in range(1, len(keys)):
            (y0, m0), (y1, m1) = keys[i - 1], keys[i]
            if (y1 - y0) * 12 + (m1 - m0) != 1:      # skip gaps
                continue
            a, b = mm[keys[i - 1]], mm[keys[i]]
            if a:
                buckets[m1].append((b / a - 1) * 100)
        stats = []
        for m in range(1, 13):
            v = buckets[m]
            if not v:
                continue
            v_s = sorted(v)
            stats.append({"month": m, "avg": round(sum(v) / len(v), 2),
                          "median": round(v_s[len(v_s) // 2], 2),
                          "win": round(sum(1 for x in v if x > 0) / len(v) * 100, 0),
                          "n": len(v)})
        if stats:
            out.append({"label": label, "series": series, "stats": stats})
    return out


# ----------------------------------------------------------------- score history
def build_score_history(cot):
    """Weekly EdgeFinder-style score history for gold: real-yield momentum +
    dollar momentum + COT crowding, each -5..+5, summed -15..+15."""
    try:
        real = dict(fred_series("DFII10"))
        dollar = dict(fred_series("DTWEXBGS"))
    except Exception as e:                                             # noqa: BLE001
        print(f"  score: {type(e).__name__}", file=sys.stderr)
        return []
    gold = next((c for c in cot if c["code"] == "GC"), None)
    if not gold:
        return []
    rk = sorted(real)
    dk = sorted(dollar)

    def ch_at(keys, mp, day, n):
        past = [k for k in keys if k <= day]
        if len(past) <= n:
            return None
        return mp[past[-1]] - mp[past[-1 - n]]

    nets = [h["net"] for h in gold["history"]]
    out = []
    for i, h in enumerate(gold["history"]):
        day = h["date"]
        cr = ch_at(rk, real, day, 5)
        cd = ch_at(dk, dollar, day, 5)
        win = nets[max(0, i - 51):i + 1]
        if cr is None or cd is None or len(win) < 10:
            continue
        mn, mx = min(win), max(win)
        crowd = 0.0 if mx == mn else ((h["net"] - mn) / (mx - mn) - 0.5) * 10
        c1 = max(-5, min(5, -cr * 1.4))
        c2 = max(-5, min(5, -cd * 1.1))
        c3 = max(-5, min(5, -crowd / 2))
        out.append({"date": day, "real": round(c1, 2), "dollar": round(c2, 2),
                    "cot": round(c3, 2), "score": round(c1 + c2 + c3, 2)})
    return out


# ------------------------------------------------------------------------ main
def main():
    os.makedirs(DATA, exist_ok=True)
    meta = {"built_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "sources": {}, "countries": COUNTRIES}

    counts = {}
    for code in COUNTRIES:
        try:
            evs = fetch_calendar(code)
            json.dump(evs, open(os.path.join(DATA, f"cal_{code.lower()}.json"), "w"),
                      ensure_ascii=False, separators=(",", ":"))
            counts[code] = len(evs)
        except Exception as e:                                     # noqa: BLE001
            counts[code] = f"ERROR {type(e).__name__}"
            print(f"calendar {code} FAILED: {e}", file=sys.stderr)
    meta["sources"]["tradingview_calendar"] = counts

    cot = []
    for name, fn, fname in [("fred", build_series, "series.json"),
                            ("cot", fetch_cot, "cot.json"),
                            ("options", fetch_options, "options.json"),
                            ("walls", fetch_walls, "walls.json"),
                            ("market", fetch_market, "market.json"),
                            ("season", fetch_season, "season.json")]:
        try:
            got = fn()
            if name == "fred":
                got, st = got
                meta["sources"]["fred"] = f"ok ({sum(1 for v in st.values() if v == 'ok')}/{len(FRED_SERIES)})"
                meta["fred_detail"] = st
            else:
                meta["sources"][name] = f"ok ({len(got)})"
            if name == "cot":
                cot = got
            json.dump(got, open(os.path.join(DATA, fname), "w"),
                      ensure_ascii=False, separators=(",", ":"))
        except Exception as e:                                     # noqa: BLE001
            meta["sources"][name] = f"ERROR {type(e).__name__}: {str(e)[:80]}"
            print(f"{name} FAILED: {e}", file=sys.stderr)

    # score history depends on the COT fetch above
    try:
        sh = build_score_history(cot)
        json.dump(sh, open(os.path.join(DATA, "score_history.json"), "w"),
                  ensure_ascii=False, separators=(",", ":"))
        meta["sources"]["score_history"] = f"ok ({len(sh)})"
    except Exception as e:                                         # noqa: BLE001
        meta["sources"]["score_history"] = f"ERROR {type(e).__name__}: {str(e)[:80]}"
        print(f"score_history FAILED: {e}", file=sys.stderr)

    json.dump(meta, open(os.path.join(DATA, "meta.json"), "w"), ensure_ascii=False, indent=1)
    for f in sorted(os.listdir(DATA)):
        p = os.path.join(DATA, f)
        print(f"  {f:<20} {os.path.getsize(p) / 1024:>9.1f} KB")
    print(json.dumps(meta["sources"], ensure_ascii=False))


if __name__ == "__main__":
    main()
