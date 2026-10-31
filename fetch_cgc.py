#!/usr/bin/env python3
"""
Fetch CGC Grain Statistics Weekly (GSW) crop-year CSVs and build data/cgc.json
for Canola, Wheat (ex-durum), Amber Durum and All Wheat.

Runs server-side in GitHub Actions (canada.ca blocks browser CORS).
Stdlib only.

Metric definitions (all kt):
  deliveries  = Primary 'Deliveries' (sum of provinces) + Process 'Producer Deliveries'
  domestic    = Process 'Milled/Mfg Grain'  (canola = crush)
  exports     = Terminal Exports (all grades/ports) + Primary Shipment Distribution
                'Export Destinations' (direct exports from primaries)
  stocks      = Summary 'Stocks' (commercial: primary + process + terminals)
"""
import csv
import io
import json
import os
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timezone

BASE = "https://www.canada.ca/content/dam/cgc-ccg/documents/statistiques-statistics/grain-statistics-weekly"
NAME_PATTERNS = ["grain-stats-weekly-{cy}.csv", "grain-statistics-weekly-{cy}.csv"]
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"

N_HIST = int(os.environ.get("CGC_HIST_YEARS", "5"))   # prior crop years for LY / 5-yr avg
OUT = os.environ.get("CGC_OUT", os.path.join(os.path.dirname(__file__), "..", "data", "cgc.json"))
LOCAL_DIR = os.environ.get("CGC_LOCAL_DIR")             # optional: read CSVs from disk (testing)

GRAINS = {"Canola": "canola", "Wheat": "wheat", "Amber Durum": "durum"}
PORTS = ["Vancouver", "Prince Rupert", "Churchill", "Thunder Bay", "Bay & Lakes", "St. Lawrence"]
PROVS = {"Manitoba": "MB", "Saskatchewan": "SK", "Alberta": "AB", "British Columbia": "BC"}
STOCK_GROUPS = {"Primary Elevators": "primary", "Process Elevators": "process"}  # rest = terminal


def crop_year_of(d: date) -> str:
    start = d.year if d.month >= 8 else d.year - 1
    return f"{start}-{start + 1}"


def prev_cy(cy: str, n: int = 1) -> str:
    s = int(cy[:4]) - n
    return f"{s}-{s + 1}"


def fetch_csv(cy: str):
    if LOCAL_DIR:
        p = os.path.join(LOCAL_DIR, f"{cy}.csv")
        if os.path.exists(p):
            with open(p, encoding="utf-8-sig") as f:
                return f.read()
        return None
    for pat in NAME_PATTERNS:
        url = f"{BASE}/{cy}/{pat.format(cy=cy)}"
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/csv,*/*"})
                with urllib.request.urlopen(req, timeout=120) as r:
                    raw = r.read()
                txt = raw.decode("utf-8-sig", errors="replace")
                if txt.lstrip().startswith("<"):   # html error page
                    break
                print(f"  {cy}: {url} ({len(raw)/1e6:.1f} MB)")
                return txt
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    break
                time.sleep(3 * (attempt + 1))
            except Exception as e:  # noqa: BLE001
                print(f"  retry {url}: {e}", file=sys.stderr)
                time.sleep(3 * (attempt + 1))
    print(f"  {cy}: not found", file=sys.stderr)
    return None


def norm(h: str) -> str:
    return h.strip().strip('"').lower().replace(" ", "_")


def num(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def new_bucket():
    return defaultdict(lambda: defaultdict(float))   # key -> week -> value


def parse_year(txt: str):
    """Return (buckets per grain key, week->date map)."""
    rdr = csv.reader(io.StringIO(txt))
    hdr = [norm(h) for h in next(rdr)]
    ix = {h: i for i, h in enumerate(hdr)}
    need = ["grain_week", "week_ending_date", "worksheet", "metric", "period", "grain", "grade", "region", "ktonnes"]
    missing = [n for n in need if n not in ix]
    if missing:
        raise ValueError(f"CSV missing columns {missing}; header={hdr}")
    g = lambda row, k: row[ix[k]].strip() if ix[k] < len(row) else ""

    data = {k: new_bucket() for k in GRAINS.values()}
    dates = {}
    for row in rdr:
        if not row or len(row) < len(need):
            continue
        grain = g(row, "grain")
        key = GRAINS.get(grain)
        if not key:
            continue
        try:
            wk = int(float(g(row, "grain_week")))
        except ValueError:
            continue
        ws, met, per, reg = g(row, "worksheet"), g(row, "metric"), g(row, "period"), g(row, "region")
        v = num(g(row, "ktonnes"))
        d = g(row, "week_ending_date")
        if wk not in dates and d:
            dates[wk] = d
        cw = per == "Current Week"
        cyp = per == "Crop Year"
        B = data[key]

        if ws == "Primary" and met == "Deliveries":
            if cw:
                B["del_wk"][wk] += v
            elif cyp:
                B["del_cy"][wk] += v
                if reg in PROVS:
                    B["delprov_" + PROVS[reg]][wk] += v
        elif ws == "Process" and met == "Producer Deliveries":
            if cw:
                B["del_wk"][wk] += v
            elif cyp:
                B["del_cy"][wk] += v
        elif ws == "Process" and met == "Milled/Mfg Grain":
            B["dom_wk" if cw else "dom_cy"][wk] += v
        elif ws == "Terminal Exports" and met == "Exports":
            suf = "wk" if cw else "cy"
            B["exp_" + suf][wk] += v
            B["expterm_" + suf][wk] += v
            if reg in PORTS:
                B[f"port_{suf}_{reg}"][wk] += v
        elif ws == "Primary Shipment Distribution" and met == "Shipment Distribution" and reg == "Export Destinations":
            suf = "wk" if cw else "cy"
            B["exp_" + suf][wk] += v
            B["expdirect_" + suf][wk] += v
        elif ws == "Summary" and met == "Stocks":
            B["stk"][wk] += v
            B["stk_" + STOCK_GROUPS.get(reg, "terminal")][wk] += v
        elif ws == "Terminal Receipts" and met == "Receipts":
            B["rcpt_wk" if cw else "rcpt_cy"][wk] += v
    return data, dates


def to_arrays(bucket, nweeks):
    out = {}
    for k, wv in bucket.items():
        out[k] = [round(wv[w], 1) if w in wv else None for w in range(1, nweeks + 1)]
    return out


def iso(d: str):
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(d, fmt).date().isoformat()
        except ValueError:
            pass
    return d


def main():
    today = date.today()
    cur = crop_year_of(today)
    years = [cur] + [prev_cy(cur, i) for i in range(1, N_HIST + 2)]  # +1 spare if cur missing
    print("Fetching", years)
    raw = {}
    for cy in years:
        t = fetch_csv(cy)
        if t:
            raw[cy] = t
    if not raw:
        sys.exit("No CGC data fetched")
    # if current crop year not yet published (early Aug), shift back
    avail = [cy for cy in years if cy in raw]
    current = avail[0]
    keep = avail[: N_HIST + 1]

    out = {
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "Canadian Grain Commission - Grain Statistics Weekly",
        "source_url": "https://www.canada.ca/en/grain-commission/services/statistics/grain-statistics-weekly.html",
        "current": current,
        "years": keep,
        "ports": PORTS,
        "provs": list(PROVS.values()),
        "grains": {k: {} for k in list(GRAINS.values()) + ["allwheat"]},
        "dates": {},
    }
    for cy in keep:
        data, dates = parse_year(raw[cy])
        nweeks = max(dates) if dates else 0
        # Stocks at week end are levels - all other *_cy are cumulative; *_wk are flows.
        # all-wheat = wheat + durum
        allw = new_bucket()
        for src in ("wheat", "durum"):
            for k, wv in data[src].items():
                for w, v in wv.items():
                    allw[k][w] += v
        data["allwheat"] = allw
        for gk, bucket in data.items():
            out["grains"][gk][cy] = to_arrays(bucket, nweeks)
        out["dates"][cy] = [iso(dates.get(w, "")) for w in range(1, nweeks + 1)]
        print(f"  parsed {cy}: {nweeks} weeks")

    lw = len(out["dates"][current])
    out["latest_week"] = lw
    out["latest_date"] = out["dates"][current][-1] if lw else None

    os.makedirs(os.path.dirname(os.path.abspath(OUT)), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(out, f, separators=(",", ":"))
    print(f"Wrote {OUT}: {current} wk {lw} ({out['latest_date']}), {os.path.getsize(OUT)/1e3:.0f} KB")


if __name__ == "__main__":
    main()
