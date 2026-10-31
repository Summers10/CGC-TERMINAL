#!/usr/bin/env python3
"""
Canadian supply & disposition (balance sheets) for Canola and Wheat (ex-durum) -> data/sd.json
 
Sources (no API keys - plain public downloads):
  * Statistics Canada 32-10-0013  Supply and disposition of grains in Canada (actuals, 1996-97 on)
  * Statistics Canada 32-10-0359  Area, yield and production (harvested acres, bu/ac)
  * AAFC "Canada: Outlook for Principal Field Crops" (monthly forecast; latest + prior report)
 
Rules:
  * Completed crop years come from StatsCan (authoritative, revised).
  * Years StatsCan hasn't finalised come from the latest AAFC report.
  * Every balance sheet must add up (supply = beg + prod + imports; supply = exports + domestic + ending)
    or nothing is published.
 
Needs: pip install curl_cffi   (falls back to system curl via fetch_cgc._get)
"""
import csv
import io
import json
import os
import re
import sys
import zipfile
from datetime import datetime, timezone
from html.parser import HTMLParser
 
sys.path.insert(0, os.path.dirname(__file__))
from fetch_cgc import _get  # noqa: E402  (same browser-like downloader as the CGC script)
 
OUT = os.environ.get("SD_OUT", os.path.join(os.path.dirname(__file__), "..", "data", "sd.json"))
ARCHIVE = os.path.join(os.path.dirname(OUT), "aafc")
LOCAL_DIR = os.environ.get("SD_LOCAL_DIR")  # testing: read sources from disk
 
SC_SD = "https://www150.statcan.gc.ca/n1/tbl/csv/32100013-eng.zip"
SC_AYP = "https://www150.statcan.gc.ca/n1/tbl/csv/32100359-eng.zip"
AAFC_BASE = "https://agriculture.canada.ca"
AAFC_LIST = AAFC_BASE + "/en/sector/crops/reports-statistics"
 
CROPS = {
    "canola": {"sd": "Canola", "ayp": "Canola (rapeseed)", "aafc": r"^canola", "lb_bu": 50.0},
    "wheat": {"sd": "Wheat, excluding durum", "ayp": "Wheat, all excluding durum wheat", "aafc": r"^wheat except durum", "lb_bu": 60.0},
}
HA_TO_AC = 2.471053814
TOL = 3.0  # kt - published tables are rounded to whole kt
 
 
def fetch(url, name):
    if LOCAL_DIR:
        with open(os.path.join(LOCAL_DIR, name), "rb") as f:
            return f.read()
    code, raw = _get(url)
    if code != 200:
        raise RuntimeError(f"download failed ({code}): {url}")
    return raw
 
 
def cy_label(start):
    return f"{start}-{start + 1}"
 
 
# ------------------------------------------------------------------ StatsCan
def statcan_rows(blob, want_crops, crop_col_name):
    z = zipfile.ZipFile(io.BytesIO(blob))
    name = next(n for n in z.namelist() if not n.lower().endswith("_metadata.csv"))
    with z.open(name) as f:
        rdr = csv.reader(io.TextIOWrapper(f, encoding="utf-8-sig"))
        hdr = next(rdr)
        ix = {h: i for i, h in enumerate(hdr)}
        ci = ix[crop_col_name]
        for r in rdr:
            if len(r) < len(hdr) or r[ix["GEO"]] != "Canada" or r[ci] not in want_crops:
                continue
            yield r, ix
 
 
def parse_statcan_sd(blob):
    want = {c["sd"]: k for k, c in CROPS.items()}
    out = {k: {} for k in CROPS}
    for r, ix in statcan_rows(blob, want, "Type of crop"):
        ref = r[ix["REF_DATE"]]
        if not ref.endswith("-07"):          # -07 = full crop year (Aug-Jul)
            continue
        v = r[ix["VALUE"]].strip()
        if v == "":
            continue
        cy = cy_label(int(ref[:4]) - 1)
        out[want[r[ix["Type of crop"]]]].setdefault(cy, {})[r[ix["Supply and disposition of grains"]]] = float(v)
    res = {}
    for k, years in out.items():
        res[k] = {}
        for cy, d in years.items():
            g = lambda n: d.get(n, 0.0)
            dom = g("Total domestic disappearance")
            fi = g("Human food") + g("Industrial use")
            fwd = g("Animal feed, waste and dockage")
            res[k][cy] = {
                "beg": g("Total beginning stocks"), "prod": g("Production"), "imp": g("Imports"),
                "supply": g("Total supplies"), "exp": g("Total exports"), "fi": fi, "fwd": fwd,
                "seed_other": dom - fi - fwd, "dom": dom, "end": g("Total ending stocks"), "src": "StatsCan",
            }
    return res
 
 
def parse_statcan_ayp(blob):
    want = {c["ayp"]: k for k, c in CROPS.items()}
    keep = {"Harvested area (acres)": "ac_harv", "Seeded area (acres)": "ac_seed",
            "Average yield (bushels per acre)": "bu_ac"}
    out = {k: {} for k in CROPS}
    for r, ix in statcan_rows(blob, want, "Type of crop"):
        m = keep.get(r[ix["Harvest disposition"]])
        v = r[ix["VALUE"]].strip()
        if not m or v == "":
            continue
        year = int(r[ix["REF_DATE"]][:4])     # harvest year 2026 -> crop year 2026-2027
        d = out[want[r[ix["Type of crop"]]]].setdefault(cy_label(year), {})
        d[m] = float(v) / 1000.0 if m.startswith("ac_") else float(v)
    return out
 
 
# ------------------------------------------------------------------ AAFC
class TableParser(HTMLParser):
    """Collect every <table>: caption text + rows of cell text."""
    def __init__(self):
        super().__init__()
        self.tables, self._t, self._row, self._cell, self._cap, self._in_cap, self._skip = [], None, None, None, None, False, 0
 
    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._t = {"caption": "", "rows": [], "foot": False}
        elif self._t is None:
            return
        elif tag == "caption":
            self._in_cap = True
        elif tag == "tfoot":
            self._t["foot"] = True
        elif tag == "tr" and not self._t["foot"]:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = ""
        elif tag == "sup":
            self._skip += 1                  # footnote markers
        elif tag == "span" and ("class", "wb-inv") in attrs:
            self._skip += 1
 
    def handle_endtag(self, tag):
        if self._t is None:
            return
        if tag == "table":
            self.tables.append(self._t)
            self._t = None
        elif tag == "caption":
            self._in_cap = False
        elif tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(" ".join(self._cell.split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self._t["rows"].append(self._row)
            self._row = None
        elif tag in ("sup", "span") and self._skip:
            self._skip -= 1
 
    def handle_data(self, data):
        if self._t is None or self._skip:
            return
        if self._in_cap:
            self._t["caption"] += data
        elif self._cell is not None:
            self._cell += data
 
 
AAFC_ROWS = [
    (r"^area seeded", "ha_seed"), (r"^area harvested", "ha_harv"), (r"^yield", "t_ha"),
    (r"^production", "prod"), (r"^imports", "imp"), (r"^total supply", "supply"), (r"^exports", "exp"),
    (r"^food and industrial", "fi"), (r"^feed, waste", "fwd"), (r"^total domestic use", "dom"),
    (r"^carry-out stocks", "end"), (r"^average price", "price"),
]
 
 
def num(s):
    s = s.replace(",", "").replace(" ", "").strip()
    return float(s) if re.fullmatch(r"-?\d+(\.\d+)?", s) else None
 
 
def parse_aafc(html):
    p = TableParser()
    p.feed(html)
    res = {}
    for key, c in CROPS.items():
        t = next((t for t in p.tables if re.search(c["aafc"], " ".join(t["caption"].split()), re.I)), None)
        if not t:
            raise ValueError(f"AAFC table for {key} not found")
        hdr = t["rows"][0]
        years = [re.match(r"(\d{4})-(\d{4})", h).group(0) for h in hdr if re.match(r"\d{4}-\d{4}", h)]
        if len(years) < 2:
            raise ValueError(f"AAFC {key}: crop-year header not found: {hdr}")
        cols = {cy: {} for cy in years}
        for row in t["rows"][1:]:
            label = row[0].lower()
            k = next((k for pat, k in AAFC_ROWS if re.search(pat, label)), None)
            if not k:
                continue
            vals = row[-len(years):]
            for cy, v in zip(years, vals):
                cols[cy][k] = num(v)
        for cy, d in cols.items():
            missing = [k for k in ("prod", "imp", "supply", "exp", "dom", "end") if d.get(k) is None]
            if missing:
                raise ValueError(f"AAFC {key} {cy}: rows missing {missing}")
            d["beg"] = d["supply"] - d["prod"] - d["imp"]
            d["seed_other"] = d["dom"] - (d.get("fi") or 0) - (d.get("fwd") or 0)
            d["src"] = "AAFC"
        res[key] = cols
    m = re.search(r"Outlook for Principal Field Crops,?\s*(\d{4}-\d{2}-\d{2})", html)
    return res, (m.group(1) if m else None)
 
 
def aafc_report_urls():
    html = fetch(AAFC_LIST, "aafc_list.html").decode("utf-8", "replace")
    links = re.findall(r'href="([^"]*canada-outlook-principal-field-crops-(\d{4}-\d{2}-\d{2}))"', html)
    seen, out = set(), []
    for href, d in sorted(links, key=lambda x: x[1], reverse=True):
        if d not in seen:
            seen.add(d)
            out.append((d, href if href.startswith("http") else AAFC_BASE + href))
    if not out:
        raise RuntimeError("no AAFC outlook links on the reports page")
    return out
 
 
# ------------------------------------------------------------------ build + validate
def check_sheet(tag, d, errs):
    if abs(d["beg"] + d["prod"] + d["imp"] - d["supply"]) > TOL:
        errs.append(f"{tag}: beg+prod+imp {d['beg'] + d['prod'] + d['imp']:.0f} != supply {d['supply']:.0f}")
    if abs(d["exp"] + d["dom"] + d["end"] - d["supply"]) > TOL:
        errs.append(f"{tag}: exp+dom+end {d['exp'] + d['dom'] + d['end']:.0f} != supply {d['supply']:.0f}")
    for k in ("prod", "supply", "exp", "dom", "end"):
        if d[k] is None or d[k] < 0:
            errs.append(f"{tag}: bad {k}={d[k]}")
 
 
def main():
    errs, warns = [], []
    sd = parse_statcan_sd(fetch(SC_SD, "32100013-eng.zip"))
    ayp = parse_statcan_ayp(fetch(SC_AYP, "32100359-eng.zip"))
    reports = aafc_report_urls()
    latest_date, latest_url = reports[0]
    latest, title_date = parse_aafc(fetch(latest_url, "aafc_latest.html").decode("utf-8", "replace"))
    prior, prior_date = None, None
    if len(reports) > 1:
        prior_date, prior_url = reports[1]
        try:
            prior, _ = parse_aafc(fetch(prior_url, "aafc_prior.html").decode("utf-8", "replace"))
        except Exception as e:  # noqa: BLE001
            warns.append(f"prior AAFC report unreadable: {e}")
    print(f"  StatsCan S&D years: canola {min(sd['canola'])}..{max(sd['canola'])}; AAFC latest {latest_date}, prior {prior_date}")
 
    out = {"updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "aafc": {"date": latest_date, "url": latest_url, "prior_date": prior_date},
           "statcan_latest": max(sd["canola"]), "crops": {}}
 
    for key, c in CROPS.items():
        years = {}
        for cy, d in sd[key].items():
            check_sheet(f"StatsCan {key} {cy}", d, errs)
            years[cy] = dict(d)
        aafc_years = sorted(latest[key])
        fc = aafc_years[-1]                                   # forecast year (newest column)
        for cy in aafc_years:
            a = latest[key][cy]
            check_sheet(f"AAFC {latest_date} {key} {cy}", a, errs)
            if cy in years:                                  # StatsCan final exists -> keep it, cross-check
                s = years[cy]
                for k in ("prod", "exp", "end"):
                    if abs(s[k] - a[k]) > max(TOL, 0.005 * s[k]):
                        warns.append(f"{key} {cy} {k}: StatsCan {s[k]:.0f} vs AAFC {a[k]:.0f} (AAFC column not yet revised)")
                years[cy]["price"] = a.get("price")
            else:
                years[cy] = dict(a)
        # area / yield
        for cy, d in years.items():
            y = ayp[key].get(cy)
            if y and "ac_harv" in y:
                d["ac_harv"], d["bu_ac"] = y["ac_harv"], y.get("bu_ac")
            elif d.get("ha_harv"):
                d["ac_harv"] = d["ha_harv"] * HA_TO_AC
                d["bu_ac"] = d["t_ha"] * 2204.62262 / c["lb_bu"] / HA_TO_AC if d.get("t_ha") else None
            d["total_use"] = d["exp"] + d["dom"]
            d["su"] = d["end"] / d["total_use"] * 100 if d["total_use"] else None
        prior_fc = None
        if prior and fc in prior[key]:
            p = dict(prior[key][fc])
            p["total_use"] = p["exp"] + p["dom"]
            p["su"] = p["end"] / p["total_use"] * 100 if p["total_use"] else None
            cur = years[fc]   # scale current (StatsCan-based) area/yield by AAFC's own revision so Chg isn't a unit artefact
            if p.get("ha_harv") and cur.get("ha_harv") and cur.get("ac_harv"):
                p["ac_harv"] = cur["ac_harv"] * p["ha_harv"] / cur["ha_harv"]
            if p.get("t_ha") and cur.get("t_ha") and cur.get("bu_ac"):
                p["bu_ac"] = cur["bu_ac"] * p["t_ha"] / cur["t_ha"]
            prior_fc = p
        elif prior:
            warns.append(f"{key}: prior AAFC report has no {fc} column")
        out["crops"][key] = {"years": dict(sorted(years.items())), "forecast_year": fc,
                             "aafc_years": aafc_years, "prior": prior_fc}
 
    # don't go backwards
    try:
        prev = json.load(open(OUT))
        if prev.get("aafc", {}).get("date", "") > latest_date:
            errs.append(f"AAFC report went backwards: {prev['aafc']['date']} -> {latest_date}")
    except (FileNotFoundError, ValueError):
        pass
 
    for w in warns:
        print("  note: " + w)
    if errs:
        print("\nS&D VALIDATION FAILED - not publishing:", file=sys.stderr)
        for e in errs:
            print("  - " + e, file=sys.stderr)
        sys.exit(1)
 
    os.makedirs(ARCHIVE, exist_ok=True)
    with open(os.path.join(ARCHIVE, f"{latest_date}.json"), "w") as f:   # month-by-month history builds up
        json.dump(latest, f, separators=(",", ":"))
    out["checks"] = {"sheets": sum(len(v["years"]) for v in out["crops"].values()), "notes": warns}
    with open(OUT, "w") as f:
        json.dump(out, f, separators=(",", ":"))
    print(f"Wrote {OUT}: AAFC {latest_date}, {out['checks']['sheets']} balance sheets verified")
 
 
if __name__ == "__main__":
    main()
 
