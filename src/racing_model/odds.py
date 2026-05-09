from __future__ import annotations

import json
import random
import sqlite3
import ssl
import time
import gzip
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol
from urllib.error import URLError
from urllib.request import Request, urlopen

from .live import parse_hkjc_race_id
from .storage import (
    FINAL_PLACE_BACKFILL_SOURCE,
    fetch_all,
    final_place_odds_completeness,
    insert_rows,
    latest_raw_odds_by_race,
    race_status,
    upsert_race_status,
)


HORSE_ODDS_QUERY = """
query racing($date: String, $venueCode: String, $oddsTypes: [OddsType], $raceNo: Int) {
  raceMeetings(date: $date, venueCode: $venueCode) {
    pmPools(oddsTypes: $oddsTypes, raceNo: $raceNo) {
      id
      status
      sellStatus
      oddsType
      lastUpdateTime
      guarantee
      minTicketCost
      name_en
      name_ch
      leg {
        number
        races
      }
      cWinSelections {
        composite
        name_ch
        name_en
        starters
      }
      oddsNodes {
        combString
        oddsValue
        hotFavourite
        oddsDropValue
        bankerOdds {
          combString
          oddsValue
        }
      }
    }
  }
}
"""


class OddsProvider(Protocol):
    source_name: str
    last_error: str | None

    def fetch_odds(self, conn: sqlite3.Connection, race_id: str) -> list[dict[str, object]]:
        ...


@dataclass
class SnapshotJitterOddsProvider:
    """Development odds provider used when no permitted live feed is available."""

    source_name: str = "dev_snapshot_jitter"
    max_move_pct: float = 0.025
    last_error: str | None = None

    def fetch_odds(self, conn: sqlite3.Connection, race_id: str) -> list[dict[str, object]]:
        latest = latest_raw_odds_by_race(conn, race_id)
        runners = fetch_all(conn, "SELECT horse_id, official_rating FROM runners WHERE race_id = ?", (race_id,))
        now = datetime.now(timezone.utc).isoformat()
        rows = []
        for runner in runners:
            horse_id = runner["horse_id"]
            if horse_id in latest:
                base = float(latest[horse_id]["win_odds"])
            else:
                rating = float(runner["official_rating"] or 30)
                base = max(1.8, 90.0 / max(rating, 1.0))
            movement = 1 + random.uniform(-self.max_move_pct, self.max_move_pct)
            win_odds = max(1.01, round(base * movement, 2))
            rows.append(
                {
                    "race_id": race_id,
                    "horse_id": horse_id,
                    "timestamp": now,
                    "win_odds": win_odds,
                    "place_odds": None,
                    "source": self.source_name,
                }
            )
        return rows


@dataclass
class HKJCGraphQLOddsProvider:
    endpoint: str
    user_agent: str
    source_name: str = "hkjc_graphql"
    last_error: str | None = None

    def fetch_odds(self, conn: sqlite3.Connection, race_id: str) -> list[dict[str, object]]:
        ref = parse_hkjc_race_id(race_id)
        if not ref:
            raise ValueError(f"Not an HKJC race id: {race_id}")
        runners = fetch_all(
            conn,
            """
            SELECT horse_id, horse_no
            FROM runners
            WHERE race_id = ? AND horse_no IS NOT NULL
            """,
            (race_id,),
        )
        horse_id_by_no = {normalize_runner_no(row["horse_no"]): row["horse_id"] for row in runners}
        payload = {
            "operationName": "racing",
            "query": HORSE_ODDS_QUERY,
            "variables": {
                "date": ref.race_date.replace("/", "-"),
                "venueCode": ref.venue,
                "oddsTypes": ["WIN", "PLA"],
                "raceNo": ref.race_no,
            },
        }
        body = json.dumps(payload).encode("utf-8")
        request = Request(
            self.endpoint,
            data=body,
            headers={
                "Content-Type": "application/json",
                "User-Agent": browser_compatible_user_agent(self.user_agent),
                "Accept": "application/json, text/plain, */*",
                "Origin": "https://bet.hkjc.com",
                "Referer": f"https://bet.hkjc.com/ch/racing/wp/{ref.race_date.replace('/', '-')}/{ref.venue}/{ref.race_no}",
            },
        )
        try:
            with urlopen(request, timeout=10) as response:
                raw_bytes = response.read()
                if raw_bytes.startswith(b"\x1f\x8b"):
                    raw_bytes = gzip.decompress(raw_bytes)
                raw = raw_bytes.decode("utf-8", errors="replace")
                data = json.loads(raw)
        except (OSError, URLError, TimeoutError) as exc:
            self.last_error = str(exc)
            raise RuntimeError(f"HKJC GraphQL request failed: {exc}") from exc
        except json.JSONDecodeError as exc:
            snippet = raw[:120] if "raw" in locals() else ""
            self.last_error = f"invalid JSON: {snippet}"
            raise RuntimeError(f"HKJC GraphQL returned invalid JSON: {snippet}") from exc

        if data.get("errors"):
            message = data["errors"][0].get("message", str(data["errors"][0]))
            self.last_error = message
            raise RuntimeError(f"HKJC GraphQL returned error: {message}")

        meetings = data.get("data", {}).get("raceMeetings") or []
        if not meetings:
            raise RuntimeError("HKJC GraphQL returned no race meeting data.")

        latest_existing = latest_raw_odds_by_race(conn, race_id)
        rows = graphql_odds_payload_to_rows(data, race_id, horse_id_by_no, self.source_name, latest_existing)
        if not rows:
            raise RuntimeError("HKJC GraphQL returned no WIN/PLA odds rows.")
        self.last_error = None
        return rows


@dataclass
class AutoOddsProvider:
    providers: list[OddsProvider]
    fallback_provider: OddsProvider
    source_name: str = "auto"
    last_error: str | None = None
    active_source: str = ""

    def fetch_odds(self, conn: sqlite3.Connection, race_id: str) -> list[dict[str, object]]:
        errors = []
        for provider in self.providers:
            try:
                rows = provider.fetch_odds(conn, race_id)
                self.active_source = provider.source_name
                self.last_error = None
                return rows
            except Exception as exc:
                errors.append(f"{provider.source_name}: {exc}")
        self.active_source = self.fallback_provider.source_name
        self.last_error = " | ".join(errors)
        rows = self.fallback_provider.fetch_odds(conn, race_id)
        for row in rows:
            row["source"] = f"{self.fallback_provider.source_name}_fallback"
        return rows


@dataclass
class OfficialOddsProviderChain:
    providers: list[OddsProvider]
    source_name: str = "official_chain"
    last_error: str | None = None
    active_source: str = ""

    def fetch_odds(self, conn: sqlite3.Connection, race_id: str) -> list[dict[str, object]]:
        errors = []
        for provider in self.providers:
            try:
                rows = provider.fetch_odds(conn, race_id)
                self.active_source = provider.source_name
                self.last_error = None
                return rows
            except Exception as exc:
                errors.append(f"{provider.source_name}: {exc}")
        self.last_error = " | ".join(errors)
        raise RuntimeError(self.last_error or "No official odds provider configured.")


@dataclass
class HKJCMQTTOddsProvider:
    host: str
    port: int
    username: str
    password: str
    wait_seconds: float
    source_name: str = "hkjc_mqtt"
    last_error: str | None = None

    def fetch_odds(self, conn: sqlite3.Connection, race_id: str) -> list[dict[str, object]]:
        try:
            import paho.mqtt.client as mqtt
            from paho.mqtt.packettypes import PacketTypes
            from paho.mqtt.properties import Properties
        except ImportError as exc:
            raise RuntimeError("paho-mqtt is not installed. Run: pip install -e .[live]") from exc

        ref = parse_hkjc_race_id(race_id)
        if not ref:
            raise ValueError(f"Not an HKJC race id: {race_id}")

        runners = fetch_all(
            conn,
            "SELECT horse_id, horse_no FROM runners WHERE race_id = ? AND horse_no IS NOT NULL",
            (race_id,),
        )
        horse_id_by_no = {normalize_runner_no(row["horse_no"]): row["horse_id"] for row in runners}
        race_no = f"{ref.race_no:02d}"
        date_token = ref.race_date.replace("/", "")
        venue = ref.venue.lower()
        win_topic = f"hk/d/prdt/wager/evt/01/upd/racing/{date_token}/{venue}/{race_no}/win/odds/full"
        pla_topic = f"hk/d/prdt/wager/evt/01/upd/racing/{date_token}/{venue}/{race_no}/pla/odds/full"
        reply_topic = "hk/d/prdt/wager/rpy/02/recovery/client/#"
        request_topic = "hk/d/prdt/wager/req/02/recovery/client"
        client_id = f"jcbw2_4e_rm_{random.randint(100000, 999999)}_{int(time.time() * 1000)}"
        messages: list[tuple[str, bytes]] = []
        connected = False

        def on_connect(client, userdata, flags, reason_code, properties=None):
            nonlocal connected
            if not mqtt_reason_success(reason_code):
                return
            connected = True
            client.subscribe([(win_topic, 0), (pla_topic, 0), (reply_topic, 0)])
            props = Properties(PacketTypes.PUBLISH)
            props.UserProperty = [("Trace-Id", f"racing_model_{client_id}")]
            payload = json.dumps(
                {
                    "systemCode": "JCBW2_4E",
                    "senderId": client_id,
                    "topics": [
                        {"recoveryTopic": win_topic, "requestAllIndicator": 0},
                        {"recoveryTopic": pla_topic, "requestAllIndicator": 0},
                    ],
                    "latestMessageInSeconds": 120,
                }
            )
            client.publish(request_topic, payload, qos=0, retain=False, properties=props)

        def on_message(client, userdata, msg):
            messages.append((msg.topic, bytes(msg.payload)))

        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id,
            transport="websockets",
            protocol=mqtt.MQTTv5,
        )
        client.username_pw_set(self.username, self.password)
        client.tls_set(cert_reqs=ssl.CERT_REQUIRED)
        client.ws_set_options(path="/")
        client.on_connect = on_connect
        client.on_message = on_message
        client.connect(self.host, self.port, keepalive=60)
        client.loop_start()
        time.sleep(self.wait_seconds)
        client.loop_stop()
        client.disconnect()

        if not connected:
            raise RuntimeError("MQTT did not connect.")

        rows = mqtt_messages_to_rows(messages, race_id, horse_id_by_no)
        if not rows:
            raise RuntimeError("MQTT connected but returned no odds messages.")
        self.last_error = None
        return rows


def build_odds_provider(settings) -> OddsProvider:
    provider = (settings.odds_provider or "auto").lower()
    graphql = HKJCGraphQLOddsProvider(settings.hkjc_graphql_url, settings.user_agent)
    mqtt = HKJCMQTTOddsProvider(
        settings.hkjc_mqtt_host,
        settings.hkjc_mqtt_port,
        settings.hkjc_mqtt_username,
        settings.hkjc_mqtt_password,
        settings.hkjc_mqtt_wait_seconds,
    )
    fallback = SnapshotJitterOddsProvider()
    if provider == "hkjc":
        return graphql
    if provider == "mqtt":
        return mqtt
    if provider == "dev":
        return fallback
    return AutoOddsProvider([graphql, mqtt], fallback)


def build_official_odds_provider(settings) -> OddsProvider:
    graphql = HKJCGraphQLOddsProvider(settings.hkjc_graphql_url, settings.user_agent)
    providers: list[OddsProvider] = [graphql]
    mqtt_configured = bool(settings.hkjc_mqtt_username and settings.hkjc_mqtt_password)
    mqtt = None
    if mqtt_configured:
        mqtt = HKJCMQTTOddsProvider(
            settings.hkjc_mqtt_host,
            settings.hkjc_mqtt_port,
            settings.hkjc_mqtt_username,
            settings.hkjc_mqtt_password,
            settings.hkjc_mqtt_wait_seconds,
        )
    provider = (settings.odds_provider or "auto").lower()
    if provider == "mqtt" and mqtt:
        return OfficialOddsProviderChain([mqtt, graphql])
    if mqtt:
        providers.append(mqtt)
    return OfficialOddsProviderChain(providers)


def refresh_odds(
    conn: sqlite3.Connection,
    race_id: str,
    provider: OddsProvider | None = None,
) -> int:
    status = race_status(conn, race_id)
    current_status = str(status["status"]) if status else "scheduled"
    if current_status not in {"scheduled", "live"}:
        return 0
    provider = provider or SnapshotJitterOddsProvider()
    rows = provider.fetch_odds(conn, race_id)
    inserted = insert_rows(conn, "odds_ticks", rows)
    source = getattr(provider, "active_source", provider.source_name)
    error = getattr(provider, "last_error", None)
    note = f"odds_provider={source}"
    if error:
        note += f"; error={error[:180]}"
    runner_count = fetch_all(conn, "SELECT count(*) AS n FROM runners WHERE race_id = ?", (race_id,))[0]["n"]
    result_count = fetch_all(conn, "SELECT count(*) AS n FROM results WHERE race_id = ?", (race_id,))[0]["n"]
    status = "resulted" if result_count > 0 else current_status
    upsert_race_status(
        conn,
        race_id,
        status,
        last_odds_refresh_at=datetime.now(timezone.utc).isoformat(),
        notes=note,
    )
    conn.commit()
    return inserted


def backfill_final_place_odds(
    conn: sqlite3.Connection,
    race_id: str,
    provider: OddsProvider,
) -> dict[str, object]:
    result_count = fetch_all(conn, "SELECT count(*) AS n FROM results WHERE race_id = ?", (race_id,))[0]["n"]
    if not int(result_count or 0):
        return {"race_id": race_id, "status": "skipped", "reason": "results_not_available", "inserted": 0}

    before = final_place_odds_completeness(conn, race_id)
    try:
        rows = provider.fetch_odds(conn, race_id)
    except Exception as exc:
        now = datetime.now(timezone.utc).isoformat()
        upsert_race_status(
            conn,
            race_id,
            "resulted",
            last_odds_refresh_at=now,
            notes=f"final_place_backfill_failed; error={str(exc)[:180]}",
        )
        conn.commit()
        after = final_place_odds_completeness(conn, race_id)
        return {
            "race_id": race_id,
            "status": "source_unavailable",
            "inserted": 0,
            "error": str(exc),
            "before": before,
            "after": after,
        }

    backfill_rows = []
    for row in rows:
        if row.get("place_odds") is None:
            continue
        backfill_rows.append(
            {
                "race_id": race_id,
                "horse_id": row["horse_id"],
                "timestamp": row.get("timestamp") or datetime.now(timezone.utc).isoformat(),
                "win_odds": row["win_odds"],
                "place_odds": row["place_odds"],
                "source": FINAL_PLACE_BACKFILL_SOURCE,
            }
        )
    inserted = insert_rows(conn, "odds_ticks", backfill_rows)
    source = getattr(provider, "active_source", provider.source_name)
    now = datetime.now(timezone.utc).isoformat()
    after = final_place_odds_completeness(conn, race_id)
    note = f"final_place_backfill={source}; inserted={inserted}; place_odds={after['place_odds_count']}/{after['runner_count']}"
    if not after["complete"]:
        note += "; official_source_missing_some_runners"
    upsert_race_status(conn, race_id, "resulted", last_odds_refresh_at=now, notes=note)
    conn.commit()
    return {
        "race_id": race_id,
        "status": "done" if backfill_rows else "no_official_place_odds",
        "source": source,
        "inserted": inserted,
        "official_rows": len(backfill_rows),
        "before": before,
        "after": after,
    }


def odds_history(conn: sqlite3.Connection, race_id: str, limit: int = 300) -> list[dict[str, object]]:
    rows = fetch_all(
        conn,
        """
        SELECT
          o.horse_id,
          ru.horse_no,
          ru.horse_name,
          ru.horse_name_zh,
          COALESCE(NULLIF(ru.horse_name_zh, ''), ru.horse_name, o.horse_id) AS display_name,
          o.timestamp,
          o.win_odds,
          o.place_odds,
          o.source
        FROM odds_ticks o
        LEFT JOIN runners ru ON ru.race_id = o.race_id AND ru.horse_id = o.horse_id
        WHERE o.race_id = ?
        ORDER BY o.timestamp DESC
        LIMIT ?
        """,
        (race_id, limit),
    )
    return [dict(row) for row in rows]


def parse_odds_value(value: object) -> float | None:
    if value in {None, "", "---"}:
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def graphql_odds_payload_to_rows(
    data: dict[str, object],
    race_id: str,
    horse_id_by_no: dict[str, str],
    source_name: str,
    latest_existing: dict[str, dict[str, object]] | None = None,
) -> list[dict[str, object]]:
    meetings = (data.get("data") or {}).get("raceMeetings") if isinstance(data.get("data"), dict) else []
    meetings = meetings or []
    pools = meetings[0].get("pmPools") if meetings and isinstance(meetings[0], dict) else []
    win_by_no: dict[str, float] = {}
    place_by_no: dict[str, float] = {}
    last_update = None
    for pool in pools or []:
        odds_type = pool.get("oddsType")
        last_update = pool.get("lastUpdateTime") or last_update
        for node in pool.get("oddsNodes") or []:
            runner_no = normalize_runner_no(node.get("combString"))
            odds_value = parse_odds_value(node.get("oddsValue"))
            if not runner_no or odds_value is None:
                continue
            if odds_type == "WIN":
                win_by_no[runner_no] = odds_value
            elif odds_type == "PLA":
                place_by_no[runner_no] = odds_value

    timestamp = last_update or datetime.now(timezone.utc).isoformat()
    rows = []
    for runner_no in sorted(
        set(win_by_no) | set(place_by_no),
        key=lambda value: (0, int(value)) if value.isdigit() else (1, value),
    ):
        horse_id = horse_id_by_no.get(normalize_runner_no(runner_no))
        if not horse_id:
            continue
        win_odds = win_by_no.get(runner_no)
        if win_odds is None:
            existing = (latest_existing or {}).get(horse_id)
            win_odds = float(existing["win_odds"]) if existing and existing.get("win_odds") is not None else None
        if win_odds is None:
            continue
        rows.append(
            {
                "race_id": race_id,
                "horse_id": horse_id,
                "timestamp": timestamp,
                "win_odds": win_odds,
                "place_odds": place_by_no.get(runner_no),
                "source": source_name,
            }
        )
    return rows


def normalize_runner_no(value: object) -> str:
    text = str(value or "").strip()
    if text.isdigit():
        return str(int(text))
    return text


def browser_compatible_user_agent(user_agent: str) -> str:
    if "Mozilla/" in user_agent:
        return user_agent
    return (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        f"Chrome/124.0 Safari/537.36 {user_agent}"
    )


def mqtt_reason_success(reason_code: object) -> bool:
    if str(reason_code) == "Success":
        return True
    value = getattr(reason_code, "value", reason_code)
    try:
        return int(value) == 0
    except (TypeError, ValueError):
        return False


def mqtt_messages_to_rows(
    messages: list[tuple[str, bytes]],
    race_id: str,
    horse_id_by_no: dict[str, str],
) -> list[dict[str, object]]:
    win_by_no: dict[str, float] = {}
    place_by_no: dict[str, float] = {}
    timestamp = datetime.now(timezone.utc).isoformat()
    for topic, payload in messages:
        text = payload.decode("utf-8", errors="replace")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            continue
        flat = list(flatten_json(data))
        odds_type = "PLA" if "/pla/" in topic.lower() else "WIN"
        for item in flat:
            runner_no = item.get("combString") or item.get("combination") or item.get("runnerNo") or item.get("no")
            odds_value = item.get("oddsValue") or item.get("odds") or item.get("winOdds") or item.get("currentOdds")
            if runner_no is None or odds_value is None:
                continue
            runner_no = normalize_runner_no(runner_no)
            odds = parse_odds_value(odds_value)
            if not odds:
                continue
            if odds_type == "WIN":
                win_by_no[runner_no] = odds
            else:
                place_by_no[runner_no] = odds
    rows = []
    for runner_no, win_odds in win_by_no.items():
        horse_id = horse_id_by_no.get(runner_no)
        if not horse_id:
            continue
        rows.append(
            {
                "race_id": race_id,
                "horse_id": horse_id,
                "timestamp": timestamp,
                "win_odds": win_odds,
                "place_odds": place_by_no.get(runner_no),
                "source": "hkjc_mqtt",
            }
        )
    return rows


def flatten_json(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from flatten_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from flatten_json(child)
