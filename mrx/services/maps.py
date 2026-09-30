"""Live map data from real public APIs. Every response carries its provider and timestamps."""
from __future__ import annotations

from datetime import datetime, timezone

from ..tools.registry import Tool, ToolError

UA = {"User-Agent": "M.R.X./0.1 (personal desktop assistant)"}
QUAKE_PERIODS = ["all_hour", "all_day", "2.5_day", "4.5_day", "2.5_week", "4.5_week", "significant_week"]
LAYERS = {
    "earthquakes": "USGS Earthquake Hazards Program (live GeoJSON feed)",
    "weather": "Open-Meteo current conditions at a coordinate",
    "flights": "OpenSky Network anonymous state vectors (rate limited)",
    "geocode": "OpenStreetMap Nominatim search",
}
UNAVAILABLE_LAYERS = {"traffic": "no keyless legitimate live traffic API is configured",
                      "news": "news items are not geolocated by their feeds; markers are not fabricated"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def earthquakes(rt, period: str = "2.5_day"):
    if period not in QUAKE_PERIODS:
        raise ToolError(f"period must be one of {QUAKE_PERIODS}")
    r = await rt.http.get(f"https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/{period}.geojson", timeout=15)
    r.raise_for_status()
    j = r.json()
    feats = [{"lat": f["geometry"]["coordinates"][1], "lon": f["geometry"]["coordinates"][0],
              "depth_km": f["geometry"]["coordinates"][2], "mag": f["properties"].get("mag"),
              "place": f["properties"].get("place"), "time": f["properties"].get("time"),
              "url": f["properties"].get("url")} for f in j.get("features", []) if f.get("geometry")]
    return {"layer": "earthquakes", "provider": "USGS", "data_updated": datetime.fromtimestamp(
        j["metadata"]["generated"] / 1000, timezone.utc).isoformat(), "retrieved_at": now_iso(),
        "period": period, "count": len(feats), "features": feats}


async def weather(rt, lat: float, lon: float):
    r = await rt.http.get("https://api.open-meteo.com/v1/forecast", timeout=15, params={
        "latitude": lat, "longitude": lon, "timezone": "auto",
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m"})
    r.raise_for_status()
    j = r.json()
    return {"layer": "weather", "provider": "Open-Meteo", "lat": j["latitude"], "lon": j["longitude"],
            "data_updated": j["current"]["time"] + " (" + j.get("timezone_abbreviation", "local") + ")",
            "retrieved_at": now_iso(), "current": j["current"], "units": j["current_units"]}


async def geocode(rt, query: str, limit: int = 5):
    r = await rt.http.get("https://nominatim.openstreetmap.org/search", timeout=15, headers=UA,
                          params={"q": query, "format": "jsonv2", "limit": limit})
    r.raise_for_status()
    res = [{"name": x["display_name"], "lat": float(x["lat"]), "lon": float(x["lon"]), "type": x.get("type"),
            "boundingbox": [float(b) for b in x["boundingbox"]]} for x in r.json()]
    return {"layer": "geocode", "provider": "OpenStreetMap Nominatim", "retrieved_at": now_iso(), "query": query, "results": res}


async def flights(rt, south: float, west: float, north: float, east: float):
    if (north - south) * (east - west) > 400:
        raise ToolError("bounding box too large for the anonymous OpenSky API; zoom in")
    r = await rt.http.get("https://opensky-network.org/api/states/all", timeout=20,
                          params={"lamin": south, "lomin": west, "lamax": north, "lomax": east})
    if r.status_code == 429:
        raise ToolError("OpenSky rate limit reached; try again shortly")
    r.raise_for_status()
    j = r.json()
    st = [{"callsign": (s[1] or "").strip(), "country": s[2], "lon": s[5], "lat": s[6], "alt_m": s[7],
           "velocity_ms": s[9], "heading": s[10]} for s in (j.get("states") or []) if s[5] is not None and s[6] is not None]
    return {"layer": "flights", "provider": "OpenSky Network", "data_updated": datetime.fromtimestamp(j["time"], timezone.utc).isoformat(),
            "retrieved_at": now_iso(), "count": len(st), "features": st[:400]}


async def get_world_map_data(rt, layer: str = "earthquakes", period: str = "2.5_day", lat: float | None = None,
                             lon: float | None = None, query: str | None = None):
    if layer == "earthquakes":
        return await earthquakes(rt, period)
    if layer == "weather":
        if lat is None or lon is None:
            raise ToolError("weather needs lat and lon")
        return await weather(rt, lat, lon)
    if layer == "geocode":
        if not query:
            raise ToolError("geocode needs a query")
        return await geocode(rt, query)
    if layer in UNAVAILABLE_LAYERS:
        raise ToolError(f"layer '{layer}' is unavailable: {UNAVAILABLE_LAYERS[layer]}")
    raise ToolError(f"unknown layer '{layer}'. Available: {list(LAYERS)}")


def tools() -> list[Tool]:
    return [Tool("get_world_map_data", "Fetch live map layer data (earthquakes, weather at a point, geocoding).",
                 {"layer": {"type": "string"}, "period": {"type": "string"}, "lat": {"type": "number"},
                  "lon": {"type": "number"}, "query": {"type": "string"}}, [], get_world_map_data,
                 plugin="maps", scope="maps", timeout_s=40),
            Tool("show_map", "Open the live world map panel, optionally centred on a place.",
                 {"place": {"type": "string"}}, [], show_map, plugin="maps", scope="maps")]


async def show_map(rt, place: str | None = None):
    payload = {"panel": "map"}
    if place:
        g = await geocode(rt, place, 1)
        if not g["results"]:
            raise ToolError(f"could not find '{place}'")
        payload.update(center=g["results"][0])
    await rt.bus.emit("ui.navigate", payload)
    return {"opened": "map", **({"center": payload["center"]["name"]} if place else {})}
