# 🌱🌍♻️ GreenDIGIT CI Grid — CI→CFP Microservice
Microservice to compute **Carbon Intensity (CI)**, **Effective CI**, and **Carbon Footprint (CFP)**. Includes ranking of sites by lowest Effective CI.

### Roadmap
- Per-job CFP: wire in your workload energy estimates (kWh) to output absolute grams CO₂e per job/window.
- Dynamic PUE: add a simple table (per site) to override the static 1.4 when you obtain better values; later, call a PUE data source when available.
- Fallback provider: optionally add providers like WattTime or UK’s Carbon Intensity API for regions where Electricity Maps isn’t available. 
- API hygiene: respect rate limits with a small backoff; cache per-site responses for 10–15 minutes to avoid redundant calls.

## How calculation works
- Effective CI (gCO₂e/kWh): CI_eff = CI × PUE
- CFP (gCO₂e): CFP = CI_eff × E (E = energy consumption in kWh)

## Folder structure
```text
greendigit-cigrid/
├─ ci_calc_service/
│  ├─ app/
│  │  ├─ main.py
│  │  └─ requirements.txt
│  ├─ Dockerfile
│  └─ .env.example
├─ data/
│  └─ sites_enriched.json
├─ scripts/
│  ├─ start_ci_docker.sh
│  ├─ gen_jwt_secret.sh
│  └─ smoke_test.sh
└─ README.md
```

## Scripts
- **start_ci_docker.sh** → Build & run CI service on port 8011.
- **gen_jwt_secret.sh** → Generate JWT_TOKEN in `.env` if missing.
- **smoke_test.sh** → Simple curl tests for health and /ci.

## Example request
```json
{
  "lat": 52.0,
  "lon": 5.0,
  "time": "2025-09-04T12:00:00Z",
  "pue": 1.4,
  "use_mock": true,
  "energy_kwh": 3.0
}
```

Returns CI, Effective CI, and optional CFP in g and kg.
