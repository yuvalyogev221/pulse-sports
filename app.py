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
from curl_cffi import requests as curl_requests
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

API_BASES = [
    "https://api.sofascore.app/api/v1",
    "https://api.sofascore.com/api/v1",
    "https://www.sofascore.com/api/v1",
]
NEWS_BASE = "https://news.google.com/rss/search"
CACHE_TTL = 60
MAX_PAGES = 40

# SofaScore uses WAF protections that can reject ordinary Python requests with HTTP 403.
# curl_cffi lets us use a current Chrome TLS/HTTP fingerprint.
session = curl_requests.Session(impersonate="chrome")
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9,he;q=0.8",
    "Referer": "https://www.sofascore.com/",
    "Origin": "https://www.sofascore.com",
    "X-Requested-With": "XMLHttpRequest",
})

# Warm up the browser-like session so SofaScore can set any edge/WAF cookies first.
try:
    session.get("https://www.sofascore.com/", timeout=10)
except Exception as exc:
    app.logger.warning("SofaScore warm-up request failed: %s", exc)

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


def get_json(path: str, params: dict[str, Any] | None = None, timeout: int = 12):
    last_error = None
    for base in API_BASES:
        url = f"{base}{path}"
        try:
            r = session.get(url, params=params, timeout=timeout, impersonate="chrome")
            if r.status_code == 403:
                last_error = requests.HTTPError(f"403 Client Error: Forbidden for url: {url}")
                continue
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            last_error = exc
            continue
    raise last_error or RuntimeError("All SofaScore API endpoints failed")


def safe_get_json(path: str, params: dict[str, Any] | None = None):
    try:
        return get_json(path, params)
    except Exception as exc:
        app.logger.warning("Upstream API request failed: %s params=%r error=%s", path, params, exc)
        return {}


def current_season_year() -> int:
    now = datetime.now(timezone.utc)
    return now.year if now.month >= 7 else now.year - 1


def season_for_tournament(tournament_id: int):
    def load():
        data = get_json(f"/unique-tournament/{tournament_id}/seasons")
        seasons = data.get("seasons") or []
        target = current_season_year()
        scored = []
        for s in seasons:
            name = str(s.get("name", ""))
            m = re.search(r"(20\d{2})", name)
            start_year = int(m.group(1)) if m else -1
            scored.append((0 if start_year == target else 1, -abs(start_year - target), s))
        scored.sort(key=lambda x: (x[0], x[1]))
        return scored[0][2] if scored else None
    return cached(f"season:{tournament_id}:{current_season_year()}", load, ttl=3600)


LEAGUES = [
    {"key": "winner", "name": "Winner League", "name_he": "ליגת ווינר סל", "sport": "basketball", "tournament_id": 1197},
    {"key": "nba", "name": "NBA", "name_he": "NBA", "sport": "basketball", "tournament_id": 132},
    {"key": "premier", "name": "Premier League", "name_he": "Premier League", "sport": "football", "tournament_id": 17},
    {"key": "champions", "name": "UEFA Champions League", "name_he": "Champions League", "sport": "football", "tournament_id": 7},
    {"key": "bundesliga", "name": "Bundesliga", "name_he": "Bundesliga", "sport": "football", "tournament_id": 35},
    {"key": "laliga", "name": "LaLiga", "name_he": "LaLiga", "sport": "football", "tournament_id": 8},
    {"key": "seriea", "name": "Serie A", "name_he": "Serie A", "sport": "football", "tournament_id": 23},
    {"key": "ligue1", "name": "Ligue 1", "name_he": "Ligue 1", "sport": "football", "tournament_id": 34},
    {"key": "europa", "name": "UEFA Europa League", "name_he": "Europa League", "sport": "football", "tournament_id": 679},
]

FAVORITE_TEAMS = [
    {"key": "hapoel", "id": 6682, "sport": "basketball", "label": "הפועל ירושלים"},
    {"key": "lakers", "id": 3427, "sport": "basketball", "label": "Los Angeles Lakers"},
    {"key": "manutd", "id": 35, "sport": "football", "label": "Manchester United"},
]


def img_team(team_id: int | str | None) -> str:
    return f"https://api.sofascore.com/api/v1/team/{team_id}/image" if team_id else ""


def normalize_event(event: dict[str, Any]) -> dict[str, Any]:
    home = event.get("homeTeam") or {}
    away = event.get("awayTeam") or {}
    status = event.get("status") or {}
    ts = event.get("startTimestamp")
    dt = datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None

    home_score = (event.get("homeScore") or {})
    away_score = (event.get("awayScore") or {})

    return {
        "id": event.get("id"),
        "date": dt.isoformat() if dt else None,
        "home": {
            "id": home.get("id"),
            "name": home.get("name", "Unknown"),
            "logo": img_team(home.get("id")),
        },
        "away": {
            "id": away.get("id"),
            "name": away.get("name", "Unknown"),
            "logo": img_team(away.get("id")),
        },
        "home_score": home_score.get("current"),
        "away_score": away_score.get("current"),
        "status": status.get("type", "notstarted"),
        "status_code": status.get("code"),
        "status_text": status.get("description") or status.get("type", ""),
        "round": event.get("roundInfo", {}).get("round"),
        "tournament": (event.get("tournament") or {}).get("name"),
    }


def paginate_events(path_builder, max_pages=MAX_PAGES):
    events = []
    for page in range(max_pages):
        data = safe_get_json(path_builder(page))
        batch = data.get("events") or []
        events.extend(batch)
        if not data.get("hasNextPage") or not batch:
            break
    return events


def team_schedule(team_id: int, direction: str):
    def load():
        raw = paginate_events(
            lambda page: f"/team/{team_id}/events/{direction}/{page}",
            max_pages=10
        )
        return [normalize_event(e) for e in raw]
    return cached(f"team:{team_id}:{direction}", load)


def league_events(league: dict[str, Any], direction: str):
    season = season_for_tournament(league["tournament_id"])
    if not season:
        return []

    def load():
        raw = paginate_events(
            lambda page: f"/unique-tournament/{league['tournament_id']}/season/{season['id']}/events/{direction}/{page}"
        )
        return [normalize_event(e) for e in raw]
    return cached(
        f"league:{league['key']}:{season['id']}:{direction}",
        load
    )


def standings(league: dict[str, Any]):
    season = season_for_tournament(league["tournament_id"])
    if not season:
        return []

    def load():
        data = safe_get_json(
            f"/unique-tournament/{league['tournament_id']}/season/{season['id']}/standings/total"
        )
        result = []
        for table in data.get("standings") or []:
            rows = []
            for row in table.get("rows") or []:
                team = row.get("team") or {}
                rows.append({
                    "position": row.get("position"),
                    "team_id": team.get("id"),
                    "team": team.get("name"),
                    "logo": img_team(team.get("id")),
                    "played": row.get("matches"),
                    "wins": row.get("wins"),
                    "draws": row.get("draws"),
                    "losses": row.get("losses"),
                    "for": row.get("scoresFor"),
                    "against": row.get("scoresAgainst"),
                    "points": row.get("points"),
                    "pct": row.get("percentage"),
                })
            result.append({
                "name": table.get("name") or league["name"],
                "rows": rows
            })
        return result
    return cached(f"standings:{league['key']}:{season['id']}", load, ttl=120)


def team_news_queries(team):
    if team["key"] == "hapoel":
        return ["Hapoel Jerusalem basketball", "הפועל ירושלים כדורסל"]
    if team["key"] == "lakers":
        return ["Los Angeles Lakers"]
    return ["Manchester United"]


def parse_news(query: str):
    try:
        r = session.get(
            NEWS_BASE,
            params={"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"},
            timeout=10,
        )
        r.raise_for_status()
        root = ET.fromstring(r.text)
        items = []
        for item in root.findall("./channel/item"):
            title = item.findtext("title") or ""
            link = item.findtext("link") or ""
            pub = item.findtext("pubDate") or ""
            source = item.find("source")
            source_name = source.text if source is not None else "News"
            try:
                published = parsedate_to_datetime(pub).astimezone(timezone.utc)
                iso = published.isoformat()
                stamp = published.timestamp()
            except Exception:
                iso, stamp = None, 0

            items.append({
                "title": html.unescape(title),
                "url": link,
                "source": source_name,
                "published": iso,
                "_stamp": stamp,
            })
        return items
    except Exception:
        return []


def latest_news():
    all_items = []
    for team in FAVORITE_TEAMS:
        for query in team_news_queries(team):
            all_items.extend(parse_news(query))
    dedup = {}
    for item in all_items:
        key = item["url"] or item["title"]
        dedup[key] = item
    result = list(dedup.values())
    result.sort(key=lambda x: x.get("_stamp", 0), reverse=True)
    for item in result:
        item.pop("_stamp", None)
    return result[:10]


def build_home():
    favorites = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        future_map = {}
        for team in FAVORITE_TEAMS:
            future_map[pool.submit(team_schedule, team["id"], "last")] = (team, "last")
            future_map[pool.submit(team_schedule, team["id"], "next")] = (team, "next")
        grouped = {}
        for future in as_completed(future_map):
            team, direction = future_map[future]
            grouped.setdefault(team["key"], {})[direction] = future.result()

    for team in FAVORITE_TEAMS:
        item = grouped.get(team["key"], {})
        last_games = item.get("last", [])
        next_games = item.get("next", [])
        for game in last_games:
            game["favorite_key"] = team["key"]
            game["favorite_label"] = team["label"]
        for game in next_games:
            game["favorite_key"] = team["key"]
            game["favorite_label"] = team["label"]
        favorites.append({
            **team,
            "results": sorted(last_games, key=lambda x: x.get("date") or "", reverse=True)[:20],
            "next": sorted(next_games, key=lambda x: x.get("date") or "")[:20],
        })

    results = []
    upcoming = []
    for t in favorites:
        results.extend(t["results"])
        upcoming.extend(t["next"])

    results.sort(key=lambda x: x.get("date") or "", reverse=True)
    upcoming.sort(key=lambda x: x.get("date") or "")

    return {
        "results": results,
        "upcoming": upcoming,
        "news": latest_news(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def all_league_data(direction: str):
    output = {}
    with ThreadPoolExecutor(max_workers=len(LEAGUES)) as pool:
        futures = {
            pool.submit(league_events, league, direction): league
            for league in LEAGUES
        }
        for f in as_completed(futures):
            league = futures[f]
            try:
                events = f.result()
            except Exception:
                events = []
            events.sort(
                key=lambda x: x.get("date") or "",
                reverse=(direction == "last")
            )
            output[league["key"]] = {
                "key": league["key"],
                "name": league["name"],
                "name_he": league["name_he"],
                "events": events,
            }
    return [output[l["key"]] for l in LEAGUES]


def search_everything(query: str):
    q = query.strip()
    if not q:
        return {"query": q, "players": [], "teams": []}

    data = safe_get_json("/search/all", params={"q": q})
    results = data.get("results") or []

    players = []
    teams = []
    for item in results[:20]:
        entity = item.get("entity") or {}
        item_type = item.get("type") or item.get("entityType") or ""
        name = entity.get("name") or item.get("name")
        entity_id = entity.get("id") or item.get("id")
        if not entity_id or not name:
            continue

        normalized = {
            "id": entity_id,
            "name": name,
            "sport": (entity.get("sport") or {}).get("name"),
            "country": ((entity.get("country") or {}).get("name")),
        }
        if str(item_type).lower() == "player" or "player" in item:
            players.append(normalized)
        elif str(item_type).lower() == "team" or "team" in item:
            teams.append(normalized)

    # Fallback to direct team/player lookups when the search response omits type.
    for item in results[:10]:
        entity = item.get("entity") or {}
        entity_id = entity.get("id") or item.get("id")
        name = entity.get("name") or item.get("name")
        if not entity_id or not name:
            continue

        if not any(t["id"] == entity_id for t in teams):
            t = safe_get_json(f"/team/{entity_id}")
            if (t.get("team") or {}).get("name"):
                teams.append({
                    "id": entity_id,
                    "name": (t.get("team") or {}).get("name"),
                    "sport": ((t.get("team") or {}).get("sport") or {}).get("name"),
                    "country": ((t.get("team") or {}).get("country") or {}).get("name"),
                })

        if not any(p["id"] == entity_id for p in players):
            p = safe_get_json(f"/player/{entity_id}")
            if (p.get("player") or {}).get("name"):
                players.append({
                    "id": entity_id,
                    "name": (p.get("player") or {}).get("name"),
                    "sport": ((p.get("player") or {}).get("sport") or {}).get("name"),
                    "country": ((p.get("player") or {}).get("country") or {}).get("name"),
                })

    return {
        "query": q,
        "players": players[:10],
        "teams": teams[:10],
    }


def player_details(player_id: int):
    def load():
        data = safe_get_json(f"/player/{player_id}")
        p = data.get("player") or {}
        team = p.get("team") or {}
        dob = p.get("dateOfBirthTimestamp")
        age = None
        if dob:
            age = max(0, int((time.time() - dob) / (365.2425 * 24 * 3600)))
        return {
            "id": p.get("id"),
            "name": p.get("name"),
            "image": f"https://api.sofascore.com/api/v1/player/{player_id}/image",
            "team": team.get("name"),
            "team_id": team.get("id"),
            "team_logo": img_team(team.get("id")),
            "age": age,
            "shirt_number": p.get("shirtNumber"),
            "country": (p.get("country") or {}).get("name"),
        }
    return cached(f"player:{player_id}", load, ttl=300)


def team_search_details(team_id: int):
    def load():
        data = safe_get_json(f"/team/{team_id}")
        t = data.get("team") or {}
        return {
            "id": t.get("id"),
            "name": t.get("name"),
            "logo": img_team(team_id),
            "country": (t.get("country") or {}).get("name"),
            "sport": (t.get("sport") or {}).get("name"),
            "city": t.get("city"),
        }
    return cached(f"team:{team_id}:details", load, ttl=300)


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/home")
def api_home():
    return jsonify(cached("home", build_home))


@app.get("/api/results")
def api_results():
    return jsonify({"leagues": cached("results", lambda: all_league_data("last"))})


@app.get("/api/upcoming")
def api_upcoming():
    return jsonify({"leagues": cached("upcoming", lambda: all_league_data("next"))})


@app.get("/api/standings")
def api_standings():
    def load():
        output = []
        with ThreadPoolExecutor(max_workers=len(LEAGUES)) as pool:
            futures = {pool.submit(standings, league): league for league in LEAGUES}
            for f in as_completed(futures):
                league = futures[f]
                try:
                    tables = f.result()
                except Exception:
                    tables = []
                output.append({
                    "key": league["key"],
                    "name": league["name"],
                    "name_he": league["name_he"],
                    "tables": tables,
                })
        order = {l["key"]: i for i, l in enumerate(LEAGUES)}
        output.sort(key=lambda x: order[x["key"]])
        return output

    return jsonify({"leagues": cached("standings", load, ttl=120)})


@app.get("/api/search")
def api_search():
    q = request.args.get("q", "")
    return jsonify(cached(f"search:{q.strip().lower()}", lambda: search_everything(q), ttl=120))


@app.get("/api/player/<int:player_id>")
def api_player(player_id: int):
    return jsonify(player_details(player_id))


@app.get("/api/team/<int:team_id>")
def api_team(team_id: int):
    details = team_search_details(team_id)
    results = sorted(team_schedule(team_id, "last"), key=lambda x: x.get("date") or "", reverse=True)[:1]
    upcoming = sorted(team_schedule(team_id, "next"), key=lambda x: x.get("date") or "")[:1]

    positions = []
    for league in LEAGUES:
        try:
            for table in standings(league):
                for row in table["rows"]:
                    if row["team_id"] == team_id:
                        positions.append({
                            "league": league["name_he"],
                            "position": row["position"],
                        })
        except Exception:
            pass

    return jsonify({
        **details,
        "standings": positions,
        "last": results[0] if results else None,
        "next": upcoming[0] if upcoming else None,
    })


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
