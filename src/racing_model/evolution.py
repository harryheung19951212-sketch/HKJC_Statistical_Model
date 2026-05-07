from __future__ import annotations

import json
import math
import shutil
import sqlite3
import subprocess
from typing import Any

from .features import build_race_features
from .model import RankingModel
from .storage import fetch_all


CALIBRATION_BINS = [
    (0.00, 0.05),
    (0.05, 0.10),
    (0.10, 0.15),
    (0.15, 0.20),
    (0.20, 0.30),
    (0.30, 0.50),
    (0.50, 1.01),
]


def evaluate_model_evolution(
    conn: sqlite3.Connection,
    model: RankingModel,
    min_expected_value: float = 0.05,
) -> dict[str, Any]:
    race_ids = resulted_race_ids(conn)
    bins = [new_calibration_bin(low, high) for low, high in CALIBRATION_BINS]
    diagnostics: list[dict[str, Any]] = []
    races_evaluated = 0
    runners_evaluated = 0
    top_pick_wins = 0
    log_loss_total = 0.0
    brier_total = 0.0
    winner_probability_total = 0.0
    value_bets = 0
    value_wins = 0
    value_staked = 0.0
    value_returned = 0.0

    for race_id in race_ids:
        runners = build_race_features(conn, race_id)
        known = [runner for runner in runners if runner.finish_position is not None]
        if not known:
            continue
        predictions = model.predict_race(known)
        if not predictions:
            continue

        result_by_horse = {runner.horse_id: int(runner.finish_position or 99) for runner in known}
        winner_rows = [runner for runner in known if runner.finish_position == 1]
        if not winner_rows:
            continue

        races_evaluated += 1
        runners_evaluated += len(predictions)
        winner = winner_rows[0]
        winner_prediction = next(row for row in predictions if row["horse_id"] == winner.horse_id)
        winner_probability = max(float(winner_prediction["win_probability"]), 1e-9)
        winner_rank = predictions.index(winner_prediction) + 1
        winner_probability_total += winner_probability
        log_loss_total += -math.log(winner_probability)
        brier_total += sum(
            (float(row["win_probability"]) - (1.0 if row["horse_id"] == winner.horse_id else 0.0)) ** 2
            for row in predictions
        )

        top_pick = predictions[0]
        top_pick_finish = result_by_horse.get(str(top_pick["horse_id"]), 99)
        if top_pick_finish == 1:
            top_pick_wins += 1

        for row in predictions:
            probability = float(row["win_probability"])
            outcome = 1.0 if row["horse_id"] == winner.horse_id else 0.0
            add_to_calibration(bins, probability, outcome)
            ev = row["expected_value"]
            odds = row["latest_win_odds"]
            if ev is not None and float(ev) >= min_expected_value and odds is not None:
                value_bets += 1
                value_staked += 10.0
                if outcome:
                    value_wins += 1
                    value_returned += 10.0 * float(odds)

        if top_pick_finish != 1 or winner_rank > 3:
            diagnostics.append(
                build_race_diagnostic(
                    race_id=race_id,
                    predictions=predictions,
                    winner_prediction=winner_prediction,
                    winner_rank=winner_rank,
                    top_pick=top_pick,
                    top_pick_finish=top_pick_finish,
                )
            )

    finalized_bins = finalize_calibration_bins(bins)
    metrics = {
        "races": races_evaluated,
        "runners": runners_evaluated,
        "top_pick_wins": top_pick_wins,
        "top_pick_hit_rate": safe_div(top_pick_wins, races_evaluated),
        "mean_winner_probability": safe_div(winner_probability_total, races_evaluated),
        "log_loss": safe_div(log_loss_total, races_evaluated),
        "brier_score": safe_div(brier_total, races_evaluated),
        "value_bets": value_bets,
        "value_wins": value_wins,
        "value_hit_rate": safe_div(value_wins, value_bets),
        "value_roi": safe_div(value_returned - value_staked, value_staked),
    }
    ideas = generate_improvement_ideas(metrics, finalized_bins, diagnostics, model)
    return {
        "metrics": metrics,
        "calibration": finalized_bins,
        "diagnostics": diagnostics[:12],
        "feature_weights": feature_weight_summary(model),
        "ideas": ideas,
        "data_quality": data_quality_notes(metrics),
    }


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


def new_calibration_bin(low: float, high: float) -> dict[str, Any]:
    return {
        "low": low,
        "high": high,
        "label": f"{int(low * 100)}-{int(min(high, 1.0) * 100)}%",
        "count": 0,
        "expected_wins": 0.0,
        "actual_wins": 0.0,
        "avg_prediction": 0.0,
        "observed_rate": 0.0,
        "gap": 0.0,
    }


def add_to_calibration(bins: list[dict[str, Any]], probability: float, outcome: float) -> None:
    for item in bins:
        if float(item["low"]) <= probability < float(item["high"]):
            item["count"] += 1
            item["expected_wins"] += probability
            item["actual_wins"] += outcome
            return


def finalize_calibration_bins(bins: list[dict[str, Any]]) -> list[dict[str, Any]]:
    finalized = []
    for item in bins:
        count = int(item["count"])
        row = dict(item)
        if count:
            row["avg_prediction"] = float(item["expected_wins"]) / count
            row["observed_rate"] = float(item["actual_wins"]) / count
            row["gap"] = row["observed_rate"] - row["avg_prediction"]
        finalized.append(row)
    return finalized


def build_race_diagnostic(
    race_id: str,
    predictions: list[dict[str, Any]],
    winner_prediction: dict[str, Any],
    winner_rank: int,
    top_pick: dict[str, Any],
    top_pick_finish: int,
) -> dict[str, Any]:
    tags = []
    if winner_rank > 3:
        tags.append("低估頭馬")
    if float(top_pick["win_probability"]) >= 0.25 and top_pick_finish != 1:
        tags.append("首選過熱")
    if float(winner_prediction["market_probability"]) > float(winner_prediction["win_probability"]):
        tags.append("低估市場熱門")
    if winner_prediction["latest_win_odds"] and float(winner_prediction["latest_win_odds"]) >= 10:
        tags.append("冷門突圍")

    return {
        "race_id": race_id,
        "winner": winner_prediction.get("display_name") or winner_prediction.get("horse_name"),
        "winner_horse_no": winner_prediction.get("horse_no"),
        "winner_rank": winner_rank,
        "winner_probability": winner_prediction["win_probability"],
        "winner_odds": winner_prediction["latest_win_odds"],
        "top_pick": top_pick.get("display_name") or top_pick.get("horse_name"),
        "top_pick_horse_no": top_pick.get("horse_no"),
        "top_pick_probability": top_pick["win_probability"],
        "top_pick_finish": top_pick_finish,
        "tags": tags or ["首選落敗"],
        "top_three": [
            {
                "horse_no": row.get("horse_no"),
                "name": row.get("display_name") or row.get("horse_name"),
                "probability": row["win_probability"],
                "odds": row["latest_win_odds"],
            }
            for row in predictions[:3]
        ],
    }


def feature_weight_summary(model: RankingModel) -> list[dict[str, Any]]:
    from .model import FEATURE_LABELS_ZH

    return [
        {
            "name": name,
            "label": FEATURE_LABELS_ZH.get(name, name),
            "weight": model.weights.get(name, 0.0),
            "direction": "positive" if model.weights.get(name, 0.0) >= 0 else "negative",
        }
        for name in sorted(model.feature_names, key=lambda item: abs(model.weights.get(item, 0.0)), reverse=True)
    ]


def generate_improvement_ideas(
    metrics: dict[str, Any],
    calibration: list[dict[str, Any]],
    diagnostics: list[dict[str, Any]],
    model: RankingModel,
) -> list[dict[str, str]]:
    ideas = []
    race_count = int(metrics["races"])
    if race_count < 30:
        ideas.append(
            {
                "title": "擴大歷史樣本",
                "reason": "目前已賽結果太少，單日結果容易令模型誤判。",
                "action": "先回填更多同馬場、同路程、同季節賽事，再做版本升級判斷。",
            }
        )

    overconfident = [row for row in calibration if int(row["count"]) >= 5 and float(row["gap"]) < -0.08]
    underconfident = [row for row in calibration if int(row["count"]) >= 5 and float(row["gap"]) > 0.08]
    if overconfident:
        ideas.append(
            {
                "title": "降低過度自信",
                "reason": "部分勝率區間實際勝出率低過模型預測。",
                "action": "加入溫度校準或縮細 softmax 分數差，避免首選勝率被推得太高。",
            }
        )
    if underconfident:
        ideas.append(
            {
                "title": "補捉被低估馬",
                "reason": "部分低至中勝率區間實際勝出率高過模型預測。",
                "action": "檢查冷門勝出場次，加入臨場賠率走勢、晨操、轉場地同跑法形勢特徵。",
            }
        )

    missed_longshots = sum(1 for item in diagnostics if "冷門突圍" in item["tags"])
    if missed_longshots:
        ideas.append(
            {
                "title": "冷門爆出分析",
                "reason": f"有 {missed_longshots} 場屬於冷門突圍或模型低估頭馬。",
                "action": "建立賽後錯誤標籤，逐場記錄是否受步速、檔位、場地偏差或騎練轉變影響。",
            }
        )

    if abs(model.weights.get("market_implied", 0.0)) > 2.0:
        ideas.append(
            {
                "title": "檢查市場權重",
                "reason": "市場賠率特徵權重偏大，模型可能太貼近公眾賠率。",
                "action": "做一個無賠率模型版本，比較純賽事因素同市場融合版本嘅 out-of-sample 表現。",
            }
        )

    if not ideas:
        ideas.append(
            {
                "title": "保持現有版本",
                "reason": "暫時未見明顯校準偏差。",
                "action": "繼續累積結果，下一步優先做 walk-forward 回測同賽事類型分組。",
            }
        )
    return ideas


def data_quality_notes(metrics: dict[str, Any]) -> list[str]:
    notes = []
    if int(metrics["races"]) < 30:
        notes.append("目前結果樣本偏少，所有升級建議應視為研究假設。")
    if int(metrics["value_bets"]) == 0:
        notes.append("暫時未有符合 EV 門檻嘅投注樣本，ROI 未能反映策略能力。")
    return notes


def build_gpt_iteration_prompt(report: dict[str, Any]) -> str:
    compact = {
        "metrics": report["metrics"],
        "calibration": report["calibration"],
        "diagnostics": report["diagnostics"][:8],
        "feature_weights": report["feature_weights"][:10],
        "local_ideas": report["ideas"],
        "data_quality": report["data_quality"],
        "model_versions": report.get("model_versions"),
    }
    return (
        "你係香港賽馬量化模型研究主管。請用繁體中文/廣東話分析以下回測、校準同錯誤診斷。"
        "目標係提高未來選馬準確度，但必須避免數據洩漏同過度擬合。"
        "請輸出：1) 現有模型最大問題，2) 三個下一輪實驗，3) 是否值得升級模型，"
        "4) 需要新增嘅資料或特徵。唔好建議自動下注，唔好聲稱保證盈利。\n\n"
        f"{json.dumps(compact, ensure_ascii=False, indent=2)}"
    )


def generate_codex_iteration(
    codex_command: str,
    model_name: str,
    reasoning_effort: str,
    report: dict[str, Any],
    timeout_seconds: int = 180,
) -> str:
    prompt = build_gpt_iteration_prompt(report)
    executable = resolve_codex_command(codex_command)
    command = [
        executable,
        "exec",
        "--ephemeral",
        "--sandbox",
        "read-only",
        "--color",
        "never",
    ]
    if model_name:
        command.extend(["--model", model_name])
    if reasoning_effort:
        command.extend(["-c", f'model_reasoning_effort="{reasoning_effort}"'])
    completed = subprocess.run(
        command,
        input=prompt,
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=timeout_seconds,
        check=False,
    )
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or "Codex CLI failed.").strip()
        raise RuntimeError(message)
    return completed.stdout.strip()


def resolve_codex_command(command: str) -> str:
    if command.lower() == "codex":
        return shutil.which("codex.cmd") or shutil.which("codex.CMD") or shutil.which("codex") or command
    return shutil.which(command) or command


def generate_openai_iteration(api_key: str, model_name: str, report: dict[str, Any]) -> str:
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("Install the openai package to generate GPT iteration notes.") from exc

    client = OpenAI(api_key=api_key)
    response = client.responses.create(
        model=model_name,
        input=build_gpt_iteration_prompt(report),
    )
    return response.output_text


def safe_div(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0
