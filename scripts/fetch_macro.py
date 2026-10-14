#!/usr/bin/env python3
"""
fetch_macro.py — US macro & economic-news data for the macro-desk site.

Sources (all keyless, verified 2026-10-14):
  1. TradingView economic calendar  -> actual / forecast / previous for US events
     https://economic-calendar.tradingview.com/events  (needs Origin/Referer header)
  2. FRED CSV (via curl — urllib frequently times out on fred.stlouisfed.org)
     https://fred.stlouisfed.org/graph/fredgraph.csv?id=<SERIES>

Outputs (into <repo>/data/):
  calendar.json   — US events: past 45d + next 45d, with actual/forecast/previous
  indicators.json — headline series with history (levels + YoY where meaningful)
  meta.json       — build time + source status

No API key required. No secrets. Runs read-only.
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

REPO = os.environ.get("MACRO_REPO", "/root/macro-desk")
DATA = os.path.join(REPO, "data")
# NOTE: do NOT send a browser User-Agent. fred.stlouisfed.org blackholes requests
# carrying a Chrome UA from this host (0 bytes, hangs to the timeout), while both
# FRED and the TradingView calendar answer curl's default UA instantly.

# ---------------------------------------------------------------- TradingView
TV_URL = ("https://economic-calendar.tradingview.com/events"
          "?from={f}&to={t}&countries=US")


def http_get(url, headers=None, timeout=60, retries=3):
    """curl with --http1.1: fred.stlouisfed.org drops HTTP/2 mid-stream (rc=92)."""
    last = None
    for attempt in range(retries):
        cmd = ["curl", "-sS", "--http1.1", "--max-time", str(timeout)]
        for k, v in (headers or {}).items():
            cmd += ["-H", f"{k}: {v}"]
        cmd.append(url)
        p = subprocess.run(cmd, capture_output=True, text=True)
        if p.returncode == 0 and p.stdout.strip():
            return p.stdout
        last = f"curl rc={p.returncode} {(p.stderr or '')[:150]}"
    raise RuntimeError(last or "curl failed")


def fetch_calendar(days_back=45, days_fwd=45):
    now = datetime.now(timezone.utc)
    f = (now - timedelta(days=days_back)).strftime("%Y-%m-%dT00:00:00.000Z")
    t = (now + timedelta(days=days_fwd)).strftime("%Y-%m-%dT00:00:00.000Z")
    raw = http_get(TV_URL.format(f=f, t=t),
                   headers={"Origin": "https://www.tradingview.com",
                            "Referer": "https://www.tradingview.com/"})
    d = json.loads(raw)
    evs = d.get("result", []) or []
    out = []
    for e in evs:
        if not e.get("date"):
            continue
        out.append({
            "id": e.get("id"),
            "title": e.get("title"),
            "country": e.get("country"),
            "indicator": e.get("indicator"),
            "category": e.get("category"),
            "period": e.get("period"),
            "importance": e.get("importance"),
            "unit": e.get("unit"),
            "scale": e.get("scale"),
            "actual": e.get("actual"),
            "forecast": e.get("forecast"),
            "previous": e.get("previous"),
            "date": e.get("date"),
            "source": e.get("source"),
        })
    out.sort(key=lambda x: x["date"])
    return out


# --------------------------------------------------------------------- FRED
FRED = [
    # (series, label_bn, label_en, mode, unit, scale)
    # NOTE on series choice — must match the headline number people quote:
    #   CPI / Core CPI  -> NSA series (CPIAUCNS / CPILFENS). The SA series
    #                      (CPIAUCSL/CPILFESL) gives 3.71% where the printed
    #                      headline is 3.40% — verified 2026-10-14.
    #   PPI             -> PPIFIS "Final Demand". PPIACO (All Commodities) is a
    #                      different, far more volatile index: 9.85% vs 5.41%.
    ("CPIAUCNS",   "CPI (YoY)",        "Consumer Price Index YoY (NSA)",  "yoy", "%", 1),
    ("CPILFENS",   "Core CPI (YoY)",   "Core CPI YoY (NSA)",              "yoy", "%", 1),
    ("PCEPI",      "PCE (YoY)",        "PCE Price Index YoY",             "yoy", "%", 1),
    ("PCEPILFE",   "Core PCE (YoY)",   "Core PCE YoY",                    "yoy", "%", 1),
    ("PPIFIS",     "PPI (YoY)",        "PPI Final Demand YoY",            "yoy", "%", 1),
    ("PAYEMS",     "NFP",              "Nonfarm Payrolls (MoM change)",   "diff", "K", 1),
    ("UNRATE",     "Unemployment",     "Unemployment Rate",               "level", "%", 1),
    ("ICSA",       "Jobless Claims",   "Initial Jobless Claims",          "level", "K", 0.001),
    ("A191RL1Q225SBEA", "GDP Growth",  "Real GDP QoQ annualized",         "level", "%", 1),
    ("FEDFUNDS",   "Fed Funds",        "Effective Federal Funds Rate",    "level", "%", 1),
    ("DGS10",      "US 10Y",           "10-Year Treasury Yield",          "level", "%", 1),
    ("DGS2",       "US 2Y",            "2-Year Treasury Yield",           "level", "%", 1),
    ("DFII10",     "Real Yield 10Y",   "10Y TIPS Real Yield",             "level", "%", 1),
    ("DTWEXBGS",   "Dollar Index",     "Broad USD Index (Fed)",           "level", "idx", 1),
]


def fred_series(series):
    csv = http_get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}", timeout=60)
    rows = []
    for line in csv.strip().splitlines()[1:]:
        parts = line.split(",")
        if len(parts) != 2:
            continue
        d, v = parts[0].strip(), parts[1].strip()
        if v in ("", ".", "NA"):
            continue
        try:
            rows.append((d, float(v)))
        except ValueError:
            continue
    return rows


def build_indicators():
    out, status = [], {}
    for series, bn, en, mode, unit, scale in FRED:
        try:
            rows = fred_series(series)
        except Exception as e:                                    # noqa: BLE001
            status[series] = f"ERROR {type(e).__name__}: {str(e)[:90]}"
            continue
        if not rows:
            status[series] = "empty"
            continue
        series_out = []
        by_month = {}
        for d, v in rows:
            try:
                y, m = int(d[:4]), int(d[5:7])
            except ValueError:
                continue
            by_month[(y, m)] = v
        for i, (d, v) in enumerate(rows):
            try:
                y, m = int(d[:4]), int(d[5:7])
            except ValueError:
                continue
            if mode == "yoy":
                # match by DATE, never by row index: series can have missing
                # months (e.g. CPIAUCNS has no 2025-10), so rows[i-12] is not
                # "12 months ago" and silently skews the YoY (3.69 vs 3.40).
                base = by_month.get((y - 1, m))
                if base:
                    series_out.append((d, round((v / base - 1) * 100, 2)))
            elif mode == "diff":
                if i >= 1:
                    series_out.append((d, round((v - rows[i - 1][1]) * scale, 1)))
            else:
                series_out.append((d, round(v * scale, 3)))
        if not series_out:
            status[series] = "no computable points"
            continue
        hist = series_out[-180:]
        out.append({
            "series": series, "label_bn": bn, "label_en": en,
            "mode": mode, "unit": unit,
            "latest": hist[-1][1], "latest_date": hist[-1][0],
            "prev": hist[-2][1] if len(hist) > 1 else None,
            "history": hist,
        })
        status[series] = "ok"
    return out, status


# --------------------------------------------------------------------- main
def main():
    os.makedirs(DATA, exist_ok=True)
    now = datetime.now(timezone.utc)
    meta = {"built_utc": now.strftime("%Y-%m-%d %H:%M UTC"), "sources": {}}

    try:
        cal = fetch_calendar()
        json.dump(cal, open(os.path.join(DATA, "calendar.json"), "w"), ensure_ascii=False)
        meta["sources"]["tradingview_calendar"] = f"ok ({len(cal)} events)"
    except Exception as e:                                        # noqa: BLE001
        meta["sources"]["tradingview_calendar"] = f"ERROR {type(e).__name__}: {str(e)[:90]}"
        print("calendar FAILED:", e, file=sys.stderr)

    try:
        ind, st = build_indicators()
        json.dump(ind, open(os.path.join(DATA, "indicators.json"), "w"), ensure_ascii=False)
        ok = sum(1 for v in st.values() if v == "ok")
        meta["sources"]["fred"] = f"ok ({ok}/{len(FRED)} series)"
        meta["fred_detail"] = st
    except Exception as e:                                        # noqa: BLE001
        meta["sources"]["fred"] = f"ERROR {type(e).__name__}: {str(e)[:90]}"
        print("indicators FAILED:", e, file=sys.stderr)

    json.dump(meta, open(os.path.join(DATA, "meta.json"), "w"), ensure_ascii=False, indent=1)
    print(json.dumps(meta, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
