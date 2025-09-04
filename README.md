# 🌱🌍♻️ GreenDIGIT CI Grid — CI→CFP Microservice

FastAPI microservice that predicts **Carbon Intensity (CI)** per location/time and derives **Effective CI** and **Carbon Footprint (CFP)**. Includes a **ranking** endpoint to order sites by best (lowest) Effective CI at a given hour.

## Roadmap
- [ ] Apply ICTF X502 certification to get Location and PUE from sites (GOC DB).
- [ ] Implement dynamic calculation of the CI. For the moment we're using mock data (generated).

## How the calculation works

$$
\mathrm{CI}_{\mathrm{eff}} = \mathrm{CI} \times \mathrm{PUE}
$$

$$
\mathrm{CFP}\;[\mathrm{gCO_2e}] = \mathrm{CI}_{\mathrm{eff}}\;[\mathrm{gCO_2e/kWh}] \times E\;[\mathrm{kWh}]
$$

Also in kg: 
$$ 
\mathrm{CFP}_{kg} = \mathrm{CFP}/1000 
$$

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
└─ README.md
```

## Quick usage

### `/ci` — compute CI / Effective CI (and optional CFP)

**JSON example**
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

**Curl**
```bash
curl -s -X POST http://localhost:8011/ci   -H "Authorization: Bearer $TOKEN"   -H "Content-Type: application/json"   -d '{"lat":52.0,"lon":5.0,"time":"2025-09-04T12:00:00Z","pue":1.4,"use_mock":true,"energy_kwh":3.0}'
```

### `/rank-sites` — order sites by best Effective CI at a start time

**JSON example**
```json
{
  "start_time": "2025-09-04T12:00:00Z",
  "use_mock": true
}
```

**Curl**
```bash
curl -s -X POST http://localhost:8011/rank-sites   -H "Authorization: Bearer $TOKEN"   -H "Content-Type: application/json"   -d '{"start_time":"2025-09-04T12:00:00Z","use_mock":true}'
```

### Auth
All protected endpoints require:
```
Authorization: Bearer <jwt>
```

### Notes
- **Mock mode** (`use_mock: true`) generates random CI values (150–600 gCO₂e/kWh) for prototyping.
- PUE can be per-site (in `data/sites_enriched.json`) or default via `PUE_DEFAULT`.
- The service validates tokens by calling your auth server’s `/verify_token` (configure `AUTH_VERIFY_URL`).
