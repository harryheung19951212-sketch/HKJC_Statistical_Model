from racing_model import weather


def test_forecast_weather_uses_hko_nine_day_forecast(monkeypatch) -> None:
    def fake_fetch_json(url: str):
        assert url == weather.HKO_FORECAST_URL
        return {
            "updateTime": "2026-05-08T12:00:00+08:00",
            "weatherForecast": [
                {
                    "forecastDate": "20260509",
                    "forecastWeather": "大致多雲，有幾陣驟雨。",
                    "forecastWind": "東風4至5級。",
                    "forecastMaxtemp": {"value": 26, "unit": "C"},
                    "forecastMintemp": {"value": 23, "unit": "C"},
                    "forecastMaxrh": {"value": 95, "unit": "percent"},
                    "forecastMinrh": {"value": 75, "unit": "percent"},
                    "PSR": "中",
                }
            ],
        }

    monkeypatch.setattr(weather, "fetch_json", fake_fetch_json)

    payload = weather.fetch_forecast_weather("2026-05-09", "ST")

    assert payload["status"] == "ok"
    assert payload["mode"] == "forecast"
    assert payload["date"] == "2026-05-09"
    assert payload["min_temp_c"] == 23.0
    assert payload["max_temp_c"] == 26.0
    assert "大致多雲" in str(payload["summary"])
    assert "到當日會自動改用即時天氣" in str(payload["message"])


def test_current_weather_can_fallback_to_forecast(monkeypatch) -> None:
    def fail_current(race_date: str, venue: str):
        raise ValueError("HKO returned non-JSON response")

    def fake_forecast(race_date: str, venue: str, fallback_reason: str = ""):
        return {
            "status": "ok",
            "mode": "forecast",
            "date": race_date,
            "venue": venue,
            "message": f"fallback: {fallback_reason}",
        }

    monkeypatch.setattr(weather, "fetch_current_weather", fail_current)
    monkeypatch.setattr(weather, "fetch_forecast_weather", fake_forecast)

    try:
        payload = weather.fetch_current_weather("2026-05-09", "ST")
    except ValueError as exc:
        payload = weather.fetch_forecast_weather("2026-05-09", "ST", fallback_reason=str(exc))

    assert payload["status"] == "ok"
    assert payload["mode"] == "forecast"
    assert "non-JSON" in str(payload["message"])


def test_fetch_json_reports_non_json_response(monkeypatch) -> None:
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b"Please include valid parameters in API request."

    monkeypatch.setattr(weather, "urlopen", lambda url, timeout: Response())

    try:
        weather.fetch_json("https://example.test/non-json")
    except ValueError as exc:
        assert "non-JSON" in str(exc)
    else:
        raise AssertionError("Expected non-JSON weather response to raise ValueError")
