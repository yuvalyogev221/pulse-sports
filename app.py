from __future__ import annotations

import html
import re
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from threading import Lock
from typing import Any

import requests
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

TSDB_BASE = "https://www.thesportsdb.com/api/v1/json/123"
NEWS_BASE = "https://news.google.com/rss/search"

CACHE_TTL = 120

session = requests.Session()
session.headers.update({
    "User-Agent": "PULSE Sports Dashboard/1.0",
    "Accept": "application/json, text/plain, */*",
})

cache: dict[str, tuple[float, Any]] = {}
cache_lock = Lock()


def cached(key: str, loader, ttl: int = CACHE_TTL):
    now = time.time()
    with cache_lock:
        hit = cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    value = loader()
    with cache_lock:
        cache[key] = (now, value)
    return value


def get_json(url: str, params: dict[str, Any] | None = None, timeout: int = 15):
    response = session.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    return response.json()


def safe_json(url: str, params: dict[str, Any] | None = None):
    try:
        return get_json(url, params)
    except Exception as exc:
        app.logger.warning("Upstream request failed: %s params=%r error=%s", url, params, exc)
        return {}


def current_season_label() -> str:
    now = datetime.now(timezone.utc)
    start_year = now.year if now.month >= 7 else now.year - 1
    return f"{start_year}-{start_year + 1}"


LEAGUES = [
    {"key": "winner", "name": "Winner League", "name_he": "ליגת ווינר סל", "sport": "Basketball",
     "tsdb_name": "Israeli Basketball Premier League", "tsdb_id": 4474},
    {"key": "nba", "name": "NBA", "name_he": "NBA", "sport": "Basketball",
     "tsdb_name": "NBA", "tsdb_id": 4387},
    {"key": "premier", "name": "Premier League", "name_he": "Premier League", "sport": "Soccer",
     "tsdb_name": "English Premier League", "tsdb_id": 4328},
    {"key": "champions", "name": "UEFA Champions League", "name_he": "Champions League", "sport": "Soccer",
     "tsdb_name": "UEFA Champions League", "tsdb_id": 4480},
    {"key": "bundesliga", "name": "Bundesliga", "name_he": "Bundesliga", "sport": "Soccer",
     "tsdb_name": "German Bundesliga", "tsdb_id": 4331},
    {"key": "laliga", "name": "LaLiga", "name_he": "LaLiga", "sport": "Soccer",
     "tsdb_name": "Spanish La Liga", "tsdb_id": 4335},
    {"key": "seriea", "name": "Serie A", "name_he": "Serie A", "sport": "Soccer",
     "tsdb_name": "Italian Serie A", "tsdb_id": 4332},
    {"key": "ligue1", "name": "Ligue 1", "name_he": "Ligue 1", "sport": "Soccer",
     "tsdb_name": "French Ligue 1", "tsdb_id": 4334},
    {"key": "europa", "name": "UEFA Europa League", "name_he": "Europa League", "sport": "Soccer",
     "tsdb_name": "UEFA Europa League", "tsdb_id": 4481},
]

FAVORITE_TEAMS = [
    {"key": "hapoel", "id": 137400, "label": "הפועל ירושלים"},
    {"key": "lakers", "id": 134867, "label": "Los Angeles Lakers"},
    {"key": "manutd", "id": 133612, "label": "Manchester United"},
]


def parse_tsdb_datetime(date_str: str | None, time_str: str | None = None):
    if not date_str:
        return None
    value = date_str
    if time_str:
        value = f"{date_str} {time_str}"
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            dt = datetime.strptime(value, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            pass
    return None


def image_or_blank(value: str | None) -> str:
    return value or ""


def normalize_tsdb_event(e: dict[str, Any]) -> dict[str, Any]:
    dt = parse_tsdb_datetime(e.get("dateEvent"), e.get("strTime"))
    home_score = e.get("intHomeScore")
    away_score = e.get("intAwayScore")

    def score(v):
        if v in (None, "", "null"):
            return None
        try:
            return int(v)
        except Exception:
            return v

    return {
        "id": e.get("idEvent"),
        "date": dt.isoformat() if dt else None,
        "home": {
            "id": e.get("idHomeTeam"),
            "name": e.get("strHomeTeam") or "Unknown",
            "logo": image_or_blank(e.get("strHomeTeamBadge")),
        },
        "away": {
            "id": e.get("idAwayTeam"),
            "name": e.get("strAwayTeam") or "Unknown",
            "logo": image_or_blank(e.get("strAwayTeamBadge")),
        },
        "home_score": score(home_score),
        "away_score": score(away_score),
        "status": "finished" if home_score not in (None, "") and away_score not in (None, "") else "notstarted",
        "status_text": e.get("strStatus") or "",
        "round": e.get("intRound"),
        "tournament": e.get("strLeague"),
    }


def find_tsdb_league(name: str):
    for league in LEAGUES:
        if league["tsdb_name"].lower() == name.lower():
            return {"idLeague": str(league["tsdb_id"]), "strLeague": league["tsdb_name"]}
    return None


def find_tsdb_season(league_id: str, target: str):
    def load():
        data = safe_json(f"{TSDB_BASE}/search_all_seasons.php", {"id": league_id})
        seasons = data.get("seasons") or []
        for s in seasons:
            if s.get("strSeason") == target:
                return target
        return None
    return cached(f"tsdb-season:{league_id}:{target}", load, ttl=3600)


def tsdb_events_for_season(league_name: str):
    league = find_tsdb_league(league_name)
    if not league:
        return []
    league_id = str(league.get("idLeague"))
    season = find_tsdb_season(league_id, current_season_label())
    if not season:
        return []
    data = safe_json(f"{TSDB_BASE}/eventsseason.php", {"id": league_id, "s": season})
    return [normalize_tsdb_event(e) for e in (data.get("events") or [])]


def tsdb_league_window(league_name: str):
    league = find_tsdb_league(league_name)
    if not league:
        return [], []
    league_id = str(league.get("idLeague"))
    upcoming = safe_json(f"{TSDB_BASE}/eventsnextleague.php", {"id": league_id}).get("events") or []
    past = safe_json(f"{TSDB_BASE}/eventspastleague.php", {"id": league_id}).get("events") or []
    return [normalize_tsdb_event(e) for e in past], [normalize_tsdb_event(e) for e in upcoming]


def tsdb_team_events(team_id: int):
    combined = []
    found = set()
    for league in LEAGUES:
        try:
            for event in tsdb_events_for_season(league["tsdb_name"]):
                if str(event["home"]["id"]) == str(team_id) or str(event["away"]["id"]) == str(team_id):
                    if event["id"] not in found:
                        found.add(event["id"])
                        combined.append(event)
        except Exception:
            continue

    # If the current season is not yet populated, fall back to the provider's
    # short team schedule endpoints.
    if not combined:
        data = safe_json(f"{TSDB_BASE}/eventsnext.php", {"id": team_id})
        data2 = safe_json(f"{TSDB_BASE}/eventslast.php", {"id": team_id})
        for event in (data.get("events") or []) + (data2.get("results") or []):
            item = normalize_tsdb_event(event)
            if item["id"] not in found:
                found.add(item["id"])
                combined.append(item)
    return combined


def league_events(league: dict[str, Any]):
    events = tsdb_events_for_season(league["tsdb_name"])
    if not events:
        past, future = tsdb_league_window(league["tsdb_name"])
        return past, future
    return split_past_future(events)


def soccer_table_from_events(events):
    rows = {}
    for e in events:
        hs, aw = e.get("home_score"), e.get("away_score")
        if hs is None or aw is None:
            continue
        for side in ("home", "away"):
            t = e[side]
            if not t.get("id"):
                continue
            r = rows.setdefault(str(t["id"]), {
                "position": 0, "team_id": t["id"], "team": t["name"], "logo": t.get("logo", ""),
                "played": 0, "wins": 0, "draws": 0, "losses": 0, "for": 0, "against": 0, "points": 0
            })
            r["played"] += 1
            scored = hs if side == "home" else aw
            conceded = aw if side == "home" else hs
            r["for"] += scored
            r["against"] += conceded
            if scored > conceded:
                r["wins"] += 1; r["points"] += 3
            elif scored == conceded:
                r["draws"] += 1; r["points"] += 1
            else:
                r["losses"] += 1
    ordered = sorted(rows.values(), key=lambda r: (r["points"], r["for"]-r["against"], r["for"]), reverse=True)
    for i, r in enumerate(ordered, start=1):
        r["position"] = i
    return ordered


def basketball_table_from_events(events):
    rows = {}
    for e in events:
        hs, aw = e.get("home_score"), e.get("away_score")
        if hs is None or aw is None:
            continue
        for side in ("home", "away"):
            t = e[side]
            if not t.get("id"):
                continue
            r = rows.setdefault(str(t["id"]), {
                "position": 0, "team_id": t["id"], "team": t["name"], "logo": t.get("logo", ""),
                "played": 0, "wins": 0, "draws": 0, "losses": 0, "for": 0, "against": 0, "points": 0
            })
            r["played"] += 1
            scored = hs if side == "home" else aw
            conceded = aw if side == "home" else hs
            r["for"] += scored; r["against"] += conceded
            if scored > conceded:
                r["wins"] += 1
            else:
                r["losses"] += 1
            r["points"] = r["wins"]
    ordered = sorted(rows.values(), key=lambda r: (r["wins"] / r["played"] if r["played"] else 0, r["wins"], r["for"]-r["against"]), reverse=True)
    for i, r in enumerate(ordered, start=1):
        r["position"] = i
    return ordered


def standings(league: dict[str, Any]):
    def load():
        # TheSportsDB has native tables for selected soccer leagues.
        if league["sport"] == "Soccer":
            data = safe_json(f"{TSDB_BASE}/lookuptable.php", {"l": league["tsdb_id"], "s": current_season_label()})
            rows = []
            for i, r in enumerate(data.get("table") or [], start=1):
                rows.append({
                    "position": r.get("intRank") or i,
                    "team_id": r.get("idTeam"),
                    "team": r.get("strTeam"),
                    "logo": r.get("strTeamBadge") or r.get("strBadge") or "",
                    "played": r.get("intPlayed"), "wins": r.get("intWin"), "draws": r.get("intDraw"),
                    "losses": r.get("intLoss"), "for": r.get("intGoalsFor"), "against": r.get("intGoalsAgainst"),
                    "points": r.get("intPoints"), "pct": None
                })
            if rows:
                return [{"name": league["name_he"], "rows": rows}]

        # Basketball tables are not provided as native free-tier tables, so build
        # a simple W/L table from the current-season results.
        events = tsdb_events_for_season(league["tsdb_name"])
        rows = basketball_table_from_events(events) if league["sport"] == "Basketball" else soccer_table_from_events(events)
        return [{"name": league["name_he"], "rows": rows}] if rows else []
    return cached(f"standings:{league['key']}", load, ttl=300)


def team_full(team_id: int):
    data = safe_json(f"{TSDB_BASE}/lookupteam.php", {"id": team_id})
    team = (data.get("teams") or [None])[0]
    if not team:
        return {}
    return {
        "id": team.get("idTeam"),
        "name": team.get("strTeam"),
        "logo": team.get("strBadge") or "",
        "country": team.get("strCountry"),
        "sport": team.get("strSport"),
        "city": team.get("strLocation"),
    }


def player_search(q: str):
    data = safe_json(f"{TSDB_BASE}/searchplayers.php", {"p": q})
    players = []
    for p in data.get("player") or []:
        dob = p.get("dateBorn")
        age = None
        if dob:
            try:
                birth = datetime.strptime(dob, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                age = datetime.now(timezone.utc).year - birth.year - (
                    (datetime.now(timezone.utc).month, datetime.now(timezone.utc).day) <
                    (birth.month, birth.day)
                )
            except Exception:
                pass
        players.append({
            "id": p.get("idPlayer"),
            "name": p.get("strPlayer"),
            "sport": p.get("strSport"),
            "country": p.get("strNationality"),
            "team": p.get("strTeam"),
            "age": age,
            "shirt_number": p.get("strNumber"),
            "image": p.get("strThumb") or p.get("strCutout") or "",
        })
    return players[:10]


def team_search(q: str):
    data = safe_json(f"{TSDB_BASE}/searchteams.php", {"t": q})
    teams = []
    for t in data.get("teams") or []:
        teams.append({
            "id": t.get("idTeam"),
            "name": t.get("strTeam"),
            "sport": t.get("strSport"),
            "country": t.get("strCountry"),
            "logo": t.get("strBadge") or "",
        })
    return teams[:10]


def latest_news():
    def load():
        queries = ["Hapoel Jerusalem basketball", "Los Angeles Lakers", "Manchester United"]
        all_items = []
        for query in queries:
            try:
                r = session.get(
                    NEWS_BASE,
                    params={"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"},
                    timeout=10,
                )
                r.raise_for_status()
                root = ET.fromstring(r.text)
                for item in root.findall("./channel/item"):
                    pub = item.findtext("pubDate") or ""
                    try:
                        dt = parsedate_to_datetime(pub).astimezone(timezone.utc)
                    except Exception:
                        dt = None
                    source = item.find("source")
                    all_items.append({
                        "title": html.unescape(item.findtext("title") or ""),
                        "url": item.findtext("link") or "",
                        "source": source.text if source is not None else "News",
                        "published": dt.isoformat() if dt else None,
                        "_stamp": dt.timestamp() if dt else 0,
                    })
            except Exception as exc:
                app.logger.warning("News request failed: %s", exc)
        dedup = {}
        for item in all_items:
            dedup[item["url"] or item["title"]] = item
        items = list(dedup.values())
        items.sort(key=lambda x: x["_stamp"], reverse=True)
        for x in items:
            x.pop("_stamp", None)
        return items[:10]
    return cached("news", load, ttl=300)


def build_home():
    results, upcoming = [], []
    def one(team):
        events = tsdb_team_events(team["id"])
        past, future = split_past_future(events)
        return team, past, future

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(one, t) for t in FAVORITE_TEAMS]
        for future in futures:
            team, past, future_events = future.result()
            for e in past[:20]:
                e["favorite_key"] = team["key"]
                e["favorite_label"] = team["label"]
                results.append(e)
            for e in future_events[:20]:
                e["favorite_key"] = team["key"]
                e["favorite_label"] = team["label"]
                upcoming.append(e)

    results.sort(key=lambda x: x.get("date") or "", reverse=True)
    upcoming.sort(key=lambda x: x.get("date") or "")
    return {
        "results": results[:60],
        "upcoming": upcoming[:60],
        "news": latest_news(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/home")
def api_home():
    return jsonify(cached("home", build_home))


@app.get("/api/results")
def api_results():
    def load():
        out = []
        for league in LEAGUES:
            try:
                past, _ = league_events(league)
            except Exception:
                past = []
            out.append({
                "key": league["key"],
                "name": league["name"],
                "name_he": league["name_he"],
                "events": past,
            })
        return out
    return jsonify({"leagues": cached("league-results", load)})


@app.get("/api/upcoming")
def api_upcoming():
    def load():
        out = []
        for league in LEAGUES:
            try:
                _, future = league_events(league)
            except Exception:
                future = []
            out.append({
                "key": league["key"],
                "name": league["name"],
                "name_he": league["name_he"],
                "events": future,
            })
        return out
    return jsonify({"leagues": cached("league-upcoming", load)})


@app.get("/api/standings")
def api_standings():
    def load():
        out = []
        for league in LEAGUES:
            try:
                tables = standings(league)
            except Exception:
                tables = []
            out.append({
                "key": league["key"],
                "name": league["name"],
                "name_he": league["name_he"],
                "tables": tables,
            })
        return out
    return jsonify({"leagues": cached("standings-all", load, ttl=300)})


@app.get("/api/search")
def api_search():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"query": q, "players": [], "teams": []})
    return jsonify({
        "query": q,
        "players": cached(f"player-search:{q.lower()}", lambda: player_search(q), ttl=300),
        "teams": cached(f"team-search:{q.lower()}", lambda: team_search(q), ttl=300),
    })


@app.get("/api/player/<int:player_id>")
def api_player(player_id: int):
    data = safe_json(f"{TSDB_BASE}/lookupplayer.php", {"id": player_id})
    p = (data.get("players") or [None])[0]
    if not p:
        return jsonify({})
    dob = p.get("dateBorn")
    age = None
    if dob:
        try:
            birth = datetime.strptime(dob, "%Y-%m-%d")
            today = datetime.now()
            age = today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))
        except Exception:
            pass
    return jsonify({
        "id": p.get("idPlayer"),
        "name": p.get("strPlayer"),
        "image": p.get("strThumb") or p.get("strCutout") or "",
        "team": p.get("strTeam"),
        "team_id": p.get("idTeam"),
        "team_logo": "",
        "age": age,
        "shirt_number": p.get("strNumber"),
        "country": p.get("strNationality"),
    })


@app.get("/api/team/<int:team_id>")
def api_team(team_id: int):
    details = team_full(team_id)
    events = tsdb_team_events(team_id)
    past, future = split_past_future(events)

    positions = []
    for league in LEAGUES:
        try:
            for table in standings(league):
                for row in table["rows"]:
                    if str(row["team_id"]) == str(team_id):
                        positions.append({
                            "league": league["name_he"],
                            "position": row["position"],
                        })
        except Exception:
            continue

    return jsonify({
        **details,
        "standings": positions,
        "last": past[0] if past else None,
        "next": future[0] if future else None,
    })


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
