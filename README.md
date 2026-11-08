CGC Terminal — build notes (updated 2026-10-31)

Repo: github.com/Summers10/CGC-TERMINAL · Site: https://summers10.github.io/CGC-TERMINAL/ Static GitHub Pages site + GitHub Actions cron (Thu 22:30 UTC, Fri 14:30 UTC) → scripts/fetch_cgc.py → data/cgc.json → index.html. The page loads the JSON in the browser; a plain fetch of the HTML stays on “Loading…”. Missing figures are shown as “not available”. Scope: Canola, Wheat (ex-durum) and Oats on the balance sheet; CGC weekly covers canola and wheat only.

Fetching (important)
canada.ca (Akamai) resets plain curl/python HTTP/2 requests from GitHub runners (curl err 92 INTERNAL_ERROR).
Working fix: curl_cffi with impersonate="chrome" (workflow step pip install curl_cffi). Script falls back to curl http1.1 / plain curl.
If ever blocked again: fallback plan = self-hosted GitHub runner on home PC.
Source
Page: https://www.canada.ca/en/grain-commission/services/statistics/grain-statistics-weekly.html
CSV: .../content/dam/cgc-ccg/documents/statistiques-statistics/grain-statistics-weekly/{CY}/grain-stats-weekly-{CY}.csv (2025-26+), grain-statistics-weekly-{CY}.csv (≤2024-25)
Columns: crop year, grain week, week ending date (dd/mm/yyyy), worksheet, metric, period (Current Week / Crop Year), grain, grade, region, ktonnes. Pre-2024 files put grain_week first → parse by header name.
Metric mapping — matches the office's GSW Excel "Summary" tab
Deliveries = Primary/Deliveries + Process/Producer Deliveries + Producer Cars/Shipments
Domestic disappearance = Process/Milled/Mfg Grain + Terminal Disposition/Canadian Domestic + Primary Shipment Distribution(Shipment Distribution)/Canadian Domestic + Producer Cars/Shipment Destinations "Canada Processors"
Crush (canola) / Milled (wheat) = Process/Milled/Mfg Grain (shown separately)
Exports = Terminal Exports + Primary Shipment Distribution/Export Destinations + Producer Cars/Shipment Destinations "United States*"
Stocks = Summary/Stocks (excl. condo storage)
Quirks avoided: Process/Provincial Deliveries and Ab/Mb/Sk-Shipment Distribution mix CY values under "Current Week".
Verified vs Excel Summary tab, wk 12 (ending 25/10/2026), kt

Canola: deliveries 445.9 / 5,430.1 · terminal receipts 316.2 / 2,325.0 · exports 238.0 / 1,886.1 · dom. disappearance 196.3 / 2,989.2 · stocks 1,561.7 Wheat: deliveries 426.8 / 6,301.2 (calc 6,301.1 rounding) · receipts 439.6 / 7,187.1 · exports 432.5 / 6,005.3 · dom. disapp. 104.5 / 1,553.4 (calc 1,553.5) · stocks 2,682.6 Year-ago CYTD (from 2025-26 file wk 12) also matched: canola del 4,112.8 / exp 1,234.8 / dom 2,732.5; wheat del 6,181.6 / exp 5,121.0 / dom 1,077.3
