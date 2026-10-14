#!/usr/bin/env python3
"""DMA Watch fetcher (runs on GitHub Actions).

Reads assets.json, downloads daily closes (Kraken for crypto, Yahoo Finance for
everything else), computes the 50- and 200-day simple moving averages from
completed daily closes only, and writes data/latest.json.
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
    return rows, meta.get("currency")


def summarise(rows):
    rows = sorted({d: c for d, c in rows if d < TODAY}.items())
    if not rows:
        raise RuntimeError("no completed daily closes")
    cl = [c for _, c in rows]
    return dict(price=cl[-1], dataDate=rows[-1][0].isoformat(), rows=len(cl),
                ma50=sum(cl[-50:]) / 50 if len(cl) >= 50 else None,
                ma200=sum(cl[-200:]) / 200 if len(cl) >= 200 else None,
                last5=[[d.isoformat(), c] for d, c in rows[-5:]])


def main():
    assets = json.load(open("assets.json"))
    out, errors = {}, {}
    for a in assets:
        k = a["key"]
        try:
            if "kraken" in a:
                r = summarise(kraken(a["kraken"])); r["source"] = "kraken:" + a["kraken"]
            else:
                rows, cur = yahoo(a["yahoo"]); r = summarise(rows)
                r["source"] = "yahoo:" + a["yahoo"]; r["currency"] = cur
            out[k] = r
        except Exception as e:
            errors[k] = f"{type(e).__name__}: {e}"[:200]
        time.sleep(0.4)  # be polite
    doc = dict(generatedAt=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
               runDate=TODAY.isoformat(), count=len(out), assets=out, errors=errors)
    import os
    os.makedirs("data", exist_ok=True)
    json.dump(doc, open("data/latest.json", "w"), indent=1)
    print(f"ok {len(out)}, errors {len(errors)}: {sorted(errors)}")


if __name__ == "__main__":
    main()
