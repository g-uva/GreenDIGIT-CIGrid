import os
import json
import requests
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, HTTPException, Body
from pydantic import BaseModel
from typing import Optional, Dict, Any
from pymongo import MongoClient

app = FastAPI()

WATTPRINT_BASE = os.getenv("WATTPRINT_BASE", "https://api.wattprint.eu")
WATTPRINT_TOKEN = os.getenv("WATTPRINT_TOKEN")

RETAIN_MONGO_URI = os.getenv("RETAIN_MONGO_URI")
RETAIN_DB_NAME   = os.getenv("RETAIN_DB_NAME", "ci-retainment-db")
RETAIN_COLL      = os.getenv("RETAIN_COLL", "pending_ci")

CNR_SQL_FORWARD_URL = os.getenv("CNR_SQL_FORWARD_URL", "http://sql-cnr-adapter:8033/cnr-sql-service")
PUE_DEFAULT = os.getenv("PUE_DEFAULT", "1.7")

SITES_PATH = os.environ.get("SITES_JSON", "/data/sites_latlngpue.json")  # volume mount
SITES_MAP: dict[str, dict] = {}  # site_name -> {lat, lon, pue}

sess = requests.Session()

def to_iso_z(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc).replace(microsecond=0)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

def wp_headers() -> Dict[str, str]:
    if not WATTPRINT_TOKEN:
        raise RuntimeError("WATTPRINT_TOKEN not set")
    return {"Accept": "application/json", "Authorization": f"Bearer {WATTPRINT_TOKEN}"}

def wattprint_fetch(lat: float, lon: float, start: datetime, end: datetime, aggregate=True) -> Dict[str, Any]:
    url = f"{WATTPRINT_BASE}/v1/footprints"
    params = {
        "lat": lat,
        "lon": lon,
        "footprint_type": "carbon",
        "start": to_iso_z(start),
        "end": to_iso_z(end),
        "aggregate": str(aggregate).lower(),
    }
    headers = wp_headers()
    print("[wattprint_fetch] URL:", url, "params:", params, flush=True)
    r = sess.get(url, params=params, headers=headers, timeout=20)
    if not r.ok:
        print("[wattprint_fetch] status:", r.status_code, "body:", r.text[:300], flush=True)
    r.raise_for_status()
    data = r.json()
    if isinstance(data, list):
        if not data:
            raise HTTPException(status_code=502, detail="Wattprint returned empty list")
        return data[0]
    return data

class CIRequest(BaseModel):
    lat: float
    lon: float
    pue: Optional[float] = 1.4
    energy_wh: Optional[float] = None
    time: Optional[datetime] = None
    metric_id: Optional[str] = None

class CIResponse(BaseModel):
    source: str
    zone: Optional[str]
    datetime: Optional[str]
    ci_gco2_per_kwh: float
    pue: float
    effective_ci_gco2_per_kwh: float
    cfp_g: Optional[float]
    cfp_kg: Optional[float]
    valid: bool
    
class MetricsEnvelope(BaseModel):
    # top-level convenience fields
    site: Optional[str] = None
#     ts: Optional[datetime] = None
    duration_s: Optional[int] = None

    # original document parts (kept as free-form dicts to avoid tight coupling)
    sites: Dict[str, Any]
    fact_site_event: Dict[str, Any]
    detail_cloud: Dict[str, Any]

    # for CI request (must be present or resolvable)
    lat: Optional[float] = None
    lon: Optional[float] = None

    # optional inputs to CI calculation
    pue: Optional[float] = None
    energy_wh: Optional[float] = None
    
def _load_sites_map() -> dict:
    """Load array JSON into a dict keyed by site_name."""
    with open(SITES_PATH, "r", encoding="utf-8") as f:
        arr = json.load(f)
    m = {}
    for x in arr:
        name = x.get("site_name")
        lat, lon = x.get("latitude"), x.get("longitude")
        if name and lat is not None and lon is not None:
            m[name] = {
                "lat": float(lat),
                "lon": float(lon),
                "pue": float(x.get("pue", float(PUE_DEFAULT))),
            }
    return m

# load once at startup
try:
    SITES_MAP = _load_sites_map()
    print(f"[sites] Loaded sites into the SITES_MAP variable.")
except Exception as e:
    print(f"[sites] failed to load {SITES_PATH}: {e}", flush=True)
    SITES_MAP = {}

def maybe_retain_invalid(ci_payload: Dict[str, Any], req: CIRequest, start: datetime, end: datetime):
    if not RETAIN_MONGO_URI:
        return
    try:
        cli = MongoClient(RETAIN_MONGO_URI, appname="ci-calc-get-ci", serverSelectionTimeoutMS=3000)
        coll = cli[RETAIN_DB_NAME][RETAIN_COLL]
        coll.insert_one({
            "metric_id": req.metric_id,
            "provider": "wattprint",
            "creation_time": datetime.now(timezone.utc),
            "request_time": [start, end],
            "lat": req.lat,
            "lon": req.lon,
            "pue": req.pue,
            "energy_wh": req.energy_wh,
            "raw_response": ci_payload,
            "valid": bool(ci_payload.get("valid", False)),
        })
    except Exception as e:
        print("[retain] insert failed:", e, flush=True)

@app.post("/get-ci", response_model=CIResponse)
def get_ci(req: CIRequest):
    when  = req.time or datetime.now(timezone.utc)
    start = when - timedelta(hours=1)
    end   = when + timedelta(hours=2)
    try:
        payload = wattprint_fetch(req.lat, req.lon, start, end, aggregate=True)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Wattprint error: {e}")
    ci = float(payload["value"])
    dt_str = payload.get("end") or payload.get("start")
    eff = ci * float(req.pue)
    cfp_g = eff * req.energy_wh if req.energy_wh is not None else None
    cfp_kg = (cfp_g / 1000.0) if cfp_g is not None else None
    valid_flag = bool(payload.get("valid", False))
    if not valid_flag:
        maybe_retain_invalid(payload, req, start, end)
    return CIResponse(
        source="wattprint",
        zone=payload.get("zone"),
        datetime=dt_str,
        ci_gco2_per_kwh=ci,
        pue=float(req.pue),
        effective_ci_gco2_per_kwh=eff,
        cfp_g=cfp_g,
        cfp_kg=cfp_kg,
        valid=valid_flag,
    )

@app.post("/ci-valid", response_model=CIResponse)
def compute_ci_valid(req: CIRequest):
    when  = req.time or datetime.now(timezone.utc)
    start = when - timedelta(hours=1)
    end   = when + timedelta(hours=2)
    try:
        payload = wattprint_fetch(req.lat, req.lon, start, end, aggregate=True)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Wattprint error: {e}")
    ci = float(payload["value"])
    dt_str = payload.get("end") or payload.get("start")
    eff = ci * float(req.pue)
    cfp_g = eff * req.energy_wh if req.energy_wh is not None else None
    cfp_kg = (cfp_g / 1000.0) if cfp_g is not None else None
    return CIResponse(
        source="wattprint",
        zone=payload.get("zone"),
        datetime=dt_str,
        ci_gco2_per_kwh=ci,
        pue=float(req.pue),
        effective_ci_gco2_per_kwh=eff,
        cfp_g=cfp_g,
        cfp_kg=cfp_kg,
        valid=bool(payload.get("valid", False)),
    )

def _infer_times(payload: MetricsEnvelope) -> tuple[datetime, datetime, datetime]:
    """Return (start_exec, stop_exec, when_for_ci)."""
    fse = payload.fact_site_event
    # parse exec window
    start = datetime.fromisoformat(fse["startexectime"].replace("Z", "+00:00"))
    stop  = datetime.fromisoformat(fse["stopexectime"].replace("Z", "+00:00"))
#     # CI 'when' – prefer top-level ts, else event_end_timestamp, else stop time
#     if payload.ts:
#         when = payload.ts
    if "event_end_timestamp" in fse:
        when = datetime.fromisoformat(fse["event_end_timestamp"].replace("Z", "+00:00"))
    else:
        when = stop
    # normalise to UTC and strip microseconds
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (start, stop, when.astimezone(timezone.utc).replace(microsecond=0))

@app.post("/transform-and-forward")
def transform_and_forward(payload: MetricsEnvelope = Body(...)):
    site_name = payload.site or payload.fact_site_event.get("site")
    if (payload.lat is None or payload.lon is None) and site_name:
        site = SITES_MAP.get(site_name)
        if not site:
            try:
                SITES_MAP.update(_load_sites_map())
                site = SITES_MAP.get(site_name)
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Failed to reload sites: {e}")
        if not site:
            raise HTTPException(status_code=400, detail=f"No mapping for site '{site_name}' in {SITES_PATH}")
        payload.lat = site["lat"]
        payload.lon = site["lon"]
        # prefer PUE from mapping unless already provided
        if payload.fact_site_event.get("PUE") is None and payload.__dict__.get("pue") is None:
            pass

    if payload.lat is None or payload.lon is None:
        raise HTTPException(status_code=400, detail="lat and lon are required or must be resolvable from 'site'")

    start_exec, stop_exec, when = _infer_times(payload)
    if payload.duration_s is None:
        payload.duration_s = int((stop_exec - start_exec).total_seconds())
        
    fse = payload.fact_site_event
    e_wh = payload.energy_wh if payload.energy_wh is not None else fse.get("energy_wh")
    if e_wh is not None:
        try:
            e_wh = float(e_wh)
        except Exception:
            e_wh = None

    # fallback derivations (optional)
    if e_wh is None and fse.get("energy_kwh") is not None:
        e_wh = float(fse["energy_kwh"]) * 1000.0
    if e_wh is None and fse.get("power_w") is not None and payload.duration_s is not None:
        # power (W) * duration (s) -> Joules / 3600 -> Wh
        e_wh = float(fse["power_w"]) * float(payload.duration_s) / 3600.0
    
    payload.energy_wh = e_wh
    if e_wh is not None:
        fse["energy_wh"] = e_wh

    site_pue = None
    site_name = payload.site or payload.fact_site_event.get("site")
    if site_name and site_name in SITES_MAP:
        site_pue = float(SITES_MAP[site_name].get("pue", float(PUE_DEFAULT)))

    ci_req = CIRequest(
        lat=payload.lat,
        lon=payload.lon,
        pue=float(payload.fact_site_event.get("PUE") or site_pue or PUE_DEFAULT),
        energy_wh=payload.energy_wh,
        time=when,
        metric_id=str(payload.detail_cloud.get("event_id", "")) or payload.detail_cloud.get("execunitid"),
    )

    start = when - timedelta(hours=1)
    end   = when + timedelta(hours=2)
    try:
        wp = wattprint_fetch(ci_req.lat, ci_req.lon, start, end, aggregate=True)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Wattprint error: {e}")

    ci = float(wp["value"])
    eff_ci = ci * float(ci_req.pue)
    
    energy_kwh = (ci_req.energy_wh / 1000.0) if ci_req.energy_wh is not None else None
    cfp_g = eff_ci * energy_kwh if energy_kwh is not None else None
    
    print(f"[ci] ci={ci} pue={ci_req.pue} eff_ci={eff_ci} "
      f"energy_wh={ci_req.energy_wh} energy_kwh={(ci_req.energy_wh/1000.0) if ci_req.energy_wh else None} "
      f"-> cfp_g={cfp_g}", flush=True)

    fse = payload.fact_site_event
    
    fse["PUE"]  = float(ci_req.pue)
    fse["CI_g"] = ci
    if cfp_g is not None:
        fse["CFP_g"] = cfp_g

    try:
        r = sess.post(CNR_SQL_FORWARD_URL, json=payload.dict(), timeout=20)
        if not r.ok:
            print("[forward] status:", r.status_code, "body:", r.text[:300], flush=True)
        r.raise_for_status()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Forwarding to CNR SQL service failed: {e}")

    return {"status": "ok", "forwarded_to": CNR_SQL_FORWARD_URL}