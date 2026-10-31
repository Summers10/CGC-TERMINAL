#!/usr/bin/env python3
"""
Fetch CGC Grain Statistics Weekly (GSW) crop-year CSVs and build data/cgc.json
for Canola and Wheat (ex-durum).
 
Runs server-side in GitHub Actions (canada.ca blocks browser CORS).
Needs: pip install curl_cffi openpyxl\nEvery run cross-checks the latest week against the official Excel 'Summary' tab\nand refuses to publish on any mismatch.
 
Metric definitions (all kt):
  Matches the CGC GSW Excel 'Summary' tab (verified wk 12 2026-27 + year-ago):
  deliveries  = Primary 'Deliveries' + Process 'Producer Deliveries' + Producer Cars 'Shipments'
  domestic    = Domestic disappearance = Process 'Milled/Mfg Grain' + Terminal Disposition
                'Canadian Domestic' + Primary Shipment Distribution 'Canadian Domestic'
                + Producer Cars destinations 'Canada Processors'
  crush       = Process 'Milled/Mfg Grain' (canola crush)
  exports     = Terminal Exports + Primary Shipment Distribution 'Export Destinations'
                + Producer Cars destinations 'United States*'
  stocks      = Summary 'Stocks' (commercial: primary + process + terminals)
"""
import csv
import io
import json
import os
import subprocess
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timezone
 
BASE = "https://www.canada.ca/content/dam/cgc-ccg/documents/statistiques-statistics/grain-statistics-weekly"
NAME_PATTERNS = ["grain-stats-weekly-{cy}.csv", "grain-statistics-weekly-{cy}.csv"]
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
 
N_HIST = int(os.environ.get("CGC_HIST_YEARS", "5"))   # prior crop years for LY / 5-yr avg
OUT = os.environ.get("CGC_OUT", os.path.join(os.path.dirname(__file__), "..", "data", "cgc.json"))
LOCAL_DIR = os.environ.get("CGC_LOCAL_DIR")             # optional: read CSVs from disk (testing)
 
GRAINS = {"Canola": "canola", "Wheat": "wheat"}
PORTS = ["Vancouver", "Prince Rupert", "Churchill", "Thunder Bay", "Bay & Lakes", "St. Lawrence"]
PROVS = {"Manitoba": "MB", "Saskatchewan": "SK", "Alberta": "AB", "British Columbia": "BC"}
STOCK_GROUPS = {"Primary Elevators": "primary", "Process Elevators": "process"}  # rest = terminal
 
 
def crop_year_of(d: date) -> str:
    start = d.year if d.month >= 8 else d.year - 1
    return f"{start}-{start + 1}"
 
 
def prev_cy(cy: str, n: int = 1) -> str:
    s = int(cy[:4]) - n
    return f"{s}-{s + 1}"
 
 
REFERER = "https://www.canada.ca/en/grain-commission/services/statistics/grain-statistics-weekly.html"
 
 
def _via_cffi(url):
    """Real Chrome TLS/HTTP2 fingerprint - canada.ca's Akamai edge resets non-browser HTTP/2 clients."""
    from curl_cffi import requests as creq  # pip install curl_cffi
    r = creq.get(url, impersonate="chrome", timeout=90, headers={"Referer": REFERER, "Accept-Language": "en-CA,en;q=0.9"})
    return r.status_code, r.content, ""
 
 
def _via_curl(url, extra):
    cmd = ["curl", "-sS", "-L", "--connect-timeout", "20", "--max-time", "90", *extra,
           "-w", "\n__HTTP__%{http_code}", url]
    r = subprocess.run(cmd, capture_output=True, timeout=120)
    body, _, code = r.stdout.rpartition(b"\n__HTTP__")
    return int(code or 0), body, r.stderr.decode(errors="replace").strip()[:120]
 
 
STRATEGIES = [
    ("cffi-chrome", _via_cffi),
    ("curl-http1.1", lambda u: _via_curl(u, ["--http1.1", "-H", "Accept: */*", "-H", "Accept-Language: en-CA,en;q=0.9"])),
    ("curl-plain", lambda u: _via_curl(u, [])),
]
_good = None  # remember the first strategy that works
 
 
def _get(url):
    global _good
    order = [_good] if _good else STRATEGIES
    for name, fn in order:
        t0 = time.time()
        try:
            code, raw, err = fn(url)
        except Exception as e:  # noqa: BLE001
            code, raw, err = 0, b"", f"{type(e).__name__}: {e}"[:120]
        print(f"    [{name}] HTTP {code} {len(raw)/1e6:.1f} MB {time.time()-t0:.0f}s {err}", flush=True)
        if code in (200, 404):
            if code == 200:
                _good = (name, fn)
            return code, raw
    return 0, b""
 
 
def fetch_csv(cy: str):
    if LOCAL_DIR:
        p = os.path.join(LOCAL_DIR, f"{cy}.csv")
        if os.path.exists(p):
            with open(p, encoding="utf-8-sig") as f:
                return f.read()
        return None
    for pat in NAME_PATTERNS:
        url = f"{BASE}/{cy}/{pat.format(cy=cy)}"
        print(f"  {cy}: {url.rsplit('/', 1)[-1]}", flush=True)
        code, raw = _get(url)
        if code == 200:
            txt = raw.decode("utf-8-sig", errors="replace")
            if not txt.lstrip().startswith("<"):
                return txt
        if code == 0 and not _good:
            sys.exit("All download methods blocked by canada.ca - see log above")
    print(f"  {cy}: not found", file=sys.stderr, flush=True)
    return None
 
 
def norm(h: str) -> str:
    return h.strip().strip('"').lower().replace(" ", "_")
 
 
def num(x) -> float:
    try:
        return float(str(x).replace(",", "").strip())   # older files use "1,063.7"
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
            B["crush_wk" if cw else "crush_cy"][wk] += v
        elif ws == "Terminal Disposition" and met == "Canadian Domestic":
            B["dom_wk" if cw else "dom_cy"][wk] += v
        elif ws == "Producer Cars" and met == "Shipments":
            B["del_wk" if cw else "del_cy"][wk] += v
        elif ws == "Producer Cars" and met == "Shipment Destinations":
            suf = "wk" if cw else "cy"
            if reg == "Canada Processors":
                B["dom_" + suf][wk] += v
            elif reg.startswith("United States"):
                B["exp_" + suf][wk] += v
                B["expdirect_" + suf][wk] += v
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
        elif ws == "Primary Shipment Distribution" and met == "Shipment Distribution" and reg == "Canadian Domestic":
            B["dom_wk" if cw else "dom_cy"][wk] += v
        elif ws == "Summary" and met in ("Stocks", "Commercial Stocks"):
            pre = "stk" if met == "Stocks" else "cstk"   # metric renamed in 2024-25
            B[pre][wk] += v
            B[pre + "_" + STOCK_GROUPS.get(reg, "terminal")][wk] += v
        elif ws == "Terminal Receipts" and met == "Receipts":
            B["rcpt_wk" if cw else "rcpt_cy"][wk] += v
    for B in data.values():
        for suf in ("", "_primary", "_process", "_terminal"):
            c = B.pop("cstk" + suf, None)
            if c:
                for w, v in c.items():
                    if w not in B["stk" + suf]:
                        B["stk" + suf][w] = v
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
 
 
# ---------------------------------------------------------------- validation
CHECK_KEYS = ["del_wk", "del_cy", "dom_wk", "dom_cy", "exp_wk", "exp_cy", "rcpt_wk", "rcpt_cy", "stk"]
TOL = 0.35   # kt; components are rounded to 0.1 so sums can differ by a few tenths
 
 
def xlsx_urls(cy, wk):
    b = f"{BASE}/{cy}/"
    ww = f"{wk:02d}"
    if int(cy[:4]) >= 2025:
        return [b + f"{ww}-grain-stats-weekly-{cy}.xlsx", b + f"gsw-shg-{ww}-en.xlsx"]
    return [b + f"gsw-shg-{ww}-en.xlsx", b + f"gsw-shg-{wk}-en.xlsx", b + f"{ww}-grain-stats-weekly-{cy}.xlsx"]
 
 
def summary_from_xlsx(blob):
    """Read the official GSW workbook 'Summary' tab -> {canola:{...}, wheat:{...}, title}."""
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(blob), read_only=True, data_only=True)
    name = next((n for n in wb.sheetnames if n.strip().lower() == "summary"), None)
    if not name:
        raise ValueError(f"no Summary sheet: {wb.sheetnames}")
    rows = [[("" if c is None else c) for c in r] for r in wb[name].iter_rows(values_only=True)]
    t = lambda v: " ".join(str(v).split()) if v != "" else ""
    title, hdr = None, None
    for i, r in enumerate(rows):
        vals = [t(v) for v in r]
        title = title or next((v for v in vals if "Handling Summary" in v), None)
        if title and hdr is None and "Canola" in vals:
            hdr = i
            col = {"canola": vals.index("Canola"), "wheat": vals.index("Wheat")}
            break
    if hdr is None:
        raise ValueError("Summary header row not found")
    res = {"title": title, "canola": {}, "wheat": {}}
    secs = [("producer deliveries", "del"), ("terminal receipts", "rcpt"), ("exports", "exp"), ("domestic disappearance", "dom")]
    per = {"Current Week": "wk", "To Date": "cy"}
    sec, stocks, scol = None, False, col
    for r in rows[hdr + 1:]:
        a = t(r[0]) if r else ""
        joined = " ".join(t(v) for v in r)
        hit = next((k for lab, k in secs if a.lower().startswith(lab)), None)
        if hit:
            sec = hit
            continue
        if "Commercial Stocks, Week" in joined:
            stocks, sec = True, None
            continue
        if stocks and "Canola" in [t(v) for v in r]:
            vals = [t(v) for v in r]
            scol = {"canola": vals.index("Canola"), "wheat": vals.index("Wheat")}
        if a in ("Current Week", "Week Ago", "To Date", "Year Ago"):
            if sec and a in per:
                for g, c in col.items():
                    v = r[c] if c < len(r) else ""
                    res[g][f"{sec}_{per[a]}"] = float(v) if isinstance(v, (int, float)) else 0.0
        elif a:
            sec = None
        if stocks and a.lower() == "total":
            for g, c in scol.items():
                v = r[c] if c < len(r) else ""
                res[g]["stk"] = float(v) if isinstance(v, (int, float)) else 0.0
            stocks = False
    return res
 
 
def validate(out):
    errs, notes = [], []
    cur, lw = out["current"], out["latest_week"]
    # 1. structure: every grain/year has core series; history years complete
    for cy in out["years"]:
        n = len(out["dates"][cy])
        if cy != cur and n < 50:
            errs.append(f"{cy}: only {n} weeks in file")
        for g in GRAINS.values():
            S = out["grains"][g].get(cy, {})
            for k in CHECK_KEYS:
                if k not in S:
                    errs.append(f"{g} {cy}: series {k} missing (CGC may have renamed a metric)")
            for k in ("del_cy", "dom_cy", "exp_cy", "stk"):
                arr = S.get(k) or []
                if arr and (arr[-1] is None or arr[-1] <= 0):
                    errs.append(f"{g} {cy}: {k} last value is {arr[-1]}")
                if any(v is not None and v < 0 for v in arr):
                    errs.append(f"{g} {cy}: negative values in {k}")
    # 2. no going backwards vs the currently published file
    try:
        prev = json.load(open(OUT))
        if (prev.get("current"), prev.get("latest_week", 0)) > (cur, lw):
            errs.append(f"latest week went backwards: {prev.get('current')} wk{prev.get('latest_week')} -> {cur} wk{lw}")
    except (FileNotFoundError, ValueError):
        pass
    # 3. official Excel Summary tab for the latest week must match exactly
    xl, url_used = None, None
    for u in xlsx_urls(cur, lw):
        code, raw = _get(u)
        if code == 200 and raw[:2] == b"PK":
            xl, url_used = summary_from_xlsx(raw), u
            break
    if xl is None:
        errs.append(f"official Excel for {cur} wk{lw} not found - cannot verify")
    else:
        if f"Week {lw}" not in (xl["title"] or "").replace(",", ""):
            errs.append(f"Excel title doesn't say Week {lw}: {xl['title']}")
        cells, maxd = 0, 0.0
        for g in GRAINS.values():
            for k in CHECK_KEYS:
                a = xl[g].get(k)
                b = (out["grains"][g][cur].get(k) or [None] * lw)[lw - 1]
                if a is None:
                    errs.append(f"Excel Summary missing {g} {k}")
                    continue
                d = abs(a - (b or 0.0))
                cells += 1
                maxd = max(maxd, d)
                if d > TOL:
                    errs.append(f"MISMATCH {g} {k} wk{lw}: Excel {a} vs terminal {b}")
        out["verified"] = {"week": lw, "crop_year": cur, "cells": cells, "max_diff": round(maxd, 2),
                           "source": url_used, "at": out["updated"]}
        print(f"  Excel cross-check: {cells} cells, max diff {maxd:.2f} kt")
    return errs
 
 
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
        "grains": {k: {} for k in GRAINS.values()},
        "dates": {},
    }
    for cy in keep:
        data, dates = parse_year(raw[cy])
        nweeks = max(dates) if dates else 0
        # Stocks at week end are levels - all other *_cy are cumulative; *_wk are flows.
        for gk, bucket in data.items():
            out["grains"][gk][cy] = to_arrays(bucket, nweeks)
        out["dates"][cy] = [iso(dates.get(w, "")) for w in range(1, nweeks + 1)]
        print(f"  parsed {cy}: {nweeks} weeks")
 
    lw = len(out["dates"][current])
    out["latest_week"] = lw
    out["latest_date"] = out["dates"][current][-1] if lw else None
 
    if not LOCAL_DIR:
        errs = validate(out)
        if errs:
            print("\nVALIDATION FAILED - not publishing:", file=sys.stderr)
            for e in errs:
                print("  - " + e, file=sys.stderr)
            sys.exit(1)
 
    os.makedirs(os.path.dirname(os.path.abspath(OUT)), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(out, f, separators=(",", ":"))
    print(f"Wrote {OUT}: {current} wk {lw} ({out['latest_date']}), {os.path.getsize(OUT)/1e3:.0f} KB")
 
 
if __name__ == "__main__":
    main()
 
