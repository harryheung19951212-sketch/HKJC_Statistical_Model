from racing_model.app_server import betting_settlement_payload


def test_betting_settlement_payload_groups_hit_miss_and_pending() -> None:
    ledger = {
        "items": [
            item("WIN", "獨贏", "測試馬", 1, 30, 80),
            item("QPL", "位置Q", "1 + 3", 0, 20, -20),
            item("TRIO", "單T", "1 + 2 + 3", None, 10, None, status="pending"),
        ]
    }

    settlement = betting_settlement_payload(ledger)

    assert settlement["summary"]["tickets"] == 3
    assert settlement["summary"]["raw_tickets"] == 3
    assert settlement["summary"]["hit"] == 1
    assert settlement["summary"]["miss"] == 1
    assert settlement["summary"]["pending"] == 1
    assert settlement["summary"]["profit"] == 60
    assert "組合贏票" in settlement["note"]


def test_betting_settlement_payload_dedupes_logical_ticket_to_latest() -> None:
    older = item("WIN", "獨贏", "測試馬", 0, 30, -30, created_at="2026-05-08T10:00:00+00:00")
    newer = item("WIN", "獨贏", "測試馬", 1, 40, 100, created_at="2026-05-08T10:05:00+00:00")
    ledger = {"items": [older, newer]}

    settlement = betting_settlement_payload(ledger)

    assert settlement["summary"]["raw_tickets"] == 2
    assert settlement["summary"]["tickets"] == 1
    assert settlement["summary"]["hit"] == 1
    assert settlement["summary"]["profit"] == 100
    assert settlement["items"][0]["recommended_stake"] == 40


def item(
    market: str,
    market_label: str,
    horse_name: str,
    outcome: int | None,
    stake: float,
    profit: float | None,
    status: str = "reconciled",
    created_at: str = "2026-05-08T10:00:00+00:00",
) -> dict[str, object]:
    return {
        "recommendation_id": f"{market}-{horse_name}-{created_at}",
        "created_at": created_at,
        "race_id": "HK20260509-ST-01",
        "risk_profile": "standard",
        "model_path": "models/baseline.json",
        "market": market,
        "market_label": market_label,
        "horse_id": horse_name,
        "horse_name": horse_name,
        "recommended_stake": stake,
        "returned": stake + profit if profit is not None else None,
        "profit": profit,
        "outcome_win": outcome,
        "reconciliation_status": status,
    }
