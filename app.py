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

TSDB_BASE = "https://www.thesportsdb.com/api/v1/json/3"
ESPN_SITE = "https://site.api.espn.com/apis/site/v2"
ESPN_STANDINGS = "https://site.api.espn.com/apis/v2"
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
     "tsdb_name": "Israeli Basketball Premier League", "espn": None},
    {"key": "nba", "name": "NBA", "name_he": "NBA", "sport": "Basketball",
     "tsdb_name": "NBA", "espn": ("basketball", "nba")},
    {"key": "premier", "name": "Premier League", "name_he": "Premier League", "sport": "Soccer",
     "tsdb_name": "English Premier League", "espn": ("soccer", "eng.1")},
    {"key": "champions", "name": "UEFA Champions League", "name_he": "Champions League", "sport": "Soccer",
     "tsdb_name": "UEFA Champions League", "espn": ("soccer", "uefa.champions")},
    {"key": "bundesliga", "name": "Bundesliga", "name_he": "Bundesliga", "sport": "Soccer",
     "tsdb_name": "German Bundesliga", "espn": ("soccer", "ger.1")},
    {"key": "laliga", "name": "LaLiga", "name_he": "LaLiga", "sport": "Soccer",
     "tsdb_name": "Spanish La Liga", "espn": ("soccer", "esp.1")},
    {"key": "seriea", "name": "Serie A", "name_he": "Serie A", "sport": "Soccer",
     "tsdb_name": "Italian Serie A", "espn": ("soccer", "ita.1")},
    {"key": "ligue1", "name": "Ligue 1", "name_he": "Ligue 1", "sport": "Soccer",
     "tsdb_name": "French Ligue 1", "espn": ("soccer", "fra.1")},
    {"key": "europa", "name": "UEFA Europa League", "name_he": "Europa League", "sport": "Soccer",
     "tsdb_name": "UEFA Europa League", "espn": None},
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
    def load():
        data = safe_json(f"{TSDB_BASE}/searchleagues.php", {"l": name})
        leagues = data.get("leagues") or []
        exact = [x for x in leagues if (x.get("strLeague") or "").lower() == name.lower()]
        return exact[0] if exact else (leagues[0] if leagues else None)
    return cached(f"tsdb-league:{name}", load, ttl=3600)


def find_tsdb_season(league_id: str, target: str):
    def load():
        data = safe_json(f"{TSDB_BASE}/search_all_seasons.php", {"id": league_id})
        seasons = data.get("seasons") or []
        for s in seasons:
            if s.get("strSeason") == target:
                return s.get("strSeason")
        # Try current site season if the requested label is not present.
        for s in seasons:
            if s.get("strSeason") == current_season_label():
                return s.get("strSeason")
        return seasons[0].get("strSeason") if seasons else target
    return cached(f"tsdb-season:{league_id}:{target}", load, ttl=3600)


def tsdb_events_for_season(league_name: str):
    league = find_tsdb_league(league_name)
    if not league:
        return []
    league_id = str(league.get("idLeague"))
    season = find_tsdb_season(league_id, current_season_label())
    data = safe_json(f"{TSDB_BASE}/eventsseason.php", {"id": league_id, "s": season})
    events = data.get("events") or []
    return [normalize_tsdb_event(e) for e in events]


def tsdb_team_events(team_id: int):
    data = safe_json(f"{TSDB_BASE}/eventsnext.php", {"id": team_id})
    next_events = [normalize_tsdb_event(e) for e in (data.get("events") or [])]

    data2 = safe_json(f"{TSDB_BASE}/eventslast.php", {"id": team_id})
    last_events = [normalize_tsdb_event(e) for e in (data2.get("results") or [])]

    # Also pull the full known season and filter to get more than the small next/last endpoints.
    found = set()
    combined = []
    for item in next_events + last_events:
        if item["id"] and item["id"] not in found:
            found.add(item["id"])
            combined.append(item)

    # Resolve team appearances through league seasons.
    for league in LEAGUES:
        try:
            for event in tsdb_events_for_season(league["tsdb_name"]):
                if str(event["home"]["id"]) == str(team_id) or str(event["away"]["id"]) == str(team_id):
                    if event["id"] not in found:
                        found.add(event["id"])
                        combined.append(event)
        except Exception:
            continue

    return combined


def normalize_espn_event(e: dict[str, Any], league_name: str):
    competitions = e.get("competitions") or []
    comp = competitions[0] if competitions else {}
    competitors = comp.get("competitors") or []
    home = next((x for x in competitors if x.get("homeAway") == "home"), None)
    away = next((x for x in competitors if x.get("homeAway") == "away"), None)
    if not home and competitors:
        home, away = competitors[0], competitors[-1]

    def team(c):
        team_obj = (c or {}).get("team") or {}
        return {
            "id": team_obj.get("id"),
            "name": team_obj.get("displayName") or team_obj.get("shortDisplayName") or "Unknown",
            "logo": team_obj.get("logo") or team_obj.get("logos", [{}])[0].get("href", "") if team_obj else "",
        }

    return {
        "id": e.get("id"),
        "date": e.get("date"),
        "home": team(home),
        "away": team(away),
        "home_score": int(home.get("score")) if home and str(home.get("score", "")).isdigit() else None,
        "away_score": int(away.get("score")) if away and str(away.get("score", "")).isdigit() else None,
        "status": ((e.get("status") or {}).get("type") or {}).get("name") or "notstarted",
        "status_text": ((e.get("status") or {}).get("type") or {}).get("shortDetail") or "",
        "round": None,
        "tournament": league_name,
    }


def espn_scoreboard(sport: str, league: str):
    def load():
        data = safe_json(f"{ESPN_SITE}/sports/{sport}/{league}/scoreboard")
        return [normalize_espn_event(e, league) for e in data.get("events") or []]
    return cached(f"espn-scoreboard:{sport}:{league}", load, ttl=60)


def espn_team_schedule(sport: str, league: str, team_id: str):
    def load():
        data = safe_json(f"{ESPN_SITE}/sports/{sport}/{league}/teams/{team_id}/schedule")
        return [normalize_espn_event(e, league) for e in data.get("events") or []]
    return cached(f"espn-team:{sport}:{league}:{team_id}", load, ttl=120)


def current_season_year_int() -> int:
    now = datetime.now(timezone.utc)
    return now.year if now.month >= 8 else now.year - 1


def split_past_future(events):
    now = datetime.now(timezone.utc)
    past = [x for x in events if x.get("date") and x["date"] < now.isoformat()]
    future = [x for x in events if x.get("date") and x["date"] >= now.isoformat()]
    past.sort(key=lambda x: x.get("date") or "", reverse=True)
    future.sort(key=lambda x: x.get("date") or "")
    return past, future


def league_events(league: dict[str, Any]):
    # TheSportsDB is preferred for all listed competitions because it is accessible from Render
    # and includes the current-season event lists for these leagues.
    events = tsdb_events_for_season(league["tsdb_name"])
    if events:
        return split_past_future(events)
    if league["espn"]:
        all_events = espn_scoreboard(*league["espn"])
        return split_past_future(all_events)
    return [], []


def normalize_standings_espn(data: dict[str, Any]):
    out = []
    for group in data.get("children") or []:
        for entry in group.get("standings", {}).get("entries", []) or []:
            team = entry.get("team") or {}
            stats = {s.get("name"): s.get("displayValue") for s in entry.get("stats", []) if s.get("name")}
            out.append({
                "position": entry.get("team", {}).get("rank") or len(out) + 1,
                "team_id": team.get("id"),
                "team": team.get("displayName") or "Unknown",
                "logo": team.get("logo") or "",
                "played": stats.get("gamesPlayed") or stats.get("matchesPlayed"),
                "wins": stats.get("wins"),
                "draws": stats.get("ties") or stats.get("draws"),
                "losses": stats.get("losses"),
                "for": stats.get("pointsFor") or stats.get("goalsFor"),
                "against": stats.get("pointsAgainst") or stats.get("goalsAgainst"),
                "points": stats.get("points"),
                "pct": stats.get("winPercent") or stats.get("percentage"),
            })
    return [{"name": "Standings", "rows": out}]


def standings(league: dict[str, Any]):
    # ESPN handles standings for the major football leagues and NBA.
    # For Winner/Europa, use TheSportsDB table endpoint when available.
    if league["espn"]:
        sport, espn_league = league["espn"]
        def load_espn():
            data = safe_json(f"{ESPN_STANDINGS}/sports/{sport}/{espn_league}/standings")
            if data:
                return normalize_standings_espn(data)
            return []
        result = cached(f"standings-espn:{league['key']}", load_espn, ttl=300)
        if result:
            return result

    tsdb_league = find_tsdb_league(league["tsdb_name"])
    if not tsdb_league:
        return []
    league_id = str(tsdb_league.get("idLeague"))
    season = find_tsdb_season(league_id, current_season_label())
    data = safe_json(f"{TSDB_BASE}/lookuptable.php", {"l": league_id, "s": season})
    rows = []
    for i, r in enumerate(data.get("table") or [], start=1):
        rows.append({
            "position": r.get("intRank") or r.get("intRanked") or i,
            "team_id": r.get("idTeam"),
            "team": r.get("strTeam"),
            "logo": r.get("strBadge") or "",
            "played": r.get("intPlayed"),
            "wins": r.get("intWin"),
            "draws": r.get("intDraw"),
            "losses": r.get("intLoss"),
            "for": r.get("intGoalsFor") or r.get("intPointsFor"),
            "against": r.get("intGoalsAgainst") or r.get("intPointsAgainst"),
            "points": r.get("intPoints"),
            "pct": None,
        })
    return [{"name": league["name_he"], "rows": rows}] if rows else []


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
