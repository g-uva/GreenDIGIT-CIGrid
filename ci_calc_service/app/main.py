import os
import json
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List

import requests
from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from urllib.parse import urlencode
from pymongo import MongoClient

# -----------------------------
# Config
# -----------------------------
ROOT_PATH = os.environ.get("ROOT_PATH", "")  # e.g., "/gd-ci-service"

# Provider selection
CI_PROVIDER = os.environ.get("CI_PROVIDER", "wattprint").lower()  # "wattprint" or "electricitymaps"

# Wattprint
WATTPRINT_BASE = os.environ.get("WATTPRINT_BASE", "https://api.wattprint.eu")
WATTPRINT_PATH = "/v1/footprints"
WATTPRINT_TOKEN = os.environ.get("WATTPRINT_TOKEN")

# ElectricityMaps (optional)
EM_TOKEN = os.environ.get("ELECTRICITYMAPS_TOKEN")
ELECTRICITYMAPS_API_LATEST = "https://api.electricitymaps.com/v3/carbon-intensity/latest"
ELECTRICITYMAPS_API_FORECAST = "https://api.electricitymaps.com/v3/carbon-intensity/forecast"

# Retainment store
RETAIN_MONGO_URI = os.environ.get("RETAIN_MONGO_URI")  # e.g. mongodb://ci-retain-db:27017/?replicaSet=rs0
RETAIN_DB_NAME = os.environ.get("RETAIN_DB_NAME", "ci-retainment-db")
RETAIN_COLL = os.environ.get("RETAIN_COLL", "pending_ci")
RETAIN_TTL_SECONDS = int(os.environ.get("RETAIN_TTL_SECONDS", "172800"))  # 2 days

# Sites JSON for /load-sites (optional; used by publisher to map nodes->sites)
SITES_JSON = os.environ.get("SITES_JSON", "/data/sites_latlngpue.json")

# Auth verification for /ci and /rank-sites (optional)
AUTH_VERIFY_URL = os.environ.get("AUTH_VERIFY_URL")  # e.g. http://login-server:8001/verify

# Defaults/timeouts
PUE_DEFAULT = float(os.environ.get("PUE_DEFAULT", "1.4"))
TIMEOUT = int(os.environ.get("TIMEOUT", "20"))
RETRIES = int(os.environ.get("RETRIES", "2"))

# -----------------------------
# App
# -----------------------------
app = FastAPI(
    title="GreenDIGIT CI Calculator",
    description="Computes carbon intensity using Wattprint or ElectricityMaps with retain-until-valid behaviour.",
    version="1.0.0",
    root_path=ROOT_PATH
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -----------------------------
# Models
# -----------------------------
class CIRequest(BaseModel):
    lat: float
    lon: float
    pue: float = Field(default=PUE_DEFAULT, description="Power Usage Effectiveness")
    energy_kwh: Optional[float] = Field(default=None, description="If provided, CFP is computed")
    time: Optional[datetime] = Field(default=None, description="UTC timestamp; if omitted provider will use latest")

class CIResponse(BaseModel):
    source: str
    zone: Optional[str] = None
    datetime: str
    ci_gco2_per_kwh: float
    pue: float
    effective_ci_gco2_per_kwh: float
    cfp_g: Optional[float] = None
    cfp_kg: Optional[float] = None

class ExternalSubmissionResponse(BaseModel):
    body: str

# -----------------------------
# Auth
# -----------------------------
def require_bearer(req: Request):
    """Simple bearer-token gate. If AUTH_VERIFY_URL is set, call it to validate token; else require presence only."""
    auth = req.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    token = auth[len("Bearer "):].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if AUTH_VERIFY_URL:
        try:
            vr = requests.get(AUTH_VERIFY_URL, headers={"Authorization": f"Bearer {token}"}, timeout=TIMEOUT)
            if vr.status_code != 200:
                raise HTTPException(status_code=401, detail="Unauthorized")
        except Exception:
            raise HTTPException(status_code=401, detail="Unauthorized")
    return True

# -----------------------------
# Helpers
# -----------------------------
def em_headers() -> Dict[str, str]:
    if not EM_TOKEN:
        raise RuntimeError("ELECTRICITYMAPS_TOKEN not set")
    return {"auth-token": EM_TOKEN}

def fetch_ci_latest(lat: float, lon: float) -> Dict[str, Any]:
    params = {"lat": lat, "lon": lon}
    last: Optional[Exception] = None
    for _ in range(RETRIES + 1):
        try:
            r = requests.get(ELECTRICITYMAPS_API_LATEST, headers=em_headers(), params=params, timeout=TIMEOUT)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last = e
    assert last is not None
    raise last

def fetch_ci_forecast(lat: float, lon: float) -> List[Dict[str, Any]]:
    params = {"lat": lat, "lon": lon}
    last: Optional[Exception] = None
    for _ in range(RETRIES + 1):
        try:
            r = requests.get(ELECTRICITYMAPS_API_FORECAST, headers=em_headers(), params=params, timeout=TIMEOUT)
            r.raise_for_status()
            payload = r.json()
            return payload.get("forecast", [])
        except Exception as e:
            last = e
    assert last is not None
    raise last

def wp_headers() -> Dict[str, str]:
    if not WATTPRINT_TOKEN:
        raise RuntimeError("WATTPRINT_COOKIE not set")
    return {"Accept": "application/json", "Authorization": f"Bearer {WATTPRINT_TOKEN}"}

def wattprint_fetch(lat: float, lon: float, start: datetime, end: datetime, aggregate: bool = True) -> Dict[str, Any]:
    qs = {
        "lat": f"{lat:.4f}",
        "lon": f"{lon:.4f}",
        "footprint_type": "carbon",
        "start": start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "end": end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "aggregate": str(aggregate).lower(),
    }
    url = f"{WATTPRINT_BASE}{WATTPRINT_PATH}?{urlencode(qs)}"
    last: Optional[Exception] = None
    for _ in range(RETRIES + 1):
        try:
            r = requests.get(url, headers=wp_headers(), timeout=TIMEOUT)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last = e
    assert last is not None
    raise last

def get_retain_collection():
    if not RETAIN_MONGO_URI:
        return None
    client = MongoClient(RETAIN_MONGO_URI, appname="ci-retain")
    coll = client[RETAIN_DB_NAME][RETAIN_COLL]
    try:
        coll.create_index("creation_time", expireAfterSeconds=RETAIN_TTL_SECONDS, name="ttl_creation_time")
        coll.create_index([("lat", 1), ("lon", 1), ("request_time", -1)], name="latlon_reqtime")
        coll.create_index("valid", name="valid_flag")
    except Exception:
        pass
    return coll

def compute_cfp(eff_value: float, energy_kwh: Optional[float]):
    if energy_kwh is None:
        return None, None
    cfp_g = eff_value * energy_kwh
    return cfp_g, cfp_g / 1000.0

def wp_pick(payload):
    """Accept list or dict; normalise to dict."""
    if isinstance(payload, list):
        if not payload:
            raise HTTPException(status_code=502, detail="Wattprint returned empty list")
        return payload[0]
    return payload


# -----------------------------
# Endpoints
# -----------------------------
@app.get("/healthz")
def healthz():
    return {"ok": True, "provider": CI_PROVIDER}

@app.get("/load-sites", summary="Return sites JSON if present")
def load_sites():
    try:
        with open(SITES_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load sites: {e}")

@app.post("/ci", response_model=CIResponse)
def compute_ci(req: CIRequest, _=Depends(require_bearer)):
    # Provider: Wattprint
    if CI_PROVIDER == "wattprint":
        when = req.time or datetime.now(timezone.utc)
        start = when - timedelta(hours=1)
        end = when + timedelta(hours=2)
        try:
            payload = wattprint_fetch(req.lat, req.lon, start, end, aggregate=True)
            payload = wp_pick(payload) # normalise for the [] map.
            valid = bool(payload.get("valid", False))
            valid = bool(payload.get("valid", False))
            if valid:
                ci = float(payload["value"])  # gCO2/kWh
                dt_str = payload.get("end") or payload.get("start")
                zone = payload.get("zone")
                eff = ci * req.pue
                cfp_g, cfp_kg = compute_cfp(eff, req.energy_kwh)
                return CIResponse(
                    source="wattprint",
                    zone=zone,
                    datetime=dt_str,
                    ci_gco2_per_kwh=ci,
                    pue=req.pue,
                    effective_ci_gco2_per_kwh=eff,
                    cfp_g=cfp_g,
                    cfp_kg=cfp_kg,
                )
            else:
                coll = get_retain_collection()
                if coll is None:
                    raise HTTPException(status_code=503, detail="CI invalid and retainment store not configured")
                coll.insert_one({
                    "provider":"wattprint",
                    "creation_time": datetime.now(timezone.utc),
                    "request_time": [start, end],
                    "lat": req.lat, "lon": req.lon, "pue": req.pue,
                    "energy_kwh": req.energy_kwh,
                    "raw_response": payload,
                    "valid": False,
                })
                raise HTTPException(status_code=202, detail="CI invalid; retained for re-check within TTL")
        except HTTPException:
            raise
        except Exception as e:
            if EM_TOKEN:
                try:
                    latest = fetch_ci_latest(req.lat, req.lon)
                    ci = float(latest["carbonIntensity"])
                    eff = ci * req.pue
                    cfp_g, cfp_kg = compute_cfp(eff, req.energy_kwh)
                    return CIResponse(
                        source="electricitymaps/latest",
                        zone=latest.get("zone"),
                        datetime=latest["datetime"],
                        ci_gco2_per_kwh=ci,
                        pue=req.pue,
                        effective_ci_gco2_per_kwh=eff,
                        cfp_g=cfp_g,
                        cfp_kg=cfp_kg,
                    )
                except Exception:
                    pass
            raise HTTPException(status_code=502, detail=f"Wattprint error: {e}")

    # Provider: ElectricityMaps
    when = req.time
    if when is None:
        latest = fetch_ci_latest(req.lat, req.lon)
        ci = float(latest["carbonIntensity"])
        eff = ci * req.pue
        cfp_g, cfp_kg = compute_cfp(eff, req.energy_kwh)
        return CIResponse(
            source="electricitymaps/latest",
            zone=latest.get("zone"),
            datetime=latest["datetime"],
            ci_gco2_per_kwh=ci,
            pue=req.pue,
            effective_ci_gco2_per_kwh=eff,
            cfp_g=cfp_g,
            cfp_kg=cfp_kg,
        )
    else:
        fc = fetch_ci_forecast(req.lat, req.lon)
        target = when.replace(minute=0, second=0, microsecond=0, tzinfo=timezone.utc)
        best = None
        best_dt = None
        for item in fc:
            dt = datetime.fromisoformat(item["datetime"].replace("Z", "+00:00"))
            if best is None or abs((dt - target).total_seconds()) < abs((best_dt - target).total_seconds()):
                best = item
                best_dt = dt
        if best is None:
            raise HTTPException(status_code=502, detail="No forecast data from ElectricityMaps")
        ci = float(best["carbonIntensity"])
        eff = ci * req.pue
        cfp_g, cfp_kg = compute_cfp(eff, req.energy_kwh)
        return CIResponse(
            source="electricitymaps/forecast",
            zone=best.get("zone"),
            datetime=best["datetime"],
            ci_gco2_per_kwh=ci,
            pue=req.pue,
            effective_ci_gco2_per_kwh=eff,
            cfp_g=cfp_g,
            cfp_kg=cfp_kg,
        )