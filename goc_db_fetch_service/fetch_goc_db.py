#!/usr/bin/env python
"""
Build a mapping: site_name -> latitude/longitude from GOCDB.

It first fetches the site list (public), then for each site makes a private
`get_site&sitename=...` call to grab LATITUDE/LONGITUDE (if authorized).

Auth options:
  1) OAuth2 Bearer token (EGI Check-in):  --token $TOKEN  (or env GOCDB_OAUTH_TOKEN)
  2) X.509 client cert:                  --cert client.pem [--key client.key]

Example:
  python gocdb_site_latlng.py --format csv --output site_latlng.csv \
      --scope EGI --max-workers 6 \
      --token "$GOCDB_TOKEN"

Docs:
  - get_site_list (public) and get_site (private; includes LATITUDE/LONGITUDE)
"""

from __future__ import annotations
import argparse
import csv
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlencode

import requests
import xml.etree.ElementTree as ET

DEFAULT_BASE_URL = "https://goc.egi.eu/gocdbpi"
PUBLIC_ENTRY = "/public/"

NS = {}  # no namespaces in the examples, keep simple

def build_url(base_url: str, method: str, **params) -> str:
    q = {"method": method}
    q.update({k: v for k, v in params.items() if v is not None})
    return f"{base_url}{PUBLIC_ENTRY}?{urlencode(q)}"

def new_session(token: Optional[str], cert: Optional[str], key: Optional[str], timeout: float) -> requests.Session:
    s = requests.Session()
    s.headers["Accept"] = "application/xml"
    if token:
        s.headers["Authorization"] = f"Bearer {token}"
    if cert:
        s.cert = (cert, key) if key else cert
    s.timeout = timeout
    return s

def fetch_site_list(session: requests.Session, base_url: str, scope: Optional[str], roc: Optional[str], country: Optional[str]) -> List[Dict]:
    url = build_url(base_url, "get_site_list", scope=scope, roc=roc, country=country)
    r = session.get(url)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    sites = []
    for site in root.findall("./SITE", NS):
        sites.append({
            "name": site.get("NAME") or "",
            "roc": site.get("ROC"),
            "country": site.get("COUNTRY"),
            "id": site.get("ID"),
            "certification_status": site.get("CERTIFICATION_STATUS"),
            "production_infrastructure": site.get("PRODUCTION_INFRASTRUCTURE"),
        })
    return sites

def fetch_site_latlng(session: requests.Session, base_url: str, sitename: str, scope: Optional[str]) -> Tuple[Optional[float], Optional[float], Optional[str]]:
    """Returns (lat, lng, error) for a single site name."""
    url = build_url(base_url, "get_site", sitename=sitename, scope=scope)
    try:
        r = session.get(url)
        if r.status_code in (401, 403):
            return None, None, f"unauthorized:{r.status_code}"
        r.raise_for_status()
        root = ET.fromstring(r.content)
        site = root.find("./SITE", NS)
        if site is None:
            return None, None, "not_found"
        lat_el = site.find("./LATITUDE", NS)
        lng_el = site.find("./LONGITUDE", NS)
        lat = float(lat_el.text) if lat_el is not None and lat_el.text else None
        lng = float(lng_el.text) if lng_el is not None and lng_el.text else None
        return lat, lng, None
    except Exception as e:
        return None, None, f"error:{type(e).__name__}"

def main():
    ap = argparse.ArgumentParser(description="Build site → (lat,lng) mapping from GOCDB.")
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL, help=f"Base API URL (default: {DEFAULT_BASE_URL})")
    ap.add_argument("--scope", default=None, help="Filter by scope (e.g., EGI). Applied to both calls.")
    ap.add_argument("--roc", default=None, help="Filter by ROC/NGI for get_site_list.")
    ap.add_argument("--country", default=None, help="Filter by country for get_site_list.")
    ap.add_argument("--token", default=None, help="OAuth2 Bearer token (EGI Check-in). Defaults to env GOCDB_OAUTH_TOKEN.")
    ap.add_argument("--cert", default=None, help="Path to client certificate PEM (for mTLS).")
    ap.add_argument("--key", default=None, help="Path to client key PEM (if separate).")
    ap.add_argument("--format", choices=["csv", "json"], default="csv", help="Output format.")
    ap.add_argument("--output", default="-", help="Output file path or '-' for stdout.")
    ap.add_argument("--max-workers", type=int, default=5, help="Parallel workers for per-site fetches.")
    ap.add_argument("--timeout", type=float, default=30.0, help="Request timeout in seconds.")
    ap.add_argument("--sleep", type=float, default=0.0, help="Optional fixed delay between requests (politeness).")
    args = ap.parse_args()

    token = args.token or os.environ.get("GOCDB_OAUTH_TOKEN")
    session = new_session(token, args.cert, args.key, args.timeout)

    # 1) site list (public)
    sites = fetch_site_list(session, args.base_url, args.scope, args.roc, args.country)
    if not sites:
        print("No sites returned by get_site_list (check filters).", file=sys.stderr)
        return 2

    # 2) fetch lat/lng per site (private – may return unauthorized)
    results: List[Dict] = []
    def task(site):
        if args.sleep:
            time.sleep(args.sleep)
        lat, lng, err = fetch_site_latlng(session, args.base_url, site["name"], args.scope)
        out = {
            "site_name": site["name"],
            "roc": site["roc"],
            "country": site["country"],
            "certification_status": site["certification_status"],
            "production_infrastructure": site["production_infrastructure"],
            "latitude": lat,
            "longitude": lng,
            "error": err,
        }
        return out

    if args.max_workers <= 1:
        for s in sites:
            results.append(task(s))
    else:
        with ThreadPoolExecutor(max_workers=args.max_workers) as ex:
            futs = [ex.submit(task, s) for s in sites]
            for f in as_completed(futs):
                results.append(f.result())

    # Sort for stable output
    results.sort(key=lambda d: d["site_name"].lower())

    # Basic stats to stderr
    have = sum(1 for r in results if r["latitude"] is not None and r["longitude"] is not None)
    unauth = sum(1 for r in results if r["error"] and r["error"].startswith("unauthorized"))
    sys.stderr.write(f"Sites: {len(results)} | with coords: {have} | unauthorized: {unauth}\n")

    # 3) write output
    if args.output == "-":
        outfh = sys.stdout
        close = False
    else:
        outfh = open(args.output, "w", newline="", encoding="utf-8")
        close = True

    try:
        if args.format == "json":
            json.dump(results, outfh, indent=2, ensure_ascii=False)
            if outfh is sys.stdout:
                print()
        else:
            fieldnames = ["site_name","roc","country","certification_status","production_infrastructure","latitude","longitude","error"]
            w = csv.DictWriter(outfh, fieldnames=fieldnames)
            w.writeheader()
            for row in results:
                w.writerow(row)
    finally:
        if close:
            outfh.close()

if __name__ == "__main__":
    import os
    sys.exit(main())
