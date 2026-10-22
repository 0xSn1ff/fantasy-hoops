#!/usr/bin/env python3
"""
fetch_yahoo.py — Phase 1 of the fantasy-hoops dashboard.

Pulls your Yahoo points-league data and writes data/yahoo.json:
  - league scoring (points modifiers)
  - YOUR roster
  - the free-agent / waiver pool
  - season + last-month (~30d) + last-week (~7d) fantasy-points-per-game for every player

Run (first time opens a browser for a one-time authorization):
    python3 -I fetch_yahoo.py

Credentials live in private/oauth2.json (see private/oauth2.json.template).
Optional config in config.json:
    {"league_id": "nba.l.123456", "team_key": "nba.l.123456.t.7", "season": 2025}
If omitted, the script auto-detects your current NBA league and your team in it.

NOTE: This hits your real league. It is meant to be run by you, not inside testing.
"""

import json
import os
import sys
import time
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
OAUTH_FILE = os.path.join(HERE, "private", "oauth2.json")
CONFIG_FILE = os.path.join(HERE, "config.json")
OUT_FILE = os.path.join(HERE, "data", "yahoo.json")

# Fantasy positions we iterate when gathering free agents (Yahoo wants a position arg).
FA_POSITIONS = ["PG", "SG", "SF", "PF", "C", "G", "F", "Util"]

# Fallback points formula if the league's stat_modifiers can't be read for some reason.
# Keys are Yahoo stat abbreviations. Edit to match your league if the fallback is used.
DEFAULT_POINTS = {
    "PTS": 1.0, "REB": 1.2, "AST": 1.5, "ST": 3.0, "BLK": 3.0,
    "TO": -1.0, "3PTM": 0.5, "FGM": 0.0, "FTM": 0.0,
}


def die(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            return json.load(f)
    return {}


def get_session():
    try:
        from yahoo_oauth import OAuth2
    except ImportError:
        die("yahoo_oauth not installed. Run: pip install -r requirements.txt")
    if not os.path.exists(OAUTH_FILE):
        die(f"Missing {OAUTH_FILE}. Copy private/oauth2.json.template to "
            f"private/oauth2.json and paste your Yahoo Client ID/Secret.")
    sc = OAuth2(None, None, from_file=OAUTH_FILE)
    if not sc.token_is_valid():
        sc.refresh_access_token()
    return sc


def resolve_league_and_team(gm, cfg):
    """Return (league_obj, my_team_key). Auto-detects if not in config."""
    league_id = cfg.get("league_id")
    if not league_id:
        season = cfg.get("season")
        ids = gm.league_ids(year=season) if season else gm.league_ids()
        if not ids:
            die("No NBA leagues found for your account. Set league_id in config.json.")
        league_id = ids[-1]  # most recent
        print(f"Auto-detected league_id: {league_id}")
    lg = gm.to_league(league_id)

    team_key = cfg.get("team_key")
    if not team_key:
        try:
            team_key = lg.team_key()  # the authenticated user's team in this league
        except Exception:
            die("Could not auto-detect your team_key. Set team_key in config.json.")
        print(f"Auto-detected team_key: {team_key}")
    return lg, team_key


def read_scoring(lg):
    """Return (modifiers, id_to_abbr) where modifiers maps stat abbr -> points value."""
    modifiers = {}
    id_to_abbr = {}
    try:
        raw = lg.yhandler.get_settings_raw(lg.league_id)
        settings = (raw["fantasy_content"]["league"][1]["settings"][0])
        # stat_categories: stat_id -> display/abbr name
        for s in settings.get("stat_categories", {}).get("stats", []):
            st = s["stat"]
            id_to_abbr[str(st["stat_id"])] = st.get("display_name") or st.get("name")
        # stat_modifiers: stat_id -> points value (points league)
        for s in settings.get("stat_modifiers", {}).get("stats", []):
            st = s["stat"]
            abbr = id_to_abbr.get(str(st["stat_id"]))
            if abbr is not None and st.get("value") not in (None, ""):
                modifiers[abbr] = float(st["value"])
    except Exception as e:
        print(f"WARN: could not parse league stat_modifiers ({e}); "
              f"falling back to DEFAULT_POINTS.", file=sys.stderr)
    if not modifiers:
        modifiers = dict(DEFAULT_POINTS)
    return modifiers, id_to_abbr


def fantasy_points(stat_row, modifiers):
    """Sum raw stats * modifiers. stat_row is keyed by stat abbr."""
    total = 0.0
    for abbr, mult in modifiers.items():
        v = stat_row.get(abbr)
        if isinstance(v, (int, float)):
            total += v * mult
    return total


def chunked(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def stats_for(lg, player_ids, req_type):
    """player_stats in chunks of 25; returns {player_id: stat_row}."""
    out = {}
    for chunk in chunked(player_ids, 25):
        for _ in range(3):  # simple retry for rate limits
            try:
                rows = lg.player_stats(chunk, req_type)
                break
            except Exception as e:
                print(f"WARN: player_stats retry ({e})", file=sys.stderr)
                time.sleep(2)
        else:
            rows = []
        for r in rows:
            out[r["player_id"]] = r
        time.sleep(0.3)  # be gentle with Yahoo
    return out


def per_game(stat_row, modifiers):
    """Return (fpg, gp). GP keys vary; try common ones."""
    gp = 0
    for key in ("GP", "GamesPlayed", "G"):
        if isinstance(stat_row.get(key), (int, float)) and stat_row[key]:
            gp = stat_row[key]
            break
    pts = fantasy_points(stat_row, modifiers)
    fpg = round(pts / gp, 2) if gp else 0.0
    return fpg, int(gp)


def build_players(lg, modifiers, raw_players, status):
    """raw_players: list of dicts from roster()/free_agents(). Returns schema players."""
    ids = [p["player_id"] for p in raw_players]
    season = stats_for(lg, ids, "season")
    lastmonth = stats_for(lg, ids, "lastmonth")
    lastweek = stats_for(lg, ids, "lastweek")

    # percent-owned for free agents (context for "obvious add vs deep stash")
    pct = {}
    try:
        for row in lg.percent_owned(ids):
            pct[row["player_id"]] = row.get("percent_owned", 0)
    except Exception:
        pass

    players = []
    for p in raw_players:
        pid = p["player_id"]
        fpg_s, gp_s = per_game(season.get(pid, {}), modifiers)
        fpg_m, _ = per_game(lastmonth.get(pid, {}), modifiers)
        fpg_w, _ = per_game(lastweek.get(pid, {}), modifiers)
        pos = p.get("eligible_positions") or p.get("display_position") or []
        if isinstance(pos, str):
            pos = pos.split(",")
        players.append({
            "name": p.get("name"),
            "team": (p.get("editorial_team_abbr") or "").upper(),
            "pos": [x for x in pos if x not in ("Util", "IL", "BN")],
            "status": status,
            "pctRostered": round(float(pct.get(pid, 0)), 1),
            "fpgSeason": fpg_s,
            "fpgL30": fpg_m,
            "fpgL7": fpg_w,
            "gpSeason": gp_s,
            "trend": round(fpg_w - fpg_s, 2),
        })
    return players


def main():
    try:
        import yahoo_fantasy_api as yfa
    except ImportError:
        die("yahoo_fantasy_api not installed. Run: pip install -r requirements.txt")

    cfg = load_config()
    sc = get_session()
    gm = yfa.Game(sc, "nba")
    lg, my_team_key = resolve_league_and_team(gm, cfg)

    modifiers, _ = read_scoring(lg)
    print(f"Scoring modifiers: {modifiers}")

    settings = lg.settings()
    league_name = settings.get("name", "My League")
    try:
        week = lg.current_week()
    except Exception:
        week = None

    # --- my roster ---
    my_team = lg.to_team(my_team_key)
    roster_raw = my_team.roster(week) if week else my_team.roster()
    my_players = build_players(lg, modifiers, roster_raw, status="ROSTERED")
    print(f"Roster: {len(my_players)} players")

    # --- free agents across positions (dedupe by player_id) ---
    seen = {}
    for pos in FA_POSITIONS:
        try:
            for p in lg.free_agents(pos):
                seen[p["player_id"]] = p
        except Exception as e:
            print(f"WARN: free_agents({pos}) failed ({e})", file=sys.stderr)
    fa_raw = list(seen.values())
    fa_players = build_players(lg, modifiers, fa_raw, status="FA")
    print(f"Free agents: {len(fa_players)} players")

    out = {
        "meta": {
            "leagueName": league_name,
            "leagueKey": lg.league_id,
            "scoringType": settings.get("scoring_type", "point"),
            "week": week,
            "scoring": [{"stat": k, "value": v} for k, v in modifiers.items()],
            "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        },
        "myTeam": my_players,
        "freeAgents": fa_players,
    }
    os.makedirs(os.path.dirname(OUT_FILE), exist_ok=True)
    with open(OUT_FILE, "w") as f:
        json.dump(out, f, indent=2)
    print(f"Wrote {OUT_FILE}")


if __name__ == "__main__":
    main()
