from racing_model.app_server import betting_settlement_payload


def test_betting_settlement_payload_groups_hit_miss_and_pending() -> None:
    ledger = {
        "items": [
            item("WIN", "獨贏", "馬1", 1, 30, 80),
            item("QPL", "位置Q", "1 + 3", 0, 20, -20),
            item("TRIO", "單T", "1 + 2 + 3", None, 10, None, status="pending"),
        ]
    }

    settlement = betting_settlement_payload(ledger)

    assert settlement["summary"]["tickets"] == 3
    assert settlement["summary"]["hit"] == 1
    assert settlement["summary"]["miss"] == 1
    assert settlement["summary"]["pending"] == 1
    assert settlement["summary"]["profit"] == 60
    assert "待派彩" in settlement["note"]


def item(
    market: str,
    market_label: str,
    horse_name: str,
    outcome: int | None,
    stake: float,
    profit: float | None,
    status: str = "reconciled",
) -> dict[str, object]:
    return {
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
