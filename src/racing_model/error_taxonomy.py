from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from .features import build_race_features
from .model import RankingModel
from .storage import fetch_all


CATEGORY_LABELS = {
    "pace_error": "步速錯判",
    "track_bias_error": "跑道偏差錯判",
    "condition_error": "狀態/近績錯判",
    "market_error": "市場訊號錯判",
    "trip_or_sectional_error": "走位/分段錯判",
    "data_quality_error": "資料質素問題",
    "calibration_error": "機率校準問題",
    "no_edge_discipline": "No-bet / EV 紀律",
}


def error_taxonomy_report(
    conn: sqlite3.Connection,
    model: RankingModel,
    min_expected_value: float = 0.05,
    refresh: bool = False,
) -> dict[str, Any]:
    reviews = [] if refresh else persisted_error_reviews(conn)
    if not reviews:
        reviews = sync_error_reviews(conn, model, min_expected_value=min_expected_value)
    return {
        "summary": summarize_reviews(reviews),
        "categories": category_rows(reviews),
        "recommendations": recommendation_rows(reviews),
        "reviews": reviews[:80],
    }


def sync_error_reviews(
    conn: sqlite3.Connection,
    model: RankingModel,
    min_expected_value: float = 0.05,
) -> list[dict[str, Any]]:
    reviews: list[dict[str, Any]] = []
    for race_id in resulted_race_ids(conn):
        reviews.extend(classify_race_errors(conn, model, race_id, min_expected_value=min_expected_value))
    replace_persisted_reviews(conn, reviews)
    return sorted(reviews, key=lambda row: (row["race_id"], row["review_key"]), reverse=True)


def classify_race_errors(
    conn: sqlite3.Connection,
    model: RankingModel,
    race_id: str,
    min_expected_value: float = 0.05,
) -> list[dict[str, Any]]:
    runners = build_race_features(conn, race_id)
    known = [runner for runner in runners if runner.finish_position is not None]
    if not known:
        return []
    predictions = model.predict_race(known)
    if not predictions:
        return []

    result_by_horse = {str(runner.horse_id): int(runner.finish_position or 99) for runner in known}
    prediction_by_horse = {str(row["horse_id"]): {**row, "model_rank": index + 1} for index, row in enumerate(predictions)}
    winner = next((runner for runner in known if int(runner.finish_position or 99) == 1), None)
    if not winner:
        return []
    winner_prediction = prediction_by_horse[str(winner.horse_id)]
    top_pick = predictions[0]
    top_pick = {**top_pick, "model_rank": 1}
    top_pick_finish = result_by_horse.get(str(top_pick["horse_id"]), 99)
    reviews: list[dict[str, Any]] = []

    if top_pick_finish != 1:
        reviews.extend(
            top_pick_reviews(
                race_id,
                top_pick,
                top_pick_finish,
                winner_prediction,
                prediction_by_horse,
            )
        )

    for row in predictions:
        ev = row.get("expected_value")
        odds = row.get("latest_win_odds")
        finish_position = result_by_horse.get(str(row["horse_id"]), 99)
        if ev is None or odds is None or float(ev) < min_expected_value or finish_position == 1:
            continue
        reviews.extend(value_bet_reviews(race_id, {**row, "model_rank": predictions.index(row) + 1}, finish_position))

    return dedupe_reviews(reviews)


def top_pick_reviews(
    race_id: str,
    top_pick: dict[str, Any],
    top_pick_finish: int,
    winner: dict[str, Any],
    prediction_by_horse: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    reviews = []
    top_probability = float(top_pick.get("win_probability") or 0.0)
    winner_probability = float(winner.get("win_probability") or 0.0)
    top_market = float(top_pick.get("market_probability") or 0.0)
    winner_market = float(winner.get("market_probability") or 0.0)
    top_pace = float(top_pick.get("same_day_pace_bias") or 0.0)
    winner_pace = float(winner.get("same_day_pace_bias") or 0.0)
    top_draw_bias = float(top_pick.get("same_day_inside_bias") or 0.0) + float(top_pick.get("same_day_outside_bias") or 0.0)
    winner_draw_bias = float(winner.get("same_day_inside_bias") or 0.0) + float(winner.get("same_day_outside_bias") or 0.0)
    top_steam = float(top_pick.get("late_steam") or 0.0) + float(top_pick.get("late_drift") or 0.0)
    winner_steam = float(winner.get("late_steam") or 0.0) + float(winner.get("late_drift") or 0.0)

    if top_probability >= 0.25:
        reviews.append(
            review(
                race_id,
                "top_pick_calibration",
                "calibration_error",
                "high" if top_probability >= 0.35 else "medium",
                top_pick,
                top_pick_finish,
                f"首選勝率 {top_probability:.1%} 但只跑第 {top_pick_finish}；頭馬模型勝率 {winner_probability:.1%}。",
                "檢查 softmax/temperature calibration；高勝率首選落敗應進入 walk-forward promotion gate。",
            )
        )

    if winner_market > winner_probability + 0.03:
        reviews.append(
            review(
                race_id,
                "winner_market_underread",
                "market_error",
                "medium",
                winner,
                1,
                f"頭馬市場機率 {winner_market:.1%} 高過模型勝率 {winner_probability:.1%}。",
                "檢查市場基線、late-flow 和 no-market variant；模型不可長期低估市場清楚支持的馬。",
            )
        )

    if winner_steam > top_steam + 0.04:
        reviews.append(
            review(
                race_id,
                "late_money_underread",
                "market_error",
                "medium",
                winner,
                1,
                f"頭馬 late-flow {winner_steam:+.3f} 明顯強過首選 {top_steam:+.3f}。",
                "驗證最後 5/2/0.5 分鐘賠率流特徵權重；需要更多賽日 ticks 才可升 stake。",
            )
        )

    if winner_draw_bias > top_draw_bias + 0.10:
        reviews.append(
            review(
                race_id,
                "track_bias_underread",
                "track_bias_error",
                "medium",
                winner,
                1,
                f"頭馬同日檔位 bias {winner_draw_bias:+.3f} 強過首選 {top_draw_bias:+.3f}。",
                "用 No Track Bias variant replay；同日 bias 未過樣本門檻前只應降 stake，不應硬推排名。",
            )
        )

    if winner_pace > top_pace + 0.10:
        reviews.append(
            review(
                race_id,
                "pace_bias_underread",
                "pace_error",
                "medium",
                winner,
                1,
                f"頭馬同日跑法 bias {winner_pace:+.3f} 強過首選 {top_pace:+.3f}。",
                "加強歷史 running-position 推斷，並把 pace error 作為下一輪 feature 實驗目標。",
            )
        )

    if winner.get("latest_win_odds") and float(winner["latest_win_odds"]) >= 10:
        reviews.append(
            review(
                race_id,
                "longshot_condition_or_trip",
                "condition_error",
                "medium",
                winner,
                1,
                f"頭馬賠率 {float(winner['latest_win_odds']):.1f}，屬冷門突圍。",
                "檢查近績、操練、班次/路程轉變及結果 comment；冷門勝出不可只靠加大市場權重處理。",
            )
        )

    trip_review = trip_comment_review(race_id, top_pick, top_pick_finish)
    if trip_review:
        reviews.append(trip_review)

    data_review = data_quality_review(race_id, top_pick, top_pick_finish)
    if data_review:
        reviews.append(data_review)

    if not reviews:
        reviews.append(
            review(
                race_id,
                "top_pick_unclassified",
                "no_edge_discipline",
                "low",
                top_pick,
                top_pick_finish,
                "首選落敗，但現有資料未能支持單一錯誤原因。",
                "保持 no-bet 紀律；需要新增 trip notes、sectionals 或 visual condition 才能分類。",
            )
        )
    return reviews


def value_bet_reviews(race_id: str, row: dict[str, Any], finish_position: int) -> list[dict[str, Any]]:
    reviews = []
    probability = float(row.get("win_probability") or 0.0)
    ev = float(row.get("expected_value") or 0.0)
    edge = float(row.get("value_gap") or 0.0)
    if probability >= 0.20:
        reviews.append(
            review(
                race_id,
                f"value_calibration_{row.get('horse_id')}",
                "calibration_error",
                "medium",
                row,
                finish_position,
                f"Positive EV bet 勝率 {probability:.1%}、EV {ev:+.3f}，但跑第 {finish_position}。",
                "把 losing value bets 納入 reliability bins；未校準前降低 Kelly fraction。",
            )
        )
    if edge < 0.03:
        reviews.append(
            review(
                race_id,
                f"thin_edge_{row.get('horse_id')}",
                "no_edge_discipline",
                "medium",
                row,
                finish_position,
                f"Value bet edge 只有 {edge:+.1%}，安全邊際偏薄。",
                "提高 min_edge 或按 calibration uncertainty 動態提高下注門檻。",
            )
        )
    if float(row.get("same_day_pace_bias") or 0.0) < -0.05:
        reviews.append(
            review(
                race_id,
                f"value_pace_negative_{row.get('horse_id')}",
                "pace_error",
                "low",
                row,
                finish_position,
                f"Value bet 面對負面同日跑法 bias {float(row.get('same_day_pace_bias') or 0.0):+.3f}。",
                "下注 gate 應檢查同日 bias 是否抵消模型 edge。",
            )
        )
    return reviews


def trip_comment_review(race_id: str, row: dict[str, Any], finish_position: int) -> dict[str, Any] | None:
    comments = str(row.get("result_comment") or row.get("comment") or "").lower()
    trip_terms = ["held up", "checked", "wide", "blocked", "hampered", "eased", "slow"]
    if not any(term in comments for term in trip_terms):
        return None
    return review(
        race_id,
        f"trip_comment_{row.get('horse_id')}",
        "trip_or_sectional_error",
        "low",
        row,
        finish_position,
        f"賽果 comment 含走位/阻滯訊號：{comments[:120]}",
        "將 result comments 正規化成 trip tags，避免把 unlucky run 當作能力下降。",
    )


def data_quality_review(race_id: str, row: dict[str, Any], finish_position: int) -> dict[str, Any] | None:
    missing = []
    if row.get("draw") in {None, 0, ""}:
        missing.append("draw")
    if not row.get("display_jockey") and not row.get("jockey"):
        missing.append("jockey")
    if not row.get("display_trainer") and not row.get("trainer"):
        missing.append("trainer")
    explanation = row.get("explanation") or {}
    factors = explanation.get("all") or []
    if not factors:
        missing.append("feature_explanation")
    if not missing:
        return None
    return review(
        race_id,
        f"data_quality_{row.get('horse_id')}",
        "data_quality_error",
        "medium",
        row,
        finish_position,
        f"首選資料缺口：{', '.join(missing)}。",
        "先修資料完整性再判斷模型錯誤，避免用低質量 features 做權重迭代。",
    )


def review(
    race_id: str,
    review_key: str,
    category: str,
    severity: str,
    row: dict[str, Any],
    finish_position: int,
    evidence: str,
    recommendation: str,
) -> dict[str, Any]:
    return {
        "race_id": race_id,
        "review_key": review_key,
        "category": category,
        "category_label": CATEGORY_LABELS.get(category, category),
        "severity": severity,
        "horse_id": row.get("horse_id"),
        "horse_no": row.get("horse_no"),
        "horse_name": row.get("display_name") or row.get("horse_name_zh") or row.get("horse_name") or "",
        "model_rank": row.get("model_rank"),
        "finish_position": finish_position,
        "probability": round(float(row.get("win_probability") or 0.0), 6),
        "expected_value": round(float(row["expected_value"]), 6) if row.get("expected_value") is not None else None,
        "odds": float(row["latest_win_odds"]) if row.get("latest_win_odds") is not None else None,
        "evidence": evidence,
        "recommendation": recommendation,
    }


def replace_persisted_reviews(conn: sqlite3.Connection, reviews: list[dict[str, Any]]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("DELETE FROM race_error_reviews")
    for row in reviews:
        conn.execute(
            """
            INSERT OR REPLACE INTO race_error_reviews (
              race_id, review_key, category, severity, horse_id, horse_no, horse_name,
              model_rank, finish_position, probability, expected_value, odds,
              evidence, recommendation, created_at, updated_at
            )
            VALUES (
              :race_id, :review_key, :category, :severity, :horse_id, :horse_no, :horse_name,
              :model_rank, :finish_position, :probability, :expected_value, :odds,
              :evidence, :recommendation, :created_at, :updated_at
            )
            """,
            {**row, "created_at": now, "updated_at": now},
        )
    conn.commit()


def summarize_reviews(reviews: list[dict[str, Any]]) -> dict[str, Any]:
    race_ids = {row["race_id"] for row in reviews}
    high = sum(1 for row in reviews if row["severity"] == "high")
    medium = sum(1 for row in reviews if row["severity"] == "medium")
    return {
        "reviewed_races": len(race_ids),
        "review_count": len(reviews),
        "high_severity": high,
        "medium_severity": medium,
        "top_category": top_category(reviews),
    }


def category_rows(reviews: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, dict[str, Any]] = {}
    for row in reviews:
        category = str(row["category"])
        item = counts.setdefault(
            category,
            {
                "category": category,
                "label": CATEGORY_LABELS.get(category, category),
                "count": 0,
                "high": 0,
                "medium": 0,
                "sample_evidence": row["evidence"],
            },
        )
        item["count"] += 1
        if row["severity"] == "high":
            item["high"] += 1
        if row["severity"] == "medium":
            item["medium"] += 1
    return sorted(counts.values(), key=lambda item: (int(item["count"]), int(item["high"])), reverse=True)


def recommendation_rows(reviews: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for category in category_rows(reviews)[:5]:
        related = [row for row in reviews if row["category"] == category["category"]]
        rows.append(
            {
                "category": category["category"],
                "label": category["label"],
                "count": category["count"],
                "next_step": most_common_text([row["recommendation"] for row in related]),
            }
        )
    return rows


def top_category(reviews: list[dict[str, Any]]) -> str | None:
    rows = category_rows(reviews)
    return rows[0]["category"] if rows else None


def most_common_text(values: list[str]) -> str:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return sorted(counts, key=lambda value: counts[value], reverse=True)[0] if counts else ""


def dedupe_reviews(reviews: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    output = []
    for row in reviews:
        key = (row["race_id"], row["review_key"])
        if key in seen:
            continue
        seen.add(key)
        output.append(row)
    return output


def resulted_race_ids(conn: sqlite3.Connection) -> list[str]:
    return [
        row["race_id"]
        for row in fetch_all(
            conn,
            """
            SELECT r.race_id
            FROM races r
            WHERE EXISTS (SELECT 1 FROM results x WHERE x.race_id = r.race_id)
            ORDER BY r.date, r.race_id
            """,
        )
    ]


def persisted_error_reviews(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = fetch_all(
        conn,
        """
        SELECT *
        FROM race_error_reviews
        ORDER BY updated_at DESC, race_id DESC, review_key
        """,
    )
    output = []
    for row in rows:
        item = dict(row)
        item["category_label"] = CATEGORY_LABELS.get(str(item["category"]), str(item["category"]))
        output.append(item)
    return output


def report_to_json(report: dict[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2)
