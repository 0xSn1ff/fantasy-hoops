#!/usr/bin/env python3
"""
fetch_schedule.py — Phase 2 of the fantasy-hoops dashboard.

Counts how many games each NBA team plays in the current fantasy week and writes
data/schedule.json:
    {"weekStart": "2026-10-20", "weekEnd": "2026-10-26",
     "games": {"ATL": 3, "BOS": 4, ...}}

Source: ESPN's public (key-free) scoreboard endpoint, one call per day of the week.
Team codes are normalized to Yahoo's abbreviations so they join cleanly with
data/yahoo.json.

Assumes a Monday->Sunday fantasy week (Yahoo's default). Override with:
    python3 -I fetch_schedule.py --start 2026-10-20 --end 2026-10-26
"""

import argparse
import datetime as dt
import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_FILE = os.path.join(HERE, "data", "schedule.json")

ESPN = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={}"

# ESPN abbr -> Yahoo abbr (only the ones that differ; others pass through).
ESPN_TO_YAHOO = {
    "GS": "GSW", "NO": "NOP", "NY": "NYK", "SA": "SAS", "UTAH": "UTA", "WSH": "WAS",
    "PHX": "PHX", "BKN": "BKN", "NOP": "NOP",
}


def norm(abbr):
    a = (abbr or "").upper()
    return ESPN_TO_YAHOO.get(a, a)


def week_bounds(args):
    if args.start and args.end:
        return (dt.date.fromisoformat(args.start), dt.date.fromisoformat(args.end))
    today = dt.date.today()
    monday = today - dt.timedelta(days=today.weekday())  # Monday=0
    return (monday, monday + dt.timedelta(days=6))


def fetch_day(day):
    url = ESPN.format(day.strftime("%Y%m%d"))
    req = urllib.request.Request(url, headers={"User-Agent": "fantasy-hoops/1.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.load(resp)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start")
    ap.add_argument("--end")
    args = ap.parse_args()

    start, end = week_bounds(args)
    games = {}
    day = start
    while day <= end:
        try:
            data = fetch_day(day)
        except Exception as e:
            print(f"WARN: schedule fetch failed for {day} ({e})", file=sys.stderr)
            day += dt.timedelta(days=1)
            continue
        for ev in data.get("events", []):
            for comp in ev.get("competitions", []):
                for c in comp.get("competitors", []):
                    abbr = norm(c.get("team", {}).get("abbreviation"))
                    if abbr:
                        games[abbr] = games.get(abbr, 0) + 1
        day += dt.timedelta(days=1)

    out = {
        "weekStart": start.isoformat(),
        "weekEnd": end.isoformat(),
        "games": dict(sorted(games.items())),
    }
    os.makedirs(os.path.dirname(OUT_FILE), exist_ok=True)
    with open(OUT_FILE, "w") as f:
        json.dump(out, f, indent=2)
    print(f"Wrote {OUT_FILE} ({len(games)} teams, {start}..{end})")


if __name__ == "__main__":
    main()
