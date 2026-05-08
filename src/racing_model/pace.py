from __future__ import annotations

from typing import Any


FRONT_STYLES = {"leader", "pace", "front", "on_pace", "front_runner"}
STALKER_STYLES = {"prominent", "stalker", "handy", "pace_presser"}
MIDFIELD_STYLES = {"midfield", "settle_midfield", "average"}
CLOSER_STYLES = {"closer", "backmarker", "hold_up", "hold-up", "rear", "deep_closer"}

PACE_ROLE_LABELS = {
    "front": "放頭/貼前",
    "stalker": "跟前",
    "midfield": "守中",
    "closer": "後上",
}

RISK_LABELS = {
    "low": "低",
    "medium": "中",
    "high": "高",
}

PACE_SHAPE_LABELS = {
    "slow": "偏慢",
    "moderate": "正常",
    "fast": "偏快",
}


def annotate_predictions_with_pace(predictions: list[dict[str, Any]]) -> dict[str, Any]:
    """Attach pace simulation fields to prediction rows in-place."""
    pace_map = build_pace_map(predictions)
    by_id = {str(row["horse_id"]): row for row in pace_map["runners"]}
    shape = pace_map["race_shape"]
    for prediction in predictions:
        horse_id = str(prediction.get("horse_id") or "")
        simulation = by_id.get(horse_id)
        if not simulation:
            continue
        prediction.update(
            {
                "pace_projected_position": simulation["projected_position"],
                "pace_role": simulation["pace_role"],
                "pace_role_label": simulation["pace_role_label"],
                "pace_score": simulation["early_speed_score"],
                "traffic_risk": simulation["traffic_risk"],
                "traffic_risk_label": simulation["traffic_risk_label"],
                "wide_risk": simulation["wide_risk"],
                "wide_risk_label": simulation["wide_risk_label"],
                "finishing_kick": simulation["finishing_kick"],
                "pace_advantage": simulation["pace_advantage"],
                "pace_note": simulation["pace_note"],
                "pace_shape": shape["shape"],
                "pace_shape_label": shape["label"],
                "pace_pressure_score": shape["pressure_score"],
            }
        )
    return pace_map


def build_pace_map(predictions: list[dict[str, Any]]) -> dict[str, Any]:
    if not predictions:
        return {
            "race_shape": {
                "shape": "unknown",
                "label": "未有資料",
                "pressure_score": 0.0,
                "leader_count": 0,
                "speed_count": 0,
                "runner_count": 0,
                "summary": "未有足夠馬匹資料模擬步速",
            },
            "runners": [],
        }

    runner_count = len(predictions)
    max_draw = max([safe_int(row.get("draw")) or 0 for row in predictions] + [runner_count, 1])
    max_probability = max(
        [safe_float(row.get("top3_probability")) or safe_float(row.get("win_probability")) or 0.0 for row in predictions]
        + [1e-9]
    )

    prepared = []
    for index, row in enumerate(predictions):
        draw = safe_int(row.get("draw")) or index + 1
        draw_pct = (draw - 1) / max(max_draw - 1, 1)
        style = normalize_style(row.get("running_style"))
        style_score = running_style_score(style)
        probability = safe_float(row.get("top3_probability")) or safe_float(row.get("win_probability")) or 0.0
        probability_score = probability / max_probability if max_probability > 0 else 0.0
        early_speed_score = clamp((0.58 * style_score) + (0.22 * (1.0 - draw_pct)) + (0.20 * probability_score))
        prepared.append(
            {
                "source": row,
                "horse_id": str(row.get("horse_id") or index),
                "horse_no": row.get("horse_no"),
                "horse_name": row.get("display_name") or row.get("horse_name_zh") or row.get("horse_name") or row.get("horse_id"),
                "draw": draw,
                "draw_pct": draw_pct,
                "style": style,
                "style_score": style_score,
                "probability_score": probability_score,
                "early_speed_score": early_speed_score,
            }
        )

    early_sorted = sorted(
        prepared,
        key=lambda item: (
            float(item["early_speed_score"]),
            safe_float(item["source"].get("win_probability")) or 0.0,
            -int(item["draw"]),
        ),
        reverse=True,
    )
    leader_count = sum(1 for item in prepared if float(item["style_score"]) >= 0.85)
    speed_count = sum(1 for item in prepared if float(item["early_speed_score"]) >= 0.68)
    pressure_score = clamp((leader_count / max(runner_count * 0.25, 1.0) * 0.45) + (speed_count / max(runner_count * 0.35, 1.0) * 0.55))
    if leader_count >= 3 or speed_count >= max(3, runner_count // 3 + 1) or pressure_score >= 0.72:
        shape = "fast"
    elif leader_count <= 1 and speed_count <= 1 and pressure_score <= 0.35:
        shape = "slow"
    else:
        shape = "moderate"

    simulated = []
    for projected_position, item in enumerate(early_sorted, start=1):
        role = projected_role(projected_position, runner_count, str(item["style"]))
        traffic_risk = runner_traffic_risk(item, projected_position, runner_count, shape)
        wide_risk = runner_wide_risk(item, projected_position, runner_count)
        finishing_kick = runner_finishing_kick(item, role)
        pace_advantage = runner_pace_advantage(item, role, shape, traffic_risk, wide_risk)
        simulated.append(
            {
                "horse_id": item["horse_id"],
                "horse_no": item["horse_no"],
                "horse_name": item["horse_name"],
                "draw": item["draw"],
                "running_style": item["style"],
                "projected_position": projected_position,
                "pace_role": role,
                "pace_role_label": PACE_ROLE_LABELS[role],
                "early_speed_score": round(float(item["early_speed_score"]), 4),
                "traffic_risk": round(traffic_risk, 4),
                "traffic_risk_label": risk_label(traffic_risk),
                "wide_risk": round(wide_risk, 4),
                "wide_risk_label": risk_label(wide_risk),
                "finishing_kick": round(finishing_kick, 4),
                "pace_advantage": round(pace_advantage, 4),
                "pace_note": pace_note(projected_position, runner_count, role, traffic_risk, wide_risk, pace_advantage),
            }
        )

    simulated_by_id = {row["horse_id"]: row for row in simulated}
    output_runners = [simulated_by_id[str(row.get("horse_id") or index)] for index, row in enumerate(predictions)]
    race_shape = {
        "shape": shape,
        "label": PACE_SHAPE_LABELS[shape],
        "pressure_score": round(pressure_score, 4),
        "leader_count": leader_count,
        "speed_count": speed_count,
        "runner_count": runner_count,
        "summary": race_shape_summary(shape, leader_count, speed_count),
    }
    return {"race_shape": race_shape, "runners": output_runners}


def exotic_pace_payload(
    horse_ids: list[str],
    runner_by_id: dict[str, dict[str, Any]],
    ordered: bool,
) -> dict[str, Any]:
    rows = [runner_by_id[str(horse_id)] for horse_id in horse_ids if str(horse_id) in runner_by_id]
    if not rows:
        return {
            "pace_fit_score": None,
            "pace_order_fit": None,
            "pace_risk_score": None,
            "pace_note": "",
            "pace_shape": None,
            "pace_shape_label": None,
        }

    risks = [safe_float(row.get("traffic_risk")) or 0.0 for row in rows]
    wide_risks = [safe_float(row.get("wide_risk")) or 0.0 for row in rows]
    max_risk = max([*risks, *wide_risks, 0.0])
    positions = [safe_int(row.get("pace_projected_position")) for row in rows]
    positions = [position for position in positions if position is not None]
    order_fit = ordered_order_fit(positions) if ordered and len(positions) == len(rows) else None
    role_diversity = len({str(row.get("pace_role") or "") for row in rows if row.get("pace_role")}) / max(len(rows), 1)
    base_fit = max(0.0, 1.0 - max_risk)
    pace_fit = (0.65 * (order_fit if order_fit is not None else base_fit)) + (0.35 * role_diversity)
    shape_label = rows[0].get("pace_shape_label")
    runner_bits = [
        f"{row.get('horse_no') or '-'} {row.get('pace_role_label') or '-'}"
        for row in rows
    ]
    fit_text = f"次序吻合 {round(order_fit * 100)}%" if order_fit is not None else f"角色分散 {round(role_diversity * 100)}%"
    note = f"節奏：{('、').join(runner_bits)}；{fit_text}；最高塞車/走外風險 {risk_label(max_risk)}"
    return {
        "pace_fit_score": round(pace_fit, 4),
        "pace_order_fit": round(order_fit, 4) if order_fit is not None else None,
        "pace_risk_score": round(max_risk, 4),
        "pace_note": note,
        "pace_shape": rows[0].get("pace_shape"),
        "pace_shape_label": shape_label,
    }


def ordered_order_fit(projected_positions: list[int]) -> float:
    if len(projected_positions) <= 1:
        return 1.0
    inversions = 0
    total_pairs = 0
    for left_index, left_position in enumerate(projected_positions):
        for right_position in projected_positions[left_index + 1 :]:
            total_pairs += 1
            if left_position > right_position:
                inversions += 1
    return clamp(1.0 - (inversions / max(total_pairs, 1)))


def projected_role(projected_position: int, runner_count: int, style: str) -> str:
    front_cutoff = max(1, round(runner_count * 0.20))
    stalker_cutoff = max(front_cutoff + 1, round(runner_count * 0.45))
    midfield_cutoff = max(stalker_cutoff + 1, round(runner_count * 0.75))
    if style in CLOSER_STYLES and projected_position > front_cutoff:
        return "closer" if projected_position > stalker_cutoff else "midfield"
    if projected_position <= front_cutoff:
        return "front"
    if projected_position <= stalker_cutoff:
        return "stalker"
    if projected_position <= midfield_cutoff:
        return "midfield"
    return "closer"


def runner_traffic_risk(item: dict[str, Any], projected_position: int, runner_count: int, shape: str) -> float:
    draw_pct = float(item["draw_pct"])
    risk = 0.08
    if projected_position > max(3, runner_count * 0.35):
        risk += 0.18
    if draw_pct <= 0.30 and projected_position > max(3, runner_count * 0.35):
        risk += 0.22
    if projected_position > runner_count * 0.45:
        risk += 0.12
    if shape == "fast" and projected_position <= max(3, runner_count * 0.35):
        risk += 0.12
    if str(item["style"]) in CLOSER_STYLES and runner_count >= 10:
        risk += 0.10
    return clamp(risk)


def runner_wide_risk(item: dict[str, Any], projected_position: int, runner_count: int) -> float:
    draw_pct = float(item["draw_pct"])
    risk = 0.05
    if draw_pct >= 0.70:
        risk += 0.25
    if draw_pct >= 0.70 and projected_position <= runner_count * 0.55:
        risk += 0.24
    if draw_pct >= 0.85:
        risk += 0.10
    return clamp(risk)


def runner_finishing_kick(item: dict[str, Any], role: str) -> float:
    style = str(item["style"])
    probability_score = float(item["probability_score"])
    base = 0.28 + (0.34 * probability_score)
    if style in CLOSER_STYLES or role == "closer":
        base += 0.24
    if role == "front":
        base -= 0.08
    return clamp(base)


def runner_pace_advantage(
    item: dict[str, Any],
    role: str,
    shape: str,
    traffic_risk: float,
    wide_risk: float,
) -> float:
    advantage = 0.0
    if role in {"front", "stalker"} and shape == "slow":
        advantage += 0.18
    if role == "front" and shape == "fast":
        advantage -= 0.14
    if role == "closer" and shape == "fast":
        advantage += 0.16
    if role == "closer" and shape == "slow":
        advantage -= 0.10
    if float(item["draw_pct"]) <= 0.30 and role in {"front", "stalker"}:
        advantage += 0.05
    advantage -= max(traffic_risk - 0.35, 0.0) * 0.22
    advantage -= max(wide_risk - 0.35, 0.0) * 0.18
    same_day_bias = safe_float(item["source"].get("same_day_pace_bias")) or 0.0
    if role in {"front", "stalker"}:
        advantage += same_day_bias * 0.08
    elif role == "closer":
        advantage -= same_day_bias * 0.05
    return max(-0.35, min(advantage, 0.35))


def pace_note(
    projected_position: int,
    runner_count: int,
    role: str,
    traffic_risk: float,
    wide_risk: float,
    pace_advantage: float,
) -> str:
    risk = max(traffic_risk, wide_risk)
    if pace_advantage >= 0.08:
        edge = "節奏有利"
    elif pace_advantage <= -0.08:
        edge = "節奏扣分"
    else:
        edge = "節奏中性"
    return f"預計第 {projected_position}/{runner_count} 位，{PACE_ROLE_LABELS[role]}；塞車/走外風險{risk_label(risk)}；{edge}"


def race_shape_summary(shape: str, leader_count: int, speed_count: int) -> str:
    label = PACE_SHAPE_LABELS.get(shape, "未明")
    return f"早段步速{label}，前置馬 {leader_count} 匹，速度候選 {speed_count} 匹"


def running_style_score(style: str) -> float:
    if style in FRONT_STYLES:
        return 1.0
    if style in STALKER_STYLES:
        return 0.72
    if style in MIDFIELD_STYLES:
        return 0.46
    if style in CLOSER_STYLES:
        return 0.16
    return 0.40


def normalize_style(value: Any) -> str:
    return str(value or "unknown").strip().lower().replace(" ", "_")


def risk_label(value: float) -> str:
    if value >= 0.62:
        return RISK_LABELS["high"]
    if value >= 0.34:
        return RISK_LABELS["medium"]
    return RISK_LABELS["low"]


def safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def safe_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(float(value), upper))
