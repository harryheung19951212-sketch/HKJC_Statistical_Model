from pathlib import Path

from racing_model.exotic_live import (
    graphql_exotic_payload_to_rows,
    mqtt_exotic_messages_to_rows,
    refresh_exotic_dividends,
)
from racing_model.exotic_dividends import load_exotic_dividend_lookup
from racing_model.storage import connect, init_db, insert_rows, upsert_race_status


class FakeExoticProvider:
    source_name = "hkjc_graphql"
    last_error = None

    def fetch_dividends(self, conn, race_id: str):
        return [
            {"market": "QPL", "combination": "2+1", "dividend": 12.5, "source": self.source_name},
            {"market": "TRIO", "combination": "3/2/1", "dividend": 85.0, "source": self.source_name},
        ]


def test_graphql_exotic_payload_maps_hkjc_markets() -> None:
    payload = {
        "data": {
            "raceMeetings": [
                {
                    "pmPools": [
                        {
                            "oddsType": "TRI",
                            "lastUpdateTime": "2026-05-06T11:00:00Z",
                            "oddsNodes": [{"combString": "01,02,03", "oddsValue": "88.5"}],
                        },
                        {
                            "oddsType": "QTT",
                            "oddsNodes": [{"combString": "4,3,2,1", "oddsValue": "1,250"}],
                        },
                    ]
                }
            ]
        }
    }

    rows = graphql_exotic_payload_to_rows(payload, "HK20260506-ST-09", "hkjc_graphql")

    assert rows[0]["market"] == "TRIO"
    assert rows[0]["combination"] == "01,02,03"
    assert rows[0]["dividend"] == 88.5
    assert rows[1]["market"] == "QUARTET"
    assert rows[1]["dividend"] == 1250.0


def test_mqtt_exotic_messages_parse_topic_market() -> None:
    topic = "hk/d/prdt/wager/evt/01/upd/racing/20260506/st/09/qpl/odds/full"
    payload = b'{"oddsNodes":[{"combString":"7+4","oddsValue":"22.0"}]}'

    rows = mqtt_exotic_messages_to_rows(
        [(topic, payload)],
        "HK20260506-ST-09",
        {"qpl": "QPL"},
        "hkjc_mqtt",
    )

    assert rows == [
        {
            "race_id": "HK20260506-ST-09",
            "market": "QPL",
            "combination": "7+4",
            "dividend": 22.0,
            "dividend_status": "probable",
            "source": "hkjc_mqtt",
            "fetched_at": rows[0]["fetched_at"],
            "notes": "live_exotic_dividend",
        }
    ]


def test_refresh_exotic_dividends_upserts_live_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "racing.db"
    init_db(db_path)
    with connect(db_path) as conn:
        insert_rows(
            conn,
            "races",
            [
                {
                    "race_id": "HK20260506-ST-09",
                    "date": "2026-05-06",
                    "track": "Sha Tin",
                    "course": "AWT",
                    "distance_m": 1200,
                    "going": "GOOD",
                    "class_rating": "Class 3",
                    "prize": 1000000,
                    "race_name": "測試賽",
                }
            ],
        )
        upsert_race_status(conn, "HK20260506-ST-09", "scheduled")
        result = refresh_exotic_dividends(conn, "HK20260506-ST-09", FakeExoticProvider())
        lookup = load_exotic_dividend_lookup(conn, "HK20260506-ST-09")

    assert result["status"] == "ok"
    assert result["inserted"] == 2
    assert ("QPL", "1+2") in lookup
    assert ("TRIO", "1+2+3") in lookup
    assert lookup[("QPL", "1+2")]["source"] == "hkjc_graphql"
