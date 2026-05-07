from __future__ import annotations

import json
from typing import Any


def build_report_prompt(race_id: str, predictions: list[dict[str, Any]]) -> str:
    data = json.dumps(predictions[:8], ensure_ascii=False, indent=2)
    return (
        "你係一個嚴謹嘅香港賽馬量化分析員。"
        "根據以下模型輸出，用繁體中文/廣東話寫一份賽前簡報。"
        "要清楚講：模型首選、value bet、主要風險、唔應過度下注嘅原因。"
        "唔好聲稱一定會贏，唔好建議自動下注。\n\n"
        f"Race ID: {race_id}\n"
        f"Model output:\n{data}\n"
    )


def generate_report(api_key: str, model: str, race_id: str, predictions: list[dict[str, Any]]) -> str:
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("Install the openai package to generate GPT reports.") from exc

    client = OpenAI(api_key=api_key)
    response = client.responses.create(
        model=model,
        input=build_report_prompt(race_id, predictions),
    )
    return response.output_text

