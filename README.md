# PULSE — Your world of sports

PULSE is a Flask sports dashboard built from the requirements in the supplied specification.

## Included

- 5 pages: Home, Results, Upcoming Games, Standings, Search
- RTL Hebrew UI
- Matte black modern sports design
- Manual page navigation + swipe navigation on touch devices
- Home favorites: Hapoel Jerusalem, Los Angeles Lakers, Manchester United
- Results and upcoming games grouped by league
- Current-season schedules
- League standings
- Search for teams and players
- Player profile details
- Team profile with standings positions + previous/next game
- 60-second automatic refresh of home data
- Manual refresh button
- Loading / empty / error-safe UI
- Israel time zone (Asia/Jerusalem)
- YYYY/MM/DD display format

## Data provider

The backend uses the public Sofascore API endpoints documented by community-maintained API references. The app keeps the data provider behind Flask routes so the browser never has to know the upstream endpoints.

News are collected from Google News RSS searches and combined into the 10 newest unique articles.

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Then open:

http://127.0.0.1:5000

## GitHub

Create a new GitHub repository and upload the project root:

```text
pulse_sports/
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

## Render

Option A — connect the GitHub repository as a new Web Service.

Use:

- Language: Python
- Build Command: `pip install -r requirements.txt`
- Start Command: `gunicorn app:app`
- Health Check Path: `/health`

The included `render.yaml` can also be used as the service blueprint.

## Important notes

1. The app is designed to fail gracefully if an upstream sports/news source is temporarily unavailable.
2. Current-season data is resolved dynamically rather than hardcoding `2026/27`, so the app can roll forward into future seasons.
3. The public upstream API is not an official contractual feed for PULSE. For a commercial/public product, replace it with a licensed sports-data provider later.
4. If your provider changes endpoints or rate limits, the frontend does not need to change; update only `app.py`.
