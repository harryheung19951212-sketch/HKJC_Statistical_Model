from racing_model.odds import OfficialOddsProviderChain, SnapshotJitterOddsProvider, build_odds_provider, graphql_odds_payload_to_rows, mqtt_messages_to_rows


class Settings:
    odds_provider = "auto"
    hkjc_graphql_url = "https://example.test/graphql"
    user_agent = "test-agent"
    hkjc_mqtt_host = "mqtt.example.test"
    hkjc_mqtt_port = 443
    hkjc_mqtt_username = ""
    hkjc_mqtt_password = ""
    hkjc_mqtt_wait_seconds = 0.1


def test_auto_odds_provider_uses_official_sources_without_dev_fallback() -> None:
    provider = build_odds_provider(Settings())

    assert isinstance(provider, OfficialOddsProviderChain)


def test_dev_odds_provider_requires_explicit_dev_setting() -> None:
    settings = Settings()
    settings.odds_provider = "dev"

    provider = build_odds_provider(settings)

    assert isinstance(provider, SnapshotJitterOddsProvider)


def test_graphql_odds_payload_matches_zero_padded_runner_numbers() -> None:
    payload = {
        "data": {
            "raceMeetings": [
                {
                    "pmPools": [
                        {
                            "oddsType": "PLA",
                            "lastUpdateTime": "2026-05-08T18:00:44.584+08:00",
                            "oddsNodes": [
                                {"combString": "01", "oddsValue": "1.7"},
                                {"combString": "02", "oddsValue": "2.3"},
                            ],
                        },
                        {
                            "oddsType": "WIN",
                            "lastUpdateTime": "2026-05-08T18:00:44.584+08:00",
                            "oddsNodes": [
                                {"combString": "01", "oddsValue": "3.4"},
                                {"combString": "02", "oddsValue": "5.1"},
                            ],
                        },
                    ]
                }
            ]
        }
    }

    rows = graphql_odds_payload_to_rows(
        payload,
        "HK20260509-ST-01",
        {"1": "H001", "2": "H002"},
        "hkjc_graphql",
    )

    assert rows == [
        {
            "race_id": "HK20260509-ST-01",
            "horse_id": "H001",
            "timestamp": "2026-05-08T18:00:44.584+08:00",
            "win_odds": 3.4,
            "place_odds": 1.7,
            "source": "hkjc_graphql",
        },
        {
            "race_id": "HK20260509-ST-01",
            "horse_id": "H002",
            "timestamp": "2026-05-08T18:00:44.584+08:00",
            "win_odds": 5.1,
            "place_odds": 2.3,
            "source": "hkjc_graphql",
        },
    ]


def test_mqtt_odds_payload_matches_zero_padded_runner_numbers() -> None:
    messages = [
        (
            "hk/d/prdt/wager/evt/01/upd/racing/20260509/st/01/win/odds/full",
            b'{"oddsNodes":[{"combString":"01","oddsValue":"3.4"},{"combString":"02","oddsValue":"5.1"}]}',
        ),
        (
            "hk/d/prdt/wager/evt/01/upd/racing/20260509/st/01/pla/odds/full",
            b'{"oddsNodes":[{"combString":"01","oddsValue":"1.7"},{"combString":"02","oddsValue":"2.3"}]}',
        ),
    ]

    rows = mqtt_messages_to_rows(messages, "HK20260509-ST-01", {"1": "H001", "2": "H002"})

    assert len(rows) == 2
    assert rows[0]["horse_id"] == "H001"
    assert rows[0]["win_odds"] == 3.4
    assert rows[0]["place_odds"] == 1.7
    assert rows[1]["horse_id"] == "H002"
    assert rows[1]["win_odds"] == 5.1
    assert rows[1]["place_odds"] == 2.3
