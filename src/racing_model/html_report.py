from __future__ import annotations

import html
from pathlib import Path
from typing import Any


def export_race_report(
    race: dict[str, Any],
    predictions: list[dict[str, Any]],
    out_path: Path | str,
) -> Path:
    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_race_report(race, predictions), encoding="utf-8")
    return target


def render_race_report(race: dict[str, Any], predictions: list[dict[str, Any]]) -> str:
    rows = "\n".join(render_prediction_row(row, index + 1) for index, row in enumerate(predictions))
    title = f"{race['race_id']} {race['track']} {race['distance_m']}m"
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    :root {{
      color-scheme: light;
      --ink: #18212f;
      --muted: #5d6878;
      --line: #d9dde5;
      --panel: #f7f8fa;
      --accent: #126c58;
      --warn: #a44821;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Arial, "Microsoft JhengHei", sans-serif;
      color: var(--ink);
      background: #ffffff;
    }}
    header {{
      padding: 28px 32px 18px;
      border-bottom: 1px solid var(--line);
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: 28px;
      letter-spacing: 0;
    }}
    .meta {{
      color: var(--muted);
      font-size: 14px;
      display: flex;
      gap: 14px;
      flex-wrap: wrap;
    }}
    main {{ padding: 24px 32px 40px; }}
    table {{
      width: 100%;
      border-collapse: collapse;
      table-layout: fixed;
      font-size: 14px;
    }}
    th, td {{
      border-bottom: 1px solid var(--line);
      padding: 12px 10px;
      text-align: right;
      overflow-wrap: anywhere;
    }}
    th:first-child, td:first-child,
    th:nth-child(2), td:nth-child(2) {{ text-align: left; }}
    th {{
      color: var(--muted);
      font-weight: 700;
      background: var(--panel);
    }}
    .horse {{ font-weight: 700; }}
    .positive {{ color: var(--accent); font-weight: 700; }}
    .negative {{ color: var(--warn); }}
    .note {{
      margin-top: 18px;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.5;
      max-width: 860px;
    }}
  </style>
</head>
<body>
  <header>
    <h1>{html.escape(title)}</h1>
    <div class="meta">
      <span>{html.escape(str(race['date']))}</span>
      <span>{html.escape(str(race['course']))}</span>
      <span>{html.escape(str(race['going']))}</span>
      <span>{html.escape(str(race['class_rating']))}</span>
    </div>
  </header>
  <main>
    <table aria-label="Race predictions">
      <thead>
        <tr>
          <th style="width: 70px;">Rank</th>
          <th>Horse</th>
          <th>Model Win %</th>
          <th>Odds</th>
          <th>Market %</th>
          <th>EV</th>
          <th>Value Gap</th>
        </tr>
      </thead>
      <tbody>
        {rows}
      </tbody>
    </table>
    <p class="note">
      This report is for model validation and decision support only. It does not place bets,
      and model output should be tested with historical data before risking capital.
    </p>
  </main>
</body>
</html>
"""


def render_prediction_row(row: dict[str, Any], rank: int) -> str:
    ev = row.get("expected_value")
    value_gap = float(row.get("value_gap") or 0.0)
    ev_class = "positive" if ev is not None and float(ev) > 0 else "negative"
    gap_class = "positive" if value_gap > 0 else "negative"
    return f"""<tr>
  <td>{rank}</td>
  <td class="horse">{html.escape(str(row['horse_name']))}</td>
  <td>{float(row['win_probability']) * 100:.1f}%</td>
  <td>{format_optional(row.get('latest_win_odds'))}</td>
  <td>{float(row.get('market_probability') or 0.0) * 100:.1f}%</td>
  <td class="{ev_class}">{format_ev(ev)}</td>
  <td class="{gap_class}">{value_gap * 100:.1f}%</td>
</tr>"""


def format_optional(value: Any) -> str:
    if value is None:
        return "-"
    return f"{float(value):.2f}"


def format_ev(value: Any) -> str:
    if value is None:
        return "-"
    return f"{float(value):+.3f}"

