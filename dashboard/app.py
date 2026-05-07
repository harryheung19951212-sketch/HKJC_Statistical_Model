from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import streamlit as st

from racing_model.config import get_settings
from racing_model.features import build_race_features
from racing_model.model import RankingModel
from racing_model.storage import connect, fetch_all


st.set_page_config(page_title="Racing Model", layout="wide")
st.title("Racing Model")

settings = get_settings()
model_path = st.sidebar.text_input("Model path", "models/baseline.json")

with connect(settings.db_path) as conn:
    races = fetch_all(conn, "SELECT * FROM races ORDER BY date DESC, race_id DESC")
    race_options = [row["race_id"] for row in races]

    if not race_options:
        st.warning("No races found. Run `racing-model import-sample` first.")
        st.stop()

    race_id = st.sidebar.selectbox("Race", race_options)
    race = next(row for row in races if row["race_id"] == race_id)

    st.caption(
        f"{race['date']} | {race['track']} | {race['course']} | "
        f"{race['distance_m']}m | {race['going']} | {race['class_rating']}"
    )

    runners = build_race_features(conn, race_id)

if not Path(model_path).exists():
    st.warning("Model not found. Run `racing-model train --model-path models/baseline.json`.")
    st.stop()

model = RankingModel.load(model_path)
predictions = model.predict_race(runners)

st.subheader("Predictions")
st.dataframe(
    [
        {
            "Horse": row["horse_name"],
            "Win %": round(float(row["win_probability"]) * 100, 1),
            "Odds": row["latest_win_odds"],
            "Market %": round(float(row["market_probability"]) * 100, 1),
            "EV": None if row["expected_value"] is None else round(float(row["expected_value"]), 3),
            "Value gap": round(float(row["value_gap"]) * 100, 1),
        }
        for row in predictions
    ],
    use_container_width=True,
)

st.subheader("Model Factors")
selected = st.selectbox("Horse", [row.horse_name for row in runners])
runner = next(row for row in runners if row.horse_name == selected)
st.json(runner.features)

with st.expander("Raw prediction JSON"):
    st.code(json.dumps(predictions, indent=2, ensure_ascii=False), language="json")

