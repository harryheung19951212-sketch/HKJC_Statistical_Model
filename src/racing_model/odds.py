from __future__ import annotations

import json
import random
import sqlite3
import ssl
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol
from urllib.error import URLError
from urllib.request import Request, urlopen

from .live import parse_hkjc_race_id
from .storage import fetch_all, insert_rows, latest_raw_odds_by_race, race_status, upsert_race_status


HORSE_ODDS_QUERY = """
query racing($date: String, $venueCode: String, $oddsTypes: [OddsType], $raceNo: Int) {
  raceMeetings(date: $date, venueCode: $venueCode) {
    pmPools(oddsTypes: $oddsTypes, raceNo: $raceNo) {
      id
      status
      sellStatus
      oddsType
      lastUpdateTime
      oddsNodes {
        combString
        oddsValue
        hotFavourite
        oddsDropValue
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
        horse_id_by_no = {str(row["horse_no"]): row["horse_id"] for row in runners}
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
                "User-Agent": self.user_agent,
                "Accept": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=10) as response:
                data = json.loads(response.read().decode("utf-8", errors="replace"))
        except (OSError, URLError, TimeoutError) as exc:
            self.last_error = str(exc)
            raise RuntimeError(f"HKJC GraphQL request failed: {exc}") from exc

        if data.get("errors"):
            message = data["errors"][0].get("message", str(data["errors"][0]))
            self.last_error = message
            raise RuntimeError(f"HKJC GraphQL returned error: {message}")

        meetings = data.get("data", {}).get("raceMeetings") or []
        if not meetings:
            raise RuntimeError("HKJC GraphQL returned no race meeting data.")

        pools = meetings[0].get("pmPools") or []
        win_by_no: dict[str, float] = {}
        place_by_no: dict[str, float] = {}
        last_update = None
        for pool in pools:
            odds_type = pool.get("oddsType")
            last_update = pool.get("lastUpdateTime") or last_update
            for node in pool.get("oddsNodes") or []:
                runner_no = str(node.get("combString") or "").strip()
                odds_value = parse_odds_value(node.get("oddsValue"))
                if not runner_no or odds_value is None:
                    continue
                if odds_type == "WIN":
                    win_by_no[runner_no] = odds_value
                elif odds_type == "PLA":
                    place_by_no[runner_no] = odds_value

        timestamp = last_update or datetime.now(timezone.utc).isoformat()
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
                    "source": self.source_name,
                }
            )
        if not rows:
            raise RuntimeError("HKJC GraphQL returned no WIN odds rows.")
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
        horse_id_by_no = {str(row["horse_no"]): row["horse_id"] for row in runners}
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
            if str(reason_code) == "Success" or int(reason_code) == 0:
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


def refresh_odds(
    conn: sqlite3.Connection,
    race_id: str,
    provider: OddsProvider | None = None,
) -> int:
    status = race_status(conn, race_id)
    if status and status["status"] != "scheduled":
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
    status = "resulted" if runner_count and result_count >= runner_count else "live"
    upsert_race_status(
        conn,
        race_id,
        status,
        last_odds_refresh_at=datetime.now(timezone.utc).isoformat(),
        notes=note,
    )
    conn.commit()
    return inserted


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
            runner_no = str(runner_no).strip()
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
