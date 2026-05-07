from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from .base import FetchResult, PoliteHttpClient


COURSE_NAMES = {
    "ST": "Sha Tin",
    "HV": "Happy Valley",
}


@dataclass(frozen=True)
class HKJCSource:
    """Public-page adapter for HKJC racing information.

    The adapter stores raw snapshots and uses low-frequency requests. Always
    confirm current provider terms before scheduled collection.
    """

    client: PoliteHttpClient
    base_url: str = "https://racing.hkjc.com"

    def fetch_racecard_page(self, race_date: str, venue: str, race_no: int) -> FetchResult:
        return self.client.fetch(self.racecard_url(race_date, venue, race_no))

    def fetch_chinese_racecard_page(self, race_date: str, venue: str, race_no: int) -> FetchResult:
        return self.client.fetch(self.racecard_url(race_date, venue, race_no, language="Chinese"))

    def fetch_results_page(self, race_date: str, venue: str, race_no: int) -> FetchResult:
        return self.client.fetch(self.results_url(race_date, venue, race_no))

    def fetch_trackwork_page(self, race_date: str, venue: str, race_no: int) -> FetchResult:
        return self.client.fetch(self.trackwork_url(race_date, venue, race_no))

    def racecard_url(self, race_date: str, venue: str, race_no: int, language: str = "English") -> str:
        return (
            f"{self.base_url}/racing/information/{language}/Racing/RaceCard.aspx"
            f"?RaceDate={race_date}&Racecourse={venue}&RaceNo={race_no}"
        )

    def results_url(self, race_date: str, venue: str, race_no: int) -> str:
        return (
            f"{self.base_url}/racing/information/English/Racing/LocalResults.aspx"
            f"?RaceDate={race_date}&Racecourse={venue}&RaceNo={race_no}"
        )

    def trackwork_url(self, race_date: str, venue: str, race_no: int) -> str:
        return (
            f"{self.base_url}/racing/information/English/Racing/LocalTrackwork.aspx"
            f"?RaceDate={race_date}&Racecourse={venue}&RaceNo={race_no}"
        )

    def parse_racecard(
        self,
        html: str,
        race_date: str,
        venue: str,
        race_no: int,
        chinese_html: str | None = None,
    ) -> dict[str, list[dict[str, Any]] | dict[str, Any]]:
        lines = html_lines(html)
        race = parse_racecard_metadata(lines, race_date, venue, race_no)
        runners = parse_racecard_runners(lines, race["race_id"])
        if chinese_html:
            zh_rows = parse_chinese_racecard_runners(html_lines(chinese_html), race["race_id"])
            runners = merge_runner_localization(runners, zh_rows)
        return {"races": [race], "runners": runners}

    def parse_results(
        self,
        html: str,
        race_date: str,
        venue: str,
        race_no: int,
    ) -> dict[str, list[dict[str, Any]]]:
        lines = html_lines(html)
        race_id = make_race_id(race_date, venue, race_no)
        race = parse_results_metadata(lines, race_date, venue, race_no)
        raw_results = parse_result_rows(lines, race_id)
        results = [
            {
                key: value
                for key, value in row.items()
                if key
                not in {
                    "horse_no",
                    "horse_name",
                    "jockey",
                    "trainer",
                    "draw",
                    "weight_lbs",
                    "running_style",
                    "win_odds",
                    "place_odds",
                }
            }
            for row in raw_results
        ]
        runners = [
            result_runner_from_row(row)
            for row in raw_results
            if row.get("horse_id") and row.get("horse_name")
        ]
        odds = [
            {
                "race_id": race_id,
                "horse_id": row["horse_id"],
                "timestamp": f"{normalize_date(race_date)}T23:59:00+08:00",
                "win_odds": row["win_odds"],
                "place_odds": row.get("place_odds"),
                "source": "hkjc_results_final",
            }
            for row in raw_results
            if row.get("win_odds") is not None
        ]
        return {"races": [race] if race else [], "runners": runners, "results": results, "odds_ticks": odds}

    def parse_trackwork(
        self,
        html: str,
        race_date: str,
        venue: str,
        race_no: int,
    ) -> dict[str, list[dict[str, Any]]]:
        lines = html_lines(html)
        return {"workouts": parse_trackwork_rows(lines)}


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if text:
            self.parts.append(text)


def html_lines(html: str) -> list[str]:
    parser = TextExtractor()
    parser.feed(html)
    text = unescape("\n".join(parser.parts))
    lines = []
    for raw in text.splitlines():
        cleaned = re.sub(r"\s+", " ", raw).strip()
        if cleaned:
            lines.append(cleaned)
    return lines


def parse_racecard_metadata(
    lines: list[str],
    race_date: str,
    venue: str,
    race_no: int,
) -> dict[str, Any]:
    race_line = next((line for line in lines if re.match(r"Race \d+\s+-", line, re.I)), "")
    distance_line = next((line for line in lines if re.search(r"\b\d{3,4}M\b", line)), "")
    prize_line = next((line for line in lines if "Prize Money:" in line), "")
    distance = int(re.search(r"(\d{3,4})M", distance_line).group(1)) if re.search(r"(\d{3,4})M", distance_line) else 0
    going = distance_line.split(",")[-1].strip() if "," in distance_line else ""
    course = distance_line.split(",")[0].strip() if "," in distance_line else ""
    prize_match = re.search(r"Prize Money:\s*\$?([\d,]+)", prize_line)
    class_match = re.search(r"(Class\s+\d+)", prize_line, re.I)
    rating_match = re.search(r"Rating:\s*([^,]+)", prize_line, re.I)
    class_rating = class_match.group(1) if class_match else (rating_match.group(1) if rating_match else "")
    return {
        "race_id": make_race_id(race_date, venue, race_no),
        "date": normalize_date(race_date),
        "track": COURSE_NAMES.get(venue.upper(), venue.upper()),
        "course": course,
        "distance_m": distance,
        "going": going,
        "class_rating": class_rating,
        "prize": float(prize_match.group(1).replace(",", "")) if prize_match else 0.0,
        "race_name": race_line,
    }


def parse_results_metadata(
    lines: list[str],
    race_date: str,
    venue: str,
    race_no: int,
) -> dict[str, Any] | None:
    race_index = next(
        (index for index, line in enumerate(lines) if re.match(rf"RACE\s+{race_no}\b", line, re.I)),
        None,
    )
    if race_index is None:
        return None
    class_line = lines[race_index + 1] if race_index + 1 < len(lines) else ""
    race_name = lines[race_index + 4] if race_index + 4 < len(lines) else lines[race_index]
    prize_line = next((line for line in lines[race_index : race_index + 12] if line.upper().startswith("HK$")), "")
    going = value_after_label(lines, race_index, "Going :")
    course = value_after_label(lines, race_index, "Course :")
    distance_match = re.search(r"(\d{3,4})M", class_line, re.I)
    class_match = re.search(r"(Class\s+\d+)", class_line, re.I)
    prize_match = re.search(r"HK\$\s*([\d,]+)", prize_line, re.I)
    return {
        "race_id": make_race_id(race_date, venue, race_no),
        "date": normalize_date(race_date),
        "track": COURSE_NAMES.get(venue.upper(), venue.upper()),
        "course": course,
        "distance_m": int(distance_match.group(1)) if distance_match else 0,
        "going": going,
        "class_rating": class_match.group(1) if class_match else class_line,
        "prize": float(prize_match.group(1).replace(",", "")) if prize_match else 0.0,
        "race_name": race_name,
    }


def value_after_label(lines: list[str], start: int, label: str) -> str:
    index = find_line(lines, label, start=start)
    if index is None or index + 1 >= len(lines):
        return ""
    return lines[index + 1]


def result_runner_from_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "race_id": row["race_id"],
        "horse_id": row["horse_id"],
        "horse_no": row.get("horse_no"),
        "horse_name": row.get("horse_name") or row["horse_id"],
        "jockey": row.get("jockey") or "unknown",
        "trainer": row.get("trainer") or "unknown",
        "draw": row.get("draw") or 0,
        "weight_lbs": row.get("weight_lbs") or 0.0,
        "official_rating": 0.0,
        "age": 0,
        "sex": "U",
        "running_style": row.get("running_style") or "unknown",
        "gear": "from_result",
    }


def parse_racecard_runners(lines: list[str], race_id: str) -> list[dict[str, Any]]:
    start = find_line(lines, "MY Race Card LIST")
    end = find_line(lines, "Stand-by Starter")
    if start is None:
        return []
    candidate_lines = lines[start + 1 : end if end is not None else len(lines)]
    table_start = first_runner_index(candidate_lines)
    rows = parse_racecard_brand_windows(candidate_lines, race_id)
    if table_start is not None:
        rows = parse_racecard_runner_tokens(candidate_lines[table_start:], race_id) + rows
    else:
        rows = [runner for line in candidate_lines if (runner := parse_racecard_runner_line(line, race_id))] + rows
    return dedupe_by_key(rows, "horse_id")


def first_runner_index(tokens: list[str]) -> int | None:
    for index in range(len(tokens) - 3):
        if is_int(tokens[index]) and looks_like_last6(tokens[index + 1]) and looks_like_brand(tokens[index + 3]):
            return index
    return None


def parse_racecard_runner_tokens(tokens: list[str], race_id: str) -> list[dict[str, Any]]:
    rows = []
    index = 0
    while index < len(tokens) - 12:
        if not (
            is_int(tokens[index])
            and looks_like_last6(tokens[index + 1])
            and looks_like_brand(tokens[index + 3])
        ):
            index += 1
            continue
        try:
            horse_name = tokens[index + 2]
            horse_id = tokens[index + 3]
            weight = float(tokens[index + 4])
            jockey = strip_allowance(tokens[index + 5])
            draw = int(tokens[index + 6])
            trainer = tokens[index + 7]
            rating = float(tokens[index + 9])
            cursor = index + 13
            if cursor < len(tokens) and looks_like_time(tokens[cursor]):
                cursor += 1
            age = int(tokens[cursor]) if cursor < len(tokens) and is_int(tokens[cursor]) else 0
            cursor += 1
            if cursor < len(tokens) and tokens[cursor] == "-":
                cursor += 1
            sex = tokens[cursor].upper() if cursor < len(tokens) else ""
            cursor += 1
            for _ in range(3):
                if cursor < len(tokens):
                    cursor += 1
            gear = ""
            if cursor < len(tokens) and looks_like_gear(tokens[cursor]):
                gear = tokens[cursor]
            rows.append(
                {
                    "race_id": race_id,
                    "horse_no": int(tokens[index]),
                    "horse_id": horse_id,
                    "horse_name": normalize_horse_name(horse_name),
                    "horse_name_zh": "",
                    "jockey": jockey,
                    "jockey_zh": "",
                    "trainer": trainer,
                    "trainer_zh": "",
                    "draw": draw,
                    "weight_lbs": weight,
                    "official_rating": rating,
                    "age": age,
                    "sex": sex,
                    "running_style": "unknown",
                    "gear": gear,
                }
            )
        except (IndexError, ValueError):
            index += 1
            continue
        index += 1
    return dedupe_by_key(rows, "horse_id")


def parse_chinese_racecard_runners(lines: list[str], race_id: str) -> list[dict[str, Any]]:
    start = find_line(lines, "我 的 排 位 表")
    end = find_line(lines, "後 備 馬 匹")
    candidate_lines = lines[start + 1 : end if end is not None else len(lines)] if start is not None else lines
    table_start = first_runner_index(candidate_lines)
    rows = parse_chinese_racecard_brand_windows(candidate_lines, race_id)
    if table_start is None:
        return rows
    return dedupe_by_key(rows + parse_chinese_racecard_runner_tokens(candidate_lines[table_start:], race_id), "horse_id")


def parse_racecard_brand_windows(tokens: list[str], race_id: str) -> list[dict[str, Any]]:
    rows = []
    for index in range(3, len(tokens) - 8):
        if not (
            looks_like_brand(tokens[index])
            and is_int(tokens[index - 3])
            and looks_like_form_token(tokens[index - 2])
        ):
            continue
        try:
            trainer_index = trainer_index_after_brand(tokens, index)
            rating_index = trainer_index + 2
            rows.append(
                {
                    "race_id": race_id,
                    "horse_no": int(tokens[index - 3]),
                    "horse_id": tokens[index],
                    "horse_name": normalize_horse_name(tokens[index - 1]),
                    "horse_name_zh": "",
                    "jockey": strip_allowance(tokens[index + 2]),
                    "jockey_zh": "",
                    "trainer": tokens[trainer_index],
                    "trainer_zh": "",
                    "draw": int(tokens[trainer_index - 1]),
                    "weight_lbs": float(tokens[index + 1]),
                    "official_rating": float(tokens[rating_index]) if looks_like_decimal(tokens[rating_index]) else 0.0,
                    "age": infer_age_from_tokens(tokens, rating_index),
                    "sex": infer_sex_from_tokens(tokens, rating_index),
                    "running_style": "unknown",
                    "gear": infer_gear_from_tokens(tokens, rating_index),
                }
            )
        except (IndexError, ValueError):
            continue
    return dedupe_by_key(rows, "horse_id")


def parse_chinese_racecard_brand_windows(tokens: list[str], race_id: str) -> list[dict[str, Any]]:
    rows = []
    for index in range(3, len(tokens) - 8):
        if not (
            looks_like_brand(tokens[index])
            and is_int(tokens[index - 3])
            and looks_like_form_token(tokens[index - 2])
        ):
            continue
        try:
            trainer_index = trainer_index_after_brand(tokens, index)
            rows.append(
                {
                    "race_id": race_id,
                    "horse_no": int(tokens[index - 3]),
                    "horse_id": tokens[index],
                    "horse_name_zh": tokens[index - 1],
                    "jockey_zh": strip_allowance(tokens[index + 2]),
                    "trainer_zh": tokens[trainer_index],
                }
            )
        except (IndexError, ValueError):
            continue
    return dedupe_by_key(rows, "horse_id")


def trainer_index_after_brand(tokens: list[str], brand_index: int) -> int:
    if brand_index + 5 < len(tokens) and is_int(tokens[brand_index + 3]) and is_int(tokens[brand_index + 4]):
        return brand_index + 5
    return brand_index + 4


def infer_age_from_tokens(tokens: list[str], start: int) -> int:
    for token in tokens[start : min(start + 10, len(tokens))]:
        if is_int(token) and 2 <= int(token) <= 15:
            return int(token)
    return 0


def infer_sex_from_tokens(tokens: list[str], start: int) -> str:
    sex_map = {"閹": "G", "雄": "M", "雌": "F", "g": "G", "m": "M", "f": "F", "c": "C"}
    for token in tokens[start : min(start + 12, len(tokens))]:
        normalized = token.lower()
        if normalized in sex_map:
            return sex_map[normalized]
    return ""


def infer_gear_from_tokens(tokens: list[str], start: int) -> str:
    for token in tokens[start : min(start + 16, len(tokens))]:
        if looks_like_gear(token) and not looks_like_brand(token):
            return token
    return ""


def parse_chinese_racecard_runner_tokens(tokens: list[str], race_id: str) -> list[dict[str, Any]]:
    rows = []
    index = 0
    while index < len(tokens) - 12:
        if not (
            is_int(tokens[index])
            and looks_like_last6(tokens[index + 1])
            and looks_like_brand(tokens[index + 3])
        ):
            index += 1
            continue
        try:
            rows.append(
                {
                    "race_id": race_id,
                    "horse_no": int(tokens[index]),
                    "horse_id": tokens[index + 3],
                    "horse_name_zh": tokens[index + 2],
                    "jockey_zh": strip_allowance(tokens[index + 5]),
                    "trainer_zh": tokens[index + 7],
                }
            )
        except (IndexError, ValueError):
            index += 1
            continue
        index += 1
    return dedupe_by_key(rows, "horse_id")


def merge_runner_localization(
    runners: list[dict[str, Any]],
    zh_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    zh_by_id = {row["horse_id"]: row for row in zh_rows}
    merged = []
    for runner in runners:
        zh = zh_by_id.get(runner["horse_id"], {})
        updated = dict(runner)
        for key in ("horse_no", "horse_name_zh", "jockey_zh", "trainer_zh"):
            if zh.get(key) not in {None, ""}:
                updated[key] = zh[key]
        merged.append(updated)
    return merged


def parse_racecard_runner_line(line: str, race_id: str) -> dict[str, Any] | None:
    pattern = re.compile(
        r"^(?P<horse_no>\d{1,2})\s+"
        r"(?P<last6>[0-9/]+)\s+"
        r"(?P<horse_name>[A-Z0-9 '\-.]+?)\s+"
        r"(?P<brand>[A-Z]\d{3})\s+"
        r"(?P<weight>\d{2,3})\s+"
        r"(?P<jockey>.+?)\s+"
        r"(?P<draw>\d{1,2})\s+"
        r"(?P<trainer>[A-Z](?:\s|[A-Za-z.])+?)\s+"
        r"(?:-|(?P<intl>\d+))\s+"
        r"(?P<rating>\d+)\s+"
        r"(?P<rating_delta>[+-]?\d+)\s+"
        r"(?P<body_weight>\d+|-)"
    )
    match = pattern.search(line)
    if not match:
        return None
    gear_match = re.search(r"\s(?P<days>\d{1,3})\s+(?P<gear>[A-Z0-9/\-]+)\s+", line)
    return {
        "race_id": race_id,
        "horse_no": int(match.group("horse_no")),
        "horse_id": match.group("brand"),
        "horse_name": match.group("horse_name").strip(),
        "horse_name_zh": "",
        "jockey": strip_allowance(match.group("jockey").strip()),
        "jockey_zh": "",
        "trainer": match.group("trainer").strip(),
        "trainer_zh": "",
        "draw": int(match.group("draw")),
        "weight_lbs": float(match.group("weight")),
        "official_rating": float(match.group("rating")),
        "age": infer_age_from_line(line),
        "sex": infer_sex_from_line(line),
        "running_style": "unknown",
        "gear": gear_match.group("gear") if gear_match else "",
    }


def parse_result_rows(lines: list[str], race_id: str) -> list[dict[str, Any]]:
    start = find_line(lines, "Pla. Horse No. Horse")
    if start is None:
        start = find_line(lines, "Pla.")
    if start is None:
        return []
    end = find_line(lines, "Dividend", start=start + 1)
    result_lines = lines[start + 1 : end if end is not None else len(lines)]
    place_dividends = parse_place_dividends(lines[end:] if end is not None else [])
    token_rows = parse_result_tokens(result_lines, race_id)
    if token_rows:
        return attach_place_dividends(token_rows, place_dividends)
    rows = []
    joined = " ".join(result_lines)
    pattern = re.compile(
        r"(?P<place>\d{1,2})\s+"
        r"(?P<horse_no>\d{1,2})\s+"
        r"(?P<horse_name>[A-Z0-9 '\-.]+)\s+\((?P<brand>[A-Z]\d{3})\)\s+"
        r"(?P<jockey>[A-Z][A-Za-z .'\-]+)\s+"
        r"(?P<trainer>[A-Z](?:\s|[A-Za-z.])+?)\s+"
        r"(?P<weight>\d{2,3})\s+"
        r"(?P<body_weight>\d{3,4})\s+"
        r"(?P<draw>\d{1,2})\s+"
        r"(?P<lbw>[-NSHNK\d/ ]+?)\s+"
        r"(?P<time>\d:\d{2}\.\d{2})\s+"
        r"(?P<odds>\d+(?:\.\d+)?)"
    )
    for match in pattern.finditer(joined):
        rows.append(
                {
                    "race_id": race_id,
                    "horse_no": int(match.group("horse_no")),
                    "horse_id": match.group("brand"),
                    "finish_position": int(match.group("place")),
                    "finish_time_sec": time_to_seconds(match.group("time")),
                    "margin_lengths": margin_to_lengths(match.group("lbw")),
                    "sectional_400_sec": None,
                    "sectional_800_sec": None,
                    "comment": "",
                    "win_odds": float(match.group("odds")),
                    "place_odds": place_dividends.get(int(match.group("horse_no"))),
                }
            )
    return rows


def parse_result_tokens(tokens: list[str], race_id: str) -> list[dict[str, Any]]:
    rows = []
    index = first_result_index(tokens) or 0
    while index < len(tokens) - 12:
        if not (
            is_int(tokens[index])
            and is_int(tokens[index + 1])
            and index + 3 < len(tokens)
            and looks_like_parenthesized_brand(tokens[index + 3])
        ):
            index += 1
            continue
        cursor = index
        try:
            place = int(tokens[cursor])
            cursor += 1
            horse_no = int(tokens[cursor])
            cursor += 1
            horse_name = normalize_horse_name(tokens[cursor])
            cursor += 1
            horse_id = tokens[cursor].strip("()")
            cursor += 1
            jockey = strip_allowance(tokens[cursor])
            cursor += 1
            trainer = tokens[cursor]
            cursor += 1
            weight_lbs = float(tokens[cursor]) if looks_like_decimal(tokens[cursor]) else 0.0
            cursor += 1
            cursor += 1  # declared horse weight
            draw = int(tokens[cursor]) if is_int(tokens[cursor]) else 0
            cursor += 1
            lbw = tokens[cursor]
            cursor += 1
            running_positions = []
            while cursor < len(tokens) and not looks_like_finish_time(tokens[cursor]):
                if is_int(tokens[cursor]):
                    running_positions.append(int(tokens[cursor]))
                cursor += 1
            finish_time = time_to_seconds(tokens[cursor])
            cursor += 1
            win_odds = float(tokens[cursor]) if cursor < len(tokens) and looks_like_decimal(tokens[cursor]) else None
            rows.append(
                {
                    "race_id": race_id,
                    "horse_no": horse_no,
                    "horse_id": horse_id,
                    "horse_name": horse_name,
                    "jockey": jockey,
                    "trainer": trainer,
                    "draw": draw,
                    "weight_lbs": weight_lbs,
                    "running_style": infer_running_style_from_positions(running_positions),
                    "finish_position": place,
                    "finish_time_sec": finish_time,
                    "margin_lengths": margin_to_lengths(lbw),
                    "sectional_400_sec": None,
                    "sectional_800_sec": None,
                    "comment": f"{horse_name}; jockey={jockey}; trainer={trainer}",
                    "win_odds": win_odds,
                    "place_odds": None,
                }
            )
            index = cursor + 1
        except (IndexError, ValueError):
            index += 1
    return dedupe_by_key(rows, "horse_id")


def attach_place_dividends(rows: list[dict[str, Any]], place_dividends: dict[int, float]) -> list[dict[str, Any]]:
    for row in rows:
        horse_no = row.get("horse_no")
        row["place_odds"] = place_dividends.get(int(horse_no)) if horse_no is not None else None
    return rows


def infer_running_style_from_positions(positions: list[int]) -> str:
    if not positions:
        return "unknown"
    early = positions[0]
    if early <= 2:
        return "leader"
    if early <= 5:
        return "pace"
    if early >= 9:
        return "closer"
    return "stalker"


def parse_place_dividends(lines: list[str]) -> dict[int, float]:
    start = find_line(lines, "PLACE")
    if start is None:
        return {}
    stop_labels = {
        "QUINELLA",
        "QUINELLA PLACE",
        "FORECAST",
        "TIERCE",
        "TRIO",
        "FIRST 4",
        "QUARTET",
        "DOUBLE",
        "TREBLE",
        "SIX UP",
    }
    output: dict[int, float] = {}
    index = start + 1
    while index + 1 < len(lines):
        label = lines[index].strip().upper()
        if label in stop_labels or label.endswith("DOUBLE") or label.endswith("TREBLE"):
            break
        if is_int(lines[index]) and looks_like_decimal(lines[index + 1]):
            output[int(lines[index])] = round(float(lines[index + 1].replace(",", "")) / 10.0, 2)
            index += 2
            continue
        index += 1
    return output


def first_result_index(tokens: list[str]) -> int | None:
    for index in range(len(tokens) - 3):
        if is_int(tokens[index]) and is_int(tokens[index + 1]) and looks_like_parenthesized_brand(tokens[index + 3]):
            return index
    return None


def parse_trackwork_rows(lines: list[str]) -> list[dict[str, Any]]:
    rows = []
    joined = " ".join(lines)
    pattern = re.compile(
        r"(?P<horse_name>[A-Z0-9 '\-.]+)\s+\((?P<brand>[A-Z]\d{3})\).*?"
        r"(?P<distance>\d{3,4})M.*?"
        r"(?P<time>\d{1,2}\.\d{2}|\d:\d{2}\.\d{2})",
        re.I,
    )
    for match in pattern.finditer(joined):
        rows.append(
            {
                "horse_id": match.group("brand"),
                "date": "",
                "track": "",
                "work_type": "trackwork",
                "distance_m": int(match.group("distance")),
                "time_sec": time_to_seconds(match.group("time")),
                "rank": None,
                "notes": match.group("horse_name").strip(),
            }
        )
    return rows


def write_hkjc_csv_bundle(parsed: dict[str, list[dict[str, Any]]], out_dir: Path | str) -> dict[str, Path]:
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    files = {}
    for key, rows in parsed.items():
        if not rows:
            continue
        filename = "odds.csv" if key == "odds_ticks" else f"{key}.csv"
        path = directory / filename
        fieldnames = list(rows[0].keys())
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        files[key] = path
    return files


def make_race_id(race_date: str, venue: str, race_no: int) -> str:
    return f"HK{race_date.replace('/', '')}-{venue.upper()}-{int(race_no):02d}"


def normalize_date(race_date: str) -> str:
    return race_date.replace("/", "-")


def find_line(lines: list[str], needle: str, start: int = 0) -> int | None:
    for index, line in enumerate(lines[start:], start=start):
        if needle.lower() in line.lower():
            return index
    return None


def dedupe_by_key(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    seen = set()
    output = []
    for row in rows:
        value = row.get(key)
        if value in seen:
            continue
        seen.add(value)
        output.append(row)
    return output


def is_int(value: str) -> bool:
    return bool(re.fullmatch(r"\d+", value))


def looks_like_last6(value: str) -> bool:
    return bool(re.fullmatch(r"[0-9/]+", value)) and "/" in value


def looks_like_form_token(value: str) -> bool:
    return value == "-" or bool(re.fullmatch(r"[0-9/\-]+", value))


def looks_like_brand(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Z]\d{3}", value))


def looks_like_parenthesized_brand(value: str) -> bool:
    return bool(re.fullmatch(r"\([A-Z]\d{3}\)", value))


def looks_like_time(value: str) -> bool:
    return bool(re.fullmatch(r"\d[.:]\d{2}\.\d{2}", value))


def looks_like_finish_time(value: str) -> bool:
    return bool(re.fullmatch(r"\d:\d{2}\.\d{2}", value))


def looks_like_gear(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Z0-9/\-]+", value)) and any(char.isalpha() for char in value)


def looks_like_decimal(value: str) -> bool:
    return bool(re.fullmatch(r"\d+(?:\.\d+)?", value))


def normalize_horse_name(value: str) -> str:
    replacements = {
        "HAILTOTHEVICTORS": "HAIL TO THE VICTORS",
        "HAPPYDEARHAPPYDEER": "HAPPY DEAR HAPPY DEER",
    }
    return replacements.get(value, value)


def strip_allowance(name: str) -> str:
    return re.sub(r"\s+\(-?\d+\)$", "", name).strip()


def infer_age_from_line(line: str) -> int:
    match = re.search(r"\s([2-9]|1[0-5])\s+-\s+[a-z]\s", line.lower())
    return int(match.group(1)) if match else 0


def infer_sex_from_line(line: str) -> str:
    match = re.search(r"\s([mfgc])\s", line.lower())
    return match.group(1).upper() if match else ""


def time_to_seconds(value: str) -> float:
    if ":" not in value:
        return float(value)
    minutes, seconds = value.split(":", 1)
    return int(minutes) * 60 + float(seconds)


def margin_to_lengths(value: str) -> float:
    text = value.strip().upper()
    if text in {"---", "-", ""}:
        return 0.0
    if text in {"N", "NOSE"}:
        return 0.05
    if text in {"SH", "SHORT HEAD"}:
        return 0.1
    if text in {"HD", "HEAD"}:
        return 0.2
    if "-" in text:
        whole, fraction = text.split("-", 1)
        return float(whole or 0) + margin_to_lengths(fraction)
    total = 0.0
    for token in text.split():
        if "/" in token:
            numerator, denominator = token.split("/", 1)
            total += float(numerator) / float(denominator)
        else:
            try:
                total += float(token)
            except ValueError:
                pass
    return total
