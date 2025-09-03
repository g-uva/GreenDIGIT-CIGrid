#!/usr/bin/env python3
"""
gocdb_postprocess.py
Adds mock lat/lng and static PUE to GOC DB site JSON.
"""

import json
import sys

SITE_COORDS = {
    "ALBERTA-LCG2": (53.5461, -113.4938),  # Edmonton, Canada
    "CERN-PROD": (46.2331, 6.0559),        # CERN, Geneva
    "INFN-ROMA1": (41.9028, 12.4964),      # Rome, Italy
    "NIKHEF-ELPROD": (52.3556, 4.9500),    # Amsterdam, NL
    "RAL-LCG2": (51.5714, -1.3080),        # RAL, UK
    "AEGIS01-IPB-SCL": (44.8176, 20.4569),  # Belgrade, Serbia
    "ALICE-IN2P3": (45.7840, 4.8700),       # Lyon, France
    "BEIJING-LCG2": (39.9042, 116.4074),    # Beijing, China
    "BUDAPEST-LCG2": (47.4979, 19.0402),    # Budapest, Hungary
    "DESY-HH": (53.5763, 9.8810),           # Hamburg, Germany
    "DESY-ZN": (52.3906, 13.0669),          # Zeuthen, Germany
    "FZK-LCG2": (49.0950, 8.4310),          # Karlsruhe, Germany
    "GRIF-LPNHE": (48.8462, 2.3460),        # Paris, France
    "IN2P3-CC": (45.7840, 4.8700),          # Lyon, France (CC-IN2P3)
    "IN2P3-CPPM": (43.2965, 5.3698),        # Marseille, France
    "INFN-CNAF": (44.4949, 11.3426),        # Bologna, Italy
    "INFN-FRASCATI": (41.8089, 12.6761),    # Frascati, Italy
    "INFN-MILANO": (45.4642, 9.1900),       # Milan, Italy
    "INFN-NAPOLI": (40.8522, 14.2681),      # Naples, Italy
    "INFN-PISA": (43.7160, 10.4000),        # Pisa, Italy
    "INFN-TORINO": (45.0703, 7.6869),       # Turin, Italy
    "KR-KISTI-GSDC-01": (36.3913, 127.3620),# Daejeon, South Korea
    "PIC": (41.3851, 2.1734),               # Barcelona, Spain
    "PRAGUE-FZU": (50.0755, 14.4378),       # Prague, Czech Republic
    "RU-JINR-LCG2": (55.7050, 37.6639),     # Dubna, Russia (near Moscow)
    "TRIUMF-LCG2": (49.2463, -123.1162),    # Vancouver, Canada
    "TU-Kosice": (48.7164, 21.2611),        # Košice, Slovakia
    "US-ATLAS-AGLT2": (42.2808, -83.7430),  # Ann Arbor, Michigan, USA
    "US-ATLAS-MWT2": (41.8781, -87.6298),   # Chicago, Illinois, USA
    "US-ATLAS-NERSC": (37.8732, -122.2573), # Berkeley, California, USA
    "US-ATLAS-SWT2": (29.7604, -95.3698),   # Houston, Texas, USA
    "USCMS-FNAL-WC1": (41.8419, -88.2415),  # Fermilab, Illinois, USA
}

STATIC_PUE = 1.4

def main(infile, outfile):
    with open(infile, "r", encoding="utf-8") as f:
        data = json.load(f)

    for site in data:
        name = site.get("site_name")
        if name in SITE_COORDS:
            lat, lon = SITE_COORDS[name]
        else:
            lat, lon = (52.0, 5.0)  # fallback (NL)
        site["latitude"] = lat
        site["longitude"] = lon
        site["pue"] = STATIC_PUE

    with open(outfile, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python gocdb_postprocess.py input.json output.json")
        sys.exit(1)
    main(sys.argv[1], sys.argv[2])
