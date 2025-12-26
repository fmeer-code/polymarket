import os
import sys
import requests
from datetime import date as dt_date
from dotenv import load_dotenv

load_dotenv(override=True)

API_KEY = os.getenv("SPORTRADAR_API_KEY", "").strip()
if not API_KEY:
    raise SystemExit("Missing SPORTRADAR_API_KEY in environment or .env")

team_query = ""
date_str = "2025-12-25"
url = f"https://api.sportradar.com/soccer/trial/v4/en/schedules/{date_str}/schedules.json"

resp = requests.get(url, params={"api_key": API_KEY})
try:
    resp.raise_for_status()
except requests.HTTPError as exc:
    extra = f"Body: {resp.text}"
    # Sportradar returns 404/Invalid route when the date is outside coverage or not yet published.
    if resp.status_code == 404:
        extra += " | Tip: pick a covered date (recent past or near-future fixtures) or check your plan tier."
    raise SystemExit(f"Schedule request failed for {date_str}: {exc} | {extra}") from exc

data = resp.json()
schedules = data.get("schedules")
if not schedules:
    raise SystemExit(
        f"No 'schedules' in response for {date_str}. Keys: {list(data.keys())} | Body: {data}"
    )

matches = []
for schedule in schedules:
    event = schedule.get("sport_event") or {}
    competitors = event.get("competitors", [])
    teams = [c.get("name", "") for c in competitors]
    if any(team_query in team.lower() for team in teams):
        matches.append(
            {
                "id": event.get("id"),
                "start_time": event.get("start_time"),
                "teams": " vs ".join(teams),
            }
        )

if not matches:
    print(f"No matches containing '{team_query}' on {date_str}")
else:
    for m in matches:
        print(f"{m['start_time']} | {m['teams']} | {m['id']}")
