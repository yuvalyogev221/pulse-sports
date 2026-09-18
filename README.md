# PULSE — Your world of sports

PULSE is a Flask sports dashboard built from the supplied product specification.

## Why this rebuild is different

Version 3 adds a build identifier and provider indicator to `/health` and `/api/home` so the deployed version can be verified immediately.

The previous versions depended on public web endpoints that returned HTTP 403 from Render. This rebuild does **not** use SofaScore or ESPN.

The sports-data layer uses **API-Sports**, with one API key shared between its Football and Basketball products. The key is kept server-side in the `API_SPORTS_KEY` environment variable.

## Features

- 5 pages: Home, Results, Upcoming Games, Standings, Search
- RTL Hebrew UI
- Matte-black modern sports design
- Mobile responsive
- Swipe navigation
- Collapsible sections
- Manual refresh + 60-second home refresh
- Israel timezone
- Home favorites: Hapoel Jerusalem, Los Angeles Lakers, Manchester United
- Football: Premier League, Champions League, Bundesliga, LaLiga, Serie A, Ligue 1, Europa League
- Basketball: NBA and Winner League (resolved from the API catalogue)
- Player and team search
- Player/team detail screens
- News via Google News RSS
- `/health` deployment check
- Friendly configuration screen when the API key is missing

## API key

1. Create a free API-Sports account at:
   https://dashboard.api-football.com/
2. Copy the API key from your dashboard.
3. In Render open:
   **Service → Environment → Add Environment Variable**
4. Add:
   - Key: `API_SPORTS_KEY`
   - Value: your API key
5. Save and redeploy.

The current API-Sports free plan is documented as 100 requests/day. The integration therefore caches league data for 10 minutes and standings for 20 minutes.

## Local run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export API_SPORTS_KEY="YOUR_KEY"
python app.py
```

Open:
http://127.0.0.1:5000

## Render

Build Command:
`pip install -r requirements.txt`

Start Command:
`gunicorn app:app`

Health Check:
`/health`

## GitHub structure

```text
PULSE_REBUILT/
├── app.py
├── requirements.txt
├── Procfile
├── render.yaml
├── .gitignore
├── README.md
├── templates/
│   └── index.html
└── static/
    ├── app.js
    └── style.css
```

## Important

The free plan has request quotas. For a high-traffic public product, use a paid/production API plan and increase caching/storage accordingly.


## Verify the deployed build

Open:

`https://YOUR-RENDER-URL/health`

Expected JSON includes:

- `"status": "ok"`
- `"build": "pulse-api-sports-v3"`
- `"provider": "API-Sports"`

If the word `TheSportsDB`, `ESPN`, or `SofaScore` appears in the Render log after deploying v3, the old code is still being served by that Render service/branch.

## Troubleshooting

After deployment, open `/api/diagnostics`. It checks the API-Sports account status, one Premier League fixtures request, and one NBA request. It returns the provider's `errors` field so configuration/quota problems are visible instead of being silently rendered as empty sections.

For NBA, API-NBA currently uses `https://v2.nba.api-sports.io`, the league value `standard`, and four-digit season values such as `2026`.
