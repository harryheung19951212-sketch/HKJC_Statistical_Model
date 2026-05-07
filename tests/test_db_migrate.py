from pathlib import Path

from racing_model.db_migrate import database_counts, migrate_sqlite_to_database
from racing_model.storage import connect, init_db, insert_rows, translate_postgres_sql


def test_migrate_sqlite_to_database_copies_core_tables(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    target = tmp_path / "target.db"
    init_db(source)
    with connect(source) as conn:
        insert_rows(
            conn,
            "races",
            [
                {
                    "race_id": "HK20260506-ST-01",
                    "date": "2026-05-06",
                    "track": "沙田",
                    "course": "草地",
                    "distance_m": 1200,
                    "going": "好地",
                    "class_rating": "第四班",
                    "prize": 1000000,
                }
            ],
        )
        insert_rows(
            conn,
            "runners",
            [
                {
                    "race_id": "HK20260506-ST-01",
                    "horse_id": "H001",
                    "horse_no": 1,
                    "horse_name": "TEST HORSE",
                    "horse_name_zh": "測試馬",
                    "jockey": "Test Jockey",
                    "jockey_zh": "測試騎師",
                    "trainer": "Test Trainer",
                    "trainer_zh": "測試練馬師",
                    "draw": 1,
                    "weight_lbs": 120,
                    "official_rating": 50,
                    "age": 4,
                    "sex": "G",
                    "running_style": "pace",
                    "gear": "",
                }
            ],
        )
        conn.commit()

    result = migrate_sqlite_to_database(source, target, replace=True)
    counts = database_counts(target)

    assert result["tables"]["races"] == 1
    assert result["tables"]["runners"] == 1
    assert counts["races"] == 1
    assert counts["runners"] == 1


def test_postgres_sql_translation_supports_existing_placeholders() -> None:
    positional, positional_params = translate_postgres_sql(
        "SELECT * FROM races WHERE race_id = ?",
        ("HK20260506-ST-01",),
    )
    named, named_params = translate_postgres_sql(
        "UPDATE race_status SET status = :status WHERE race_id = :race_id",
        {"status": "resulted", "race_id": "HK20260506-ST-01"},
    )

    assert positional == "SELECT * FROM races WHERE race_id = %s"
    assert positional_params == ("HK20260506-ST-01",)
    assert named == "UPDATE race_status SET status = %(status)s WHERE race_id = %(race_id)s"
    assert named_params["status"] == "resulted"
