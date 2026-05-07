from pathlib import Path

from racing_model.exotic_dividends import (
    exotic_dividend_report,
    load_exotic_dividend_lookup,
    normalize_combination_key,
    upsert_exotic_dividends,
)
from racing_model.storage import connect, init_db


def test_exotic_dividend_import_normalizes_ordering(tmp_path: Path) -> None:
    db_path = tmp_path / "racing.db"
    init_db(db_path)
    with connect(db_path) as conn:
        result = upsert_exotic_dividends(
            conn,
            "HK20260506-ST-01",
            [
                {"market": "QPL", "combination": "2+1", "dividend": 12.5},
                {"market": "FCT", "combination": "2>1", "dividend": 35.0},
            ],
            source="manual_test",
        )
        report = exotic_dividend_report(conn, "HK20260506-ST-01")
        lookup = load_exotic_dividend_lookup(conn, "HK20260506-ST-01")

    assert result["imported"] == 2
    assert report["count"] == 2
    assert ("QPL", "1+2") in lookup
    assert ("FCT", "2>1") in lookup
    assert normalize_combination_key("QPL", "9/3") == "3+9"


def test_exotic_dividend_lookup_handles_empty_race(tmp_path: Path) -> None:
    db_path = tmp_path / "racing.db"
    init_db(db_path)
    with connect(db_path) as conn:
        lookup = load_exotic_dividend_lookup(conn, "HK20260506-ST-02")

    assert lookup == {}
