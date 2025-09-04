#!/usr/bin/env python3
import os, random, json
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Dict, Any
import requests
from dateutil import parser as dtparse

from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field

# --------------------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------------------
ELECTRICITYMAPS_API_FORECAST = "https://api.electricitymap.org/v3/carbon-intensity/forecast"
ELECTRICITYMAPS_API_LATEST   = "https://api.electricitymap.org/v3/carbon-intensity/latest"
DEFAULT_PUE = float(os.environ.get("PUE_DEFAULT", "1.4"))
EM_TIMEOUT  = 20
RETRIES     = 2

EM_TOKEN       = os.environ.get("ELECTRICITYMAPS_TOKEN")  # may be empty on purpose for mock mode
SITES_JSON     = os.environ.get("SITES_JSON")  # optional: path to a JSON array of sites

# --------------------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------------------
security = HTTPBearer()
AUTH_VERIFY_URL = os.environ.get("AUTH_VERIFY_URL", "https://mc-a4.lab.uvalight.net/gd-cim-api/verify_token")

def require_bearer(creds: HTTPAuthorizationCredentials = Depends(security)):
    token = creds.credentials
    try:
        r = requests.get(AUTH_VERIFY_URL, headers={"Authorization": f"Bearer {token}"}, timeout=5)
        if r.status_code != 200:
            raise HTTPException(status_code=401, detail="Unauthorized")
        # optionally: email = r.json().get("sub")
        return True
    except Exception:
        raise HTTPException(status_code=401, detail="Unauthorized")

# --------------------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------------------
class CIRequest(BaseModel):
    lat: float
    lon: float
    time: Optional[datetime] = Field(None, description="UTC timepoint. If omitted, uses latest.")
    pue: float = DEFAULT_PUE
    use_mock: bool = False
    energy_kwh: Optional[float] = None

class CIResponse(BaseModel):
    source: str
    zone: Optional[str] = None
    datetime: str
    ci_gco2_per_kwh: float
    pue: float
    effective_ci_gco2_per_kwh: float
    cfp_g: Optional[float] = None
    cfp_kg: Optional[float] = None

class Site(BaseModel):
    site_name: str
    lat: float
    lon: float
    pue: Optional[float] = None

class RankRequest(BaseModel):
    start_time: Optional[datetime] = None  # if omitted, uses now (rounded to hour)
    sites: Optional[List[Site]] = None     # if omitted, will load from SITES_JSON (if set)
    use_mock: bool = False
    pue_default: float = DEFAULT_PUE
    energy_kwh: Optional[float] = None

class RankedSite(BaseModel):
    site_name: str
    lat: float
    lon: float
    zone: Optional[str] = None
    datetime: str
    ci_gco2_per_kwh: float
    pue: float
    effective_ci_gco2_per_kwh: float

class RankResponse(BaseModel):
    start_time: str
    results: List[RankedSite]

# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------
def em_headers() -> Dict[str, str]:
    if not EM_TOKEN:
        raise RuntimeError("ELECTRICITYMAPS_TOKEN not set (required unless use_mock=True)")
    return {"auth-token": EM_TOKEN}

def round_to_hour(dt: datetime) -> datetime:
    dt = dt.astimezone(timezone.utc)
    return dt.replace(minute=0, second=0, microsecond=0)

def fetch_ci_latest(lat: float, lon: float) -> Dict[str, Any]:
    params = {"lat": lat, "lon": lon}
    last = None
    for _ in range(RETRIES + 1):
        try:
            r = requests.get(ELECTRICITYMAPS_API_LATEST, headers=em_headers(), params=params, timeout=EM_TIMEOUT)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last = e
    raise last

def fetch_ci_forecast(lat: float, lon: float) -> List[Dict[str, Any]]:
    params = {"lat": lat, "lon": lon}
    last = None
    for _ in range(RETRIES + 1):
        try:
            r = requests.get(ELECTRICITYMAPS_API_FORECAST, headers=em_headers(), params=params, timeout=EM_TIMEOUT)
            r.raise_for_status()
            payload = r.json()
            return payload.get("forecast", [])
        except Exception as e:
            last = e
    raise last

def pick_ci_at_time(forecast: List[Dict[str, Any]], when_utc: datetime) -> Dict[str, Any]:
    # Choose the entry matching the requested hour; fall back to nearest hour in forecast
    when_utc = round_to_hour(when_utc)
    if not forecast:
        raise ValueError("Empty forecast")
    # exact match first
    for p in forecast:
        if dtparse.parse(p["datetime"]).astimezone(timezone.utc) == when_utc:
            return p
    # nearest hour
    best = min(forecast, key=lambda p: abs((dtparse.parse(p["datetime"]).astimezone(timezone.utc) - when_utc).total_seconds()))
    return best

def mock_ci_value() -> int:
    return random.randint(150, 600)  # gCO2/kWh

# --------------------------------------------------------------------------------------
# FastAPI app
# --------------------------------------------------------------------------------------
app = FastAPI(
    title="GreenDIGIT WP6.2 Authentication Server API",
    version="1.0.0",
    swagger_ui_parameters={"persistAuthorization": True},
    root_path="/gd-ci-service"
)

@app.get("/health")
def health():
    return {"ok": True, "time": datetime.now(timezone.utc).isoformat()}

@app.post("/ci", response_model=CIResponse)
def compute_ci(req: CIRequest, _=Depends(require_bearer)):
    if req.use_mock:
        when = round_to_hour(req.time or datetime.now(timezone.utc))
        ci = float(mock_ci_value())
        eff = ci * req.pue
        cfp_g = cfp_kg = None
        if req.energy_kwh is not None:
            cfp_g = eff * req.energy_kwh
            cfp_kg = cfp_g / 1000.0
        return CIResponse(source="mock", zone=None, datetime=when.isoformat(),
                          ci_gco2_per_kwh=ci, pue=req.pue, effective_ci_gco2_per_kwh=eff, cfp_g=cfp_g, cfp_kg=cfp_kg)
        
    def compute_cfp(energy_kwh: float = None):
        cfp_g = cfp_kg = None
        if energy_kwh is not None:
            cfp_g = eff * req.energy_kwh
            cfp_kg = cfp_g / 1000.0
        return cfp_g, cfp_kg

    if req.time:
        fc = fetch_ci_forecast(req.lat, req.lon)
        chosen = pick_ci_at_time(fc, req.time)
        ci = float(chosen["carbonIntensity"])
        eff = ci * req.pue
        cfp_g, cfp_kg = compute_cfp(req.energy_kwh)
        return CIResponse(source="electricitymaps", zone=None, datetime=chosen["datetime"],
                          ci_gco2_per_kwh=ci, pue=req.pue, effective_ci_gco2_per_kwh=eff, cfp_g=cfp_g, cfp_kg=cfp_kg)
    else:
        latest = fetch_ci_latest(req.lat, req.lon)
        ci = float(latest["carbonIntensity"])
        eff = ci * req.pue
        cfp_g, cfp_kg = compute_cfp(req.energy_kwh)
        return CIResponse(source="electricitymaps", zone=latest.get("zone"), datetime=latest["datetime"],
                          ci_gco2_per_kwh=ci, pue=req.pue, effective_ci_gco2_per_kwh=eff, cfp_g=cfp_g, cfp_kg=cfp_kg)

def load_sites_from_env() -> List[Site]:
    if not SITES_JSON:
        raise HTTPException(status_code=400, detail="No sites provided and SITES_JSON env not set")
    with open(SITES_JSON, "r", encoding="utf-8") as f:
        raw = json.load(f)
    sites: List[Site] = []
    for s in raw:
        sites.append(Site(
            site_name=s.get("site_name") or s.get("name") or "unknown",
            lat=float(s["latitude"]),
            lon=float(s["longitude"]),
            pue=float(s.get("pue") or DEFAULT_PUE),
        ))
    return sites

@app.post("/rank-sites", response_model=RankResponse)
def rank_sites(req: RankRequest, _=Depends(require_bearer)):
    when = round_to_hour(req.start_time or datetime.now(timezone.utc))
    sites = req.sites or load_sites_from_env()

    results: List[RankedSite] = []
    for s in sites:
        pue = float(s.pue or req.pue_default)
        if req.use_mock:
            ci = float(mock_ci_value())
            zone = None
            dt_str = when.isoformat()
        else:
            try:
                fc = fetch_ci_forecast(s.lat, s.lon)
                chosen = pick_ci_at_time(fc, when)
                ci = float(chosen["carbonIntensity"])
                zone = None
                dt_str = chosen["datetime"]
            except Exception as e:
                # Return a “mock on failure” so pipeline still runs; comment this if you prefer hard failures
                ci = float(mock_ci_value())
                zone = None
                dt_str = when.isoformat()
        eff = ci * pue
        results.append(RankedSite(site_name=s.site_name, lat=s.lat, lon=s.lon, zone=zone,
                                  datetime=dt_str, ci_gco2_per_kwh=ci, pue=pue,
                                  effective_ci_gco2_per_kwh=eff))
    # sort by effective CI ascending (best first)
    results.sort(key=lambda r: r.effective_ci_gco2_per_kwh)
    return RankResponse(start_time=when.isoformat(), results=results)
