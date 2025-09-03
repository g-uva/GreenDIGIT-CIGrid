#!/usr/bin/env python3
"""
CI→CFP micro-service:
- Pull site coordinates from GOC DB (or a prebuilt CSV/JSON)
- Query Electricity Maps 72h CI forecast per site (by lat/lon)
- Compute Effective CI = CI * PUE  (default PUE=1.4)
- Output: per-site summary JSON and per-hour CSV

Requirements:
  pip install requests python-dateutil

Env:
  ELECTRICITYMAPS_TOKEN = "<your token>"
Usage:
  python ci_calculator.py --sites-json site_latlng.json --out-dir out/
  # or build sites JSON first using your fetcher, then run this.

Notes:
  - Electricity Maps: /v3/carbon-intensity/forecast supports geolocation.
  - We use lifecycle CI (gCO2eq/kWh) as returned by the API.
"""

from __future__ import annotations
import argparse, csv, os, sys, time, json
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Tuple
import requests
from dateutil import parser as dtparse
import random

ELECTRICITYMAPS_API = "https://api.electricitymap.org/v3/carbon-intensity/forecast"
DEFAULT_PUE = 1.4
TIMEOUT = 20
RETRIES = 2
SLEEP_BETWEEN = 0.5  # politeness / rate-limit friendly

def load_sites(path: str) -> List[Dict]:
    """
    Expects a JSON array with objects like:
    {
      "site_name": "...",
      "country": "...",
      "latitude": 48.7164,
      "longitude": 21.2611
    }
    Use your existing fetcher to generate this (csv->json allowed).
    """
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    # Filter to those with coords
    sites = [s for s in data if s.get("latitude") is not None and s.get("longitude") is not None]
    # Normalise keys
    for s in sites:
        s["site_name"] = s.get("site_name") or s.get("name") or s.get("NAME") or "unknown"
    return sites

def em_headers() -> Dict[str, str]:
    tok = os.environ.get("ELECTRICITYMAPS_TOKEN")
    if not tok:
        print("ELECTRICITYMAPS_TOKEN not set", file=sys.stderr)
        sys.exit(2)
    print(f"Token being used: {tok}")
    return {"auth-token": f"{tok}"}

def fetch_ci_forecast(lat: float, lon: float, use_mock: bool = False) -> List[Dict]:
    """
    Returns list of points: [{"datetime": "...Z", "carbonIntensity": <int>, "emissionFactorType": "lifecycle", ...}, ...]
    """
    if use_mock:
        points = []
        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        for h in range(72):
            dt = now + timedelta(hours=h)
            ci = random.randint(150, 600)
            points.append({"datetime": dt.isoformat(), "carbonIntensity": ci})
        return points
    
    params = {"lat": lat, "lon": lon}
    last_err = None
    for _ in range(RETRIES + 1):
        try:
            r = requests.get(ELECTRICITYMAPS_API, headers=em_headers(), params=params, timeout=TIMEOUT)
            r.raise_for_status()
            payload = r.json()
            # Payload format: {"zone":"XX", "forecast":[{...}, ...]}
            fc = payload.get("forecast") or []
            return fc
        except Exception as e:
            last_err = e
            time.sleep(0.8)
    raise last_err

def summarise_forecast(points: List[Dict], pue: float) -> Dict:
    if not points:
        return {"count": 0}
    vals = []
    for p in points:
        ci = p.get("carbonIntensity")
        if ci is None:
            continue
        effective = ci * pue
        vals.append((p["datetime"], ci, effective))
    if not vals:
        return {"count": 0}
    # sort by time
    vals.sort(key=lambda x: x[0])
    cis = [v[1] for v in vals]
    eff = [v[2] for v in vals]
    best_idx = eff.index(min(eff))
    best_time = vals[best_idx][0]
    return {
        "count": len(vals),
        "start": vals[0][0],
        "end": vals[-1][0],
        "ci_min": min(cis),
        "ci_avg": sum(cis)/len(cis),
        "ci_max": max(cis),
        "effective_ci_min": min(eff),
        "effective_ci_avg": sum(eff)/len(eff),
        "effective_ci_max": max(eff),
        "best_hour_effective_ci": eff[best_idx],
        "best_hour_start": best_time,
    }

def write_hourly_csv(path: str, site_name: str, points: List[Dict], pue: float):
    os.makedirs(path, exist_ok=True)
    fn = os.path.join(path, f"{site_name.replace(' ','_')}_72h.csv")
    with open(fn, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["datetime_utc","ci_gco2_per_kwh","pue","effective_ci_gco2_per_kwh"])
        for p in points:
            ci = p.get("carbonIntensity")
            if ci is None:
                continue
            t = p.get("datetime")
            w.writerow([t, ci, pue, ci * pue])
    return fn

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sites-json", required=True, help="Path to JSON produced from GOC DB fetch (site list with lat/lon).")
    ap.add_argument("--out-dir", default="out", help="Directory for outputs.")
    ap.add_argument("--pue", type=float, default=DEFAULT_PUE, help="Static PUE (default 1.4).")
    ap.add_argument("--limit", type=int, default=None, help="Limit number of sites for a quick run.")
    ap.add_argument("--use-mock", action="store_true", help="Use mock CI values instead of API")
    args = ap.parse_args()

    sites = load_sites(args.sites_json)
    if args.limit:
        sites = sites[:args.limit]

    summaries = []
    for i, s in enumerate(sites, 1):
        name = s["site_name"]
        lat, lon = s["latitude"], s["longitude"]
        print(f"[{i}/{len(sites)}] {name} @ ({lat},{lon}) …", file=sys.stderr)
        try:
            points = fetch_ci_forecast(lat, lon, use_mock=args.use_mock)
            sumry = summarise_forecast(points, args.pue)
            csv_path = write_hourly_csv(os.path.join(args.out_dir, "hourly"), name, points, args.pue)
            summaries.append({
                "site_name": name,
                "country": s.get("country"),
                "latitude": lat,
                "longitude": lon,
                "pue": args.pue,
                "hourly_csv": csv_path,
                **sumry
            })
        except Exception as e:
            summaries.append({
                "site_name": name,
                "country": s.get("country"),
                "latitude": lat,
                "longitude": lon,
                "pue": args.pue,
                "error": f"{type(e).__name__}: {e}",
            })
        time.sleep(SLEEP_BETWEEN)

    # Write summary
    os.makedirs(args.out_dir, exist_ok=True)
    with open(os.path.join(args.out_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summaries, f, indent=2, ensure_ascii=False)

    # Also a quick TSV for scanning
    tsv = os.path.join(args.out_dir, "summary.tsv")
    with open(tsv, "w", encoding="utf-8") as f:
        f.write("site_name\tcountry\tstart\tend\teffective_ci_min\teffective_ci_avg\teffective_ci_max\tbest_hour_start\terror\n")
        for s in summaries:
            f.write("\t".join([
                s.get("site_name",""),
                s.get("country","") or "",
                s.get("start","") or "",
                s.get("end","") or "",
                f"{s.get('effective_ci_min','')}",
                f"{s.get('effective_ci_avg','')}",
                f"{s.get('effective_ci_max','')}",
                s.get("best_hour_start","") or "",
                s.get("error","") or ""
            ]) + "\n")
    print(f"Done. Wrote {len(summaries)} site summaries to {args.out_dir}/", file=sys.stderr)

if __name__ == "__main__":
    main()
