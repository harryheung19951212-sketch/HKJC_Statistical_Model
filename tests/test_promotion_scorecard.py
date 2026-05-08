from pathlib import Path

from racing_model.promotion_scorecard import build_promotion_scorecard, promotion_scorecard
from racing_model.storage import connect, import_csv, init_db


def test_promotion_scorecard_wraps_walk_forward_and_pool_replay(tmp_path: Path) -> None:
    db_path = tmp_path / "racing.db"
    init_db(db_path)
    sample_dir = Path("data/sample")
    with connect(db_path) as conn:
        for table, filename in {
            "races": "races.csv",
            "runners": "runners.csv",
            "results": "results.csv",
            "workouts": "workouts.csv",
            "odds_ticks": "odds.csv",
        }.items():
            import_csv(conn, table, sample_dir / filename)
        conn.commit()

        report = promotion_scorecard(conn, epochs=2)

    assert report["summary"]["gate"] == "unverified"
    assert report["summary"]["metric_count"] == 10
    assert report["overall_metrics"]["folds"] == 1
    assert {row["section_id"] for row in report["sections"]} == {"accuracy", "calibration", "pool", "slice", "risk"}
    assert report["pool_scorecard"]
    assert report["slice_scorecard"]
    assert report["calibration_bins"]


def test_promotion_scorecard_passes_when_all_objectives_are_clean() -> None:
    report = build_promotion_scorecard(clean_model_versions(), clean_pool_replay())

    assert report["summary"]["gate"] == "pass"
    assert report["summary"]["gate_label"] == "可升級觀察"
    assert report["overall_metrics"]["log_loss_delta"] < 0
    assert report["overall_metrics"]["top3_delta"] > 0
    assert all(row["status"] == "pass" for row in report["sections"])
    assert report["pool_scorecard"][0]["status"] == "pass"
    assert report["slice_scorecard"][0]["status"] == "pass"


def test_promotion_scorecard_blocks_negative_execution_roi() -> None:
    replay = clean_pool_replay()
    replay["summary"]["execution_roi"] = -0.12
    replay["markets"][0]["execution_roi"] = -0.12

    report = build_promotion_scorecard(clean_model_versions(), replay)

    assert report["summary"]["gate"] == "blocked"
    assert next(row for row in report["sections"] if row["section_id"] == "pool")["status"] == "block"
    assert report["pool_scorecard"][0]["status"] == "block"


def clean_model_versions() -> dict[str, object]:
    baseline = version_row(
        "baseline",
        "Baseline",
        log_loss=1.20,
        brier=0.78,
        top1=0.28,
        top3=0.60,
        roi=0.02,
        profit=80,
        drawdown=40,
    )
    best = version_row(
        "no_market",
        "No Market",
        log_loss=1.08,
        brier=0.70,
        top1=0.34,
        top3=0.68,
        roi=0.06,
        profit=180,
        drawdown=35,
    )
    return {
        "summary": {
            "race_count": 40,
            "folds": 35,
            "best_variant_id": "no_market",
            "best_label": "No Market",
        },
        "versions": [best, baseline],
        "oos_gate": {
            "gate": "pass",
            "message": "所有可驗分片通過。",
            "eligible_slices": 1,
            "blocked_slices": 0,
            "sample_small_slices": 0,
        },
    }


def version_row(
    variant_id: str,
    label: str,
    log_loss: float,
    brier: float,
    top1: float,
    top3: float,
    roi: float,
    profit: float,
    drawdown: float,
) -> dict[str, object]:
    return {
        "variant_id": variant_id,
        "label": label,
        "metrics": {
            "races": 35,
            "log_loss": log_loss,
            "brier_score": brier,
            "top_pick_hit_rate": top1,
            "top3_hit_rate": top3,
            "value_roi": roi,
            "value_profit": profit,
            "max_drawdown": drawdown,
        },
        "calibration_bins": [
            {"label": "0-10%", "count": 30, "gap": 0.02},
            {"label": "10-20%", "count": 30, "gap": -0.03},
            {"label": "20-50%", "count": 30, "gap": 0.01},
        ],
        "slice_scorecard": [
            {
                "slice_id": "distance:1200",
                "dimension": "distance",
                "value": "1200",
                "races": 12,
                "log_loss": log_loss,
                "brier_score": brier,
                "top_pick_hit_rate": top1,
                "value_roi": roi,
            }
        ],
    }


def clean_pool_replay() -> dict[str, object]:
    return {
        "summary": {
            "tickets": 30,
            "reconciled": 28,
            "executed": 25,
            "execution_roi": 0.08,
            "execution_max_drawdown": 30,
            "stale_price": 0,
            "negative_ev_at_execution": 0,
        },
        "markets": [
            {
                "market": "WIN",
                "market_label": "獨贏",
                "tickets": 30,
                "reconciled": 28,
                "executed": 25,
                "roi": 0.07,
                "execution_roi": 0.08,
                "hit_rate": 0.32,
                "profit": 210,
                "max_drawdown": 35,
                "execution_max_drawdown": 30,
                "settlement_rate": 0.93,
                "execution_rate": 0.89,
            }
        ],
    }
