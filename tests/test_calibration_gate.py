import gc
from pathlib import Path
from tempfile import TemporaryDirectory

from racing_model.calibration_gate import calibration_gate_from_report
from racing_model.evolution import evaluate_model_evolution
from racing_model.model import RankingModel
from racing_model.storage import connect, import_csv, init_db


def test_calibration_gate_blocks_failed_slice_even_when_overall_bins_pass() -> None:
    report = {
        "metrics": {"races": 50, "runners": 500},
        "calibration": [
            {"label": "10-15%", "count": 180, "avg_prediction": 0.12, "observed_rate": 0.13, "gap": 0.01}
        ],
        "calibration_slices": [
            {
                "slice_id": "distance:短途 <=1200米",
                "dimension": "distance",
                "dimension_label": "路程",
                "value": "短途 <=1200米",
                "label": "路程: 短途 <=1200米",
                "races": 12,
                "runners": 120,
                "worst_bin": {
                    "label": "20-30%",
                    "count": 32,
                    "avg_prediction": 0.26,
                    "observed_rate": 0.08,
                    "gap": -0.18,
                },
                "bins": [
                    {
                        "label": "20-30%",
                        "count": 32,
                        "avg_prediction": 0.26,
                        "observed_rate": 0.08,
                        "gap": -0.18,
                    }
                ],
            }
        ],
    }

    gate = calibration_gate_from_report(report)

    assert gate["status"] == "blocked"
    assert gate["stake_factor"] == 0.25
    assert gate["promote_allowed"] is False
    assert gate["worst_slice"]["slice_id"] == "distance:短途 <=1200米"
    assert "分片校準未過關" in gate["message"]


def test_calibration_gate_ignores_tiny_slice_bins() -> None:
    report = {
        "metrics": {"races": 50, "runners": 500},
        "calibration": [
            {"label": "10-15%", "count": 180, "avg_prediction": 0.12, "observed_rate": 0.13, "gap": 0.01}
        ],
        "calibration_slices": [
            {
                "slice_id": "track:ST",
                "dimension": "track",
                "dimension_label": "馬場",
                "value": "ST",
                "label": "馬場: ST",
                "races": 12,
                "runners": 120,
                "worst_bin": {
                    "label": "50-100%",
                    "count": 1,
                    "avg_prediction": 0.65,
                    "observed_rate": 0.0,
                    "gap": -0.65,
                },
                "bins": [
                    {
                        "label": "50-100%",
                        "count": 1,
                        "avg_prediction": 0.65,
                        "observed_rate": 0.0,
                        "gap": -0.65,
                    },
                    {
                        "label": "10-15%",
                        "count": 80,
                        "avg_prediction": 0.12,
                        "observed_rate": 0.13,
                        "gap": 0.01,
                    },
                ],
            }
        ],
    }

    gate = calibration_gate_from_report(report)

    assert gate["status"] == "pass"
    assert gate["worst_slice"]["worst_bin"]["label"] == "10-15%"


def test_calibration_gate_blocks_failed_pool_market() -> None:
    report = {
        "metrics": {"races": 50, "runners": 500},
        "calibration": [
            {"label": "10-15%", "count": 180, "avg_prediction": 0.12, "observed_rate": 0.13, "gap": 0.01}
        ],
        "pool_calibration": {
            "markets": [
                {
                    "market": "PLACE",
                    "market_label": "位置",
                    "tickets": 30,
                    "avg_probability": 0.55,
                    "observed_rate": 0.30,
                    "gap": -0.25,
                    "worst_bin": {
                        "label": "50-100%",
                        "count": 30,
                        "avg_prediction": 0.55,
                        "observed_rate": 0.30,
                        "gap": -0.25,
                    },
                    "bins": [
                        {
                            "label": "50-100%",
                            "count": 30,
                            "avg_prediction": 0.55,
                            "observed_rate": 0.30,
                            "gap": -0.25,
                        }
                    ],
                }
            ]
        },
    }

    gate = calibration_gate_from_report(report)

    assert gate["status"] == "blocked"
    assert gate["worst_pool_market"]["market"] == "PLACE"
    assert "彩池校準未過關" in gate["message"]


def test_model_evolution_exports_slice_calibration_bins() -> None:
    with TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
        db_path = Path(temp_dir) / "racing.db"
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

            report = evaluate_model_evolution(conn, RankingModel.new())
        gc.collect()

    assert report["calibration_slices"]
    first = report["calibration_slices"][0]
    assert first["slice_id"]
    assert first["bins"]
    assert first["worst_bin"] is not None
