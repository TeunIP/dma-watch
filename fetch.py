#!/usr/bin/env python3
"""DMA Watch fetcher (runs on GitHub Actions).

Reads assets.json, downloads daily closes (Kraken for crypto, Yahoo Finance for
everything else), computes the 50- and 200-day simple moving averages from
completed daily closes only, and writes data/latest.json.

A daily close counts as completed for crypto once its UTC day has ended, and
for a stock once its exchange's regular session of that day has ended (plus a
20-minute margin for the closing auction), so a run after the US close also
includes that day's European, Asian and US closes.

data/latest.json is only rewritten when a close or an error changed, so
"generatedAt" is the time of the last change, not of the last check.
"""
import json, time, datetime as dt, urllib.request, urllib.error

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
TODAY = dt.datetime.now(dt.timezone.utc).date()  # bars dated today or later are incomplete


def get(url, tries=3):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
                return json.load(r)
        except Exception as e:  # retry with backoff
            err = e
            time.sleep(2 + 3 * i)
    raise err


def kraken(pair):
    since = int((dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=260)).timestamp())
    d = get(f"https://api.kraken.com/0/public/OHLC?pair={pair}&interval=1440&since={since}")
    if d.get("error"):
        raise RuntimeError(";".join(d["error"]))
    rows = next(v for k, v in d["result"].items() if k != "last")
    return [(dt.datetime.fromtimestamp(int(r[0]), dt.timezone.utc).date(), float(r[4])) for r in rows]


def yahoo(sym):
    for host in ("query1", "query2"):
        try:
            d = get(f"https://{host}.finance.yahoo.com/v8/finance/chart/{sym}?range=2y&interval=1d&includePrePost=false")
            res = d["chart"]["result"][0]
            break
        except Exception as e:
            err = e
    else:
        raise err
    meta = res["meta"]
    tz = dt.timezone(dt.timedelta(seconds=meta.get("gmtoffset", 0)))
    closes = res["indicators"]["quote"][0]["close"]
    rows = [(dt.datetime.fromtimestamp(t, tz).date(), c) for t, c in zip(res["timestamp"], closes) if c is not None]
    # Keep a bar only when its session is over: earlier exchange-local dates always,
    # today's bar only after today's regular session has ended (plus 20 minutes).
    now = time.time()
    local_today = dt.datetime.fromtimestamp(now, tz).date()
    reg = (meta.get("currentTradingPeriod") or {}).get("regular") or {}
    end = reg.get("end")
    closed_today = bool(end) and dt.datetime.fromtimestamp(end, tz).date() == local_today and now >= end + 1200
    rows = [(d, c) for d, c in rows if d < local_today or (d == local_today and closed_today)]
    return rows, meta.get("currency")


def summarise(rows, crypto=False):
    rows = sorted({d: c for d, c in rows if not crypto or d < TODAY}.items())
    if not rows:
        raise RuntimeError("no completed daily closes")
    cl = [c for _, c in rows]
    return dict(price=cl[-1], dataDate=rows[-1][0].isoformat(), rows=len(cl),
                ma50=sum(cl[-50:]) / 50 if len(cl) >= 50 else None,
                ma200=sum(cl[-200:]) / 200 if len(cl) >= 200 else None,
                last5=[[d.isoformat(), c] for d, c in rows[-5:]])


def main():
    # The previous output is the fallback: an asset never goes back to an older close
    # (Yahoo sometimes leaves out the latest European bar in the hours after midnight),
    # and an asset that fails this run keeps its last good values (listed in "errors").
    try:
        old = json.load(open("data/latest.json"))
    except Exception:
        old = {}
    prev = old.get("assets", {})
    assets = json.load(open("assets.json"))
    out, errors, kept = {}, {}, []
    for a in assets:
        k = a["key"]
        try:
            if "kraken" in a:
                r = summarise(kraken(a["kraken"]), crypto=True); r["source"] = "kraken:" + a["kraken"]
            else:
                rows, cur = yahoo(a["yahoo"]); r = summarise(rows)
                r["source"] = "yahoo:" + a["yahoo"]; r["currency"] = cur
            p = prev.get(k)
            if p and p.get("source") == r["source"] and p.get("dataDate", "") > r["dataDate"]:
                r = p; kept.append(k)
            out[k] = r
        except Exception as e:
            errors[k] = f"{type(e).__name__}: {e}"[:200]
            if k in prev:
                out[k] = prev[k]
        time.sleep(0.4)  # be polite
    new = dict(count=len(out), assets=out, errors=errors, kept=kept)
    if old and all(old.get(x) == v for x, v in new.items()):
        print(f"unchanged: {len(out)} assets, errors {sorted(errors)}, kept {kept}")
        return  # nothing new, so no commit
    doc = dict(generatedAt=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
               runDate=TODAY.isoformat(), **new)
    import os
    os.makedirs("data", exist_ok=True)
    json.dump(doc, open("data/latest.json", "w"), indent=1)
    print(f"ok {len(out)}, errors {len(errors)}: {sorted(errors)}, kept {kept}")


if __name__ == "__main__":
    main()
