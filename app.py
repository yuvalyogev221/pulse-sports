from __future__ import annotations

import html
import os
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from threading import Lock
from typing import Any

import requests
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

# ------------------------------------------------------------
# PULSE data architecture
# ------------------------------------------------------------
# One API-Sports account/key is used for both Football and Basketball.
# The browser NEVER receives this key. Requests are proxied through Flask.
# ------------------------------------------------------------

API_KEY = os.getenv("API_SPORTS_KEY", "").strip()

FOOTBALL_BASE = "https://v3.football.api-sports.io"
BASKETBALL_BASE = "https://v1.basketball.api-sports.io"
NEWS_BASE = "https://news.google.com/rss/search"

CACHE_SECONDS = 600  # 10 minutes for normal pages

session = requests.Session()
session.headers.update({
    "User-Agent": "PULSE Sports Dashboard/2.0",
    "Accept": "application/json",
})

cache: dict[str, tuple[float, Any]] = {}
cache_lock = Lock()


def cached(key: str, loader, ttl: int = CACHE_SECONDS):
    now = time.time()
    with cache_lock:
        hit = cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    value = loader()
    with cache_lock:
        cache[key] = (now, value)
    return value


def current_football_season() -> int:
    now = datetime.now(timezone.utc)
    return now.year if now.month >= 7 else now.year - 1


def current_basketball_season() -> str:
    now = datetime.now(timezone.utc)
    start = now.year if now.month >= 9 else now.year - 1
    return f"{start}-{start + 1}"


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def api_get(base: str, path: str, params: dict[str, Any] | None = None):
    if not API_KEY:
        raise RuntimeError(
            "API_SPORTS_KEY is missing. Add the API-Sports key in Render Environment Variables."
        )

    url = f"{base}{path}"
    try:
        r = session.get(url, params=params, headers={"x-apisports-key": API_KEY}, timeout=20)
    except requests.RequestException as exc:
        app.logger.warning("API network error: %s params=%r error=%s", url, params, exc)
        raise

    if r.status_code in (401, 403):
        app.logger.warning(
            "API authorization failed: status=%s url=%s params=%r",
            r.status_code, url, params
        )
    elif r.status_code == 429:
        app.logger.warning("API rate limit reached: %s params=%r", url, params)
    r.raise_for_status()
    return r.json()


def safe_api_get(base: str, path: str, params: dict[str, Any] | None = None):
    try:
        return api_get(base, path, params)
    except Exception:
        return {}


# ------------------------------------------------------------
# Competition configuration
# ------------------------------------------------------------

FOOTBALL_LEAGUES = [
    {"key": "premier", "name": "Premier League", "name_he": "Premier League", "id": 39},
    {"key": "champions", "name": "UEFA Champions League", "name_he": "Champions League", "id": 2},
    {"key": "bundesliga", "name": "Bundesliga", "name_he": "Bundesliga", "id": 78},
    {"key": "laliga", "name": "LaLiga", "name_he": "LaLiga", "id": 140},
    {"key": "seriea", "name": "Serie A", "name_he": "Serie A", "id": 135},
    {"key": "ligue1", "name": "Ligue 1", "name_he": "Ligue 1", "id": 61},
    {"key": "europa", "name": "UEFA Europa League", "name_he": "Europa League", "id": 3},
]

BASKETBALL_LEAGUES = [
    {"key": "nba", "name": "NBA", "name_he": "NBA", "id": 12},
    # API-Sports can change the numeric ID of smaller competitions in their catalogue.
    # We resolve Israel's league once per process and cache it for a day.
    {"key": "winner", "name": "Winner League", "name_he": "ליגת ווינר סל", "id": None},
]


def resolve_winner_league_id():
    def load():
        data = safe_api_get(BASKETBALL_BASE, "/leagues", {"search": "Winner League"})
        leagues = data.get("response") or []
        # Prefer an Israel competition whose name contains winner/super league.
        for item in leagues:
            country = str(item.get("country", {}).get("name", "")).lower()
            name = str(item.get("name", "")).lower()
            if country == "israel" and ("winner" in name or "super" in name or "israel" in name):
                return item.get("id")
        # Next best: exact-ish name match.
        for item in leagues:
            name = str(item.get("name", "")).lower()
            if "winner" in name:
                return item.get("id")
        return None

    return cached("winner-league-id", load, ttl=86400)


def basketball_leagues():
    result = []
    for league in BASKETBALL_LEAGUES:
        item = dict(league)
        if item["id"] is None:
            item["id"] = resolve_winner_league_id()
        result.append(item)
    return result


# ------------------------------------------------------------
# Normalization
# ------------------------------------------------------------

def normalize_football_fixture(item: dict[str, Any]) -> dict[str, Any]:
    fixture = item.get("fixture") or {}
    league = item.get("league") or {}
    teams = item.get("teams") or {}
    goals = item.get("goals") or {}
    status = fixture.get("status") or {}

    return {
        "id": fixture.get("id"),
        "date": fixture.get("date"),
        "home": {
            "id": (teams.get("home") or {}).get("id"),
            "name": (teams.get("home") or {}).get("name") or "Unknown",
            "logo": (teams.get("home") or {}).get("logo") or "",
        },
        "away": {
            "id": (teams.get("away") or {}).get("id"),
            "name": (teams.get("away") or {}).get("name") or "Unknown",
            "logo": (teams.get("away") or {}).get("logo") or "",
        },
        "home_score": goals.get("home"),
        "away_score": goals.get("away"),
        "status": status.get("short") or "",
        "status_text": status.get("long") or "",
        "round": league.get("round"),
        "tournament": league.get("name"),
    }


def normalize_basketball_game(item: dict[str, Any]) -> dict[str, Any]:
    teams = item.get("teams") or {}
    scores = item.get("scores") or {}
    status = item.get("status") or {}
    home_team = teams.get("home") or {}
    away_team = teams.get("away") or {}
    home_score = scores.get("home") or {}
    away_score = scores.get("away") or {}

    dt = item.get("date")
    if dt and not str(dt).endswith("Z") and "+" not in str(dt):
        dt = f"{dt}+00:00"

    return {
        "id": item.get("id"),
        "date": dt,
        "home": {
            "id": home_team.get("id"),
            "name": home_team.get("name") or "Unknown",
            "logo": home_team.get("logo") or "",
        },
        "away": {
            "id": away_team.get("id"),
            "name": away_team.get("name") or "Unknown",
            "logo": away_team.get("logo") or "",
        },
        "home_score": home_score.get("total"),
        "away_score": away_score.get("total"),
        "status": status.get("short") or "",
        "status_text": status.get("long") or status.get("short") or "",
        "round": item.get("stage"),
        "tournament": (item.get("league") or {}).get("name"),
    }


# ------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------

def football_fixtures_for_league(league_id: int):
    season = current_football_season()

    def load():
        data = safe_api_get(
            FOOTBALL_BASE,
            "/fixtures",
            {"league": league_id, "season": season}
        )
        return [normalize_football_fixture(x) for x in (data.get("response") or [])]

    return cached(f"football-fixtures:{league_id}:{season}", load, ttl=600)


def basketball_fixtures_for_league(league_id: int | None):
    if not league_id:
        return []
    season = current_basketball_season()

    def load():
        data = safe_api_get(
            BASKETBALL_BASE,
            "/games",
            {"league": league_id, "season": season}
        )
        return [normalize_basketball_game(x) for x in (data.get("response") or [])]

    return cached(f"basketball-fixtures:{league_id}:{season}", load, ttl=600)


def split_past_future(events: list[dict[str, Any]]):
    now = datetime.now(timezone.utc)
    past, future = [], []

    for event in events:
        raw = event.get("date")
        if not raw:
            continue
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            dt = dt.astimezone(timezone.utc)
        except ValueError:
            continue

        # Status codes make completed basketball games more reliable.
        status = str(event.get("status", "")).upper()
        is_finished = status in {
            "FT", "AOT", "POST", "CANC", "ABD", "AWD", "WO", "FINAL", "FINISHED"
        }
        if is_finished or dt < now:
            # Avoid putting an obviously upcoming, unstarted event into past.
            if not is_finished and event.get("home_score") is None and event.get("away_score") is None:
                future.append(event)
            else:
                past.append(event)
        else:
            future.append(event)

    past.sort(key=lambda e: e.get("date") or "", reverse=True)
    future.sort(key=lambda e: e.get("date") or "")
    return past, future


def all_league_fixture_data():
    leagues = []

    for league in FOOTBALL_LEAGUES:
        events = football_fixtures_for_league(league["id"])
        past, future = split_past_future(events)
        leagues.append({
            **league,
            "sport": "football",
            "events_past": past,
            "events_future": future,
        })

    for league in basketball_leagues():
        events = basketball_fixtures_for_league(league["id"])
        past, future = split_past_future(events)
        leagues.append({
            **league,
            "sport": "basketball",
            "events_past": past,
            "events_future": future,
        })

    # Keep the exact user-requested order: Winner, NBA, Premier, Champions, Bundesliga, LaLiga, Serie A, Ligue 1, Europa
    order = ["winner", "nba", "premier", "champions", "bundesliga", "laliga", "seriea", "ligue1", "europa"]
    rank = {key: i for i, key in enumerate(order)}
    leagues.sort(key=lambda x: rank.get(x["key"], 999))
    return leagues


# ------------------------------------------------------------
# Standings
# ------------------------------------------------------------

def football_standings(league_id: int):
    season = current_football_season()

    def load():
        data = safe_api_get(
            FOOTBALL_BASE,
            "/standings",
            {"league": league_id, "season": season}
        )
        tables = []
        for group in data.get("response") or []:
            league = group.get("league") or {}
            for table in league.get("standings") or []:
                rows = []
                for row in table:
                    team = row.get("team") or {}
                    rows.append({
                        "position": row.get("rank"),
                        "team_id": team.get("id"),
                        "team": team.get("name"),
                        "logo": team.get("logo") or "",
                        "played": (row.get("all") or {}).get("played"),
                        "wins": (row.get("all") or {}).get("win"),
                        "draws": (row.get("all") or {}).get("draw"),
                        "losses": (row.get("all") or {}).get("lose"),
                        "for": (row.get("all") or {}).get("goals", {}).get("for"),
                        "against": (row.get("all") or {}).get("goals", {}).get("against"),
                        "points": row.get("points"),
                        "pct": None,
                    })
                tables.append({"name": table and league.get("name") or "Standings", "rows": rows})
        return tables

    return cached(f"football-standings:{league_id}:{season}", load, ttl=1200)


def basketball_standings(league_id: int | None):
    if not league_id:
        return []

    season = current_basketball_season()

    def load():
        data = safe_api_get(
            BASKETBALL_BASE,
            "/standings",
            {"league": league_id, "season": season}
        )
        rows = []

        for item in data.get("response") or []:
            # API-Basketball has used a few response shapes over time.
            team = item.get("team") or {}
            rows.append({
                "position": item.get("position") or item.get("rank"),
                "team_id": team.get("id") or item.get("id"),
                "team": team.get("name") or item.get("name"),
                "logo": team.get("logo") or item.get("logo") or "",
                "played": item.get("games") or item.get("played"),
                "wins": item.get("wins"),
                "draws": item.get("draws"),
                "losses": item.get("losses"),
                "for": item.get("for"),
                "against": item.get("against"),
                "points": item.get("points") or item.get("win"),
                "pct": item.get("percentage"),
            })

        if rows:
            rows.sort(key=lambda x: (x["position"] is None, x["position"] or 999))
            return [{"name": "Standings", "rows": rows}]
        return []

    return cached(f"basketball-standings:{league_id}:{season}", load, ttl=1200)


def all_standings():
    output = []
    for league in FOOTBALL_LEAGUES:
        output.append({
            **league,
            "sport": "football",
            "tables": football_standings(league["id"]),
        })
    for league in basketball_leagues():
        output.append({
            **league,
            "sport": "basketball",
            "tables": basketball_standings(league["id"]),
        })

    order = ["winner", "nba", "premier", "champions", "bundesliga", "laliga", "seriea", "ligue1", "europa"]
    rank = {key: i for i, key in enumerate(order)}
    output.sort(key=lambda x: rank.get(x["key"], 999))
    return output


# ------------------------------------------------------------
# Favorites / home page
# ------------------------------------------------------------

def find_football_team_id(search: str):
    data = safe_api_get(FOOTBALL_BASE, "/teams", {"search": search})
    candidates = data.get("response") or []
    if not candidates:
        return None
    # Prefer exact case-insensitive team name.
    for item in candidates:
        team = item.get("team") or {}
        if str(team.get("name", "")).lower() == search.lower():
            return team.get("id")
    return (candidates[0].get("team") or {}).get("id")


def find_basketball_team(search: str):
    data = safe_api_get(BASKETBALL_BASE, "/teams", {"search": search})
    candidates = data.get("response") or []
    if not candidates:
        return None
    for item in candidates:
        if str(item.get("name", "")).lower() == search.lower():
            return item
    return candidates[0]


def home_data():
    # Home uses the already-loaded league schedules whenever possible,
    # avoiding extra API calls.
    all_data = cached("all-league-fixtures", all_league_fixture_data, ttl=600)

    league_by_key = {x["key"]: x for x in all_data}

    # English Premier League: filter by Manchester United team id 33 (stable API ID).
    favorite_ids = {
        "manutd": {"sport": "football", "team_id": 33, "league_keys": ["premier"]},
    }

    # Resolve basketball team IDs once per day. This costs a small number of API calls.
    lakers = cached("team-search:lakers", lambda: find_basketball_team_id("Los Angeles Lakers"), ttl=86400)
    hapoel = cached("team-search:hapoel-jerusalem", lambda: find_basketball_team_id("Hapoel Jerusalem"), ttl=86400)

    favorite_ids["lakers"] = {"sport": "basketball", "team_id": lakers, "league_keys": ["nba"]}
    favorite_ids["hapoel"] = {"sport": "basketball", "team_id": hapoel, "league_keys": ["winner"]}

    results = []
    future = []

    for fav in favorite_ids.values():
        for league_key in fav["league_keys"]:
            league = league_by_key.get(league_key)
            if not league:
                continue
            team_id = fav["team_id"]
            if not team_id:
                continue

            for event in league.get("events_past", []):
                if str(event["home"]["id"]) == str(team_id) or str(event["away"]["id"]) == str(team_id):
                    results.append(event)

            for event in league.get("events_future", []):
                if str(event["home"]["id"]) == str(team_id) or str(event["away"]["id"]) == str(team_id):
                    future.append(event)

    results.sort(key=lambda e: e.get("date") or "", reverse=True)
    future.sort(key=lambda e: e.get("date") or "")

    return {
        "results": results[:60],
        "upcoming": future[:60],
        "news": latest_news(),
        "updated_at": iso_now(),
        "configured": bool(API_KEY),
    }


# ------------------------------------------------------------
# News
# ------------------------------------------------------------

def latest_news():
    def load():
        queries = [
            "Hapoel Jerusalem basketball",
            "Los Angeles Lakers",
            "Manchester United",
        ]
        items = []

        for query in queries:
            try:
                response = session.get(
                    NEWS_BASE,
                    params={"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"},
                    timeout=12,
                )
                response.raise_for_status()
                root = ET.fromstring(response.text)

                for item in root.findall("./channel/item"):
                    pub = item.findtext("pubDate") or ""
                    try:
                        dt = parsedate_to_datetime(pub).astimezone(timezone.utc)
                    except Exception:
                        dt = None

                    source = item.find("source")
                    items.append({
                        "title": html.unescape(item.findtext("title") or ""),
                        "url": item.findtext("link") or "",
                        "source": source.text if source is not None else "News",
                        "published": dt.isoformat() if dt else None,
                        "_stamp": dt.timestamp() if dt else 0,
                    })
            except Exception as exc:
                app.logger.warning("News request failed: %s", exc)

        unique = {}
        for item in items:
            unique[item["url"] or item["title"]] = item

        result = list(unique.values())
        result.sort(key=lambda x: x["_stamp"], reverse=True)
        for item in result:
            item.pop("_stamp", None)
        return result[:10]

    return cached("news", load, ttl=300)


# ------------------------------------------------------------
# Search
# ------------------------------------------------------------

TEAM_ALIASES = {
    "לייקרס": "Los Angeles Lakers",
    "לוס אנג'לס לייקרס": "Los Angeles Lakers",
    "הפועל ירושלים": "Hapoel Jerusalem",
    "הפועל ירושלים כדורסל": "Hapoel Jerusalem",
    "מנצ'סטר יונייטד": "Manchester United",
    "מנצסטר יונייטד": "Manchester United",
}


def translate_alias(q: str):
    q_norm = q.strip().lower()
    for he, en in TEAM_ALIASES.items():
        if q_norm == he.lower():
            return en
    return q


def search_football_players(q: str):
    data = safe_api_get(
        FOOTBALL_BASE,
        "/players",
        {"search": q, "season": current_football_season()}
    )
    output = []
    for item in data.get("response") or []:
        p = item.get("player") or {}
        stats = (item.get("statistics") or [{}])[0]
        team = stats.get("team") or {}
        output.append({
            "id": p.get("id"),
            "name": p.get("name"),
            "sport": "Football",
            "country": p.get("nationality"),
            "team": team.get("name"),
            "age": p.get("age"),
            "shirt_number": None,
            "image": p.get("photo") or "",
            "source": "football",
        })
    return output[:10]


def search_basketball_players(q: str):
    data = safe_api_get(BASKETBALL_BASE, "/players", {"search": q})
    output = []
    for p in data.get("response") or []:
        team = p.get("team") or {}
        output.append({
            "id": p.get("id"),
            "name": p.get("name"),
            "sport": "Basketball",
            "country": p.get("nationality"),
            "team": team.get("name"),
            "age": p.get("age"),
            "shirt_number": p.get("number"),
            "image": p.get("photo") or "",
            "source": "basketball",
        })
    return output[:10]


def search_football_teams(q: str):
    data = safe_api_get(FOOTBALL_BASE, "/teams", {"search": q})
    output = []
    for item in data.get("response") or []:
        t = item.get("team") or {}
        output.append({
            "id": t.get("id"),
            "name": t.get("name"),
            "sport": "Football",
            "country": (t.get("country") or ""),
            "logo": t.get("logo") or "",
            "source": "football",
        })
    return output[:10]


def search_basketball_teams(q: str):
    data = safe_api_get(BASKETBALL_BASE, "/teams", {"search": q})
    output = []
    for t in data.get("response") or []:
        output.append({
            "id": t.get("id"),
            "name": t.get("name"),
            "sport": "Basketball",
            "country": (t.get("country") or {}).get("name") if isinstance(t.get("country"), dict) else t.get("country"),
            "logo": t.get("logo") or "",
            "source": "basketball",
        })
    return output[:10]


def search_all(q: str):
    q = translate_alias(q)
    # Do the two sports in sequence to keep API rate use predictable.
    football_players = search_football_players(q) if len(q) >= 3 else []
    basketball_players = search_basketball_players(q) if len(q) >= 3 else []
    football_teams = search_football_teams(q) if len(q) >= 3 else []
    basketball_teams = search_basketball_teams(q) if len(q) >= 3 else []

    return {
        "query": q,
        "players": (football_players + basketball_players)[:10],
        "teams": (football_teams + basketball_teams)[:10],
    }


def football_player_profile(player_id: int):
    data = safe_api_get(
        FOOTBALL_BASE,
        "/players",
        {"id": player_id, "season": current_football_season()}
    )
    item = (data.get("response") or [None])[0]
    if not item:
        return {}
    p = item.get("player") or {}
    stats = (item.get("statistics") or [{}])[0]
    team = stats.get("team") or {}
    return {
        "id": p.get("id"),
        "name": p.get("name"),
        "image": p.get("photo") or "",
        "team": team.get("name"),
        "team_id": team.get("id"),
        "team_logo": team.get("logo") or "",
        "age": p.get("age"),
        "shirt_number": stats.get("games", {}).get("number"),
        "country": p.get("nationality"),
    }


def basketball_player_profile(player_id: int):
    data = safe_api_get(BASKETBALL_BASE, "/players", {"id": player_id})
    p = (data.get("response") or [None])[0]
    if not p:
        return {}
    team = p.get("team") or {}
    return {
        "id": p.get("id"),
        "name": p.get("name"),
        "image": p.get("photo") or "",
        "team": team.get("name"),
        "team_id": team.get("id"),
        "team_logo": team.get("logo") or "",
        "age": p.get("age"),
        "shirt_number": p.get("number"),
        "country": p.get("nationality"),
    }


def football_team_profile(team_id: int):
    data = safe_api_get(FOOTBALL_BASE, "/teams", {"id": team_id})
    item = (data.get("response") or [None])[0]
    if not item:
        return {}
    t = item.get("team") or {}
    return {
        "id": t.get("id"),
        "name": t.get("name"),
        "logo": t.get("logo") or "",
        "country": t.get("country") or "",
        "sport": "Football",
        "city": (item.get("venue") or {}).get("city"),
    }


def basketball_team_profile(team_id: int):
    data = safe_api_get(BASKETBALL_BASE, "/teams", {"id": team_id})
    t = (data.get("response") or [None])[0]
    if not t:
        return {}
    country = t.get("country")
    country_name = country.get("name") if isinstance(country, dict) else country
    return {
        "id": t.get("id"),
        "name": t.get("name"),
        "logo": t.get("logo") or "",
        "country": country_name or "",
        "sport": "Basketball",
        "city": t.get("city"),
    }


def team_profile(team_id: int, source: str):
    if source == "basketball":
        details = basketball_team_profile(team_id)
        # Load team games in current season.
        season = current_basketball_season()
        data = safe_api_get(
            BASKETBALL_BASE,
            "/games",
            {"team": team_id, "season": season}
        )
        events = [normalize_basketball_game(x) for x in (data.get("response") or [])]
    else:
        details = football_team_profile(team_id)
        season = current_football_season()
        data = safe_api_get(
            FOOTBALL_BASE,
            "/fixtures",
            {"team": team_id, "season": season}
        )
        events = [normalize_football_fixture(x) for x in (data.get("response") or [])]

    past, future = split_past_future(events)

    positions = []
    for league in all_standings():
        for table in league.get("tables", []):
            for row in table.get("rows", []):
                if str(row.get("team_id")) == str(team_id):
                    positions.append({
                        "league": league["name_he"],
                        "position": row.get("position"),
                    })

    return {
        **details,
        "standings": positions,
        "last": past[0] if past else None,
        "next": future[0] if future else None,
    }


# ------------------------------------------------------------
# Flask routes
# ------------------------------------------------------------

@app.get("/")
def index():
    return render_template("index.html")


@app.get("/health")
def health():
    return jsonify({"status": "ok", "api_key_configured": bool(API_KEY)})


@app.get("/api/home")
def api_home():
    return jsonify(cached("home", home_data, ttl=600))


@app.get("/api/results")
def api_results():
    data = cached("all-league-fixtures", all_league_fixture_data, ttl=600)
    return jsonify({
        "leagues": [
            {
                "key": x["key"],
                "name": x["name"],
                "name_he": x["name_he"],
                "sport": x["sport"],
                "events": x["events_past"],
            }
            for x in data
        ],
        "updated_at": iso_now(),
        "configured": bool(API_KEY),
    })


@app.get("/api/upcoming")
def api_upcoming():
    data = cached("all-league-fixtures", all_league_fixture_data, ttl=600)
    return jsonify({
        "leagues": [
            {
                "key": x["key"],
                "name": x["name"],
                "name_he": x["name_he"],
                "sport": x["sport"],
                "events": x["events_future"],
            }
            for x in data
        ],
        "updated_at": iso_now(),
        "configured": bool(API_KEY),
    })


@app.get("/api/standings")
def api_standings():
    return jsonify({
        "leagues": cached("all-standings", all_standings, ttl=1200),
        "updated_at": iso_now(),
        "configured": bool(API_KEY),
    })


@app.get("/api/search")
def api_search():
    q = request.args.get("q", "").strip()
    if not q or len(q) < 3:
        return jsonify({"query": q, "players": [], "teams": []})
    return jsonify(cached(
        f"search:{q.lower()}",
        lambda: search_all(q),
        ttl=600
    ))


@app.get("/api/player/<source>/<int:player_id>")
def api_player(source: str, player_id: int):
    if source == "basketball":
        return jsonify(basketball_player_profile(player_id))
    return jsonify(football_player_profile(player_id))


@app.get("/api/team/<source>/<int:team_id>")
def api_team(source: str, team_id: int):
    return jsonify(cached(
        f"team-profile:{source}:{team_id}",
        lambda: team_profile(team_id, source),
        ttl=600
    ))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
