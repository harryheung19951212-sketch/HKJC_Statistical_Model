from racing_model.betting import build_betting_decisions
from racing_model.pool_rules import POOL_RULES, required_expected_value


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
        },
        *[
            {
                "horse_id": f"H00{index}",
                "horse_no": index,
                "display_name": f"Runner {index}",
                "win_probability": probability,
                "latest_win_odds": None,
                "latest_win_odds_source": None,
                "top3_probability": min(probability * 3, 0.4),
                "place_odds": None,
                "place_odds_source": None,
            }
            for index, probability in enumerate([0.20, 0.16, 0.12, 0.10, 0.07], start=2)
        ],
    ]

    result = build_betting_decisions(predictions, "scheduled", bankroll=10000, risk_profile="standard")

    assert result["tickets"]
    assert result["total_recommended_stake"] <= result["max_race_stake"]
    assert {ticket["market"] for ticket in result["tickets"]} == {"WIN", "PLACE"}
    assert all(ticket["action"] == "有值博" for ticket in result["tickets"])
    exotic_markets = {candidate["market"] for candidate in result["exotic_candidates"]}
    assert {"QPL", "TRIO", "QIN", "FCT", "TCE", "FIRST4", "QUARTET"}.issubset(exotic_markets)
    assert all("recommended_stake" in candidate for candidate in result["exotic_candidates"])
    assert all(candidate["minimum_ticket_cost"] > 0 for candidate in result["exotic_candidates"])


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
    assert result["banker_leg_suggestions"]
    trio_banker = next(row for row in result["banker_leg_suggestions"] if row["market"] == "TRIO")
    assert trio_banker["bankers"] == ["1 馬1"]
    assert trio_banker["combination_count"] > 0
    assert "腳" in trio_banker["structure"]


def test_banker_leg_suggestions_include_all_leg_cover() -> None:
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
        for index, probability in enumerate([0.30, 0.22, 0.16, 0.12, 0.08, 0.06, 0.04, 0.02], start=1)
    ]

    result = build_betting_decisions(predictions, "scheduled", bankroll=10000, risk_profile="standard")

    first4 = next(row for row in result["banker_leg_suggestions"] if row["market"] == "FIRST4")
    assert first4["all_legs"] is True
    assert first4["bankers"] == ["1 馬1"]
    assert len(first4["legs"]) == 7
    assert first4["combination_count"] == 35
    assert first4["minimum_ticket_cost"] == 35.0
    assert first4["recommended_stake"] == 0.0


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
    assert qpl["cost_adjusted_expected_value"] < qpl["expected_value"]
    assert qpl["required_dividend"] > qpl["break_even_dividend"]
    assert qpl["pool_rule"]["takeout_rate"] > 0
    assert qpl["recommended_stake"] > 0
    qpl_candidate = next(row for row in result["exotic_candidates"] if row["market"] == "QPL" and row["combination_key"] == "1+2")
    assert qpl_candidate["recommended_stake"] == qpl["recommended_stake"]
    assert qpl_candidate["stake_action"] == "有值博"


def test_pool_cost_gate_rejects_small_nominal_edge() -> None:
    predictions = [
        {
            "horse_id": "H001",
            "horse_no": 1,
            "display_name": "Cost Gate",
            "win_probability": 0.30,
            "latest_win_odds": 3.52,
            "latest_win_odds_source": "hkjc_mqtt",
            "top3_probability": 0.50,
            "place_odds": 1.95,
            "place_odds_source": "hkjc_mqtt",
        }
    ]

    result = build_betting_decisions(predictions, "scheduled", bankroll=10000, risk_profile="standard")
    win = next(row for row in result["decisions"] if row["market"] == "WIN")

    assert win["expected_value"] > 0
    assert win["expected_value"] < win["required_expected_value"]
    assert win["recommended_stake"] == 0
    assert win["cost_adjusted_expected_value"] == win["expected_value"] - POOL_RULES["WIN"].efficiency_buffer


def test_pool_rules_cover_all_betting_markets() -> None:
    assert set(POOL_RULES) == {"WIN", "PLACE", "QIN", "QPL", "FCT", "TRIO", "TCE", "FIRST4", "QUARTET"}
    assert POOL_RULES["WIN"].payout_rate == 0.825
    assert POOL_RULES["FCT"].payout_rate == 0.805
    assert POOL_RULES["TRIO"].payout_rate == 0.770
    assert POOL_RULES["TCE"].payout_rate == 0.750
    assert required_expected_value("QUARTET", 0.05) > required_expected_value("WIN", 0.05)
