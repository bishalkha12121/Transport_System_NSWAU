import math
import os
import re
from datetime import datetime, timezone
from typing import Optional

from google.transit import gtfs_realtime_pb2

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()

API_KEY       = os.environ.get("TFNSW_API_KEY", "")
TFNSW         = "https://api.transport.nsw.gov.au/v1/tp"
SUPABASE_URL  = os.environ.get("SUPABASE_URL", "")
SUPABASE_ANON = os.environ.get("SUPABASE_ANON_KEY", "")

app = FastAPI()


def hdrs():
    return {"Authorization": f"apikey {API_KEY}", "Accept": "application/json"}


def to_hhmm(iso: str) -> str:
    return iso[11:16] if iso else ""


def mode_from_class(cls) -> str:
    return {1: "train", 2: "metro", 4: "lrt", 5: "bus", 9: "ferry"}.get(cls, "bus")


def extract_line_code(name: str) -> str:
    m = re.match(r"^([A-Z][0-9]+|[0-9]+[A-Z]?)", (name or "").upper())
    return m.group(1) if m else ""


def line_class(n: str) -> str:
    n = (n or "").upper()
    if n in {"T1", "T2", "T3", "T4", "T5", "T8", "T9"}:
        return n.lower()
    if re.match(r"^M\d", n):  return "metro"
    if re.match(r"^L\d", n):  return "lrt"
    if re.match(r"^F\d?", n): return "ferry"
    if re.match(r"^\d", n):   return "bus"
    return "train"


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6_371_000
    dlat = (lat2 - lat1) * math.pi / 180
    dlon = (lon2 - lon1) * math.pi / 180
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(lat1 * math.pi / 180) *
         math.cos(lat2 * math.pi / 180) *
         math.sin(dlon / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ── /api/config ───────────────────────────────────────────────────────────────
@app.get("/api/config")
async def config():
    return {"supabaseUrl": SUPABASE_URL, "supabaseAnonKey": SUPABASE_ANON}


# ── /api/health ───────────────────────────────────────────────────────────────
@app.get("/api/health")
async def health():
    key_set     = bool(API_KEY)
    key_preview = f"{API_KEY[:12]}…" if API_KEY else "NOT SET"
    tfnsw_status, tfnsw_body = None, ""
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                f"{TFNSW}/departure_mon",
                params={"outputFormat": "rapidJSON", "type_dm": "stop",
                        "name_dm": "10101100", "TfNSWDM": "true", "version": "10.2.1.42"},
                headers=hdrs(),
            )
            tfnsw_status = r.status_code
            tfnsw_body   = r.text[:500]
    except Exception as e:
        tfnsw_body = str(e)
    return {"keySet": key_set, "keyPreview": key_preview,
            "tfnswStatus": tfnsw_status, "tfnswBody": tfnsw_body}


# ── /api/stop ─────────────────────────────────────────────────────────────────
@app.get("/api/stop")
async def stop_finder(q: str = "Central Station"):
    params = {"outputFormat": "rapidJSON", "coordOutputFormat": "EPSG:4326",
              "type_sf": "any", "name_sf": q, "TfNSWSF": "true", "version": "10.2.1.42"}
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{TFNSW}/stop_finder", params=params, headers=hdrs())
        if r.status_code != 200:
            return JSONResponse({"error": f"TfNSW {r.status_code}"}, status_code=r.status_code)
        raw  = r.json()
        locs = [
            {
                "id":    l["id"],
                "name":  l.get("disassembledName") or l.get("name", ""),
                "type":  l.get("type"),
                "lat":   (l.get("coord") or [None, None])[0],
                "lon":   (l.get("coord") or [None, None])[1],
                "modes": [m for s in l.get("assignedStops", []) for m in (s.get("modes") or [])],
            }
            for l in (raw.get("locations") or [])
            if l.get("type") == "stop" and l.get("id") and not l["id"].startswith("G")
        ][:8]
        return {"locations": locs}
    except Exception as e:
        print("stop finder:", e)
        return JSONResponse({"error": "Stop finder failed"}, status_code=500)


# ── /api/departures ───────────────────────────────────────────────────────────
@app.get("/api/departures")
async def departures(stopId: str = "10101100", limit: int = 30):
    limit  = min(limit, 50)
    params = {"outputFormat": "rapidJSON", "coordOutputFormat": "EPSG:4326",
              "mode": "direct", "type_dm": "stop", "name_dm": stopId,
              "departureMonitorMacro": "true", "TfNSWDM": "true", "version": "10.2.1.42"}
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(f"{TFNSW}/departure_mon", params=params, headers=hdrs())
        if r.status_code != 200:
            print(f"departures TfNSW {r.status_code}:", r.text[:300])
            return JSONResponse({"error": f"TfNSW {r.status_code}"}, status_code=r.status_code)
        raw    = r.json()
        events = (raw.get("stopEvents") or [])[:limit]
        now_ms = datetime.now(timezone.utc).timestamp() * 1000

        result = []
        for e in events:
            tr       = e.get("transportation") or {}
            prod     = tr.get("product") or {}
            dest     = tr.get("destination") or {}
            planned  = e.get("departureTimePlanned") or ""
            realtime = e.get("departureTimeEstimated") or ""
            dep_iso  = realtime or planned

            dep_ms, delay = 0, 0
            if dep_iso:
                try:
                    dep_ms = datetime.fromisoformat(dep_iso).timestamp() * 1000
                except Exception:
                    pass
            if realtime and planned:
                try:
                    delay = round((datetime.fromisoformat(realtime) -
                                   datetime.fromisoformat(planned)).total_seconds() / 60)
                except Exception:
                    pass

            short_line = (tr.get("disassembledName") or
                          extract_line_code(prod.get("shortName") or tr.get("number") or ""))
            loc_props  = ((e.get("location") or {}).get("properties")) or {}
            result.append({
                "line":        short_line or "",
                "lineClass":   line_class(short_line or ""),
                "mode":        mode_from_class(prod.get("class")),
                "destination": dest.get("name") or "",
                "via":         tr.get("description") or "",
                "time":        to_hhmm(dep_iso),
                "minsAway":    round((dep_ms - now_ms) / 60000) if dep_ms else 0,
                "delay":       delay,
                "cancelled":   bool(e.get("isCancelled")),
                "platform":    loc_props.get("platformName") or "",
                "occupancy":   loc_props.get("occupancy"),
            })
        return {"stopId": stopId, "departures": result}
    except Exception as e:
        print("departures:", e)
        return JSONResponse({"error": "Departures failed"}, status_code=500)


# ── /api/nearby ───────────────────────────────────────────────────────────────
@app.get("/api/nearby")
async def nearby(lat: float = -33.8688, lon: float = 151.2093, radius: int = 2000):
    radius = min(radius, 5000)
    params = {"outputFormat": "rapidJSON", "coordOutputFormat": "EPSG:4326",
              "mode": "direct", "type_dm": "coord",
              "name_dm": f"{lon}:{lat}:EPSG:4326",
              "coordRadius": radius,
              "TfNSWDM": "true", "version": "10.2.1.42"}
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(f"{TFNSW}/departure_mon", params=params, headers=hdrs())
        if r.status_code != 200:
            return JSONResponse({"error": f"TfNSW {r.status_code}"}, status_code=r.status_code)
        raw  = r.json()
        seen = {}
        for e in (raw.get("stopEvents") or []):
            loc    = e.get("location") or {}
            parent = loc.get("parent") or loc
            sid    = parent.get("id") or ""
            if not sid:
                continue
            if sid not in seen:
                coord = parent.get("coord") or loc.get("coord") or [lat, lon]
                seen[sid] = {
                    "id":    sid,
                    "name":  (parent.get("disassembledName") or parent.get("name") or "").replace(", Sydney", ""),
                    "dist":  round(haversine(lat, lon, coord[0], coord[1])),
                    "mode":  mode_from_class((e.get("transportation") or {}).get("product", {}).get("class")),
                    "lines": [],
                    "lat":   coord[0],
                    "lon":   coord[1],
                }
            tr   = e.get("transportation") or {}
            code = tr.get("disassembledName") or extract_line_code(tr.get("number") or "")
            if code and code not in seen[sid]["lines"]:
                seen[sid]["lines"].append(code)
        stops = sorted(seen.values(), key=lambda s: s["dist"])[:12]
        return {"stops": stops, "lat": lat, "lon": lon}
    except Exception as e:
        print("nearby:", e)
        return JSONResponse({"error": "Nearby stops failed"}, status_code=500)


# ── /api/trip ─────────────────────────────────────────────────────────────────
@app.get("/api/trip")
async def trip(
    fromId:  Optional[str]   = None,
    fromLat: Optional[float] = None,
    fromLon: Optional[float] = None,
    toId:    Optional[str]   = None,
):
    if (not fromId and not (fromLat and fromLon)) or not toId:
        return JSONResponse({"error": "Provide fromId (or fromLat+fromLon) and toId"}, status_code=400)

    now      = datetime.now()
    itd_date = now.strftime("%Y%m%d")
    itd_time = now.strftime("%H%M")
    origin   = ({"type_origin": "coord", "name_origin": f"{fromLon}:{fromLat}:EPSG:4326"}
                if fromLat and fromLon else
                {"type_origin": "stop", "name_origin": fromId})
    params   = {"outputFormat": "rapidJSON", "coordOutputFormat": "EPSG:4326",
                "depArrMacro": "dep", "itdDate": itd_date, "itdTime": itd_time,
                **origin,
                "type_destination": "stop", "name_destination": toId,
                "calcNumberOfTrips": "5", "TfNSWSF": "true", "version": "10.2.1.42"}
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(f"{TFNSW}/trip", params=params, headers=hdrs())
        if r.status_code != 200:
            return JSONResponse({"error": f"TfNSW {r.status_code}"}, status_code=r.status_code)
        raw = r.json()

        journeys = []
        for j in (raw.get("journeys") or []):
            legs = []
            for leg in (j.get("legs") or []):
                tr      = leg.get("transportation") or {}
                prod    = tr.get("product") or {}
                code    = (tr.get("disassembledName") or
                           extract_line_code(prod.get("shortName") or tr.get("number") or ""))
                is_walk = bool(leg.get("isFootpathLeg"))
                o       = leg.get("origin") or {}
                d       = leg.get("destination") or {}
                legs.append({
                    "mode":         "walk" if is_walk else mode_from_class(prod.get("class")),
                    "line":         code or "",
                    "lineClass":    "walk" if is_walk else line_class(code or ""),
                    "from":         o.get("name") or "",
                    "to":           d.get("name") or "",
                    "depTime":      to_hhmm(o.get("departureTimeEstimated") or o.get("departureTimePlanned") or ""),
                    "arrTime":      to_hhmm(d.get("arrivalTimeEstimated") or d.get("arrivalTimePlanned") or ""),
                    "durationSecs": leg.get("duration") or 0,
                    "walk":         is_walk,
                    "distanceM":    leg.get("distance"),
                })

            j_legs  = j.get("legs") or []
            first_o = (j_legs[0].get("origin") or {}) if j_legs else {}
            last_d  = (j_legs[-1].get("destination") or {}) if j_legs else {}
            raw_dep = first_o.get("departureTimeEstimated") or first_o.get("departureTimePlanned") or ""
            raw_arr = last_d.get("arrivalTimeEstimated") or last_d.get("arrivalTimePlanned") or ""
            total_s = j.get("duration") or sum(l["durationSecs"] for l in legs)
            journeys.append({
                "depTime":      to_hhmm(raw_dep),
                "arrTime":      to_hhmm(raw_arr),
                "durationMin":  round(total_s / 60),
                "interchanges": j.get("interchanges") or 0,
                "legs":         legs,
            })

        journeys.sort(key=lambda j: j["durationMin"])
        return {"journeys": journeys}
    except Exception as e:
        print("trip:", e)
        return JSONResponse({"error": "Trip planning failed"}, status_code=500)


# ── /api/vehicles ─────────────────────────────────────────────────────────────
GTFS_MODES = {
    "train":   "sydneytrains",
    "metro":   "metro",
    "bus":     "buses",
    "ferry":   "ferries",
    "lrt":     "lightrail",
}

@app.get("/api/vehicles")
async def vehicles(mode: str = "bus"):
    feed_name = GTFS_MODES.get(mode, "buses")
    url = f"https://api.transport.nsw.gov.au/v1/gtfs/vehiclepos/{feed_name}"
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(url, headers={**hdrs(), "Accept": "application/x-google-protobuf"})
        if r.status_code != 200:
            return JSONResponse({"error": f"TfNSW {r.status_code}"}, status_code=r.status_code)

        feed = gtfs_realtime_pb2.FeedMessage()
        feed.ParseFromString(r.content)

        result = []
        for entity in feed.entity:
            if not entity.HasField("vehicle"):
                continue
            v    = entity.vehicle
            pos  = v.position
            if not (pos.latitude and pos.longitude):
                continue
            trip     = v.trip
            route_id = trip.route_id or ""
            # Extract human-readable route number (e.g. "2508_632n" → "632N")
            route_num = route_id.split("_")[-1].upper() if "_" in route_id else route_id
            label     = v.vehicle.label or ""
            result.append({
                "id":      entity.id,
                "lat":     round(pos.latitude,  6),
                "lon":     round(pos.longitude, 6),
                "bearing": round(pos.bearing)   if pos.bearing else None,
                "route":   route_num,
                "label":   label,
                "speed":   round(pos.speed * 3.6) if pos.speed else None,  # m/s → km/h
                "mode":    mode,
            })
        return {"vehicles": result, "mode": mode}
    except Exception as e:
        print("vehicles:", e)
        return JSONResponse({"error": str(e)}, status_code=500)


# Static files – must be mounted last so API routes take priority
app.mount("/", StaticFiles(directory="public", html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 3400))
    uvicorn.run("app:app", host="0.0.0.0", port=port)
