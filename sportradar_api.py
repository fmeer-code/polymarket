import os
import requests
from datetime import datetime, timezone
import json
import time
from dotenv import load_dotenv

# Always reload .env so edits are picked up during development
load_dotenv(override=True)

api_key = os.getenv("SPORTRADAR_API_KEY", "").strip()
if not api_key:
    raise ValueError("SPORTRADAR_API_KEY must be set in the environment")

# sport_event_id can be passed as CLI arg or pulled from env SPORT_EVENT_ID
sport_event_id = "sr:sport_event:57797073"
GOAL_LOG_PATH = "goal_events.txt"

headers = {"x-api-key": api_key}

params = {
    "format": "json",
    "sport_event_id": sport_event_id,
}


def _iso_millis(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds")


def _timestamp_with_millis(raw_timestamp: str | None) -> str:
    """Normalize a raw timestamp to UTC ISO format with millisecond precision."""
    if not raw_timestamp:
        return _iso_millis(datetime.now(timezone.utc))

    try:
        normalized = raw_timestamp.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return raw_timestamp

    return _iso_millis(parsed)


def _event_exists(event_id: str) -> bool:
    """Check if Sportradar knows about the given sport event ID."""
    url = (
        "https://api.sportradar.com/soccer/trial/v4/en"
        f"/sport_events/{event_id}/summary.json"
    )
    resp = requests.get(url, params={"api_key": api_key})
    if resp.status_code == 404:
        return False
    resp.raise_for_status()
    return True


if not _event_exists(sport_event_id):
    raise SystemExit(
        f"Sportradar event not found or unavailable: {sport_event_id}. "
        "Pick a valid SPORT_EVENT_ID before subscribing."
    )


def _get_with_backoff(url: str, *, params=None, headers=None, max_attempts: int = 3):
    """GET with simple backoff for Sportradar 429 throttling."""
    for attempt in range(1, max_attempts + 1):
        resp = requests.get(url, params=params, headers=headers, allow_redirects=False)
        if resp.status_code != 429:
            resp.raise_for_status()
            return resp

        retry_after = resp.headers.get("Retry-After")
        try:
            retry_after = float(retry_after)
        except (TypeError, ValueError):
            retry_after = 5.0
        sleep_for = retry_after + (attempt - 1)
        print(
            f"Received 429 Too Many Requests. Sleeping {sleep_for:.1f}s "
            f"(attempt {attempt}/{max_attempts})."
        )
        time.sleep(sleep_for)

    # last attempt
    resp.raise_for_status()
    return resp


sub_resp = _get_with_backoff(
    "https://api.sportradar.com/soccer/trial/v4/stream/events/subscribe",
    params=params,
    headers=headers,
)

redirect_url = sub_resp.headers["Location"]
stream_resp = requests.get(redirect_url, stream=True, headers=headers)
stream_resp.raise_for_status()


def _split_line_into_json_fragments(line: str):
    """
    Sportradar occasionally concatenates objects back-to-back like `}{`.
    This splits those into parseable fragments while preserving braces.
    """
    if "}{" not in line:
        return [line]

    fragments = []
    parts = line.split("}{")
    for idx, part in enumerate(parts):
        if idx != 0:
            part = "{" + part
        if idx != len(parts) - 1:
            part = part + "}"
        fragments.append(part)
    return fragments


for line in stream_resp.iter_lines():
    if not line:
        continue

    decoded_line = line.decode("utf-8")
    for fragment in _split_line_into_json_fragments(decoded_line):
        try:
            payload = json.loads(fragment)
        except json.JSONDecodeError:
            continue

        event_payload = payload.get("payload", {}).get("event", {}) or {}

        if event_payload.get("type") == "score_change":
            scorer = event_payload.get("competitor")
            if not scorer:
                continue

            event_time = _timestamp_with_millis(
                event_payload.get("updated_time") or event_payload.get("time")
            )

            print(f"{event_time} {scorer}")
            with open(GOAL_LOG_PATH, "a", encoding="utf-8") as log_file:
                log_file.write(f"{event_time} {scorer}\n")

        if "heartbeat" in payload:
            hb = payload["heartbeat"]
            hb_type = hb.get("type", "unknown")
            hb_from = hb.get("from")
            hb_to = hb.get("to")
            now = _iso_millis(datetime.now(timezone.utc))
            print(f"{now} heartbeat {hb_type} {hb_from}->{hb_to}")
            continue
