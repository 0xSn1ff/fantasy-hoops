#!/usr/bin/env python3
"""
build_data.py — Phase 4 of the fantasy-hoops dashboard.

Merges the three data sources into data/merged.json, the single file the dashboard
embeds:
    data/yahoo.json          (required)  roster + free agents + FP/G windows
    data/schedule.json       (optional)  games this week per team
    data/lineupexperts.json  (optional)  LE Free Agent Finder / Buy-Sell / Drop Advisor

Players are joined to LineupExperts entries on a normalized name (with a last-name +
first-initial fuzzy fallback). LE entries that match no Yahoo player are kept in an
"leOnly" bucket so nothing is silently dropped.

Run:  python3 -I build_data.py
"""

import json
import os
import re
import sys
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
D = os.path.join(HERE, "data")
YAHOO = os.path.join(D, "yahoo.json")
SCHED = os.path.join(D, "schedule.json")
LE = os.path.join(D, "lineupexperts.json")
OUT = os.path.join(D, "merged.json")

SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def load(path, required=False):
    if not os.path.exists(path):
        if required:
            print(f"ERROR: missing {path}", file=sys.stderr)
            sys.exit(1)
        return None
    with open(path) as f:
        return json.load(f)


def norm_name(name):
    """lowercase, drop accents/punctuation/suffixes -> 'joshgiddey'."""
    if not name:
        return ""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = s.lower()
    s = re.sub(r"[^a-z\s]", " ", s)
    parts = [p for p in s.split() if p not in SUFFIXES]
    return "".join(parts)


def name_key_fuzzy(name):
    """('giddey', 'j') last-name + first-initial, for 'J. Giddey' vs 'Josh Giddey'."""
    if not name:
        return ("", "")
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z\s.]", " ", s)
    toks = [t for t in re.split(r"[\s.]+", s) if t and t not in SUFFIXES]
    if not toks:
        return ("", "")
    last = toks[-1]
    first_initial = toks[0][0] if toks[0] else ""
    return (last, first_initial)


class LEIndex:
    """Lookup from a Yahoo player to its LineupExperts attributes."""

    def __init__(self, le):
        self.exact = {}        # norm_name -> le record
        self.fuzzy = {}        # (last, initial) -> list of le records
        self.matched = set()   # ids of le records matched to a yahoo player
        self.records = []

        def add(name, team, **fields):
            rec = {"name": name, "team": (team or "").upper(), **fields,
                   "_id": len(self.records)}
            self.records.append(rec)
            self.exact.setdefault(norm_name(name), rec)
            self.fuzzy.setdefault(name_key_fuzzy(name), []).append(rec)

        if not le:
            return
        for i, r in enumerate(le.get("freeAgentFinder", [])):
            add(r.get("name"), r.get("team"), leFinderRank=r.get("rank", i + 1))
        for r in le.get("buyLowSellHigh", []):
            add(r.get("name"), r.get("team"), leTag=r.get("tag"))
        for r in le.get("dropAdvisor", []):
            add(r.get("name"), r.get("team"), leDrop=True)

    def lookup(self, name, team):
        hit = self.exact.get(norm_name(name))
        if not hit:
            cands = self.fuzzy.get(name_key_fuzzy(name), [])
            if team:
                cands = [c for c in cands if not c["team"] or c["team"] == team.upper()] or cands
            hit = cands[0] if len(cands) == 1 else (cands[0] if cands else None)
        if not hit:
            return {}
        # Collapse ALL le records for this person (finder + buy/sell + drop may be separate rows)
        merged = {}
        for r in self.exact_and_fuzzy_group(hit):
            self.matched.add(r["_id"])
            for k, v in r.items():
                if k in ("leFinderRank", "leTag", "leDrop") and v is not None:
                    merged[k] = v
        return merged

    def exact_and_fuzzy_group(self, hit):
        key = name_key_fuzzy(hit["name"])
        group = self.fuzzy.get(key, [])
        return group if group else [hit]

    def unmatched(self):
        out = []
        for r in self.records:
            if r["_id"] in self.matched:
                continue
            out.append({k: v for k, v in r.items() if not k.startswith("_")})
        return out


def apply_schedule(players, games):
    for p in players:
        p["gamesThisWeek"] = games.get((p.get("team") or "").upper(), None)


def apply_le(players, idx):
    for p in players:
        attrs = idx.lookup(p.get("name"), p.get("team"))
        p["leFinderRank"] = attrs.get("leFinderRank")
        p["leTag"] = attrs.get("leTag")
        p["leDrop"] = bool(attrs.get("leDrop", False))


def main():
    yahoo = load(YAHOO, required=True)
    sched = load(SCHED)
    le = load(LE)

    games = (sched or {}).get("games", {})
    idx = LEIndex(le)

    for bucket in ("myTeam", "freeAgents"):
        apply_schedule(yahoo.get(bucket, []), games)
        apply_le(yahoo.get(bucket, []), idx)

    meta = yahoo.get("meta", {})
    meta["sources"] = {
        "yahoo": True,
        "schedule": sched is not None,
        "lineupExperts": le is not None,
    }
    if sched:
        meta["weekStart"] = sched.get("weekStart")
        meta["weekEnd"] = sched.get("weekEnd")

    out = {
        "meta": meta,
        "myTeam": yahoo.get("myTeam", []),
        "freeAgents": yahoo.get("freeAgents", []),
        "leOnly": idx.unmatched(),
    }
    with open(OUT, "w") as f:
        json.dump(out, f, indent=2)
    n_le = len(idx.records)
    print(f"Wrote {OUT}: {len(out['freeAgents'])} FA, "
          f"{len(out['myTeam'])} roster, "
          f"{n_le - len(idx.unmatched())}/{n_le} LE entries matched, "
          f"{len(out['leOnly'])} LE-only.")


if __name__ == "__main__":
    main()
