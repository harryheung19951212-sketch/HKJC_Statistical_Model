from racing_model.adaptive import blend_market_probabilities, stabilize_uniform_probabilities


def test_market_blend_can_promote_strong_market_signal() -> None:
    rows = [
        {
            "horse_id": "H001",
            "win_probability": 0.45,
            "top3_probability": 0.65,
            "market_probability": 0.08,
            "place_market_probability": 0.18,
            "latest_win_odds": 12.0,
            "place_odds": 3.5,
        },
        {
            "horse_id": "H002",
            "win_probability": 0.30,
            "top3_probability": 0.55,
            "market_probability": 0.42,
            "place_market_probability": 0.60,
            "latest_win_odds": 2.2,
            "place_odds": 1.3,
        },
        {
            "horse_id": "H003",
            "win_probability": 0.25,
            "top3_probability": 0.50,
            "market_probability": 0.10,
            "place_market_probability": 0.22,
            "latest_win_odds": 9.0,
            "place_odds": 2.8,
        },
    ]

    blended = blend_market_probabilities(rows, market_weight=0.7)

    assert blended[0]["horse_id"] == "H002"
    assert blended[0]["raw_model_win_probability"] == 0.30
    assert blended[0]["win_probability"] > 0.30
    assert blended[0]["expected_value"] is not None
    assert all(0 <= row["top3_probability"] <= 1 for row in blended)


def test_uniform_prediction_safeguard_uses_market_signal() -> None:
    rows = [
        {
            "horse_id": "H001",
            "win_probability": 0.25,
            "top3_probability": 0.75,
            "market_probability": 0.05,
            "place_market_probability": 0.20,
            "latest_win_odds": 18.0,
            "place_odds": 4.0,
        },
        {
            "horse_id": "H002",
            "win_probability": 0.25,
            "top3_probability": 0.75,
            "market_probability": 0.45,
            "place_market_probability": 0.85,
            "latest_win_odds": 2.1,
            "place_odds": 1.2,
        },
        {
            "horse_id": "H003",
            "win_probability": 0.25,
            "top3_probability": 0.75,
            "market_probability": 0.12,
            "place_market_probability": 0.40,
            "latest_win_odds": 8.0,
            "place_odds": 2.8,
        },
        {
            "horse_id": "H004",
            "win_probability": 0.25,
            "top3_probability": 0.75,
            "market_probability": 0.08,
            "place_market_probability": 0.25,
            "latest_win_odds": 12.0,
            "place_odds": 3.5,
        },
    ]

    stabilized, safeguard = stabilize_uniform_probabilities(rows)

    assert safeguard
    assert safeguard["code"] == "uniform_probability_market_fallback"
    assert stabilized[0]["horse_id"] == "H002"
    assert stabilized[0]["raw_model_win_probability"] == 0.25
    assert stabilized[0]["win_probability"] > stabilized[-1]["win_probability"]
    assert stabilized[0]["top3_probability"] > stabilized[-1]["top3_probability"]
    assert all(row["expected_value"] is not None for row in stabilized)
    assert all(row["market_fallback_no_edge"] for row in stabilized)
    assert all(row["model_edge_available"] is False for row in stabilized)
    assert all(row["expected_value_source"] == "market_fair_probability_fallback" for row in stabilized)


def test_uniform_prediction_safeguard_does_not_fire_without_market_spread() -> None:
    rows = [
        {"horse_id": "H001", "win_probability": 0.5, "top3_probability": 1.0, "market_probability": 0.0},
        {"horse_id": "H002", "win_probability": 0.5, "top3_probability": 1.0, "market_probability": 0.0},
    ]

    stabilized, safeguard = stabilize_uniform_probabilities(rows)

    assert safeguard is None
    assert stabilized == rows
