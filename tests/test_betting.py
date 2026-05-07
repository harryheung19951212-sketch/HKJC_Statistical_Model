from racing_model.betting import build_betting_decisions


def test_betting_decision_uses_fractional_kelly_and_race_cap() -> None:
    predictions = [
        {
            "horse_id": "H001",
            "horse_no": 1,
            "display_name": "測試馬",
            "display_jockey": "騎師",
            "display_trainer": "練馬師",
            "win_probability": 0.35,
            "latest_win_odds": 4.0,
            "latest_win_odds_source": "hkjc_mqtt",
            "top3_probability": 0.62,
            "place_odds": 2.2,
            "place_odds_source": "hkjc_mqtt",
        }
    ]

    result = build_betting_decisions(predictions, "scheduled", bankroll=10000, risk_profile="standard")

    assert result["tickets"]
    assert result["total_recommended_stake"] <= result["max_race_stake"]
    assert {ticket["market"] for ticket in result["tickets"]} == {"WIN", "PLACE"}
    assert all(ticket["action"] == "有值博" for ticket in result["tickets"])
    exotic_markets = {candidate["market"] for candidate in result["exotic_candidates"]}
    assert {"QPL", "TRIO", "QIN", "FCT", "TCE", "FIRST4", "QUARTET"}.issubset(exotic_markets)


def test_resulted_race_is_review_only() -> None:
    predictions = [
        {
            "horse_id": "H001",
            "horse_no": 1,
            "display_name": "測試馬",
            "win_probability": 0.50,
            "latest_win_odds": 5.0,
            "latest_win_odds_source": "hkjc_results_final",
            "top3_probability": 0.80,
            "place_odds": 2.0,
            "place_odds_source": "hkjc_results_final",
        }
    ]

    result = build_betting_decisions(predictions, "resulted", bankroll=10000, risk_profile="aggressive")

    assert result["tickets"] == []
    assert all(decision["recommended_stake"] == 0 for decision in result["decisions"])
    assert {decision["action"] for decision in result["decisions"]} == {"只供回測"}


def test_trio_upgrade_path_compares_position_q_pairs() -> None:
    predictions = [
        {
            "horse_id": f"H00{index}",
            "horse_no": index,
            "display_name": f"馬{index}",
            "win_probability": probability,
            "latest_win_odds": 3.0 + index,
            "latest_win_odds_source": "hkjc_mqtt",
            "top3_probability": min(probability * 3, 0.9),
            "place_odds": 1.5,
            "place_odds_source": "hkjc_mqtt",
        }
        for index, probability in enumerate([0.34, 0.24, 0.18, 0.12, 0.07, 0.05], start=1)
    ]

    result = build_betting_decisions(predictions, "scheduled", bankroll=10000, risk_profile="standard")

    assert result["upgrade_paths"]
    top_path = result["upgrade_paths"][0]
    assert top_path["to_label"] == "單T"
    assert len(top_path["from_markets"]) == 2
    assert top_path["trio_break_even_dividend"] > 1


def test_exotic_dividend_turns_candidate_into_ev_ticket() -> None:
    predictions = [
        {
            "horse_id": f"H00{index}",
            "horse_no": index,
            "display_name": f"馬{index}",
            "win_probability": probability,
            "latest_win_odds": 3.0 + index,
            "latest_win_odds_source": "hkjc_mqtt",
            "top3_probability": min(probability * 3, 0.9),
            "place_odds": 1.5,
            "place_odds_source": "hkjc_mqtt",
        }
        for index, probability in enumerate([0.34, 0.24, 0.18, 0.12, 0.07, 0.05], start=1)
    ]
    dividends = {
        ("QPL", "1+2"): {
            "dividend": 20.0,
            "dividend_status": "probable",
            "source": "manual_test",
        }
    }

    result = build_betting_decisions(
        predictions,
        "scheduled",
        bankroll=10000,
        risk_profile="standard",
        exotic_dividends=dividends,
    )

    qpl = next(ticket for ticket in result["tickets"] if ticket["market"] == "QPL")
    assert qpl["horse_id"] == "1+2"
    assert qpl["expected_value"] > 0
    assert qpl["recommended_stake"] > 0
