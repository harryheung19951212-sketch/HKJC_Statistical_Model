from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from urllib.request import urlopen

from .live import parse_hkjc_race_id
from .storage import fetch_all


HKO_CURRENT_URL = "https://data.weather.gov.hk/weatherAPI/opendata/weather.php?dataType=rhrread&lang=tc"
HKO_DAILY_URL = "https://data.weather.gov.hk/weatherAPI/opendata/opendata.php?dataType=RYES&date={date}&lang=tc&rformat=json"
HKT = timezone(timedelta(hours=8))

_CACHE: dict[str, tuple[float, dict[str, object]]] = {}


def race_weather(conn, race_id: str) -> dict[str, object]:
    races = fetch_all(conn, "SELECT race_id, date, track FROM races WHERE race_id = ?", (race_id,))
    if not races:
        return {"status": "missing", "message": "搵唔到賽事天氣資料"}
    race = dict(races[0])
    race_date = str(race["date"])
    ref = parse_hkjc_race_id(race_id)
    venue = ref.venue if ref else venue_from_track(str(race.get("track", "")))
    cache_key = f"{race_date}:{venue}"
    ttl = 600 if race_date == datetime.now(HKT).date().isoformat() else 43200
    cached = _CACHE.get(cache_key)
    if cached and time.time() - cached[0] < ttl:
        return cached[1]

    try:
        payload = (
            fetch_current_weather(race_date, venue)
            if race_date == datetime.now(HKT).date().isoformat()
            else fetch_daily_weather(race_date, venue)
        )
    except Exception as exc:
        payload = {
            "status": "error",
            "date": race_date,
            "venue": venue,
            "source": "香港天文台",
            "message": f"天氣資料暫時未能載入：{exc}",
        }
    _CACHE[cache_key] = (time.time(), payload)
    return payload


def fetch_daily_weather(race_date: str, venue: str) -> dict[str, object]:
    date_token = race_date.replace("-", "")
    url = HKO_DAILY_URL.format(date=date_token)
    data = fetch_json(url)
    prefix = daily_station_prefix(venue)
    station_name = data.get(f"{prefix}LocationName") or venue_label(venue)
    min_temp = parse_float(data.get(f"{prefix}MinTemp"))
    max_temp = parse_float(data.get(f"{prefix}MaxTemp"))
    min_humidity = parse_float(data.get("HKOReadingsMinRH"))
    max_humidity = parse_float(data.get("HKOReadingsMaxRH"))
    rainfall = data.get("HKOReadingsRainfall")
    report_date = normalize_date_token(str(data.get("ReportTimeInfoDate") or date_token))
    summary = format_daily_summary(station_name, min_temp, max_temp, min_humidity, max_humidity, rainfall)
    return {
        "status": "ok",
        "mode": "historical_daily",
        "date": report_date,
        "venue": venue,
        "station": station_name,
        "source": "香港天文台",
        "source_url": url,
        "summary": summary,
        "min_temp_c": min_temp,
        "max_temp_c": max_temp,
        "min_humidity_percent": min_humidity,
        "max_humidity_percent": max_humidity,
        "rainfall": rainfall,
        "update_time": normalize_date_token(str(data.get("BulletinDate") or "")),
    }


def fetch_current_weather(race_date: str, venue: str) -> dict[str, object]:
    data = fetch_json(HKO_CURRENT_URL)
    place = venue_label(venue)
    temp_row = find_place((data.get("temperature") or {}).get("data") or [], place)
    humidity_row = ((data.get("humidity") or {}).get("data") or [{}])[0]
    rainfall_place = "灣仔" if venue == "HV" else place
    rainfall_row = find_place((data.get("rainfall") or {}).get("data") or [], rainfall_place)
    temp = parse_float(temp_row.get("value"))
    humidity = parse_float(humidity_row.get("value"))
    rainfall = rainfall_row.get("max")
    summary = format_current_summary(place, temp, humidity, rainfall)
    return {
        "status": "ok",
        "mode": "current",
        "date": race_date,
        "venue": venue,
        "station": place,
        "source": "香港天文台",
        "source_url": HKO_CURRENT_URL,
        "summary": summary,
        "temperature_c": temp,
        "humidity_percent": humidity,
        "rainfall_mm": rainfall,
        "update_time": data.get("updateTime") or (data.get("temperature") or {}).get("recordTime"),
    }


def fetch_json(url: str) -> dict[str, object]:
    with urlopen(url, timeout=15) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def daily_station_prefix(venue: str) -> str:
    return {"ST": "ShaTin", "HV": "HappyValley"}.get(venue.upper(), "HKOReadings")


def venue_label(venue: str) -> str:
    return {"ST": "沙田", "HV": "跑馬地"}.get(venue.upper(), venue)


def venue_from_track(track: str) -> str:
    text = track.lower()
    if "happy" in text or "跑馬" in track:
        return "HV"
    return "ST"


def find_place(rows: list[dict[str, object]], place: str) -> dict[str, object]:
    return next((row for row in rows if row.get("place") == place), rows[0] if rows else {})


def parse_float(value: object) -> float | None:
    if value in {None, "", "--"}:
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def normalize_date_token(value: str) -> str:
    if len(value) == 8 and value.isdigit():
        return f"{value[:4]}-{value[4:6]}-{value[6:]}"
    return value


def format_daily_summary(
    station: object,
    min_temp: float | None,
    max_temp: float | None,
    min_humidity: float | None,
    max_humidity: float | None,
    rainfall: object,
) -> str:
    parts = [str(station)]
    if min_temp is not None and max_temp is not None:
        parts.append(f"{min_temp:.1f}-{max_temp:.1f}°C")
    if min_humidity is not None and max_humidity is not None:
        parts.append(f"濕度 {min_humidity:.0f}-{max_humidity:.0f}%")
    if rainfall not in {None, ""}:
        parts.append(f"雨量 {rainfall}")
    return "｜".join(parts)


def format_current_summary(
    station: str,
    temp: float | None,
    humidity: float | None,
    rainfall: object,
) -> str:
    parts = [station]
    if temp is not None:
        parts.append(f"{temp:.0f}°C")
    if humidity is not None:
        parts.append(f"濕度 {humidity:.0f}%")
    if rainfall not in {None, ""}:
        parts.append(f"雨量 {rainfall}mm")
    return "｜".join(parts)
