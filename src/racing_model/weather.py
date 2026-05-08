from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from urllib.request import urlopen

from .live import parse_hkjc_race_id
from .storage import fetch_all


HKO_CURRENT_URL = "https://data.weather.gov.hk/weatherAPI/opendata/weather.php?dataType=rhrread&lang=tc"
HKO_FORECAST_URL = "https://data.weather.gov.hk/weatherAPI/opendata/weather.php?dataType=fnd&lang=tc"
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
    today = datetime.now(HKT).date()
    race_day = parse_weather_date(race_date)
    ttl = 600 if race_day == today else 43200
    cached = _CACHE.get(cache_key)
    if cached and time.time() - cached[0] < ttl:
        return cached[1]

    try:
        if race_day == today:
            try:
                payload = fetch_current_weather(race_date, venue)
            except Exception as exc:
                payload = fetch_forecast_weather(race_date, venue, fallback_reason=str(exc))
        elif race_day and race_day > today:
            payload = fetch_forecast_weather(race_date, venue)
        else:
            payload = fetch_daily_weather(race_date, venue)
    except Exception as exc:
        payload = {
            "status": "error",
            "date": race_date,
            "venue": venue,
            "source": "香港天文台",
            "message": f"天氣預測暫時未能載入，先睇住模型預測：{exc}",
        }
    _CACHE[cache_key] = (time.time(), payload)
    return payload


def fetch_forecast_weather(race_date: str, venue: str, fallback_reason: str = "") -> dict[str, object]:
    date_token = race_date.replace("-", "")
    data = fetch_json(HKO_FORECAST_URL)
    forecasts = data.get("weatherForecast") or []
    if not isinstance(forecasts, list):
        forecasts = []
    forecast = next((row for row in forecasts if str(row.get("forecastDate") or "") == date_token), None)
    if not isinstance(forecast, dict):
        raise ValueError(f"HKO forecast does not include {race_date}")
    min_temp = nested_value(forecast.get("forecastMintemp"))
    max_temp = nested_value(forecast.get("forecastMaxtemp"))
    min_humidity = nested_value(forecast.get("forecastMinrh"))
    max_humidity = nested_value(forecast.get("forecastMaxrh"))
    weather = str(forecast.get("forecastWeather") or "")
    wind = str(forecast.get("forecastWind") or "")
    psr = str(forecast.get("PSR") or "")
    summary = format_forecast_summary(venue_label(venue), min_temp, max_temp, min_humidity, max_humidity, weather, psr)
    message = "賽日前顯示天文台預測；到當日會自動改用即時天氣。"
    if fallback_reason:
        message = f"即時天氣暫時未能載入，先顯示天文台預測：{fallback_reason}"
    return {
        "status": "ok",
        "mode": "forecast",
        "date": normalize_date_token(date_token),
        "venue": venue,
        "station": venue_label(venue),
        "source": "香港天文台九天天氣預報",
        "source_url": HKO_FORECAST_URL,
        "summary": summary,
        "forecast_weather": weather,
        "forecast_wind": wind,
        "rain_probability": psr,
        "min_temp_c": min_temp,
        "max_temp_c": max_temp,
        "min_humidity_percent": min_humidity,
        "max_humidity_percent": max_humidity,
        "update_time": data.get("updateTime"),
        "message": message,
    }


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
        body = response.read().decode("utf-8", errors="replace").strip()
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        snippet = body[:120] if body else "<empty response>"
        raise ValueError(f"HKO returned non-JSON response: {snippet}") from exc


def parse_weather_date(value: str):
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


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


def nested_value(value: object) -> float | None:
    if isinstance(value, dict):
        return parse_float(value.get("value"))
    return parse_float(value)


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


def format_forecast_summary(
    station: str,
    min_temp: float | None,
    max_temp: float | None,
    min_humidity: float | None,
    max_humidity: float | None,
    weather: str,
    psr: str,
) -> str:
    parts = [station]
    if min_temp is not None and max_temp is not None:
        parts.append(f"{min_temp:.0f}-{max_temp:.0f}°C")
    if min_humidity is not None and max_humidity is not None:
        parts.append(f"濕度 {min_humidity:.0f}-{max_humidity:.0f}%")
    if psr:
        parts.append(f"降雨概率 {psr}")
    if weather:
        parts.append(weather)
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
