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
    out, seen, cur = [], set(), start
    while cur < end:
        nxt = min(cur + timedelta(days=CHUNK_DAYS), end)
        url = TV_URL.format(f=cur.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                            t=nxt.strftime("%Y-%m-%dT%H:%M:%S.000Z"), c=country)
        try:
            d = json.loads(http_get(url, headers=CAL_HEADERS, timeout=45))
        except Exception as e:                                     # noqa: BLE001
            print(f"  {country} {cur:%Y-%m}: {type(e).__name__}", file=sys.stderr)
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
    ("GOLD - COMMODITY EXCHANGE INC.", "Gold", "GC"),
    ("SILVER - COMMODITY EXCHANGE INC.", "Silver", "SI"),
    # "CRUDE OIL, LIGHT SWEET - NYMEX" is a dead series (ends 2022-02); the live
    # WTI contract in this dataset is the financially-settled one.
    ("WTI FINANCIAL CRUDE OIL - NEW YORK MERCANTILE EXCHANGE", "WTI Crude", "CL"),
    ("E-MINI S&P 500 - CHICAGO MERCANTILE EXCHANGE", "E-mini S&P 500", "ES"),
]


def fetch_cot():
    out = []
    for market, label, code in COT_MARKETS:
        # quote(safe="") is required: an unencoded "&" in a market name ("S&P 500")
        # splits the Socrata query into extra params and the request 400s.
        q = ("https://publicreporting.cftc.gov/resource/72hh-3qpy.json"
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
                lng = float(r.get("m_money_positions_long_all") or 0)
                sht = float(r.get("m_money_positions_short_all") or 0)
            except (TypeError, ValueError):
                continue
            hist.append({"date": (r.get("report_date_as_yyyy_mm_dd") or "")[:10],
                         "long": lng, "short": sht, "net": lng - sht,
                         "oi": r.get("open_interest_all")})
        hist.reverse()
        if hist:
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

    for name, fn, fname in [("fred", build_series, "series.json"),
                            ("cot", fetch_cot, "cot.json"),
                            ("options", fetch_options, "options.json"),
                            ("walls", fetch_walls, "walls.json")]:
        try:
            got = fn()
            if name == "fred":
                got, st = got
                meta["sources"]["fred"] = f"ok ({sum(1 for v in st.values() if v == 'ok')}/{len(FRED_SERIES)})"
                meta["fred_detail"] = st
            else:
                meta["sources"][name] = f"ok ({len(got)})"
            json.dump(got, open(os.path.join(DATA, fname), "w"),
                      ensure_ascii=False, separators=(",", ":"))
        except Exception as e:                                     # noqa: BLE001
            meta["sources"][name] = f"ERROR {type(e).__name__}: {str(e)[:80]}"
            print(f"{name} FAILED: {e}", file=sys.stderr)

    json.dump(meta, open(os.path.join(DATA, "meta.json"), "w"), ensure_ascii=False, indent=1)
    for f in sorted(os.listdir(DATA)):
        p = os.path.join(DATA, f)
        print(f"  {f:<20} {os.path.getsize(p) / 1024:>9.1f} KB")
    print(json.dumps(meta["sources"], ensure_ascii=False))


if __name__ == "__main__":
    main()
